"""Test consequential ranking and registry-catalog semantics."""
import math
from pathlib import Path
import sys
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scoring import expected_area, rank_unit, allocate_building, similarity


def unit(**values):
    return {"id":"test-unit","module_count":10,"power_kw":4.2,"commissioned":"2024-01-01",
            "flags":[],"municipality":"Test municipality","raw":{"HauptausrichtungNeigungswinkel":"3179"},**values}


def group(gid,area):
    return {"id":gid,"area_m2":area,"image_quality":1,"capture_dates":["2026-04-22"]}


def test_catalog_tracking_is_not_horizontal():
    horizontal=expected_area(unit())[0]
    tracking=expected_area(unit(raw={"HauptausrichtungNeigungswinkel":"811"}))[0]
    vertical=expected_area(unit(raw={"HauptausrichtungNeigungswinkel":"806"}))[0]
    assert horizontal==pytest.approx(20.5*math.cos(math.radians(2.5)))
    assert tracking==pytest.approx(20.5*math.cos(math.radians(30)))
    assert vertical==pytest.approx(20.5*.25)


def test_continuous_area_score_has_no_flat_interval():
    scores=similarity(np.array([10,20,30]),20)
    assert scores[1]>scores[0] and scores[1]>scores[2]


def test_bad_module_count_is_omitted():
    module,power=expected_area(unit(flags=["module_count_power_review"]))
    assert module is None and power>0


def test_high_score_without_coordinates_remains_low_confidence():
    area=expected_area(unit())[0]
    result,alternatives=rank_unit(unit(),[group("b",area),group("a",area)])
    assert result["group_id"]=="a" and result["best_tie_count"]==2
    assert result["confidence"]=="low" and not result["identity_verified"]


def test_post_capture_is_penalized():
    pool=[group("a",20)]
    before,_=rank_unit(unit(),pool)
    after,_=rank_unit(unit(commissioned="2026-06-01"),pool)
    assert after["score"]==pytest.approx(before["score"]*.7,abs=.0001)
    assert after["post_capture"]


def test_statistical_allocation_requires_free_roof_capacity():
    buildings=[{"id":"occupied","municipality":"Test municipality","area_m2":100,"compactness":.8},
               {"id":"small","municipality":"Test municipality","area_m2":35,"compactness":.9},
               {"id":"eligible","municipality":"Test municipality","area_m2":100,"compactness":.8}]
    occupied={"occupied"}
    result,_=allocate_building(unit(),buildings,occupied)
    assert result["building_id"]=="eligible" and result["confidence"]=="very_low"
    assert "eligible" in occupied and not result["identity_verified"]
