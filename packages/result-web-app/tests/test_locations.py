"""Regression tests for shared-location clustering across allocation stages."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scoring import LocationConstraints, allocate_building


def unit():
    return {"id": "test-solar", "module_count": 10, "power_kw": 4.2,
            "commissioned": "2026-07-01", "flags": [], "municipality": "Test",
            "raw": {"HauptausrichtungNeigungswinkel": "3179"}}


def building(bid="roof", area=100):
    return {"id": bid, "area_m2": area, "municipality": "Test", "compactness": .8}


def test_location_cannot_select_a_distant_image_or_statistical_point():
    constraints = LocationConstraints()
    constraints.reserve("site", (0, 0))
    assert constraints.allows("site", (60, 0))
    assert not constraints.allows("site", (6000, 0))
    with pytest.raises(ValueError):
        constraints.reserve("site", (6000, 0))


def test_pairwise_limit_prevents_a_chain_from_drifting():
    constraints = LocationConstraints()
    constraints.reserve("site", (0, 0))
    constraints.reserve("site", (90, 0))
    assert not constraints.allows("site", (180, 0))
    assert not constraints.allows("site", (-90, 0))


def test_unrelated_or_missing_locations_do_not_share_anchors():
    constraints = LocationConstraints()
    constraints.reserve("solar-without-location-a", (0, 0))
    assert constraints.allows("solar-without-location-b", (6000, 0))


def test_later_unit_reuses_its_location_building_with_capacity():
    used = {}
    result, alternatives = allocate_building(unit(), [building()], {"roof"},
        reusable_ids={"roof"}, observed_area={"roof": 10}, used_area=used)
    assert result["building_id"] == "roof"
    assert result["available_roof_area_m2"] == 35
    assert result["score"] == alternatives[0]["score"]
    assert used["roof"] > 20
    assert not result["identity_verified"]


def test_no_remote_substitution_when_anchor_roof_is_full():
    result, alternatives = allocate_building(unit(), [building()], {"roof"},
        reusable_ids={"roof"}, observed_area={"roof": 30})
    assert result is None and not alternatives


def test_repeated_fallback_units_consume_remaining_roof_capacity():
    used = {}
    occupied = set()
    roof = building(area=100)
    first, _ = allocate_building(unit(), [roof], occupied, used_area=used)
    second, _ = allocate_building(unit(), [roof], occupied, reusable_ids={"roof"}, used_area=used)
    third, _ = allocate_building(unit(), [roof], occupied, reusable_ids={"roof"}, used_area=used)
    assert first and second and third is None


def test_fallback_does_not_reuse_an_unrelated_occupied_building():
    result, _ = allocate_building(unit(), [building()], {"roof"})
    assert result is None
