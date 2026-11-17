"""Continuous, explainable ranking and explicitly statistical roof allocation."""
from collections import Counter, defaultdict
import math
import numpy as np


VERSION = "technical-ranking-v3-location-consistent"
LOCATION_DIAMETER_M = 100.0
SLOPES = {"806": (90, 90), "807": (61, 89), "808": (41, 60),
          "809": (21, 40), "810": (5, 20), "3179": (0, 5)}


def expected_area(unit):
    """Engineering priors, not measured roof slopes or calibrated power estimates."""
    year = int((unit.get("commissioned") or "2015")[:4])
    module_area = 1.3 if year < 2005 else 1.6 if year < 2015 else 1.75 if year < 2020 else 1.95 if year < 2023 else 2.05
    density = .12 if year < 2005 else .15 if year < 2015 else .175 if year < 2020 else .195 if year < 2023 else .21
    raw = unit["raw"]
    angles = [SLOPES.get(raw.get("HauptausrichtungNeigungswinkel"), (15, 45))]
    if raw.get("Nebenausrichtung"):
        angles.append(SLOPES.get(raw.get("NebenausrichtungNeigungswinkel"), (15, 45)))
    cosine = sum(math.cos(math.radians(sum(a) / 2)) for a in angles) / len(angles)
    cosine = max(.25, cosine)
    modules = unit.get("module_count")
    module_estimate = modules * module_area * cosine if modules and modules > 0 and "module_count_power_review" not in unit["flags"] else None
    power_estimate = unit["power_kw"] / density * cosine if unit.get("power_kw") and unit["power_kw"] > 0 else None
    return module_estimate, power_estimate


def similarity(area, expected, sigma=.55):
    if expected is None:
        return None
    return np.exp(-.5 * (np.log(np.maximum(area, .01) / expected) / sigma) ** 2)


def score_arrays(unit, pool):
    areas = np.array([g["area_m2"] for g in pool])
    module, power = expected_area(unit)
    module_score, power_score = similarity(areas, module), similarity(areas, power)
    area_score = (.65 * module_score + .35 * power_score if module_score is not None and power_score is not None
                  else module_score if module_score is not None else power_score if power_score is not None else np.zeros(len(pool)))
    quality = np.array([g["image_quality"] for g in pool])
    result = .85 * area_score + .15 * quality
    distances = None
    if unit.get("_point") is not None:
        distances = np.array([unit["_point"].distance(g["_geometry"]) for g in pool])
        result = .8 * np.exp(-distances / 25) + .2 * result
    timing = np.array([.7 if unit.get("commissioned") and unit["commissioned"] > g["capture_dates"][-1] else 1 for g in pool])
    return result * timing, area_score, distances, module, power


def rank_unit(unit, pool, limit=5):
    if not pool:
        return None, []
    scores, area_scores, distances, module, power = score_arrays(unit, pool)
    order = sorted(range(len(pool)), key=lambda i: (-round(float(scores[i]), 10), pool[i]["id"]))
    i = order[0]
    best = pool[i]
    gap = float(scores[i] - scores[order[1]]) if len(order) > 1 else None
    ties = int(np.count_nonzero(np.abs(scores - scores[i]) <= 1e-8))
    near = int(np.count_nonzero(scores >= scores[i] - .02))
    temporal = bool(unit.get("commissioned") and unit["commissioned"] > best["capture_dates"][-1])
    distance = float(distances[i]) if distances is not None else None
    confidence = "medium" if distance is not None and distance <= 25 and (gap or 0) >= .1 and not temporal else "low"
    reasons = [f"Selected highest technical score among {len(pool)} building-supported array groups.",
               f"Observed projected area {best['area_m2']:.2f} m2; area similarity {area_scores[i]:.3f}.",
               f"Best-next gap {gap*100:.4f} score points." if gap is not None else "Only one candidate in the spatial search.",
               f"{near} alternatives within 2 score points of the best; {ties} numerical best-score ties."]
    if distance is not None:
        reasons.append(f"Public coordinate is {distance:.1f} m from the selected array.")
    else:
        reasons.append("No independent address/coordinate evidence; ranking does not establish identity.")
    if temporal:
        reasons.append("Commissioned after this image; visible equipment may predate commissioning or belong to an older system.")
    reasons.append("Panel-facing azimuth and roof slope are not measured; categorical tilt only informs an area prior.")
    alternatives = [{"group_id": pool[j]["id"], "score": round(float(scores[j]) * 100, 4),
                     "area_m2": pool[j]["area_m2"]} for j in order[:limit]]
    result = {"group_id": best["id"], "score": round(float(scores[i]) * 100, 4),
              "area_similarity": round(float(area_scores[i]), 6), "expected_module_area_m2": module,
              "expected_power_area_m2": power, "coordinate_distance_m": distance,
              "score_gap": round(gap * 100, 6) if gap is not None else None, "best_tie_count": ties,
              "near_best_count": near, "candidate_pool_size": len(pool), "confidence": confidence,
              "association_status": "inferred_image_association", "identity_verified": False,
              "post_capture": temporal, "reason": " ".join(reasons)}
    return result, alternatives


