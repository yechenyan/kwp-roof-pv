"""Signed evidence points for anonymous registry-to-rooftop comparisons."""
from dataclasses import dataclass
import math
from bisect import bisect_right

from scoring import SLOPES, expected_area

POINT_SCALE = 100
MINIMUM_CANDIDATE_POINTS = 42
FALLBACK_COUNT = 5
DIRECTIONS = {str(code): index * 45 for index, code in enumerate(range(695, 703))}


def interpolate(value, knots, points):
    index = bisect_right(knots, value)
    if index == 0:
        return points[0]
    if index == len(knots):
        return points[-1]
    left, right = knots[index - 1:index + 1]
    return points[index - 1] + (points[index] - points[index - 1]) * (value - left) / (right - left)


AREA_AGREEMENT = tuple(map(math.log, (1, 1.15, 1.35)))
AREA_CONFLICT = tuple(map(math.log, (1, 1.20, 1.50, 2)))


@dataclass(frozen=True)
class RoofOption:
    groups: tuple[str, ...]
    projected: float
    parallel: float | None
    poses: tuple[tuple[float, float], ...]


def measure_building(roof):
    projected = roof["projected_area_m2"]
    parallel = roof["roof_parallel_area_m2"]
    poses = ()
    if parallel is not None:
        faces = sorted(
            ((face["projected_area_m2"], face["slope_deg"], face["azimuth_deg"])
             for face in roof["_faces"] if face["projected_area_m2"] > .02),
            key=lambda face: face[0], reverse=True,
        )
        if faces and faces[0][0] >= .80 * projected:
            measured = faces[:1]
        elif len(faces) >= 2 and faces[0][0] + faces[1][0] >= .90 * projected and faces[1][0] >= .15 * projected:
            measured = faces[:2]
        else:
            measured = ()
        if all(slope >= 10 and azimuth is not None for _, slope, azimuth in measured):
            poses = tuple((azimuth, slope) for _, slope, azimuth in measured)
    return RoofOption(tuple(roof["group_ids"]), projected, parallel, poses)


def roof_options(roof, _groups=None):
    """Return the single building-wide option in the experiment interface."""
    return (measure_building(roof),)


def registered_poses(raw):
    main = DIRECTIONS.get(raw.get("Hauptausrichtung"))
    tilt = SLOPES.get(raw.get("HauptausrichtungNeigungswinkel"))
    other = DIRECTIONS.get(raw.get("Nebenausrichtung"))
    other_tilt = SLOPES.get(raw.get("NebenausrichtungNeigungswinkel"))
    if raw.get("Hauptausrichtung") == "704":
        return ((90, tilt), (270, other_tilt or tilt))
    if raw.get("Nebenausrichtung") or raw.get("EinheitlicheAusrichtungUndNeigungswinkel") == "0":
        return ((main, tilt), (other, other_tilt))
    return ((main, tilt),)


def pose_points(observed, registered, reliable_tilts=None):
    if not observed or len(observed) != len(registered):
        return 0, 0
    if len(observed) == 2 and any(bearing is None for bearing, _ in registered):
        # A missing bearing cannot resolve the correspondence between two faces.
        return 0, 0

    def combine(points):
        worst = min(points, default=0)
        # A strong disagreement on one face retains its full deduction.
        return worst if worst < 0 else sum(points) / len(registered)

    def compare(assignment):
        bearings, tilts = [], []
        for i, ((azimuth, slope), (bearing, band)) in enumerate(zip(assignment, registered)):
            if bearing is not None:
                gap = abs((azimuth - bearing + 180) % 360 - 180)
                bearings.append(interpolate(gap, (0, 22.5, 45, 90, 180), (12, 12, 6, 0, -24)))
            if band is not None and (reliable_tilts is None or reliable_tilts[i]):
                gap = max(band[0] - slope, slope - band[1], 0)
                tilts.append(interpolate(gap, (0, 5, 10, 20, 25, 40), (6, 6, 3, 0, -12, -20)))
        return combine(bearings), combine(tilts)

    choices = (observed, observed[::-1]) if len(observed) == 2 else (observed,)
    return max(map(compare, choices), key=sum)


class CandidateEvidence:
    def __init__(self, unit):
        raw = unit["raw"]
        self.poses = registered_poses(raw)
        self.reliable_tilts = tuple(raw.get(name) in SLOPES for name in (
            "HauptausrichtungNeigungswinkel", "NebenausrichtungNeigungswinkel"))
        # Reuse the year and quality rules without projecting the registry estimate.
        planar = {**raw, "HauptausrichtungNeigungswinkel": "3179", "Nebenausrichtung": None}
        factor = math.cos(math.radians(2.5))
        module, power = (value / factor if value is not None else None
                         for value in expected_area({**unit, "raw": planar}))
        self.center = module or power
        self.source = "modules" if module is not None else "power" if power is not None else "unavailable"
        self.agree = not (module and power) or max(module / power, power / module) <= 1.25
        if not self.agree:
            self.lower, self.upper = min(module, power) / 1.25, max(module, power) * 1.25
        elif self.center:
            width = 1.25 if module is not None else 1.55
            self.lower, self.upper = self.center / width, self.center * width
        bands = [band for _, band in self.poses]
        self.projection = None
        if all(band is not None and band[1] < 90 for band in bands):
            low = math.cos(math.radians(max(band[1] for band in bands)))
            high = math.cos(math.radians(min(band[0] for band in bands)))
            self.projection = (low, high)

    def area_points(self, option):
        if not self.center or (option.parallel is None and self.projection is None):
            return 0, "unavailable"
        center, low, high = self.center, self.lower, self.upper
        weight = 1 if self.source == "modules" and self.agree else .85
        if option.parallel is None:
            low_cos, high_cos = self.projection
            center *= math.sqrt(low_cos * high_cos)
            low *= low_cos
            high *= high_cos
            observed = option.projected
            mode = "projected"
            weight *= .8
        else:
            observed = option.parallel
            mode = "roof_parallel"
        ratio = max(observed / center, center / observed)
        agreement = interpolate(math.log(ratio), AREA_AGREEMENT, (40, 24, 0))
        if mode == "roof_parallel" and self.source == "modules" and self.agree:
            separation = math.log(max(1, observed / (1.25 * high), low / (1.25 * observed)))
            agreement -= interpolate(separation, AREA_CONFLICT, (0, 10, 24, 40))
        return weight * agreement, mode

    def compare(self, options):
        def evaluate(option):
            area, mode = self.area_points(option)
            direction, tilt = pose_points(option.poses, self.poses, self.reliable_tilts)
            points = tuple(round(value * POINT_SCALE) for value in (area, direction, tilt))
            return points, mode, option

        # Carry one consistent array hypothesis through every evidence component.
        return max(map(evaluate, options), key=lambda row: (sum(row[0]), row[0][0], -len(row[2].groups)))
