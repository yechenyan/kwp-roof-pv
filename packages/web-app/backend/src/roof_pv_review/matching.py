"""Independent registry identity review with frozen proposal provenance."""
from collections import Counter
import json
import os
from pathlib import Path
from threading import RLock
from roof_pv_registry.snapshot import latest_snapshot
from roof_pv_registry.source import atomic_json, digest
from roof_pv_matching.engine import build, load_batch, fingerprint
from .store import Conflict, now


class MatchingService:
    def __init__(self, store):
        self.store = store
        self.root = store.root
        self.lock = RLock()
        self.registry_cache = None
        self.batches = {}
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS registry_reviews(batch_id TEXT NOT NULL,proposal_id TEXT NOT NULL,solar_id TEXT NOT NULL,group_id TEXT NOT NULL,state TEXT NOT NULL,evidence_type TEXT NOT NULL,evidence TEXT NOT NULL,note TEXT NOT NULL,reviewer TEXT NOT NULL,version INTEGER NOT NULL,updated_at TEXT NOT NULL,group_confirmed INTEGER NOT NULL,PRIMARY KEY(batch_id,proposal_id));
                CREATE TABLE IF NOT EXISTS registry_review_events(id INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT NOT NULL,proposal_id TEXT NOT NULL,created_at TEXT NOT NULL,reviewer TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS registry_reviews_by_group ON registry_reviews(batch_id,group_id);
                CREATE INDEX IF NOT EXISTS registry_reviews_by_unit ON registry_reviews(batch_id,solar_id);
                INSERT OR IGNORE INTO schema_migrations VALUES(4);
            """)

    def registry(self):
        with self.lock:
            paths = sorted((self.root / "registry").glob("*/manifest.json"))
            if not paths:
                raise KeyError("No completed MaStR snapshot yet.")
            key = digest(paths[-1])
            if self.registry_cache is None or self.registry_cache[0] != key:
                self.registry_cache = (key, latest_snapshot(self.root))
            return self.registry_cache[1]

    def batch(self, run_id):
        with self.store.connection() as db:
            self.store.manifest(db, run_id)
        paths = [p for p in (self.root / "matching" / run_id).glob("*/manifest.json") if not p.parent.name.startswith(".")]
        if not paths:
            raise KeyError("No matching proposals. Build a proposal batch first.")
        path = max(paths, key=lambda p: json.loads(p.read_text())["created_at"])
        with self.lock:
            key = digest(path)
            cached = self.batches.get(run_id)
            if cached and cached[0] == key:
                return cached[1]
            batch = load_batch(path.parent)
            batch["by_id"] = {g["id"]: g for g in batch["groups"]["features"]}
            batch["by_candidate"] = {member: g["id"] for g in batch["groups"]["features"] for member in g["properties"]["member_ids"]}
            self.batches[run_id] = (key, batch)
            return batch

    def current_versions(self, db, run_id):
        return {r["id"]: (r["version"], r["active"], r["status"], r["mounting"]) for r in db.execute("SELECT id,version,active,status,mounting FROM candidates WHERE run_id=?", (run_id,))}

    def group_stale(self, group, versions):
        return any(member not in versions or versions[member][0] != version or not versions[member][1] for member, version in group["properties"]["member_versions"].items())

    def summary(self, run_id):
        try:
            registry = self.registry()
        except KeyError:
            return {"available": False, "registry_ready": False}
        result = {"available": False, "registry_ready": True, "registry": {key: registry.manifest[key] for key in ["snapshot_id", "snapshot_date", "summary", "limitations"]}}
        try:
            batch = self.batch(run_id)
        except KeyError:
            return result
        with self.store.connection() as db:
            versions = self.current_versions(db, run_id)
            reviews = [dict(r) for r in db.execute("SELECT * FROM registry_reviews WHERE batch_id=?", (batch["manifest"]["batch_id"],))]
        result.update(available=True, batch=batch["manifest"], review_counts=dict(Counter(r["state"] for r in reviews)),
                      stale_groups=sum(self.group_stale(g, versions) for g in batch["groups"]["features"]),
                      registry_snapshot_changed=batch["manifest"]["identity"]["registry_snapshot_id"] != registry.manifest["snapshot_id"])
        return result

    def unit_public(self, unit):
        return {key: value for key, value in unit.items() if key != "raw"}

    def units(self, run_id, query, cohort, offset, limit, association="all"):
        registry = self.registry()
        try:
            batch = self.batch(run_id)
        except KeyError:
            batch = None
        decisions = {}
        if batch:
            with self.store.connection() as db:
                versions = self.current_versions(db, run_id)
                for row in db.execute("SELECT * FROM registry_reviews WHERE batch_id=?", (batch["manifest"]["batch_id"],)):
                    if row["group_id"] in batch["by_id"] and not self.group_stale(batch["by_id"][row["group_id"]], versions):
                        decisions.setdefault(row["solar_id"], []).append(row["state"])
        values = []
        query = query.casefold().strip()
        for unit in sorted(registry.solar.values(), key=lambda u: u["id"]):
            if query and query not in " ".join(str(unit.get(key) or "") for key in ["id", "municipality", "postcode"]).casefold():
                continue
            if cohort == "target" and unit["cohort"] not in ["active_building_under30", "active_building_exactly30"]:
                continue
            if cohort == "coordinates" and unit["longitude"] is None:
                continue
            value = self.unit_public(unit)
            states = decisions.get(unit["id"], [])
            value["association_state"] = next((state for state in ["verified_identity", "probable_assignment", "ambiguous", "rejected", "pending"] if state in states), "unreviewed")
            value["association_count"] = sum(state in ["verified_identity", "probable_assignment"] for state in states)
            if association == "associated" and not value["association_count"]:
                continue
            if association == "unreviewed" and states:
                continue
            if association == "verified" and "verified_identity" not in states:
                continue
            ranking = batch["rankings"]["units"].get(unit["id"], {}) if batch else {}
            value.update(proposal_state=ranking.get("state", "unmatched_registry"))
            values.append(value)
        return {"total": len(values), "offset": offset, "limit": limit, "items": values[offset:offset + limit]}

    def unit(self, run_id, solar_id):
        registry = self.registry()
        if solar_id not in registry.solar:
            raise KeyError("Unknown MaStR solar unit.")
        unit = registry.solar[solar_id]
        try:
            batch = self.batch(run_id)
            with self.store.connection() as db:
                versions = self.current_versions(db, run_id)
                reviews = [dict(r) for r in db.execute("SELECT * FROM registry_reviews WHERE batch_id=? AND solar_id=?", (batch["manifest"]["batch_id"], solar_id))]
            def image_location(group_id):
                group = batch["by_id"][group_id]
                return {"member_ids": group["properties"]["member_ids"], "position": group["properties"]["review_point"],
                        "area_m2": group["properties"]["area_m2"], "stale": self.group_stale(group, versions)}
            ranking = batch["rankings"]["units"].get(solar_id, {})
            alternatives = [{**a, **image_location(a["group_id"])} for a in ranking.get("alternatives", [])]
            ranking = {**ranking, "alternatives": alternatives}
            reviewed_associations = [{**r, **image_location(r["group_id"])} for r in reviews if r["group_id"] in batch["by_id"]]
        except KeyError:
            ranking = None
            reviewed_associations = []
        storage = [r for r in registry.data["storage_relationships"] if r["solar_id"] == solar_id]
        return {"unit": self.unit_public(unit), "raw": {k: v for k, v in unit["raw"].items() if not k.startswith("_")},
                "ranking": ranking, "reviewed_associations": reviewed_associations, "storage": storage, "snapshot_id": registry.manifest["snapshot_id"]}

    def group(self, run_id, group_id):
        batch = self.batch(run_id)
        registry = self.registry()
        if group_id not in batch["by_id"]:
            raise KeyError("Unknown array group.")
        group = batch["by_id"][group_id]
        with self.store.connection() as db:
            versions = self.current_versions(db, run_id)
            reviews = [dict(r) for r in db.execute("SELECT * FROM registry_reviews WHERE batch_id=? AND group_id=?", (batch["manifest"]["batch_id"], group_id))]
            conflicts = [dict(r) for r in db.execute("SELECT solar_id,group_id,state FROM registry_reviews WHERE batch_id=? AND group_id<>? AND state IN ('verified_identity','probable_assignment')", (batch["manifest"]["batch_id"], group_id))]
        alternatives = batch["rankings"]["groups"][group_id]
        solar_ids = {a["solar_id"] for a in alternatives} | {r["solar_id"] for r in reviews}
        units = {solar_id: self.unit_public(registry.solar[solar_id]) for solar_id in solar_ids if solar_id in registry.solar}
        return {"batch_id": batch["manifest"]["batch_id"], "registry_snapshot_id": batch["manifest"]["identity"]["registry_snapshot_id"],
                "group": group, "alternatives": alternatives, "units": units, "reviews": reviews,
                "ranking_statistics": batch["rankings"].get("group_statistics", {}).get(group_id),
                "stale": self.group_stale(group, versions), "competing_reviews": conflicts,
                "registry_snapshot_changed": batch["manifest"]["identity"]["registry_snapshot_id"] != registry.manifest["snapshot_id"]}

    def candidate(self, run_id, candidate_id):
        batch = self.batch(run_id)
        group_id = batch["by_candidate"].get(candidate_id)
        if not group_id:
            return {"group": None, "reason": "Candidate is rejected or was added after the proposal batch; rebuild proposals."}
        return self.group(run_id, group_id)

    def review(self, run_id, group_id, data):
        batch = self.batch(run_id)
        if data["batch_id"] != batch["manifest"]["batch_id"]:
            raise Conflict("The proposal batch changed. Reload before identity review.")
        registry = self.registry()
        if registry.manifest["snapshot_id"] != batch["manifest"]["identity"]["registry_snapshot_id"]:
            raise Conflict("The registry snapshot changed. Rebuild proposals first.")
        if group_id not in batch["by_id"] or data["solar_id"] not in registry.solar:
            raise KeyError("Unknown group or solar unit.")
        group = batch["by_id"][group_id]
        proposal_id = "link-" + fingerprint([data["solar_id"], group_id])[:20]
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            versions = self.current_versions(db, run_id)
            if self.group_stale(group, versions):
                raise Conflict("Image members changed. Rebuild proposals before identity review.")
            if data["state"] == "verified_identity":
                if not data["group_confirmed"] or any(versions[m][2:] != ("confirmed", "roof") for m in group["properties"]["member_ids"]):
                    raise ValueError("Verify the array grouping and confirm all member regions as rooftop PV first; rebuild stale proposals afterwards.")
                if data["evidence_type"] not in ["authorized_address", "operator_confirmation", "field_verification", "registry_coordinate_review"] or len(data["evidence"].strip()) < 10:
                    raise ValueError("Independent identity evidence and its reference are required.")
            before = db.execute("SELECT * FROM registry_reviews WHERE batch_id=? AND proposal_id=?", (data["batch_id"], proposal_id)).fetchone()
            if (before["version"] if before else 0) != data["expected_version"]:
                raise Conflict("This identity decision changed in another tab. Reload before saving.")
            after = {"batch_id": data["batch_id"], "proposal_id": proposal_id, "solar_id": data["solar_id"], "group_id": group_id,
                     "state": data["state"], "evidence_type": data["evidence_type"], "evidence": data["evidence"].strip(),
                     "note": data["note"], "reviewer": data["reviewer"].strip(), "version": data["expected_version"] + 1,
                     "updated_at": now(), "group_confirmed": int(data["group_confirmed"])}
            db.execute("INSERT OR REPLACE INTO registry_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", tuple(after.values()))
            payload = {"before": dict(before) if before else None, "after": after, "member_versions": group["properties"]["member_versions"],
                       "registry_snapshot_id": registry.manifest["snapshot_id"], "matcher": batch["manifest"]["identity"]}
            db.execute("INSERT INTO registry_review_events(batch_id,proposal_id,created_at,reviewer,payload) VALUES(?,?,?,?,?)", (data["batch_id"], proposal_id, now(), after["reviewer"], json.dumps(payload)))
        return after

    def reserve_build(self, run_id):
        job = self.root / "jobs" / "matching.json"
        lock = self.root / "state" / "matching.lock"
        try:
            with lock.open("x") as stream:
                stream.write(json.dumps({"pid": os.getpid(), "run_id": run_id, "created_at": now()}))
        except FileExistsError:
            raise Conflict("A matching build is already running.") from None
        atomic_json(job, {"kind": "matching", "name": "MaStR proposal build", "run_id": run_id, "status": "running", "completed": 0, "total": 1})

    def rebuild(self, run_id, reserved=False):
        if not reserved:
            self.reserve_build(run_id)
        job = self.root / "jobs" / "matching.json"
        lock = self.root / "state" / "matching.lock"
        try:
            result = build(self.root, self.store.snapshot(run_id), self.registry())
            atomic_json(job, {"kind": "matching", "name": "MaStR proposal build", "run_id": run_id, "status": "completed", "completed": 1, "total": 1, "batch_id": result["manifest"]["batch_id"]})
            return result["manifest"]
        except Exception as exc:
            atomic_json(job, {"kind": "matching", "name": "MaStR proposal build", "run_id": run_id, "status": "failed", "completed": 0, "total": 1, "error": str(exc)})
            raise
        finally:
            lock.unlink(missing_ok=True)

    def export_bundle(self, run_id):
        batch = self.batch(run_id)
        registry = self.registry()
        if registry.manifest["snapshot_id"] != batch["manifest"]["identity"]["registry_snapshot_id"]:
            raise Conflict("Rebuild proposals for the current registry snapshot before exporting associations.")
        with self.store.connection() as db:
            db.execute("BEGIN")
            image_snapshot = self.store.snapshot(run_id)
            current = self.current_versions(db, run_id)
            if image_snapshot["revision"] != db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()[0]:
                raise Conflict("Image review changed during matching export. Retry.")
            reviews = [dict(r) for r in db.execute("SELECT * FROM registry_reviews WHERE batch_id=?", (batch["manifest"]["batch_id"],))]
            events = [dict(r) for r in db.execute("SELECT * FROM registry_review_events WHERE batch_id=? ORDER BY id", (batch["manifest"]["batch_id"],))]
        stale = {g["id"] for g in batch["groups"]["features"] if self.group_stale(g, current)}
        return {"batch": batch, "registry": registry, "image_snapshot": image_snapshot, "reviews": reviews, "events": events, "stale_groups": stale}
