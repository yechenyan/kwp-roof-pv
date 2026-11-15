"""Propose array groups and reversible rankings; never auto-verify identities."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid

from pyproj import Transformer
from shapely.geometry import Point, box, shape, mapping
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree
from roof_pv_registry.source import atomic_json, digest

CONFIG = {"algorithm": "array-ranking-v1", "group_distance_m": 2.0, "minimum_building_overlap": .2,
          "alternatives": 5, "coordinate_search_m": 100, "area_weight": .65, "power_area_weight": .35,
          "coordinate_weight": .8, "post_capture_factor": .7,
          "validation": "Experimental proposal rules. Scores are not calibrated probabilities; automatic acceptance is disabled.",
          "missing_features": ["postcode_geometry", "roof_plane_slope", "panel_facing_azimuth", "independent_small_unit_identity_truth"]}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def array_groups(snapshot, buildings, municipalities, config=CONFIG):
    project = Transformer.from_crs(4326, 25833, always_xy=True).transform
    restore = Transformer.from_crs(25833, 4326, always_xy=True).transform
    features = [f for f in snapshot["candidates"]["features"] if f["properties"]["status"] != "rejected"]
    geometries = [transform(project, shape(f["geometry"])) for f in features]
    municipal_geometries = [(m["properties"]["gem_name"], transform(project, shape(m["geometry"]))) for m in municipalities]
    source_tiles = snapshot["manifest"].get("source_tiles", [])
    tile_tree = STRtree([box(*tile["bounds"]) for tile in source_tiles])
    parts, part_ids = [], []
    for building in buildings:
        geometry = shape(building["geometry"])
        for index, part in enumerate(geometry.geoms if geometry.geom_type == "MultiPolygon" else [geometry]):
            parts.append(transform(project, part))
            part_ids.append(str(building["properties"].get("building_id", building.get("id"))) + f":part:{index}")
    building_tree = STRtree(parts)
    associations = []
    for geom in geometries:
        hits = [(geom.intersection(parts[int(i)]).area / geom.area, int(i)) for i in building_tree.query(geom, predicate="intersects")]
        best = max(hits, default=(0, -1))
        associations.append(part_ids[best[1]] if best[0] >= config["minimum_building_overlap"] else None)
    parents = list(range(len(features)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    tree = STRtree(geometries)
    for i, geom in enumerate(geometries):
        if associations[i] is None:
            continue
        for j in tree.query(geom.buffer(config["group_distance_m"])):
            j = int(j)
            if j > i and associations[j] == associations[i] and geom.distance(geometries[j]) <= config["group_distance_m"]:
                parents[root(j)] = root(i)
    components = defaultdict(list)
    for i in range(len(features)):
        components[root(i)].append(i)
    groups = []
    for members in components.values():
        source = [features[i] for i in members]
        geometry = unary_union([geometries[i] for i in members])
        ids = sorted(f["id"] for f in source)
        versions = {f["id"]: f["properties"]["version"] for f in source}
        dates = sorted({date for f in source for date in f["properties"].get("acquisition_dates", [])})
        if source_tiles:
            dates = sorted({source_tiles[int(i)]["acquisition_date"] for i in tile_tree.query(geometry, predicate="intersects")})
        if not dates and snapshot["manifest"]["input"].get("acquisition_date"):
            dates = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", snapshot["manifest"]["input"]["acquisition_date"])))
        names = sorted({name for name, boundary in municipal_geometries if geometry.intersects(boundary)})
        flags = ["image_group_not_human_verified"]
        if any(f["properties"]["status"] != "confirmed" for f in source):
            flags.append("contains_unreviewed_or_uncertain_pv")
        if any(f["properties"].get("mounting", "unknown") != "roof" for f in source):
            flags.append("roof_mounting_unverified")
        if any(f["properties"].get("mounting") in ["ground", "other"] for f in source):
            flags.append("contains_non_roof_review")
        if any(f["properties"]["category"] in ["geometry_conflict", "rgb_only"] for f in source):
            flags.append("model_geometry_needs_review")
        if len(ids) > 10:
            flags.append("large_group_check_for_multiple_installations")
        if associations[members[0]] is None:
            flags.append("no_reliable_building_part")
        group_id = "array-" + fingerprint({"ids": ids, "versions": versions, "geometry": geometry.wkb_hex, "algorithm": config["algorithm"]})[:16]
        groups.append({"type": "Feature", "id": group_id, "geometry": mapping(transform(restore, geometry)),
                       "properties": {"id": group_id, "member_ids": ids, "member_versions": versions,
                                      "building_parts": sorted({associations[i] for i in members if associations[i]}),
                                      "area_m2": round(geometry.area, 4), "capture_dates": dates, "municipalities": names,
                                      "flags": flags, "grouping_state": "proposed", "review_point": list(transform(restore, geometry.representative_point()).coords)[0]}})
    return sorted(groups, key=lambda f: f["id"])


def area_evidence(unit):
    raw = unit["raw"]
    slope = {"806": (60, 90), "807": (60, 90), "808": (41, 60), "809": (21, 40), "810": (5, 20), "811": (0, 5)}.get(raw.get("HauptausrichtungNeigungswinkel"), (0, 65))
    # Unknown or mixed roof faces need broad uncertainty; no image slope is invented.
    if raw.get("EinheitlicheAusrichtungUndNeigungswinkel") != "1":
        slope = (0, max(65, slope[1]))
    cosine = (max(.15, math.cos(math.radians(slope[1]))), math.cos(math.radians(slope[0])))
    modules = unit["module_count"]
    year = int((unit.get("commissioned") or "2000")[:4])
    module_area = (.6, 2.2) if year < 2010 else (1, 2.4) if year < 2020 else (1.4, 3.1)
    module_interval = None
    if modules and "module_count_power_review" not in unit["flags"]:
        module_interval = (modules * module_area[0] * cosine[0], modules * module_area[1] * cosine[1])
    power = unit["power_kw"]
    power_interval = (power / .25 * cosine[0], power / .08 * cosine[1]) if power and power > 0 else None
    return module_interval, power_interval


def interval_similarity(area, interval):
    if not interval or area <= 0:
        return None
    low, high = interval
    if low <= area <= high:
        return 1.0
    return min(area / low, high / area)


def rank(groups, units, municipalities, config=CONFIG):
    project = Transformer.from_crs(4326, 25833, always_xy=True).transform
    geometries = [transform(project, shape(g["geometry"])) for g in groups]
    tree = STRtree(geometries)
    group_heaps = [[] for _ in groups]
    group_stats = [{"candidate_pool_size": 0, "best_tie_count": 0, "best_score": -1.0} for _ in groups]
    unit_rankings = {}
    municipality_geometry = {m["properties"]["ags"]: transform(project, shape(m["geometry"])) for m in municipalities}
    name_groups = defaultdict(list)
    for i, group in enumerate(groups):
        for name in group["properties"]["municipalities"]:
            name_groups[name].append(i)
    active_building = lambda u: u["raw"].get("ArtDerSolaranlage") == "853" and u["raw"].get("EinheitBetriebsstatus") == "35"
    for unit in units:
        entry = {"solar_id": unit["id"], "alternatives": [], "state": "unmatched_registry", "flags": list(unit["flags"])}
        unit_rankings[unit["id"]] = entry
        if not active_building(unit):
            entry["flags"].append("context_record_outside_active_building_scope")
            continue
        point = transform(project, Point(unit["longitude"], unit["latitude"])) if unit["longitude"] is not None else None
        if point is not None and (unit["ags"] not in municipality_geometry or not municipality_geometry[unit["ags"]].covers(point)):
            entry["state"] = "source_conflict"
            entry["flags"].append("coordinate_outside_declared_municipality")
            continue
        indices = [int(i) for i in tree.query(point.buffer(config["coordinate_search_m"]), predicate="intersects")] if point is not None else name_groups[unit["municipality"]]
        module_interval, power_interval = area_evidence(unit)
        heap = []
        best_score, best_count, pool_size = -1.0, 0, 0
        for i in indices:
            group = groups[i]
            p = group["properties"]
            if "contains_non_roof_review" in p.get("flags", []):
                continue
            module_score = interval_similarity(p["area_m2"], module_interval)
            power_score = interval_similarity(p["area_m2"], power_interval)
            if module_score is not None and power_score is not None:
                area_score = module_score * config["area_weight"] + power_score * config["power_area_weight"]
            else:
                area_score = module_score if module_score is not None else power_score or 0
            distance = point.distance(geometries[i]) if point is not None else None
            score = area_score if distance is None else config["coordinate_weight"] * math.exp(-distance / 25) + (1 - config["coordinate_weight"]) * area_score
            dates = p["capture_dates"]
            commissioned = unit.get("commissioned")
            timing = ("unknown" if not dates or not commissioned else "post_capture" if commissioned > max(dates)
                      else "capture_day_or_mixed_dates" if commissioned >= min(dates) else "pre_capture")
            if timing == "post_capture":
                score *= config["post_capture_factor"]
            pool_size += 1
            if score > best_score + 1e-12:
                best_score, best_count = score, 1
            elif abs(score - best_score) <= 1e-12:
                best_count += 1
            statistics = group_stats[i]
            statistics["candidate_pool_size"] += 1
            if score > statistics["best_score"] + 1e-12:
                statistics["best_score"], statistics["best_tie_count"] = score, 1
            elif abs(score - statistics["best_score"]) <= 1e-12:
                statistics["best_tie_count"] += 1
            evidence = {"module_projected_area_interval_m2": module_interval, "power_projected_area_interval_m2": power_interval,
                        "module_area_similarity": module_score, "power_area_similarity": power_score,
                        "observed_area_m2": p["area_m2"], "coordinate_distance_m": round(distance, 3) if distance is not None else None,
                        "timing": timing, "capture_dates": dates, "commissioned": commissioned,
                        "facing_azimuth_compared": False, "postcode_geometry_compared": False,
                        "correlated_area_features": True, "identity_verified": False}
            item = {"proposal_id": "link-" + fingerprint([unit["id"], group["id"]])[:20], "solar_id": unit["id"],
                    "group_id": group["id"], "score": round(score, 6), "state": "post_capture" if timing == "post_capture" else "ambiguous",
                    "evidence": evidence}
            value = (score, group["id"], item)
            if len(heap) < config["alternatives"]:
                heapq.heappush(heap, value)
            elif value[:2] > heap[0][:2]:
                heapq.heapreplace(heap, value)
            value = (score, unit["id"], item)
            if len(group_heaps[i]) < config["alternatives"]:
                heapq.heappush(group_heaps[i], value)
            elif value[:2] > group_heaps[i][0][:2]:
                heapq.heapreplace(group_heaps[i], value)
        entry["alternatives"] = [item for _, _, item in sorted(heap, reverse=True)]
        entry.update(candidate_pool_size=pool_size, best_tie_count=best_count, alternatives_truncated=pool_size > len(heap))
        if entry["alternatives"]:
            entry["state"] = entry["alternatives"][0]["state"]
            entry["score_gap"] = round(entry["alternatives"][0]["score"] - entry["alternatives"][1]["score"], 6) if len(heap) > 1 else None
            if entry["score_gap"] == 0:
                entry["flags"].append("tied_best_alternatives")
        entry["unmatched_option"] = True
    return {"units": unit_rankings, "groups": {group["id"]: [item for _, _, item in sorted(heap, reverse=True)] for group, heap in zip(groups, group_heaps)},
            "group_statistics": {group["id"]: statistics for group, statistics in zip(groups, group_stats)}}


def build(home, snapshot, registry, config=CONFIG):
    home = Path(home)
    run_id = snapshot["run_id"]
    run_folder = home / "runs" / run_id
    def artifact(name):
        if name not in snapshot["manifest"]["artifacts"]:
            return []
        path = run_folder / name
        if digest(path) != snapshot["manifest"]["artifacts"][name]:
            raise ValueError("Matching context artifact changed.")
        return json.loads(path.read_text())["features"]
    identity = {"run_id": run_id, "review_revision": snapshot["revision"], "run_manifest_sha256": snapshot["manifest_sha256"],
                "registry_snapshot_id": registry.manifest["snapshot_id"], "registry_manifest_sha256": digest(registry.folder / "manifest.json"),
                "config": config, "code_sha256": digest(Path(__file__))}
    batch_id = "matching-" + fingerprint(identity)[:16]
    target = home / "matching" / run_id / batch_id
    if (target / "manifest.json").exists():
        return load_batch(target)
    temporary = target.parent / ("." + batch_id + "-" + uuid.uuid4().hex[:8])
    temporary.mkdir(parents=True)
    try:
        municipalities = artifact("municipalities.geojson")
        groups = array_groups(snapshot, artifact("buildings.geojson"), municipalities, config)
        rankings = rank(groups, registry.data["solar_units"], municipalities, config)
        atomic_json(temporary / "groups.geojson", {"type": "FeatureCollection", "features": groups})
        atomic_json(temporary / "rankings.json", rankings)
        manifest = {"schema_version": 1, "batch_id": batch_id, "status": "completed", "identity": identity,
                    "created_at": datetime.now(timezone.utc).isoformat(), "summary": {
                        "array_groups": len(groups), "grouped_regions": sum(len(g["properties"]["member_ids"]) for g in groups),
                        "multi_region_groups": sum(len(g["properties"]["member_ids"]) > 1 for g in groups),
                        "registry_records": len(rankings["units"]), "states": dict(Counter(u["state"] for u in rankings["units"].values())),
                        "verified_identities": 0, "automatic_acceptance": False},
                    "artifacts": {name: digest(temporary / name) for name in ["groups.geojson", "rankings.json"]}}
        atomic_json(temporary / "manifest.json", manifest)
        os.replace(temporary, target)
        return load_batch(target)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def load_batch(folder):
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    for name, expected in manifest["artifacts"].items():
        if Path(name).name != name or digest(folder / name) != expected:
            raise ValueError("Matching artifact failed integrity verification.")
    return {"manifest": manifest, "groups": json.loads((folder / "groups.geojson").read_text()),
            "rankings": json.loads((folder / "rankings.json").read_text())}
