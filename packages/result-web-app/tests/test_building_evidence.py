"""Check overlap accounting and the projected-area fallback."""

from pathlib import Path
import sys

import pytest
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from building_evidence import BuildingShortlists, assemble_buildings, measure_faces
from candidate_scoring import CandidateEvidence, RoofOption, pose_points, registered_poses


def group(identifier, geometry):
    return {"id": identifier, "_geometry": geometry, "roof_supported": True, "member_ids": [identifier],
            "building_parts": ["roof:part:0"], "municipalities": ["Town"], "capture_dates": ["2026-04-22"],
            "image_quality": .8}


def test_overlapping_groups_contribute_one_union_to_the_inventory():
    footprint = box(0, 0, 12, 12)
    items = [group("west", box(0, 0, 6, 4)), group("east", box(4, 0, 10, 4))]
    building, = assemble_buildings(items, {"roof": footprint}, {"10101": footprint}, {})
    assert building["projected_area_m2"] == 40
    assert building["group_ids"] == ["east", "west"]
    assert building["postcode"] == "10101"
    assert building["roof_parallel_area_m2"] is None


def test_pitched_faces_do_not_both_count_the_shared_boundary_area():
    footprint = box(0, 0, 10, 4)
    faces = [[box(0, 0, 5.01, 4).wkb_hex, 30, 90], [box(5, 0, 10, 4).wkb_hex, 30, 270]]
    method, measured, support, estimate = measure_faces(footprint, footprint, [footprint.wkb_hex, faces])
    assert method == "roof_parallel_estimate"
    assert support == 1
    assert sum(face["projected_area_m2"] for face in measured) == pytest.approx(40)
    assert estimate == pytest.approx(46.1880215)


def test_flat_roof_keeps_the_observed_projection():
    footprint = box(0, 0, 10, 4)
    method, faces, _, estimate = measure_faces(footprint, footprint, [footprint.wkb_hex, [[footprint.wkb_hex, 0, None]]])
    assert method == "projected_area_fallback"
    assert faces[0]["azimuth_deg"] is None
    assert estimate is None


def test_boundary_building_is_retained_but_excluded_from_postcode_shortlists():
    footprint = box(0, 0, 12, 12)
    candidate, = assemble_buildings([group("a", footprint)], {"roof": footprint},
                                   {"10101": box(0, 0, 6, 12), "10102": box(6, 0, 12, 12)}, {})
    assert candidate["postcode_status"] == "boundary_conflict"
    outcome = BuildingShortlists([candidate]).rank({"postcode": "10101", "municipality": "Town"})
    assert outcome["candidate_status"] == "no_postcode_candidate"
    assert outcome["candidate_pool_size"] == 0


def test_negative_evidence_keeps_the_geographic_candidate_available_for_review():
    footprint = box(0, 0, 1, 1)
    building, = assemble_buildings([group("a", footprint)], {"roof": footprint},
                                   {"10101": footprint}, {"roof": [footprint.wkb_hex, [[footprint.wkb_hex, 40, 0]]]})
    outcome = BuildingShortlists([building]).rank({
        "postcode": "10101", "municipality": "Town", "commissioned": "2024-01-01",
        "module_count": 60, "power_kw": 29, "flags": [],
        "raw": {"Hauptausrichtung": "699", "HauptausrichtungNeigungswinkel": "3179"},
    })
    assert outcome["candidate_status"] == "low_compatibility"
    assert outcome["candidate_pool_size"] == 1
    assert outcome["candidate_qualified_count"] == 0
    assert outcome["score"] == outcome["alternatives"][0]["score"] < 0
    assert outcome["alternatives"][0]["building_id"] == "roof"


