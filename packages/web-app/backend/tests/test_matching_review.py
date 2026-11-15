"""Identity confirmation, stale proposals and GIS exports remain independent of PV review."""
import json
import sqlite3
from shapely.geometry import Point, shape
from fastapi.testclient import TestClient
from roof_pv_registry.source import atomic_json, digest
from roof_pv_registry.snapshot import normalize
from roof_pv_review.app import create_app


def prepare(review):
    client, store, root, run_id, feature = review
    folder = root / "registry/mastr-20261115-test"
    folder.mkdir(parents=True)
    atomic_json(folder / "source.json", {"url": "https://download.marktstammdatenregister.de/Gesamtdatenexport_20261115_26.2.zip", "ags": ["12065144"]})
    atomic_json(folder / "extraction.json", {"status": "completed", "source_sha256": digest(folder / "source.json")})
    for name, rows in {"solar": [{"EinheitMastrNummer": "SEE1", "Gemeindeschluessel": "12065144", "Gemeinde": "Hohen Neuendorf", "Bruttoleistung": "10", "AnzahlModule": "25", "ArtDerSolaranlage": "853", "EinheitBetriebsstatus": "35", "Inbetriebnahmedatum": "2020-01-01"}], "storage": [], "systems": [], "locations": [], "catalog": []}.items():
        atomic_json(folder / (name + ".json"), rows)
    normalize(folder)
    client.app.state.matching.rebuild(run_id)
    base = f"/api/runs/{run_id}"
    detail = client.get(base + "/matching/candidates/test-candidate").json()
    return client, store, root, base, detail


def identity(detail, **updates):
    return {"batch_id": detail["batch_id"], "solar_id": "SEE1", "expected_version": 0,
            "state": "probable_assignment", "reviewer": "Identity reviewer", "evidence_type": "attribute_comparison",
            "evidence": "The array area is compatible; independent identity remains unverified.", **updates}


def test_identity_review_is_independent_versioned_and_persists(review):
    client, store, root, base, detail = prepare(review)
    path = base + "/matching/groups/" + detail["group"]["id"]
    assert client.patch(path, json=identity(detail, state="verified_identity")).status_code == 422
    assert client.patch(path, json=identity(detail)).status_code == 200
    assert client.patch(path, json=identity(detail)).status_code == 409
    assert store.snapshot("test-run")["revision"] == 0
    restarted = TestClient(create_app(root, root / "none"))
    restored = restarted.get(path).json()
    assert restored["reviews"][0]["state"] == "probable_assignment"
    assert restored["reviews"][0]["version"] == 1
    assert restarted.get(base + "/matching/units?cohort=target").json()["total"] == 1
    assert restarted.get(base + "/matching/units/SEE1").json()["unit"]["longitude"] is None


def test_partial_batches_are_hidden_and_build_reservation_rejects_duplicate_requests(review):
    client, _, root, base, detail = prepare(review)
    hidden = root / "matching/test-run/.partial-build"
    hidden.mkdir()
    (hidden / "manifest.json").write_text(json.dumps({"created_at": "9999"}))
    assert client.get(base + "/matching/candidates/test-candidate").json()["batch_id"] == detail["batch_id"]
    service = client.app.state.matching
    service.reserve_build("test-run")
    assert client.post(base + "/matching/build").status_code == 409
    (root / "state/matching.lock").unlink()
    assert client.post(base + "/matching/build").status_code == 202


def test_map_locations_and_saved_association_filters_do_not_invent_registry_coordinates(review):
    client, _, _, base, detail = prepare(review)
    assert client.get(base + "/matching/units?association=associated").json()["total"] == 0
    assert client.get(base + "/matching/units?association=unreviewed").json()["total"] == 1
    path = base + "/matching/groups/" + detail["group"]["id"]
    assert client.patch(path, json=identity(detail)).status_code == 200
    unit = client.get(base + "/matching/units/SEE1").json()
    assert unit["unit"]["longitude"] is None and unit["unit"]["latitude"] is None
    saved = unit["reviewed_associations"][0]
    assert saved["member_ids"] == ["test-candidate"] and not saved["stale"]
    assert shape(detail["group"]["geometry"]).covers(Point(saved["position"]))
    assert client.get(path).json()["units"]["SEE1"]["id"] == "SEE1"
    rows = client.get(base + "/matching/units?association=associated").json()
    assert rows["total"] == 1 and rows["items"][0]["association_count"] == 1
    assert rows["items"][0]["association_state"] == "probable_assignment"
    assert client.get(base + "/matching/units?association=verified").json()["total"] == 0
    client.patch(base + "/candidates/test-candidate", json={"expected_version": 0, "status": "confirmed", "reviewer": "Image reviewer"})
    assert client.get(base + "/matching/units?association=associated").json()["total"] == 0
    assert client.get(base + "/matching/units/SEE1").json()["reviewed_associations"][0]["stale"]


def test_image_edits_stale_the_group_and_verified_export_requires_separate_evidence(review):
    client, store, root, base, detail = prepare(review)
    path = base + "/matching/groups/" + detail["group"]["id"]
    client.patch(base + "/candidates/test-candidate", json={"expected_version": 0, "status": "confirmed", "mounting": "roof", "reviewer": "Image reviewer"})
    assert client.get(path).json()["stale"]
    assert client.patch(path, json=identity(detail)).status_code == 409
    client.app.state.matching.rebuild("test-run")
    detail = client.get(base + "/matching/candidates/test-candidate").json()
    path = base + "/matching/groups/" + detail["group"]["id"]
    assert client.patch(path, json=identity(detail, state="verified_identity", group_confirmed=True, evidence_type="authorized_address", evidence="Authorized source record ABC-123 establishes this building and unit.")).status_code == 200
    exported = client.post(base + "/exports")
    assert exported.status_code == 201, exported.text
    metadata = exported.json()
    assert metadata["matching"]["verified_identity_count"] == 1
    with sqlite3.connect(root / "exports" / (metadata["export_id"] + ".gpkg")) as db:
        assert db.execute("SELECT count(*) FROM mastr_solar").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM verified_mastr_pv").fetchone()[0] == 1
        assert db.execute("SELECT srs_id FROM gpkg_contents WHERE table_name='verified_mastr_pv'").fetchone()[0] == 3035
        assert db.execute("SELECT count(*) FROM mastr_review_events").fetchone()[0] == 1
    client.patch(base + "/candidates/test-candidate", json={"expected_version": 1, "status": "uncertain", "reason": "Image ambiguity", "reviewer": "Image reviewer"})
    stale_export = client.post(base + "/exports").json()
    assert stale_export["matching"]["verified_identity_count"] == 0
    assert stale_export["matching"]["stale_group_count"] == 1
