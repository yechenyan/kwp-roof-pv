"""Transactional local review state with immutable source observations."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from pyproj import Transformer
from shapely.geometry import box, shape, mapping
from shapely.ops import transform
from shapely.ops import unary_union, split
from functools import lru_cache
from shapely.prepared import prep
from .delivery import web_delivery


def now():
    return datetime.now(timezone.utc).isoformat()


def checksum(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


class Conflict(Exception):
    """The source run or candidate changed while the reviewer was editing."""


class Store:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "state").mkdir(parents=True, exist_ok=True)
        self.database = self.root / "state" / "review.sqlite3"
        self.metric_project = Transformer.from_crs(4326, 3035, always_xy=True).transform
        self._footprints = {}
        self._prepared_footprints = {}
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_migrations VALUES(1);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, manifest_sha TEXT NOT NULL, manifest TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS candidates(run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, base TEXT NOT NULL, geometry TEXT, status TEXT NOT NULL DEFAULT 'pending', reason TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', reviewer TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0, updated_at TEXT, PRIMARY KEY(run_id,id));
                CREATE TABLE IF NOT EXISTS areas(run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, geometry TEXT NOT NULL, note TEXT NOT NULL, reviewer TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id,id));
                CREATE TABLE IF NOT EXISTS events(run_id TEXT NOT NULL REFERENCES runs(id), revision INTEGER NOT NULL, item_id TEXT NOT NULL, kind TEXT NOT NULL, reviewer TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id,revision));
            """)
            if "active" not in {row[1] for row in db.execute("PRAGMA table_info(candidates)")}:
                db.execute("ALTER TABLE candidates ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(2)")
            if "mounting" not in {row[1] for row in db.execute("PRAGMA table_info(candidates)")}:
                db.execute("ALTER TABLE candidates ADD COLUMN mounting TEXT NOT NULL DEFAULT 'unknown'")
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(3)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def import_runs(self):
        for folder in sorted((self.root / "runs").glob("*")):
            path = folder / "manifest.json"
            if folder.name.startswith(".") or not path.is_file():
                continue
            manifest = json.loads(path.read_text())
            if manifest.get("status") != "completed" or manifest.get("schema_version") != 1 or manifest.get("run_id") != folder.name:
                continue
            sha = checksum(path)
            delivery = web_delivery(folder, manifest)
            if delivery:
                for name in delivery["artifacts"]:
                    if checksum(folder / name) != manifest["artifacts"][name]:
                        raise ValueError("Web delivery artifact failed integrity verification.")
            with self.connection() as db:
                existing = db.execute("SELECT manifest_sha FROM runs WHERE id=?", (folder.name,)).fetchone()
                if existing:
                    if existing[0] != sha:
                        raise Conflict("An imported run manifest changed. Restore it or create a new run.")
                    continue
                artifacts = delivery["artifacts"] if delivery else manifest["artifacts"]
                for name in artifacts:
                    expected = manifest["artifacts"][name]
                    if Path(name).is_absolute() or ".." in Path(name).parts or not (folder / name).is_file() or checksum(folder / name) != expected:
                        raise ValueError("Run artifact failed integrity verification.")
                for tile in ([] if delivery and delivery["imagery"] == "preview" else manifest.get("source_tiles", [])):
                    reference = Path(tile["file"])
                    if reference.is_absolute() or ".." in reference.parts or checksum(self.root / reference) != tile["sha256"]:
                        raise ValueError("Referenced source imagery failed integrity verification.")
                db.execute("INSERT INTO runs(id,manifest_sha,manifest) VALUES(?,?,?)", (folder.name, sha, json.dumps(manifest)))
                features = json.loads((folder / "candidates.geojson").read_text())["features"]
                for feature in features:
                    self.validate_geometry(manifest, feature["geometry"])
                    db.execute("INSERT INTO candidates(run_id,id,base) VALUES(?,?,?)", (folder.name, feature["id"], json.dumps(feature)))

    def manifest(self, db, run_id):
        row = db.execute("SELECT manifest FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError("Unknown run.")
        manifest = json.loads(row[0])
        delivery = web_delivery(self.root / "runs" / run_id, manifest)
        if delivery:
            manifest["delivery"] = {"imagery": delivery["imagery"]}
        return manifest

    def footprint(self, manifest):
        key = manifest["run_id"]
        if key in self._footprints:
            return self._footprints[key]
        if manifest.get("aoi_geometry"):
            result = shape(manifest["aoi_geometry"])
        else:
            project = Transformer.from_crs(manifest["input"]["crs"], 4326, always_xy=True).transform
            result = transform(project, box(*manifest["input"]["bounds"]))
        self._footprints[key] = result
        return result

    def validate_geometry(self, manifest, geometry):
        geom = shape(geometry)
        if geom.geom_type not in ["Polygon", "MultiPolygon"] or geom.is_empty or not geom.is_valid:
            raise ValueError("Geometry must be a valid non-empty Polygon or MultiPolygon.")
        key = manifest["run_id"]
        if key not in self._prepared_footprints:
            self._prepared_footprints[key] = prep(self.footprint(manifest).buffer(1e-9))
        if not self._prepared_footprints[key].covers(geom):
            raise ValueError("Geometry is outside this run's image footprint.")
        metric = transform(Transformer.from_crs(4326, 3035, always_xy=True).transform, geom)
        if metric.area < 0.02:
            raise ValueError("Geometry is too small to review.")
        json.dumps(geometry, allow_nan=False)
        return float(metric.area)

    @lru_cache(maxsize=50000)
    def geometry_metrics(self, geometry_json):
        geom = shape(json.loads(geometry_json))
        point = geom.representative_point()
        return transform(self.metric_project, geom).area, [point.x, point.y]

    def feature(self, row):
        feature = json.loads(row["base"])
        original_geometry = feature["geometry"]
        if row["geometry"]:
            feature["geometry"] = json.loads(row["geometry"])
        area, point = self.geometry_metrics(json.dumps(feature["geometry"]))
        feature["properties"].update(status=row["status"], reason=row["reason"], note=row["note"], reviewer=row["reviewer"], version=row["version"], updated_at=row["updated_at"], geometry_modified=bool(row["geometry"]), projected_area_m2=area, original_geometry=original_geometry, active=bool(row["active"]))
        feature["properties"].update(mounting=row["mounting"], quality_flag={"pending": "unreviewed", "confirmed": "human_confirmed_pv", "rejected": "human_rejected", "uncertain": "human_uncertain"}[row["status"]], quality_reason=row["reason"] or ("Awaiting human review." if row["status"] == "pending" else "Human image review; not a registry identity or calibrated probability."))
        feature["properties"]["review_point"] = point
        return feature

    def snapshot(self, run_id, event_limit=None):
        with self.connection() as db:
            db.execute("BEGIN")
            manifest = self.manifest(db, run_id)
            revision = db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()[0]
            features, retired = [], []
            for row in db.execute("SELECT * FROM candidates WHERE run_id=? ORDER BY id", (run_id,)):
                (features if row["active"] else retired).append(self.feature(row))
            areas = [{"type": "Feature", "id": row["id"], "geometry": json.loads(row["geometry"]), "properties": {key: row[key] for key in ["id", "note", "reviewer", "created_at"]}} for row in db.execute("SELECT * FROM areas WHERE run_id=? ORDER BY id", (run_id,))]
            if event_limit is None:
                events = [dict(row) for row in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY revision", (run_id,))]
            else:
                events = [dict(row) for row in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY revision DESC LIMIT ?", (run_id, event_limit))][::-1]
            manifest_sha = db.execute("SELECT manifest_sha FROM runs WHERE id=?", (run_id,)).fetchone()[0]
        return {"schema_version": 1, "run_id": run_id, "revision": revision, "manifest_sha256": manifest_sha, "manifest": manifest, "footprint": mapping(self.footprint(manifest)), "candidates": {"type": "FeatureCollection", "features": features}, "retired_candidates": {"type": "FeatureCollection", "features": retired}, "reviewed_areas": {"type": "FeatureCollection", "features": areas}, "events": events}

    def event(self, db, run_id, item_id, kind, reviewer, payload):
        db.execute("UPDATE runs SET revision=revision+1 WHERE id=?", (run_id,))
        revision = db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()[0]
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)", (run_id, revision, item_id, kind, reviewer, now(), json.dumps(payload, allow_nan=False)))

    def update(self, run_id, candidate_id, data):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            manifest = self.manifest(db, run_id)
            row = db.execute("SELECT * FROM candidates WHERE run_id=? AND id=?", (run_id, candidate_id)).fetchone()
            if not row:
                raise KeyError("Unknown candidate.")
            if not row["active"]:
                raise Conflict("This candidate was superseded by a manual operation.")
            if row["version"] != data["expected_version"]:
                raise Conflict("Candidate changed in another tab. Reload before saving.")
            geometry = row["geometry"]
            if data.get("reset_geometry"):
                geometry = None
            elif data.get("geometry"):
                self.validate_geometry(manifest, data["geometry"])
                geometry = json.dumps(data["geometry"], allow_nan=False)
            before = self.feature(row)
            db.execute("UPDATE candidates SET geometry=?, status=?, reason=?, note=?, reviewer=?, version=version+1, updated_at=? WHERE run_id=? AND id=?", (geometry, data["status"], data["reason"], data["note"], data["reviewer"], now(), run_id, candidate_id))
            if data.get("mounting"):
                db.execute("UPDATE candidates SET mounting=? WHERE run_id=? AND id=?", (data["mounting"], run_id, candidate_id))
            after = self.feature(db.execute("SELECT * FROM candidates WHERE run_id=? AND id=?", (run_id, candidate_id)).fetchone())
            self.event(db, run_id, candidate_id, "candidate_updated", data["reviewer"], {"before": before, "after": after})
        return after

    def add(self, run_id, geometry, reviewer, note, kind="candidate"):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            manifest = self.manifest(db, run_id)
            self.validate_geometry(manifest, geometry)
            item_id = f"{run_id}:manual:{uuid.uuid4().hex}"
            if kind == "area":
                db.execute("INSERT INTO areas VALUES(?,?,?,?,?,?)", (run_id, item_id, json.dumps(geometry), note, reviewer, now()))
            else:
                feature = {"type": "Feature", "id": item_id, "geometry": geometry, "properties": {"id": item_id, "run_id": run_id, "category": "manual", "default_model": None, "observation_ids": [], "score_mean": None}}
                db.execute("INSERT INTO candidates(run_id,id,base,note,reviewer,updated_at) VALUES(?,?,?,?,?,?)", (run_id, item_id, json.dumps(feature), note, reviewer, now()))
            self.event(db, run_id, item_id, "area_checked" if kind == "area" else "candidate_added", reviewer, {"geometry": geometry, "note": note})
        return item_id

    def transform_candidates(self, run_id, data):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            manifest = self.manifest(db, run_id)
            parents = []
            for item_id, version in data["expected_versions"].items():
                row = db.execute("SELECT * FROM candidates WHERE run_id=? AND id=?", (run_id, item_id)).fetchone()
                if not row or not row["active"] or row["version"] != version:
                    raise Conflict("A selected parent changed. Reload before merging or splitting.")
                parents.append(self.feature(row))
            if data["operation"] == "merge":
                if len(parents) < 2:
                    raise ValueError("Merge requires at least two candidates.")
                geometries = [mapping(unary_union([shape(f["geometry"]) for f in parents]))]
            else:
                if len(parents) != 1:
                    raise ValueError("Split requires exactly one candidate.")
                line = shape(data["cut_line"])
                if line.geom_type != "LineString" or not line.is_valid or line.is_empty:
                    raise ValueError("Split requires a valid cutting LineString.")
                pieces = split(shape(parents[0]["geometry"]), line)
                geometries = [mapping(geom) for geom in pieces.geoms if geom.geom_type in ["Polygon", "MultiPolygon"] and geom.area > 0]
                if len(geometries) < 2 or len(geometries) > 30:
                    raise ValueError("The cutting line must split the selected polygon into 2 to 30 parts.")
            for geometry in geometries:
                self.validate_geometry(manifest, geometry)
            children = []
            evidence = sorted({observation for f in parents for observation in f["properties"].get("observation_ids", [])})
            for geometry in geometries:
                item_id = f"{run_id}:manual:{uuid.uuid4().hex}"
                feature = {"type": "Feature", "id": item_id, "geometry": geometry, "properties": {"id": item_id, "run_id": run_id, "category": "manual", "default_model": None, "observation_ids": evidence, "score_mean": None, "parent_ids": [f["id"] for f in parents], "manual_operation": data["operation"]}}
                db.execute("INSERT INTO candidates(run_id,id,base,note,reviewer,updated_at) VALUES(?,?,?,?,?,?)", (run_id, item_id, json.dumps(feature), data["note"], data["reviewer"], now()))
                children.append(feature)
            for parent in parents:
                db.execute("UPDATE candidates SET active=0, version=version+1,updated_at=? WHERE run_id=? AND id=?", (now(), run_id, parent["id"]))
            self.event(db, run_id, children[0]["id"], "candidates_transformed", data["reviewer"], {"operation": data["operation"], "parents": parents, "children": children})
        return [f["id"] for f in children]

    def undo(self, run_id, expected_revision, reviewer):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()
            if not revision:
                raise KeyError("Unknown run.")
            if revision[0] != expected_revision:
                raise Conflict("Review revision changed. Reload before undo.")
            event = db.execute("SELECT * FROM events WHERE run_id=? ORDER BY revision DESC LIMIT 1", (run_id,)).fetchone()
            if not event:
                raise ValueError("There is no review action to undo.")
            payload = json.loads(event["payload"])
            if event["kind"] == "candidate_updated":
                previous = payload["before"]
                p = previous["properties"]
                geometry = json.dumps(previous["geometry"]) if p["geometry_modified"] else None
                db.execute("UPDATE candidates SET geometry=?,status=?,reason=?,note=?,reviewer=?,version=version+1,updated_at=? WHERE run_id=? AND id=?", (geometry, p["status"], p["reason"], p["note"], reviewer, now(), run_id, event["item_id"]))
                db.execute("UPDATE candidates SET mounting=? WHERE run_id=? AND id=?", (p.get("mounting", "unknown"), run_id, event["item_id"]))
            elif event["kind"] == "candidate_added":
                db.execute("UPDATE candidates SET active=0,version=version+1,updated_at=? WHERE run_id=? AND id=?", (now(), run_id, event["item_id"]))
            elif event["kind"] == "area_checked":
                db.execute("DELETE FROM areas WHERE run_id=? AND id=?", (run_id, event["item_id"]))
            elif event["kind"] == "candidates_transformed":
                for child in payload["children"]:
                    db.execute("UPDATE candidates SET active=0,version=version+1,updated_at=? WHERE run_id=? AND id=?", (now(), run_id, child["id"]))
                for parent in payload["parents"]:
                    db.execute("UPDATE candidates SET active=1,version=version+1,updated_at=? WHERE run_id=? AND id=?", (now(), run_id, parent["id"]))
            else:
                raise ValueError("The latest action cannot be undone again.")
            self.event(db, run_id, event["item_id"], "action_undone", reviewer, {"undone_revision": event["revision"], "original_kind": event["kind"], "original_payload": payload})
