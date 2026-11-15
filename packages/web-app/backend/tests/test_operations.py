"""Lineage-preserving merge/split, conservative classifications, and undo."""
import json
from shapely.geometry import shape
from roof_pv_review.store import checksum


def test_merge_retires_parents_and_never_inherits_confirmation(review):
    client, store, root, run_id, original = review
    base = f"/api/runs/{run_id}"
    source_file = root / "runs" / run_id / "candidates.geojson"
    before = checksum(source_file)
    added = client.post(base + "/candidates", json={"geometry": original["geometry"], "reviewer": "Reviewer"}).json()["id"]
    assert client.patch(base + "/candidates/test-candidate", json={"expected_version": 0, "status": "confirmed", "reviewer": "Reviewer", "mounting": "roof"}).status_code == 200
    payload = {"operation": "merge", "expected_versions": {"test-candidate": 1, added: 0}, "reviewer": "Reviewer"}
    merged = client.post(base + "/transform", json=payload)
    assert merged.status_code == 201, merged.text
    state = store.snapshot(run_id)
    assert len(state["candidates"]["features"]) == 1
    assert len(state["retired_candidates"]["features"]) == 2
    child = state["candidates"]["features"][0]
    assert child["properties"]["status"] == "pending"
    assert child["properties"]["mounting"] == "unknown"
    assert set(child["properties"]["parent_ids"]) == {"test-candidate", added}
    assert checksum(source_file) == before
    assert client.post(base + "/transform", json=payload).status_code == 409
    assert client.post(base + "/undo", json={"expected_revision": state["revision"], "reviewer": "Reviewer"}).status_code == 200
    restored = store.snapshot(run_id)
    assert len(restored["candidates"]["features"]) == 2
    parent = next(f for f in restored["candidates"]["features"] if f["id"] == "test-candidate")
    assert parent["properties"]["status"] == "confirmed" and parent["properties"]["mounting"] == "roof"


def test_split_requires_a_crossing_line_and_preserves_area(review):
    client, store, _, run_id, original = review
    base = f"/api/runs/{run_id}"
    west, south, east, north = shape(original["geometry"]).bounds
    payload = {"operation": "split", "expected_versions": {"test-candidate": 0}, "reviewer": "Reviewer", "cut_line": {"type": "LineString", "coordinates": [[west - .001, (south + north)/2], [east + .001, (south + north)/2]]}}
    old_area = store.snapshot(run_id)["candidates"]["features"][0]["properties"]["projected_area_m2"]
    response = client.post(base + "/transform", json=payload)
    assert response.status_code == 201, response.text
    state = store.snapshot(run_id)
    assert len(state["candidates"]["features"]) == 2
    assert abs(sum(f["properties"]["projected_area_m2"] for f in state["candidates"]["features"]) - old_area) < .01
    assert all(f["properties"]["status"] == "pending" for f in state["candidates"]["features"])


def test_undo_checks_revision_and_keeps_audit_history(review):
    client, store, _, run_id, _ = review
    base = f"/api/runs/{run_id}"
    assert client.patch(base + "/candidates/test-candidate", json={"expected_version": 0, "status": "confirmed", "reviewer": "Reviewer", "mounting": "roof"}).status_code == 200
    assert client.post(base + "/undo", json={"expected_revision": 2, "reviewer": "Reviewer"}).status_code == 409
    assert client.post(base + "/undo", json={"expected_revision": 1, "reviewer": "Reviewer"}).status_code == 200
    state = store.snapshot(run_id)
    assert state["revision"] == 2 and len(state["events"]) == 2
    assert state["candidates"]["features"][0]["properties"]["status"] == "pending"
    assert state["candidates"]["features"][0]["properties"]["mounting"] == "unknown"


def test_export_distinguishes_confirmed_pv_from_confirmed_rooftop(review):
    import geopandas as gpd
    client, _, root, run_id, _ = review
    base = f"/api/runs/{run_id}"
    assert client.patch(base + "/candidates/test-candidate", json={"expected_version": 0, "status": "confirmed", "reviewer": "Reviewer", "mounting": "ground"}).status_code == 200
    result = client.post(base + "/exports").json()
    path = root / "exports" / (result["export_id"] + ".gpkg")
    assert len(gpd.read_file(path, layer="confirmed_pv")) == 1
    assert len(gpd.read_file(path, layer="confirmed_rooftop_pv")) == 0
    points = gpd.read_file(path, layer="candidate_points")
    polygons = gpd.read_file(path, layer="candidates")
    assert points.geometry.iloc[0].within(polygons.geometry.iloc[0])
    assert points.geometry.geom_type.iloc[0] == "Point"
