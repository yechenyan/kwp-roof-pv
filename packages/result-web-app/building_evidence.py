"""Assemble observed PV by cadastral building and rank unlocated registry records."""

from collections import defaultdict
import gzip
import json
import math

import numpy as np
from shapely import from_wkb, set_precision
from shapely.geometry import MultiPolygon, mapping, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from candidate_scoring import CandidateEvidence, FALLBACK_COUNT, MINIMUM_CANDIDATE_POINTS, POINT_SCALE, measure_building
from scoring import score_arrays


MINIMUM_ROOF_SUPPORT = .95
AREA_MODES = ("unavailable", "projected", "roof_parallel")


def polygons(geometry):
    if geometry.geom_type == "Polygon" and not geometry.is_empty:
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygons(part)


def postcode_fabric(path, project):
    source = json.loads(path.read_bytes())
    parts = defaultdict(list)
    for feature in source["features"]:
        code = feature["properties"]["postcode"]
        geometry = transform(project, shape(feature["geometry"]))
        if not geometry.is_valid:
            raise ValueError(f"Invalid postcode geometry: {code}")
        parts[code].append(geometry)
    return {code: unary_union(geometries) for code, geometries in sorted(parts.items())}, source["provenance"]


def label_postcode(footprint, codes, shapes, index):
    covered = [((footprint.intersection(shapes[i]).area / footprint.area), codes[i])
               for i in index.query(footprint, predicate="intersects")]
    dominant = [(share, code) for share, code in covered if share >= MINIMUM_ROOF_SUPPORT]
    if len(dominant) == 1 and all(code == dominant[0][1] or share < .05 for share, code in covered):
        return dominant[0][1], "assigned"
    return None, "boundary_conflict" if any(share > 0 for share, _ in covered) else "outside_postcode_coverage"


def load_roofs(path, footprint_digest, group_digest):
    source = json.loads(gzip.decompress(path.read_bytes()))
    if (source["schema"] != 1 or source["crs"] != 25833
            or source["footprint_sha256"] != footprint_digest or source["group_sha256"] != group_digest):
        raise ValueError("LoD2 roof snapshot belongs to a different survey or group batch.")
    return source


def measure_faces(geometry, footprint, lod2):
    """Return exclusive PV intersections and an estimate for roof-parallel panels."""
    if lod2 is None:
        return "no_lod2_id", [], None, None
    ground_hex, roof_rows = lod2
    ground = from_wkb(bytes.fromhex(ground_hex))
    if ground.intersection(footprint).area < .75 * ground.union(footprint).area:
        return "footprint_disagreement", [], None, None
    provisional = []
    for roof_hex, tilt, azimuth in roof_rows:
        shared = set_precision(unary_union(list(polygons(from_wkb(bytes.fromhex(roof_hex)).intersection(geometry)))), .001)
        if shared.area > .02:
            provisional.append((shared, tilt, azimuth))
    if not provisional:
        return "no_usable_roof_intersection", [], None, None
    supported = unary_union([part for part, _, _ in provisional])
    support_share = min(1.0, supported.area / geometry.area)
    if sum(part.area for part, _, _ in provisional) > 1.02 * supported.area or support_share < .90:
        return "roof_projection_disagreement", [], support_share, None

    remaining = supported
    faces = []
    for part, tilt, azimuth in sorted(provisional, key=lambda row: -row[0].area):
        exclusive = unary_union(list(polygons(part.intersection(remaining))))
        if exclusive.is_empty:
            continue
        remaining = remaining.difference(exclusive)
        faces.append({"_geometry": exclusive, "projected_area_m2": exclusive.area, "slope_deg": tilt,
                      "azimuth_deg": azimuth})
    pitched = sum(face["projected_area_m2"] for face in faces if face["slope_deg"] >= 10)
    if support_share < MINIMUM_ROOF_SUPPORT or pitched < MINIMUM_ROOF_SUPPORT * supported.area:
        return "projected_area_fallback", faces, support_share, None
    for face in faces:
        face["roof_parallel_area_m2"] = face["projected_area_m2"] / math.cos(math.radians(face["slope_deg"]))
    estimated = max(0, geometry.area - supported.area) + sum(f["roof_parallel_area_m2"] for f in faces)
    return "roof_parallel_estimate", faces, support_share, estimated


