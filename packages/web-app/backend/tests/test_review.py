"""Review persistence, conflicts, geometry validation, and truthful exports."""
import json
import sqlite3
import geopandas as gpd
import pytest
from roof_pv_review.app import create_app
from fastapi.testclient import TestClient


def body(**updates):
    return {"expected_version": 0, "status": "confirmed", "reviewer": "Test reviewer", **updates}


def test_review_persists_across_service_restart_and_conflicts(review):
    client, store, root, run_id, feature = review
    path = f"/api/runs/{run_id}/candidates/test-candidate"
    assert client.patch(path, json=body(note="Reviewed image")).status_code == 200
    assert client.patch(path, json=body(status="rejected", reason="stale")).status_code == 409
    restarted = TestClient(create_app(root, root / "none"))
    snapshot = restarted.get(f"/api/runs/{run_id}").json()
    assert snapshot["revision"] == 1
    assert snapshot["candidates"]["features"][0]["properties"]["status"] == "confirmed"
    assert len(snapshot["events"]) == 1
    payload = json.loads(snapshot["events"][0]["payload"])
    assert payload["before"]["properties"]["status"] == "pending"


def test_rejection_requires_reason_and_geometry_stays_inside_image(review):
    client, _, _, run_id, feature = review
    path = f"/api/runs/{run_id}/candidates/test-candidate"
    assert client.patch(path, json=body(status="uncertain")).status_code == 422
    outside = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    assert client.patch(path, json=body(geometry=outside)).status_code == 422
    assert client.patch(path, json=body(reviewer="  ")).status_code == 422
    assert client.get(f"/api/runs/{run_id}").json()["revision"] == 0


def test_manual_candidate_is_pending_and_area_is_separate(review):
    client, _, _, run_id, feature = review
    data = {"geometry": feature["geometry"], "reviewer": "Test reviewer", "note": "Missed candidate"}
    response = client.post(f"/api/runs/{run_id}/candidates", json=data)
    assert response.status_code == 201
    assert client.post(f"/api/runs/{run_id}/reviewed-areas", json=data).status_code == 201
    snapshot = client.get(f"/api/runs/{run_id}").json()
    assert snapshot["revision"] == 2
    assert len(snapshot["reviewed_areas"]["features"]) == 1
    assert len(snapshot["candidates"]["features"]) == 2
    manual = next(f for f in snapshot["candidates"]["features"] if f["properties"]["category"] == "manual")
    assert manual["properties"]["status"] == "pending"


def test_export_snapshot_is_consistent_and_every_spatial_layer_is_3035(review):
    client, _, root, run_id, _ = review
    before = client.post(f"/api/runs/{run_id}/exports").json()
    assert before["confirmed_count"] == 0
    assert client.patch(f"/api/runs/{run_id}/candidates/test-candidate", json=body()).status_code == 200
    after_response = client.post(f"/api/runs/{run_id}/exports")
    assert after_response.status_code == 201
    after = after_response.json()
    assert after["confirmed_count"] == 1 and after["review_revision"] == 1
    output = root / "exports" / (after["export_id"] + ".gpkg")
    for name in ["candidates", "confirmed_pv", "coverage", "reviewed_areas", "rgbi_observations", "rgb_observations"]:
        frame = gpd.read_file(output, layer=name)
        assert frame.crs.to_epsg() == 3035
        assert frame.geometry.is_valid.all()
    assert len(gpd.read_file(output, layer="confirmed_pv")) == 1
    old = root / "exports" / (before["export_id"] + ".gpkg")
    assert len(gpd.read_file(old, layer="confirmed_pv")) == 0
    with sqlite3.connect(output) as db:
        metadata = json.loads(db.execute("SELECT metadata_json FROM export_metadata").fetchone()[0])
        assert metadata["review_revision"] == 1
        assert db.execute("SELECT count(*) FROM review_events").fetchone()[0] == 1
    assert client.get(after["download_url"]).status_code == 200


def test_incomplete_run_not_loaded_and_reimport_is_idempotent(review):
    client, store, root, run_id, _ = review
    folder = root / "runs" / "incomplete"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"status": "failed"}))
    for _ in range(2):
        store.import_runs()
    assert len(client.get('/api/runs').json()) == 1
    assert len(store.snapshot(run_id)["candidates"]["features"]) == 1


def test_original_geometry_is_retained_and_can_be_restored(review):
    client, store, _, run_id, feature = review
    modified = json.loads(json.dumps(feature["geometry"]))
    modified["coordinates"][0][1][0] -= .000001
    path = f"/api/runs/{run_id}/candidates/test-candidate"
    response = client.patch(path, json=body(geometry=modified))
    assert response.status_code == 200
    properties = response.json()["properties"]
    assert properties["geometry_modified"]
    assert properties["original_geometry"] == json.loads(json.dumps(feature["geometry"]))
    assert client.patch(path, json=body(expected_version=1, reset_geometry=True)).status_code == 200
    assert not store.snapshot(run_id)["candidates"]["features"][0]["properties"]["geometry_modified"]


def test_local_service_rejects_cross_origin_browser_writes(review):
    client, _, _, run_id, _ = review
    response = client.post(f"/api/runs/{run_id}/exports", headers={"Origin": "https://unrelated.example"})
    assert response.status_code == 403