class LocationConstraints:
    """Keep every selected point at a registry location in one bounded cluster."""

    def __init__(self, maximum_diameter_m=LOCATION_DIAMETER_M):
        self.maximum_diameter_m = maximum_diameter_m
        self.points = defaultdict(list)

    def allows(self, location_key, point):
        return all(math.dist(point, existing) <= self.maximum_diameter_m
                   for existing in self.points.get(location_key, []))

    def reserve(self, location_key, point):
        if not self.allows(location_key, point):
            raise ValueError("Selected point violates registry-location consistency.")
        self.points[location_key].append(tuple(point))


def allocate_building(unit, buildings, occupied, *, reusable_ids=None, used_area=None, observed_area=None):
    """Deterministic planning allocation to feasible roofs, never a detected PV outline."""
    module, power = expected_area(unit)
    demand = module or power or 25
    reusable_ids = reusable_ids or set()
    used_area = used_area if used_area is not None else {}
    observed_area = observed_area or {}
    def available(b):
        return max(0, .45 * b["area_m2"] - observed_area.get(b["id"], 0) - used_area.get(b["id"], 0))
    pool = [b for b in buildings if b["municipality"] == unit["municipality"]
            and (b["id"] not in occupied or b["id"] in reusable_ids)
            and 30 <= b["area_m2"] <= 1000 and available(b) >= demand]
    if not pool:
        return None, []
    def value(b):
        size_fit = math.exp(-.5 * (math.log(available(b) / (demand * 1.4)) / .6) ** 2)
        return .8 * size_fit + .2 * b["compactness"]
    pool.sort(key=lambda b: (-round(value(b), 10), b["id"]))
    best = pool[0]
    free_area = available(best)
    best_score = value(best)
    alternatives = [{"building_id": b["id"], "score": round(value(b) * 100, 4), "building_area_m2": b["area_m2"]}
                    for b in pool[:5]]
    gap = (best_score - value(pool[1])) * 100 if len(pool) > 1 else None
    ties = sum(abs(value(b) - best_score) < 1e-8 for b in pool)
    near = sum(value(b) >= best_score - .02 for b in pool)
    reason = (f"Post-capture statistical allocation: highest roof-capacity/compactness score among {len(pool)} eligible "
              f"eligible buildings after location and occupancy constraints. Assumed usable roof fraction 45%; demand {demand:.1f} m2; "
              f"remaining usable area before allocation {free_area:.1f} m2. "
              "Greedy order: descending registered power, then solar ID. No observed PV or independent identity evidence "
              "at this building. Cadastral construction footprints and actual roof suitability are not independently verified. "
              "Capacity suitability is not an empirically validated improvement in location accuracy.")
    if best["id"] in reusable_ids:
        reason += " Reuses a building already associated with this registry location; existing detected PV and earlier allocations consume roof capacity."
    occupied.add(best["id"])
    used_area[best["id"]] = used_area.get(best["id"], 0) + demand
    return {"building_id": best["id"], "score": round(best_score * 100, 4), "confidence": "very_low",
            "association_status": "statistical_building_allocation", "identity_verified": False,
            "post_capture": True, "candidate_pool_size": len(pool), "reason": reason,
            "expected_module_area_m2": module, "expected_power_area_m2": power,
            "score_gap": round(gap, 6) if gap is not None else None,
            "best_tie_count": ties, "near_best_count": near,
            "allocated_projected_area_m2": demand, "available_roof_area_m2": free_area}, alternatives


def flag_competition(records, groups):
    links = defaultdict(list)
    for r in records:
        if r.get("group_id"):
            links[r["group_id"]].append(r)
    for group_id, rows in links.items():
        total = sum(r["power_kw"] or 0 for r in rows)
        area = groups[group_id]["area_m2"]
        for row in rows:
            row["competing_registrations"] = len(rows)
            row["group_assigned_power_kw"] = round(total, 4)
            row["capacity_conflict"] = total > area * .5
            if len(rows) > 1:
                row["confidence"] = "low"
                row["reason"] += f" {len(rows)} registrations select this group; shared location/extension or competition remains unresolved."
            if row["capacity_conflict"]:
                row["reason"] += " Combined assigned power exceeds the broad 0.5 kW/m2 projected-area screen."
    return dict(Counter(r["confidence"] for r in records))
