"""Jointly allocate one unverified building proposal per eligible registry record."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
import math

from ortools.sat.python import cp_model
from shapely.strtree import STRtree

from building_evidence import AREA_MODES
from candidate_scoring import CandidateEvidence, MINIMUM_CANDIDATE_POINTS, POINT_SCALE
from scoring import LOCATION_DIAMETER_M, expected_area


POWER_LIMIT = .30
AREA_ALLOWANCE = 1.35
TIME_LIMIT_SECONDS = 25
MINIMUM_POINTS = MINIMUM_CANDIDATE_POINTS * POINT_SCALE


@dataclass(frozen=True)
class RoofBudget:
    watts: int
    area: int
    center: tuple[float, float]


@dataclass(frozen=True)
class Choice:
    roof: int
    option: int
    points: tuple[int, int, int]
    mode: str
    groups: tuple[str, ...]

    @property
    def score(self):
        return sum(self.points)


@dataclass(frozen=True)
class Request:
    key: str
    site: str
    watts: int
    area: int
    choices: tuple[Choice, ...]


def projected_demand(unit):
    module, power = expected_area(unit)
    estimate = .65 * module + .35 * power if module and power else module or power
    return max(1, round(estimate * 10)) if estimate is not None else 0


def roof_budget(area_m2, center, booked_watts=0, booked_area=0):
    return RoofBudget(max(0, math.floor(area_m2 * POWER_LIMIT * 1000 + 1e-7) - booked_watts),
                      max(0, math.floor(area_m2 * AREA_ALLOWANCE * 10 + 1e-7) - booked_area), center)


def overlapping_groups(geometries):
    names = sorted(geometries)
    shapes = [geometries[key] for key in names]
    if not shapes:
        return ()
    left, right = STRtree(shapes).query(shapes, predicate="intersects")
    return tuple((names[i], names[j]) for i, j in sorted(zip(left, right)) if i < j
                 and shapes[i].intersection(shapes[j]).area >= max(.25, .1 * min(shapes[i].area, shapes[j].area)))


def components(requests, overlaps):
    parents = list(range(len(requests)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(left, right):
        parents[root(right)] = root(left)

    owners = {}
    for i, request in enumerate(requests):
        resources = {("site", request.site)}
        for choice in request.choices:
            resources.add(("roof", choice.roof))
            resources.update(("group", group) for group in choice.groups)
        for resource in sorted(resources):
            join(owners.setdefault(resource, i), i)
    for a, b in overlaps:
        first = owners.get(("group", a))
        second = owners.get(("group", b))
        if first is not None and second is not None:
            join(first, second)
    partitions = defaultdict(list)
    for i, request in enumerate(requests):
        partitions[root(i)].append(request)
    return partitions.values()


def solve_component(requests, budgets, overlaps):
    if len(requests) == 1:
        request, = requests
        chosen = max(request.choices, key=lambda item: (item.score, item.points[0]))
        best = chosen.score - MINIMUM_POINTS
        return {request.key: chosen}, {"optimal": True, "surplus": best, "bound": best}

    model = cp_model.CpModel()
    roof_load = defaultdict(list)
    groups = defaultdict(list)
    sites = defaultdict(lambda: defaultdict(list))
    entries, weights = [], []
    scale = len(requests) + 1
    for request in requests:
        choices = []
        for item in request.choices:
            x = model.new_bool_var("")
            roof_load[item.roof].append((request, x))
            sites[request.site][item.roof].append((request.key, x))
            for group in item.groups:
                groups[group].append(len(entries))
            entries.append((request.key, item, x))
            choices.append(x)
            weights.append(((item.score - MINIMUM_POINTS) * scale + 1) * x)
        model.add_at_most_one(choices)
    for roof, items in roof_load.items():
        budget = budgets[roof]
        model.add(sum(request.watts * x for request, x in items) <= budget.watts)
        model.add(sum(request.area * x for request, x in items) <= budget.area)
    for variables in groups.values():
        if len(variables) > 1:
            model.add_at_most_one(entries[i][2] for i in variables)
    for a, b in overlaps:
        shared = sorted(set(groups.get(a, ())) | set(groups.get(b, ())))
        if len(shared) > 1:
            model.add_at_most_one(entries[i][2] for i in shared)
    for site in sites.values():
        if len({key for variables in site.values() for key, _ in variables}) <= 1:
            continue
        occupied = {}
        for roof, variables in site.items():
            occupied[roof] = model.new_bool_var("")
            model.add_max_equality(occupied[roof], [x for _, x in variables])
        for a, b in combinations(occupied, 2):
            if math.dist(budgets[a].center, budgets[b].center) > LOCATION_DIAMETER_M:
                model.add_at_most_one(occupied[a], occupied[b])
    model.maximize(sum(weights))
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 4
    solver.parameters.random_seed = 293
    solver.parameters.max_time_in_seconds = TIME_LIMIT_SECONDS
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"No usable joint proposal solution: {solver.status_name(status)}")
    selected = {key: item for key, item, x in entries if solver.boolean_value(x)}
    return selected, {"optimal": status == cp_model.OPTIMAL,
                      "surplus": sum(item.score - MINIMUM_POINTS for item in selected.values()),
                      "bound": math.floor(solver.best_objective_bound / scale)}


def assign(requests, budgets, overlaps):
    proposals, totals = {}, Counter(components=0, optimal_components=0, surplus=0, bound=0)
    for part in components(requests, overlaps):
        choices, stats = solve_component(part, budgets, overlaps)
        proposals.update(choices)
        totals["components"] += 1
        totals["optimal_components"] += stats["optimal"]
        totals.update({key: stats[key] for key in ("surplus", "bound")})
    return proposals, dict(totals)


def joint_proposals(rankings, units, shortlists, buildings, groups, records):
    owners = {gid: i for i, building in enumerate(buildings) for gid in building["group_ids"]}
    booked = [row for row in records if row["association_status"] == "inferred_image_association"]
    reserved = {row["group_id"] for row in booked}
    booked_watts, booked_area, booked_sites = Counter(), Counter(), defaultdict(list)
    for row in booked:
        b = owners.get(row["group_id"])
        if b is not None:
            booked_watts[b] += round(row["power_kw"] * 1000)
            booked_area[b] += round(groups[row["group_id"]]["_geometry"].area * 10)
        booked_sites[row["location_id"] or row["solar_id"]].append(groups[row["group_id"]]["_location_point"])
    budgets = [roof_budget(b["projected_area_m2"], tuple(b["_geometry"].representative_point().coords[0]),
                           booked_watts[i], booked_area[i]) for i, b in enumerate(buildings)]
    overlaps = overlapping_groups({gid: groups[gid]["_geometry"] for gid in owners.keys() | reserved})
    blocked = reserved | {b for a, b in overlaps if a in reserved} | {a for a, b in overlaps if b in reserved}
    requests = []
    for row, ranking in sorted(rankings, key=lambda item: item[0]["solar_id"]):
        unit = units[row["solar_id"]]
        evidence = CandidateEvidence(unit)
        demand = projected_demand(unit)
        watts = round(unit["power_kw"] * 1000)
        site = row["location_id"] or row["solar_id"]
        choices = []
        for roof, area, direction, tilt, preferred, mode in ranking:
            if area + direction + tilt < MINIMUM_POINTS:
                break
            budget = budgets[roof]
            if watts > budget.watts or not 0 < demand <= budget.area:
                continue
            if any(math.dist(budget.center, point) > LOCATION_DIAMETER_M for point in booked_sites.get(site, ())):
                continue
            for i, option in enumerate(shortlists.options[roof]):
                if blocked.intersection(option.groups):
                    continue
                if i == preferred:
                    points, method = (area, direction, tilt), AREA_MODES[mode]
                else:
                    points, method, _ = evidence.compare((option,))
                if sum(points) >= MINIMUM_POINTS:
                    choices.append(Choice(roof, i, points, method, option.groups))
        if choices:
            requests.append(Request(row["solar_id"], site, watts, demand, tuple(choices)))

    selected, diagnostics = assign(requests, budgets, overlaps)
    for row, ranking in rankings:
        choice = selected.get(row["solar_id"])
        row["joint_proposal"] = None
        if choice is not None:
            rank = next(i for i, item in enumerate(ranking, 1) if item[0] == choice.roof)
            row["joint_proposal"] = {"building_id": shortlists.building_ids[choice.roof], "rank": rank,
                                     "array_option": choice.option, "area_method": choice.mode, "group_ids": choice.groups,
                                     "points": choice.points, "score": choice.score / POINT_SCALE}
    return {"requests_with_feasible_choices": len(requests), "proposals": len(selected), **diagnostics,
            "power_kw_per_projected_m2": POWER_LIMIT, "area_allowance": AREA_ALLOWANCE,
            "location_limit_m": LOCATION_DIAMETER_M}