def test_area_agreement_and_conflict_are_monotonic():
    evidence = CandidateEvidence({"commissioned": "2024-01-01", "module_count": 10,
                                  "power_kw": 4.2, "raw": {}, "flags": []})
    areas = (evidence.center, evidence.center * 1.15, evidence.center * 1.35, evidence.center * 3)
    points = [evidence.area_points(RoofOption(("a",), area, area, ()))[0] for area in areas]
    assert points[:3] == pytest.approx([40, 24, 0], abs=1e-10)
    assert points[3] < -24
    assert evidence.area_points(RoofOption(("a",), 10, None, ()))[0] == 0


def test_east_west_face_assignment_uses_both_directions_and_tilt_ranges():
    poses = registered_poses({"Hauptausrichtung": "704", "HauptausrichtungNeigungswinkel": "809"})
    assert pose_points(((270, 30), (90, 35)), poses) == (12, 6)
    assert pose_points(((270, 30),), poses) == (0, 0)


def test_ranking_scores_the_full_building_union_once():
    footprint = box(0, 0, 20, 5)
    groups = [group("a", box(0, 0, 2, 2)), group("b", box(1, 0, 3, 2)), group("c", box(15, 0, 17, 2))]
    lod2 = {"roof": [footprint.wkb_hex, [[footprint.wkb_hex, 30, 180]]]}
    building, = assemble_buildings(groups, {"roof": footprint}, {"10101": footprint}, lod2)
    shortlists = BuildingShortlists([building])
    measurement, = shortlists.measurements
    assert measurement.groups == ("a", "b", "c")
    assert measurement.projected == 10
    assert measurement.parallel == pytest.approx(11.5470054)

    unit = {"postcode": "10101", "municipality": "Town", "commissioned": "2022-01-01",
            "module_count": 6, "power_kw": 2.4, "flags": [],
            "raw": {"Hauptausrichtung": "699", "HauptausrichtungNeigungswinkel": "809"}}
    ranking, = shortlists.rank(unit, full=True)["_ranking"]
    assert ranking[0] == ranking[4] == 0
    assert ranking[1] > 3700
    assert ranking[2:4] == [1200, 600]


def test_strong_geometry_conflicts_reduce_the_total_below_the_candidate_threshold():
    from candidate_scoring import MINIMUM_CANDIDATE_POINTS, POINT_SCALE
    evidence = CandidateEvidence({"commissioned": "2024-01-01", "module_count": 10,
                                  "power_kw": 4.2, "flags": [],
                                  "raw": {"Hauptausrichtung": "699", "HauptausrichtungNeigungswinkel": "809"}})
    for pose, component in (((20, 30), 1), ((180, 65), 2)):
        roof = RoofOption(("a",), evidence.center, evidence.center, (pose,))
        points, _, _ = evidence.compare((roof,))
        assert points[0] == 4000
        assert points[component] <= -1200
        assert sum(points) < MINIMUM_CANDIDATE_POINTS * POINT_SCALE


def test_a_dual_face_conflict_is_not_averaged_away():
    assert pose_points(((90, 65), (270, 30)), ((90, (21, 40)), (270, (21, 40)))) == (12, -12)


def test_total_only_distinguishes_area_only_and_consistent_shape():
    from candidate_scoring import MINIMUM_CANDIDATE_POINTS, POINT_SCALE
    evidence = CandidateEvidence({"commissioned": "2024-01-01", "module_count": 10,
                                  "power_kw": 4.2, "flags": [],
                                  "raw": {"Hauptausrichtung": "699", "HauptausrichtungNeigungswinkel": "809"}})
    area_only = RoofOption(("a",), evidence.center, evidence.center, ())
    strong = RoofOption(("b",), evidence.center * 1.15, evidence.center * 1.15, ((180, 30),))
    area_score, _, _ = evidence.compare((area_only,))
    points, _, roof = evidence.compare((area_only, strong))
    assert sum(area_score) == 4000 < MINIMUM_CANDIDATE_POINTS * POINT_SCALE
    assert roof == strong
    assert sum(points) == MINIMUM_CANDIDATE_POINTS * POINT_SCALE
