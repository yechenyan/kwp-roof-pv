"""Revision-cached overviews and bounded spatial queries for the review UI."""
from collections import OrderedDict
import json
from threading import RLock
from shapely.geometry import box, shape, mapping
from shapely.strtree import STRtree
from .store import Conflict, checksum

SUMMARY_FIELDS = ["id", "category", "status", "mounting", "version", "projected_area_m2", "review_point", "building_overlap_ratio"]
MAP_FIELDS = SUMMARY_FIELDS + ["score_mean", "default_model", "acquisition_dates", "building_ids"]


def selected_properties(feature, fields):
    return {key: feature["properties"][key] for key in fields if key in feature["properties"]}


class ViewportService:
    def __init__(self, store):
        self.store = store
        self.cache = OrderedDict()
        self.context = OrderedDict()
        self.lock = RLock()

    def state(self, run_id):
        with self.lock:
            with self.store.connection() as db:
                row = db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown run.")
            existing = self.cache.get(run_id)
            if existing and existing["snapshot"]["revision"] == row[0]:
                self.cache.move_to_end(run_id)
                return existing
            snapshot = self.store.snapshot(run_id, event_limit=50)
            features = snapshot["candidates"]["features"]
            geometries = [shape(f["geometry"]) for f in features]
            source = snapshot["manifest"]
            manifest = {key: source[key] for key in ["run_id", "kind", "input", "preview_bounds", "counts", "limitations"] if key in source}
            identity = source.get("identity", {})
            manifest["identity"] = {"input_sha256": identity.get("input_sha256", ""), "config": {"models": identity.get("config", {}).get("models", {})}}
            summaries = [{"type": "Feature", "id": f["id"], "geometry": mapping(g.envelope),
                          "properties": {**selected_properties(f, SUMMARY_FIELDS), "geometry_is_envelope": True}}
                         for f, g in zip(features, geometries)]
            overview = {key: snapshot[key] for key in ["run_id", "revision", "manifest_sha256", "reviewed_areas"]}
            overview.update(manifest=manifest, footprint=mapping(shape(snapshot["footprint"]).simplify(.00001, preserve_topology=True)),
                            candidates={"type": "FeatureCollection", "features": summaries},
                            events=[{key: e[key] for key in ["revision", "item_id", "kind", "reviewer", "created_at"]} for e in snapshot["events"]],
                            geometry_mode="envelope_overview_use_detail_for_edits")
            value = {"snapshot": snapshot, "overview": overview, "geometries": geometries, "tree": STRtree(geometries),
                     "by_id": {f["id"]: f for f in features}}
            self.cache[run_id] = value
            self.cache.move_to_end(run_id)
            while len(self.cache) > 2:
                self.cache.popitem(last=False)
            return value

    def detail(self, run_id, item_id):
        try:
            return self.state(run_id)["by_id"][item_id]
        except KeyError:
            raise KeyError("Unknown active candidate.") from None

    def contextual(self, run_id, name):
        with self.lock:
            with self.store.connection() as db:
                manifest = self.store.manifest(db, run_id)
            filename = name + ".geojson"
            if filename not in manifest["artifacts"]:
                return [], [], STRtree([])
            path = self.store.root / "runs" / run_id / filename
            signature = (path.stat().st_size, path.stat().st_mtime_ns)
            key = (run_id, name)
            cached = self.context.get(key)
            if cached and cached[0] == signature:
                self.context.move_to_end(key)
                return cached[1:]
            if checksum(path) != manifest["artifacts"][filename]:
                raise Conflict("Immutable contextual geometry changed.")
            features = json.loads(path.read_text())["features"]
            geometries = [shape(f["geometry"]) for f in features]
            value = (signature, features, geometries, STRtree(geometries))
            self.context[key] = value
            while len(self.context) > 6:
                self.context.popitem(last=False)
            return value[1:]

    def view(self, run_id, bounds, zoom, layers, filters):
        if len(bounds) != 4 or not (-180 <= bounds[0] < bounds[2] <= 180 and -90 <= bounds[1] < bounds[3] <= 90):
            raise ValueError("Use west,south,east,north bounds in longitude/latitude.")
        if not set(layers) <= {"buildings", "rgbi", "rgb"}:
            raise ValueError("Unknown contextual layer.")
        state = self.state(run_id)
        result = {"revision": state["snapshot"]["revision"], "layers": {}, "counts": {}, "limits": {}, "truncated": {}}
        query = box(*bounds)
        if zoom < 16:
            return {**result, "candidates": {"type": "FeatureCollection", "features": []}}

        def include(feature):
            p = feature["properties"]
            if filters["status"] != "all" and p["status"] != filters["status"]:
                return False
            if filters["category"] != "all" and p["category"] != filters["category"]:
                return False
            support = p.get("building_overlap_ratio")
            return (filters["building"] == "all" or filters["building"] == "with" and support is not None and support > 0
                    or filters["building"] == "without" and support == 0 or filters["building"] == "unknown" and support is None)

        indices = sorted(int(i) for i in state["tree"].query(query, predicate="intersects") if include(state["snapshot"]["candidates"]["features"][int(i)]))
        result["counts"]["candidates"] = len(indices)
        result["limits"]["candidates"] = 1000
        result["truncated"]["candidates"] = len(indices) > 1000
        result["candidates"] = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": state["snapshot"]["candidates"]["features"][i]["id"],
             "geometry": mapping(state["geometries"][i].simplify(.0000005, preserve_topology=True)),
             "properties": selected_properties(state["snapshot"]["candidates"]["features"][i], MAP_FIELDS)} for i in indices[:1000]]}
        for name in layers:
            features, geometries, tree = self.contextual(run_id, name)
            hits = sorted(int(i) for i in tree.query(query, predicate="intersects"))
            limit = 2000 if name == "buildings" else 1000
            result["counts"][name], result["limits"][name], result["truncated"][name] = len(hits), limit, len(hits) > limit
            result["layers"][name] = {"type": "FeatureCollection", "features": [{**features[i], "geometry": mapping(geometries[i].simplify(.0000005, preserve_topology=True))} for i in hits[:limit]]}
        return result
