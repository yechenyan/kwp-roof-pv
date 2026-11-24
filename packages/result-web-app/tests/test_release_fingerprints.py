"""Prevent publishing mixed-build PDFs, web data and GIS files."""

from pathlib import Path
import sys

import pytest
from shapely.geometry import MultiPolygon, box, mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import RANKING_INDEX, REPORT_INPUTS, VERIFIED_BUILD_FILES, digest, read, write
from package_delivery import GUIDE, REPORTS, guide_inputs, published_matches, release_fingerprints


@pytest.fixture
def verified_build(tmp_path):
    files = {}
    for name in VERIFIED_BUILD_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == RANKING_INDEX:
            write(path, {"shards": []})
        else:
            path.write_text(name)
        files[name] = digest(path)
    write(tmp_path / "verification.json", {"building_evidence_and_postcode_reconciliation": True,
                                           "build_files_sha256": files})
    for name in (*REPORTS, GUIDE):
        path = tmp_path / name
        path.write_text(name)
        input_names = guide_inputs(tmp_path) if name == GUIDE else REPORT_INPUTS
        write(path.with_suffix(".provenance.json"), {"inputs_sha256": {key: files[key] for key in input_names},
                                                    "pdf_sha256": digest(path)})
    return tmp_path


@pytest.mark.parametrize(("results_only", "expected"), [(False, REPORTS), (True, (GUIDE,))])
def test_verified_reports_and_data_can_publish(verified_build, results_only, expected):
    _, reports = release_fingerprints(verified_build, results_only=results_only)
    assert set(reports) == set(expected)


def test_replaced_pdf_cannot_publish_with_new_geo_data(verified_build):
    (verified_build / REPORTS[0]).write_text("Older report")
    with pytest.raises(ValueError, match="Report differs from the verified build"):
        release_fingerprints(verified_build)


@pytest.mark.parametrize(("name", "results_only"), [(REPORTS[1], False), (GUIDE, True)])
def test_individually_valid_report_from_different_build_is_rejected(verified_build, name, results_only):
    path = (verified_build / name).with_suffix(".provenance.json")
    report = read(path)
    report["inputs_sha256"]["summary.json"] = "different build"
    write(path, report)
    with pytest.raises(ValueError, match="Report differs from the verified build"):
        release_fingerprints(verified_build, results_only=results_only)


def test_web_file_changed_after_verification_is_rejected(verified_build):
    (verified_build / "postcode-totals.json").write_text("Older postcode data")
    with pytest.raises(ValueError, match="Build file differs from verification: postcode-totals.json"):
        release_fingerprints(verified_build)


def test_published_joint_match_uses_the_full_building_union():
    inventory = [
        {"geometry": mapping(box(i, 0, i + 1, 1)),
         "properties": {"id": key, "capture_dates": ["2026-04-22"]}}
        for i, key in enumerate(("selected", "alternative", "coordinate", "second-array"))
    ]
    geometry = MultiPolygon([box(0, 0, 1, 1), box(3, 0, 4, 1)])
    buildings = [{"geometry": mapping(geometry), "properties": {
        "building_id": "roof", "group_ids": ["second-array", "selected"], "capture_dates": ["2026-04-22"],
    }}]
    base = {"municipality": "Hohen Neuendorf", "postcode": "16540", "power_kw": 6,
            "commissioned": None, "identity_verified": False}
    records = [
        {**base, "solar_id": "joint", "association_status": "building_candidates", "score": 55,
         "alternatives": ["alternative"],
         "joint_proposal": {"group_ids": ["second-array", "selected"], "building_id": "roof",
                            "score": 49, "points": [3400, 1000, 500]}},
        {**base, "solar_id": "coordinate", "association_status": "inferred_image_association",
         "score": 81, "point": [2.5, 0.5], "group_id": "coordinate"},
        {**base, "solar_id": "unassigned", "association_status": "building_candidates", "score": 48},
    ]
    metadata = {"created_at": "example", "summary": {"joint_proposal_count": 1,
                "states": {"inferred_image_association": 1}}}

    matches = published_matches(records, inventory, buildings, metadata)

    assert matches["snapshot"] == "example"
    joint, coordinate = matches["features"]
    assert joint["id"] == "joint" and shape(joint["geometry"]).equals(geometry)
    assert joint["properties"]["group_count"] == 2
    assert joint["properties"]["score"] == 49
    assert joint["properties"]["evidence_points"] == [34, 10, 5]
    assert "alternatives" not in joint["properties"]
    assert coordinate["id"] == "coordinate" and coordinate["properties"]["point"] == [2.5, 0.5]
    assert not any(item["properties"]["identity_verified"] for item in matches["features"])