def assemble_buildings(groups, cadastral, postcodes, lod2):
    grouped = defaultdict(list)
    for group in groups:
        if not group["roof_supported"]:
            continue
        building_ids = {key.rsplit(":part:", 1)[0] for key in group["building_parts"]}
        if len(building_ids) != 1 or not building_ids <= cadastral.keys():
            raise ValueError(f"Roof-supported group has no unique cadastral building: {group['id']}")
        grouped[next(iter(building_ids))].append(group)

    codes, shapes = list(postcodes), list(postcodes.values())
    postal_index = STRtree(shapes)
    buildings = []
    for key, members in sorted(grouped.items()):
        footprint = cadastral[key]
        geometry = unary_union([item["_geometry"] for item in members])
        if geometry.area <= 0 or not geometry.is_valid:
            raise ValueError(f"Invalid observed PV building geometry: {key}")
        postcode, postal_status = label_postcode(footprint, codes, shapes, postal_index)
        lod2_id = key.removesuffix("BL") if len(key) == 18 else key
        area_method, faces, support, estimated = measure_faces(geometry, footprint, lod2.get(lod2_id))
        group_weights = [len(item["member_ids"]) for item in members]
        buildings.append({
            "building_id": key, "municipalities": sorted({m for item in members for m in item["municipalities"]}),
            "postcode": postcode, "postcode_status": postal_status,
            "group_ids": sorted(item["id"] for item in members), "region_count": sum(group_weights),
            "capture_dates": sorted({d for item in members for d in item["capture_dates"]}),
            "projected_area_m2": geometry.area, "roof_parallel_area_m2": estimated,
            "area_method": area_method, "lod2_support_share": support,
            "image_quality": float(np.average([item["image_quality"] for item in members], weights=group_weights)),
            "_geometry": geometry, "_faces": faces,
        })
    return buildings


def observation_features(buildings, restore):
    inventory, faces = [], []
    for building in buildings:
        details = {k: v for k, v in building.items() if not k.startswith("_")}
        details["roof_face_count"] = len(building["_faces"])
        inventory.append({"type": "Feature", "geometry": mapping(transform(restore, building["_geometry"])),
                          "properties": details})
        for number, face in enumerate(building["_faces"], 1):
            faces.append({"type": "Feature", "geometry": mapping(transform(restore, face["_geometry"])),
                          "properties": {"building_id": building["building_id"], "face_number": number,
                                         **{k: v for k, v in face.items() if not k.startswith("_")}}})
    return inventory, faces


