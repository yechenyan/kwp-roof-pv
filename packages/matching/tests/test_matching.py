"""Grouping boundaries and temporal/coordinate ambiguity are kept explicit."""
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform
from roof_pv_matching.engine import array_groups, rank
from roof_pv_registry.snapshot import normalize_solar

RESTORE = Transformer.from_crs(25833, 4326, always_xy=True).transform


def feature(item_id, geometry, **properties):
    return {"type": "Feature", "id": item_id, "geometry": mapping(transform(RESTORE, geometry)), "properties": {"id": item_id, **properties}}


def test_close_fragments_group_only_within_the_same_building_part():
    candidates = [feature(str(i), box(x, 5837000, x + 3, 5837003), version=0, status="pending", category="both") for i, x in enumerate([383500, 383504, 383510])]
    buildings = [feature("b1", box(383499, 5836999, 383508, 5837005), building_id="b1"), feature("b2", box(383509, 5836999, 383515, 5837005), building_id="b2")]
    municipalities = [feature("m", box(383000, 5836000, 384000, 5838000), gem_name="Hohen Neuendorf", ags="12065144")]
    snapshot = {"candidates": {"features": candidates}, "manifest": {"input": {"acquisition_date": "2026-04-22"}}}
    groups = array_groups(snapshot, buildings, municipalities)
    assert sorted(len(g["properties"]["member_ids"]) for g in groups) == [1, 2]
    assert sum(g["properties"]["area_m2"] for g in groups) == 27
    assert all(g["properties"]["grouping_state"] == "proposed" for g in groups)


def test_nearest_polygon_does_not_override_later_commissioning_or_bad_coordinates():
    municipalities = [feature("m", box(383000, 5836000, 384000, 5838000), gem_name="Hohen Neuendorf", ags="12065144")]
    group = feature("array1", box(383500, 5837000, 383510, 5837010), area_m2=100, capture_dates=["2026-04-22"], municipalities=["Hohen Neuendorf"])
    point = RESTORE(383505, 5837005)
    base = {"EinheitMastrNummer": "SEE1", "ArtDerSolaranlage": "853", "EinheitBetriebsstatus": "35", "Bruttoleistung": "10", "AnzahlModule": "25", "Inbetriebnahmedatum": "2026-05-01", "Gemeindeschluessel": "12065144", "Gemeinde": "Hohen Neuendorf", "Laengengrad": str(point[0]), "Breitengrad": str(point[1])}
    bad = {**base, "EinheitMastrNummer": "SEE2", "Laengengrad": "13", "Breitengrad": "52"}
    result = rank([group], [normalize_solar(base, {}), normalize_solar(bad, {})], municipalities)
    assert result["units"]["SEE1"]["state"] == "post_capture"
    assert result["units"]["SEE1"]["alternatives"][0]["evidence"]["coordinate_distance_m"] == 0
    assert result["units"]["SEE2"]["state"] == "source_conflict"
    assert not result["units"]["SEE2"]["alternatives"]
    assert all(a["state"] != "verified_identity" for a in result["groups"]["array1"])
