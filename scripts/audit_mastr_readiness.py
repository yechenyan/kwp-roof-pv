"""Compare a frozen MaStR extract with an image/review snapshot without assigning identities."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET

import roof_pv_review  # Configure the packaged PROJ database before GIS operations.
import geopandas as gpd
from shapely.geometry import Point, box
from shapely.strtree import STRtree


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def coordinate(row):
    x, y = number(row.get("Laengengrad")), number(row.get("Breitengrad"))
    return Point(x, y) if x is not None and y is not None and -180 <= x <= 180 and -90 <= y <= 90 else None


def target(row, include_thirty=False):
    power = number(row.get("Bruttoleistung"))
    return (row.get("ArtDerSolaranlage") == "853" and row.get("EinheitBetriebsstatus") == "35"
            and power is not None and power > 0 and (power <= 30 if include_thirty else power < 30)
            and coordinate(row) is None)


def percentiles(values):
    values = sorted(values)
    if not values:
        return None
    return {label: round(values[round((len(values) - 1) * q)], 3)
            for label, q in [("min", 0), ("p10", .1), ("median", .5), ("p90", .9), ("max", 1)]}


def counts(rows, field):
    return dict(sorted(Counter(str(row.get(field) or "missing") for row in rows).items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-home", type=Path, required=True, help="Pilot directory containing data and results.")
    parser.add_argument("--home", type=Path, default=Path("var"))
    parser.add_argument("--run", default="municipal-27320a7f3fb1")
    parser.add_argument("--output", type=Path, default=Path("var/analysis/mastr-readiness"))
    parser.add_argument("--summary", type=Path, default=Path("docs/mastr-readiness.json"))
    args = parser.parse_args()
    source = args.registry_home
    folder = args.home / "runs" / args.run
    manifest = load(folder / "manifest.json")
    assert manifest["status"] == "completed"
    extraction, links, archive = [load(source / "results" / (name + ".json"))
                                  for name in ["registry_extraction", "registry_links", "registry_archive"]]
    assert extraction["complete"] and links["complete"]
    assert extraction["expected_members"] == extraction["processed_members"]
    assert links["expected_members"] == links["processed_members"]
    tables = {}
    inputs = {}
    table_specs = [("solar", "EinheitenSolar_", "registry_shards", "EinheitMastrNummer"),
                   ("storage", "EinheitenStromSpeicher_", "registry_shards", "EinheitMastrNummer"),
                   ("storage_systems", "AnlagenStromSpeicher_", "registry_links", "MaStRNummer"),
                   ("locations", "Lokationen_", "registry_links", "MastrNummer")]
    for name, prefix, cache, id_field in table_specs:
        path = source / "data/processed" / f"mastr_{name}.json"
        rows = load(path)
        assert len({r[id_field] for r in rows}) == len(rows), f"Duplicate identifier in {name}"
        cached = []
        for shard in sorted((source / "data/raw" / cache).glob(prefix + "*.json")):
            value = load(shard)
            assert value["archive_url"] == archive["url"]
            cached.extend(value["rows"])
            inputs[str(shard.relative_to(source))] = sha(shard)
        assert {r[id_field]: r for r in rows} == {r[id_field]: r for r in cached}
        assert len(rows) == len(cached)
        for row in rows:
            original = {child.tag: child.text for child in ET.fromstring(row["_source_xml"])}
            assert original == {k: v for k, v in row.items() if not k.startswith("_source_")}
        tables[name] = rows
        inputs[str(path.relative_to(source))] = sha(path)
        save(args.output / "inputs" / path.name, rows)
    assert sum(len(list((source / "data/raw" / cache).glob(prefix + "*.json")))
               for _, prefix, cache, _ in table_specs) == extraction["processed_members"] + links["processed_members"]
    catalog_path = source / "data/raw/Katalogwerte.xml"
    catalog = {e.findtext("Id"): e.findtext("Wert") for e in ET.parse(catalog_path).getroot()}
    inputs["data/raw/Katalogwerte.xml"] = sha(catalog_path)
    for name in ["registry_extraction", "registry_links", "registry_archive"]:
        inputs[f"results/{name}.json"] = sha(source / f"results/{name}.json")
    solar, storage, systems, locations = [tables[name] for name, *_ in table_specs]
    assert {r["Gemeindeschluessel"] for r in solar} == {"12065036", "12065144"}
    target_rows = [r for r in solar if target(r)]
    target_ids = {r["EinheitMastrNummer"] for r in target_rows}
    module_flags = [{key: row.get(key) for key in ["EinheitMastrNummer", "Bruttoleistung", "AnzahlModule", "Inbetriebnahmedatum"]}
                    for row in target_rows if (number(row.get("AnzahlModule")) or 0) > 0
                    and not .1 <= number(row["Bruttoleistung"]) / number(row["AnzahlModule"]) <= .8]
    dates = sorted({tile["acquisition_date"] for tile in manifest["source_tiles"]})
    early, late = dates[0], dates[-1]
    registry_by_location = defaultdict(list)
    storage_by_location = defaultdict(list)
    location_map = {r["MastrNummer"]: r for r in locations}
    system_map = {r["MaStRNummer"]: r for r in systems}
    for row in solar:
        registry_by_location[row.get("LokationMaStRNummer")].append(row)
    for row in storage:
        if row.get("LokationMaStRNummer"):
            storage_by_location[row["LokationMaStRNummer"]].append(row)
    storage_pairs = []
    for pv in solar:
        loc = pv.get("LokationMaStRNummer")
        listed = set(re.findall(r"SEE\d+", location_map.get(loc, {}).get("VerknuepfteEinheitenMaStRNummern", "")))
        for st in storage_by_location[loc]:
            system = system_map.get(st.get("SpeMastrNummer"), {})
            reciprocal = pv["EinheitMastrNummer"] in listed and st["EinheitMastrNummer"] in listed
            system_lists_storage = st["EinheitMastrNummer"] in set(re.findall(r"SEE\d+", system.get("VerknuepfteEinheitenMaStRNummern", "")))
            storage_pairs.append({"solar_id": pv["EinheitMastrNummer"], "storage_id": st["EinheitMastrNummer"],
                                  "location_id": loc, "storage_system_id": st.get("SpeMastrNummer"),
                                  "location_lists_both": reciprocal, "system_lists_storage": system_lists_storage,
                                  "usable_capacity_kwh": number(system.get("NutzbareSpeicherkapazitaet")),
                                  "net_power_kw": number(st.get("Nettonennleistung"))})
    verified_pairs = [p for p in storage_pairs if p["location_lists_both"] and p["system_lists_storage"]]
    verified_target_ids = {p["solar_id"] for p in verified_pairs} & target_ids
    storage_pv_ids = defaultdict(set)
    for pair in verified_pairs:
        storage_pv_ids[pair["storage_id"]].add(pair["solar_id"])

    # Read a consistent review snapshot without initializing or mutating the application database.
    database = args.home / "state/review.sqlite3"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        revision = db.execute("SELECT revision FROM runs WHERE id=?", (args.run,)).fetchone()[0]
        candidate_rows = [dict(r) for r in db.execute("SELECT * FROM candidates WHERE run_id=? ORDER BY id", (args.run,))]
    features = []
    for row in candidate_rows:
        if not row["active"]:
            continue
        feature = json.loads(row["base"])
        if row["geometry"]:
            feature["geometry"] = json.loads(row["geometry"])
        feature["properties"].update(review_status=row["status"], mounting=row["mounting"], candidate_version=row["version"])
        features.append(feature)
    candidates = gpd.GeoDataFrame.from_features(features, crs=4326).to_crs(25833)
    municipalities = gpd.read_file(folder / "municipalities.geojson").to_crs(25833)
    buildings = gpd.read_file(folder / "buildings.geojson").to_crs(25833)
    geoms = candidates.geometry.to_list()
    tree = STRtree(geoms)
    building_tree = STRtree(buildings.geometry.to_list())
    tile_tree = STRtree([box(*t["bounds"]) for t in manifest["source_tiles"]])
    geo_inputs = {name: sha(folder / name) for name in ["manifest.json", "municipalities.geojson", "buildings.geojson"]}
    for name in ["municipalities.geojson", "buildings.geojson"]:
        assert geo_inputs[name] == manifest["artifacts"][name]
    coord_rows = [r for r in solar if coordinate(r) is not None]
    points = gpd.GeoSeries([coordinate(r) for r in coord_rows], crs=4326).to_crs(25833)
    diagnostics = []
    for row, point in zip(coord_rows, points):
        nearest = int(tree.nearest(point))
        local_dates = sorted({manifest["source_tiles"][int(i)]["acquisition_date"] for i in tile_tree.query(point, predicate="intersects")})
        muni_hits = municipalities[municipalities.geometry.covers(point)]
        declared = municipalities[municipalities.ags == row["Gemeindeschluessel"]].geometry.iloc[0]
        commissioned = row.get("Inbetriebnahmedatum")
        timing = ("unknown" if not local_dates or not commissioned else "after_local_capture" if commissioned > max(local_dates)
                  else "before_local_capture" if commissioned < min(local_dates) else "capture_day_or_mixed_dates")
        diagnostics.append({"solar_id": row["EinheitMastrNummer"], "declared_municipality": row["Gemeinde"],
                            "power_kw": number(row.get("Bruttoleistung")), "commissioned": commissioned,
                            "inside_study_area": not muni_hits.empty, "inside_declared_municipality": declared.covers(point),
                            "distance_to_declared_municipality_m": round(point.distance(declared), 2),
                            "local_capture_dates": local_dates, "timing": timing,
                            "nearest_candidate_id": candidates.iloc[nearest]["id"],
                            "distance_to_nearest_polygon_m": round(point.distance(geoms[nearest]), 3),
                            "candidate_count_within_25m": len(tree.query(point.buffer(25), predicate="intersects")),
                            "candidate_count_within_50m": len(tree.query(point.buffer(50), predicate="intersects")),
                            "building_count_at_coordinate": len(building_tree.query(point, predicate="intersects")),
                            "identity_assigned": False})

    def registry_summary(rows):
        targets = [r for r in rows if target(r)]
        return {"solar_total": len(rows), "active_total": sum(r.get("EinheitBetriebsstatus") == "35" for r in rows),
                "types": counts(rows, "ArtDerSolaranlage"), "statuses": counts(rows, "EinheitBetriebsstatus"),
                "target_under30": len(targets), "target_at_most30": sum(target(r, True) for r in rows),
                "target_gross_power_kw": round(sum(number(r["Bruttoleistung"]) for r in targets), 3),
                "coordinate_pairs": sum(coordinate(r) is not None for r in rows),
                "target_before_earliest_capture": sum((r.get("Inbetriebnahmedatum") or "9999") < early for r in targets),
                "target_on_earliest_capture": sum(r.get("Inbetriebnahmedatum") == early for r in targets),
                "target_between_capture_dates": sum(early < (r.get("Inbetriebnahmedatum") or "") <= late for r in targets),
                "target_after_latest_capture": sum((r.get("Inbetriebnahmedatum") or "") > late for r in targets),
                "target_missing_commission_date": sum(not r.get("Inbetriebnahmedatum") for r in targets),
                "target_field_coverage": {field: sum(bool(r.get(field)) for r in targets) for field in [
                    "Strasse", "Hausnummer", "Laengengrad", "Breitengrad", "Postleitzahl", "Ort", "AnzahlModule",
                    "Hauptausrichtung", "HauptausrichtungNeigungswinkel", "Nebenausrichtung", "LokationMaStRNummer"]},
                "target_module_count_distribution": percentiles([number(r["AnzahlModule"]) for r in targets if number(r.get("AnzahlModule")) is not None]),
                "target_power_distribution_kw": percentiles([number(r["Bruttoleistung"]) for r in targets]),
                "target_postcodes": counts(targets, "Postleitzahl"),
                "target_commission_years": dict(sorted(Counter(r.get("Inbetriebnahmedatum", "missing")[:4] for r in targets).items())),
                "target_with_reciprocal_storage": len({r["EinheitMastrNummer"] for r in targets} & verified_target_ids),
                "target_sharing_location_with_other_solar": sum(len(registry_by_location[r.get("LokationMaStRNummer")]) > 1 for r in targets)}

    by_city = {}
    building_candidates = defaultdict(set)
    for _, row in candidates.iterrows():
        for bid in row.get("building_ids") or []:
            building_candidates[bid].add(row["id"])
    for _, municipality in municipalities.iterrows():
        city = municipality["gem_name"]
        subset = candidates[candidates.geometry.intersects(municipality.geometry)]
        by_city[city] = {"registry": registry_summary([r for r in solar if r["Gemeindeschluessel"] == municipality["ags"]]),
                         "image": {"intersecting_candidates": len(subset), "model_categories": counts(subset.to_dict("records"), "category"),
                                   "building_overlap_candidates": int((subset.building_overlap_ratio > 0).sum())}}
    summary = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
               "scope": "Read-only readiness audit; nearest-neighbour diagnostics are not identity matches.",
               "run_id": args.run, "review_revision": revision, "registry_archive": archive["url"],
               "registry_snapshot": "2026-11-14", "capture_dates": dates,
               "registry_selection_caveat": "All expected shards were processed; the original local filter used exact Gemeinde names, not a nationwide AGS-only filter. AGS completeness has not been independently re-extracted.",
               "verification": {"source_shards": extraction["processed_members"] + links["processed_members"],
                                "retained_rows_equal_shard_cache_and_original_xml": True,
                                "historical_compressed_member_crc_not_redownloaded": True,
                                "input_sha256": inputs, "image_inputs_sha256": geo_inputs,
                                "review_snapshot_sha256": hashlib.sha256(json.dumps(candidate_rows, sort_keys=True).encode()).hexdigest()},
               "registry": registry_summary(solar), "by_municipality": by_city,
               "registry_quality": {"target_module_power_screen_count": len(module_flags),
                                    "screen_definition": "Gross kWp divided by module count outside [0.1, 0.8]; review flag only, not a physical impossibility rule.",
                                    "module_power_examples": module_flags[:4],
                                    "target_uniform_orientation_flag": counts(target_rows, "EinheitlicheAusrichtungUndNeigungswinkel")},
               "image": {"active_candidates": len(candidates), "status_counts": counts(candidates.to_dict("records"), "review_status"),
                         "mounting_counts": counts(candidates.to_dict("records"), "mounting"),
                         "model_categories": counts(candidates.to_dict("records"), "category"),
                         "building_overlap_candidates": int((candidates.building_overlap_ratio > 0).sum()),
                         "unique_buildings_intersected": len(building_candidates),
                         "buildings_intersecting_multiple_candidates": sum(len(v) > 1 for v in building_candidates.values()),
                         "candidate_count_per_building": dict(sorted(Counter(len(v) for v in building_candidates.values()).items())),
                         "projected_area_m2": percentiles(candidates.geometry.area.to_list()),
                         "candidate_area_below_5m2": int((candidates.geometry.area < 5).sum()),
                         "candidate_area_below_10m2": int((candidates.geometry.area < 10).sum()),
                         "available_building_fields": list(buildings.columns),
                         "has_postcode_polygons": False, "has_roof_plane_slope_or_azimuth": False},
               "coordinate_diagnostics": {"present": len(diagnostics), "inside_study_area": sum(r["inside_study_area"] for r in diagnostics),
                   "outside_declared_municipality": sum(not r["inside_declared_municipality"] for r in diagnostics),
                   "inside_and_before_local_capture": sum(r["inside_study_area"] and r["timing"] == "before_local_capture" for r in diagnostics),
                   "nearest_within_thresholds_m": {str(limit): sum(r["inside_study_area"] and r["timing"] == "before_local_capture" and r["distance_to_nearest_polygon_m"] <= limit for r in diagnostics) for limit in [0, 10, 25, 50]},
                   "multiple_candidates_within_25m_before_capture": sum(r["inside_study_area"] and r["timing"] == "before_local_capture" and r["candidate_count_within_25m"] > 1 for r in diagnostics),
                   "distance_m_for_inside_before_capture": percentiles([r["distance_to_nearest_polygon_m"] for r in diagnostics if r["inside_study_area"] and r["timing"] == "before_local_capture"]),
                   "timing_counts": counts(diagnostics, "timing")},
               "storage": {"units": len(storage), "systems": len(systems), "locations": len(locations),
                   "same_location_pairs_before_reciprocity": len(storage_pairs), "reciprocal_location_and_system_pairs": len(verified_pairs),
                   "nonreciprocal_pairs": len(storage_pairs) - len(verified_pairs),
                   "target_solar_units_with_reciprocal_storage": len(verified_target_ids),
                   "unique_storage_units_in_verified_pairs": len(storage_pv_ids),
                   "storage_units_shared_by_multiple_solar_records": sum(len(ids) > 1 for ids in storage_pv_ids.values()),
                   "exclusive_battery_allocation_performed": False},
               "catalog": {key: catalog[key] for key in sorted({str(r[field]) for r in solar for field in ["ArtDerSolaranlage", "EinheitBetriebsstatus", "Hauptausrichtung", "HauptausrichtungNeigungswinkel"] if r.get(field)})}}
    save(args.output / "coordinate-diagnostics.json", diagnostics)
    save(args.output / "module-power-diagnostics.json", module_flags)
    save(args.output / "storage-location-diagnostics.json", storage_pairs)
    save(args.output / "summary.json", summary)
    save(args.summary, summary)
    print(json.dumps({"summary": str(args.summary), "details": str(args.output), "registry": summary["registry"],
                      "image": summary["image"], "coordinate_diagnostics": summary["coordinate_diagnostics"], "storage": summary["storage"]}, indent=2))


if __name__ == "__main__":
    main()