class BuildingShortlists:
    def __init__(self, buildings):
        self.building_ids = [building["building_id"] for building in buildings]
        self.building_indices = {key: index for index, key in enumerate(self.building_ids)}
        self.measurements = [measure_building(building) for building in buildings]
        self.by_postcode = defaultdict(list)
        self.by_municipality = defaultdict(list)
        for building in buildings:
            for municipality in building["municipalities"]:
                self.by_municipality[municipality].append(building)
                if building["postcode"] is not None:
                    self.by_postcode[municipality, building["postcode"]].append(building)

    def rank(self, unit, *, full=False):
        postcode = unit["postcode"]
        if not postcode:
            return self.unlocated("postcode_missing", "The registry record does not have a postcode.")
        candidates = self.by_postcode[unit["municipality"], postcode]
        if not candidates:
            boundary = any(b["postcode_status"] == "boundary_conflict" for b in self.by_municipality[unit["municipality"]])
            reason = "No detected building has the same municipality and an assigned LGB postcode."
            if boundary:
                reason += " Buildings with unresolved postcode boundaries were excluded."
            return self.unlocated("no_postcode_candidate", reason)
        evidence = CandidateEvidence(unit)
        inputs = [{**b, "area_m2": b["projected_area_m2"]} for b in candidates]
        previous_projection_scores = score_arrays(unit, inputs)[0] * 100
        comparisons = []
        for roof, previous in zip(candidates, previous_projection_scores):
            index = self.building_indices[roof["building_id"]]
            option = self.measurements[index]
            points, mode, _ = evidence.compare((option,))
            compared = (len(option.poses) == len(evidence.poses)
                        and any(bearing is not None or band is not None for bearing, band in evidence.poses)
                        and (len(option.poses) == 1 or all(bearing is not None for bearing, _ in evidence.poses)))
            comparisons.append((index, points, mode, round(float(previous), 4), compared))
        comparisons.sort(key=lambda item: (-sum(item[1]), -item[1][0], item[0]))
        totals = [sum(points) for _, points, *_ in comparisons]
        top = totals[0]
        qualified_count = sum(total >= MINIMUM_CANDIDATE_POINTS * POINT_SCALE for total in totals)
        reason = "Candidates share the registry municipality and LGB postcode. Building identity remains unverified."
        if not qualified_count:
            reason += " No roof reached the candidate score threshold. The highest-scoring roofs are available for review."
        projected_center = evidence.center * math.sqrt(math.prod(evidence.projection)) if evidence.center and evidence.projection else None
        result = {"association_status": "building_candidates", "confidence": "low",
                  "candidate_status": "ranked" if qualified_count else "low_compatibility", "reason": reason,
                  "candidate_pool_size": len(candidates), "candidate_qualified_count": qualified_count,
                  "candidate_area_source": evidence.source, "candidate_registered_area_m2": evidence.center,
                  "candidate_projected_area_m2": projected_center, "score": top / POINT_SCALE,
                  "score_gap": ((top - totals[1]) / POINT_SCALE
                                if len(totals) > 1 and qualified_count != 1 else None),
                  "near_best_count": sum(value >= top - 2 * POINT_SCALE
                                         for value in totals[:qualified_count or None]),
                  "alternatives": [{"building_id": self.building_ids[index], "score": sum(points) / POINT_SCALE,
                                    "projected_score": previous, "area_method": mode,
                                    "orientation_compared": bool(compared)}
                                   for index, points, mode, previous, compared in comparisons[:FALLBACK_COUNT]]}
        if full:
            result["_ranking"] = [[index, *points, 0, AREA_MODES.index(mode)]
                                  for index, points, mode, _, _ in comparisons]
        return result

    @staticmethod
    def unlocated(status, reason):
        return {"association_status": "unmatched_registry", "candidate_status": status,
                "candidate_pool_size": 0, "reason": reason}


def postal_summary(records, postcodes, boundaries, restore):
    totals = defaultdict(lambda: dict(registry_count=0, registry_power_kw=0, target_count=0,
                                      target_power_kw=0, post_capture_count=0))
    for row in records:
        group = totals[row["municipality"], row["postcode"] or ""]
        watts = round((row["power_kw"] or 0) * 1000)
        group["registry_count"] += 1
        group["registry_power_kw"] += watts
        if row["cohort"] == "active_building_under30":
            group["target_count"] += 1
            group["target_power_kw"] += watts
            group["post_capture_count"] += row["association_status"] == "post_capture_unlocated"
    features, rows = [], []
    for (municipality, postcode), group in sorted(totals.items()):
        for field in ("registry_power_kw", "target_power_kw"):
            group[field] /= 1000
        geometry = None
        if postcode in postcodes and municipality in boundaries:
            geometry = MultiPolygon(list(polygons(postcodes[postcode].intersection(boundaries[municipality]))))
        area_available = geometry is not None and geometry.area > 0
        row = {"municipality": municipality, "postcode": postcode, "has_geometry": area_available, **group}
        rows.append(row)
        if area_available:
            features.append({"type": "Feature", "geometry": mapping(transform(restore, geometry)), "properties": row})
    return features, rows
