"""Single-owner, version-pinned GeoPackage exports without inference imports."""
import json
import os
import sqlite3
import uuid
from pathlib import Path
import geopandas as gpd
import pyogrio
from shapely.geometry import shape
from shapely import make_valid
from shapely.ops import unary_union
from shapely.validation import explain_validity
from .store import now, checksum
from .report import make_report


def layer(features):
    rows = []
    for feature in features:
        properties = {key: json.dumps(value, separators=(",", ":")) if isinstance(value, (list, dict)) else value for key, value in feature["properties"].items() if key != "original_geometry"}
        rows.append({**properties, "geometry": shape(feature["geometry"])})
    if not rows:
        return gpd.GeoDataFrame({"id": [], "geometry": []}, geometry="geometry", crs=4326).to_crs(3035)
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326).to_crs(3035)
    frame["geometry_repaired_for_export"] = False
    frame["geometry_export_repair_reason"] = ""
    frame["geometry_repair_area_delta_m2"] = 0.0
    for index in frame.index[~frame.geometry.is_valid]:
        original = frame.geometry.loc[index]
        candidates = [("make_valid", make_valid(original)), ("buffer_zero", original.buffer(0))]
        eligible = []
        for method, repaired in candidates:
            if repaired.geom_type == "GeometryCollection":
                repaired = unary_union([part for part in repaired.geoms if part.geom_type in ["Polygon", "MultiPolygon"]])
            difference = abs(repaired.area - original.area)
            if not repaired.is_empty and repaired.is_valid and difference <= max(.01, original.area * 1e-6):
                eligible.append((difference, method, repaired))
        if not eligible:
            raise ValueError("Geometry repair is too large or non-areal; manual correction is required.")
        difference, method, repaired = min(eligible, key=lambda value: value[0])
        frame.at[index, "geometry"] = repaired
        frame.at[index, "geometry_repaired_for_export"] = True
        frame.at[index, "geometry_export_repair_reason"] = explain_validity(original) + "; " + method + " polygonal components"
        frame.at[index, "geometry_repair_area_delta_m2"] = difference
    return frame


def export_snapshot(root, snapshot, registry_bundle=None):
    root = Path(root)
    run_id, revision = snapshot["run_id"], snapshot["revision"]
    export_id = f"{run_id}-r{revision}-{uuid.uuid4().hex[:8]}"
    folder = root / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / ("." + export_id + ".gpkg")
    output = folder / (export_id + ".gpkg")
    manifest = {"schema_version": 1, "export_id": export_id, "created_at": now(), "run_id": run_id, "review_revision": revision, "run_manifest_sha256": snapshot["manifest_sha256"], "crs": "EPSG:3035", "scope": "PV image observations and human review only; no MaStR identity, power or storage assignment.", "confirmed_count": sum(f["properties"]["status"] == "confirmed" for f in snapshot["candidates"]["features"]), "candidate_count": len(snapshot["candidates"]["features"]), "attribution": "GeoBasis-DE/LGB, dl-de/by-2-0"}
    datasets = {"candidates": snapshot["candidates"]["features"], "confirmed_pv": [f for f in snapshot["candidates"]["features"] if f["properties"]["status"] == "confirmed"], "reviewed_areas": snapshot["reviewed_areas"]["features"], "coverage": [{"type": "Feature", "geometry": snapshot["footprint"], "properties": {"run_id": run_id, "acquisition_date": snapshot["manifest"]["input"]["acquisition_date"], "meaning": "Image footprint; NoData is excluded from model scores, not asserted PV absence."}}]}
    datasets["retired_candidates"] = snapshot.get("retired_candidates", {"features": []})["features"]
    datasets["confirmed_rooftop_pv"] = [f for f in snapshot["candidates"]["features"] if f["properties"]["status"] == "confirmed" and f["properties"].get("mounting") == "roof"]
    datasets["candidate_points"] = datasets["candidates"]
    datasets["confirmed_rooftop_points"] = datasets["confirmed_rooftop_pv"]
    if registry_bundle is not None:
        bundle = registry_bundle
        registry = bundle["registry"]
        batch = bundle["batch"]
        if checksum(registry.folder / "manifest.json") != batch["manifest"]["identity"]["registry_manifest_sha256"]:
            raise ValueError("Registry provenance changed since matching.")
        groups = []
        for feature in batch["groups"]["features"]:
            groups.append({**feature, "properties": {**feature["properties"], "stale": feature["id"] in bundle["stale_groups"], "batch_id": batch["manifest"]["batch_id"]}})
        datasets["array_groups"] = groups
        verified = [r for r in bundle["reviews"] if r["state"] == "verified_identity" and r["group_id"] not in bundle["stale_groups"]]
        datasets["verified_mastr_pv"] = [{"type": "Feature", "geometry": batch["by_id"][r["group_id"]]["geometry"], "properties": {**r, "registry_snapshot_id": registry.manifest["snapshot_id"]}} for r in verified]
        datasets["verified_mastr_points"] = datasets["verified_mastr_pv"]
        manifest["matching"] = {"batch_id": batch["manifest"]["batch_id"], "registry_snapshot_id": registry.manifest["snapshot_id"],
                                "registry_snapshot_date": registry.manifest["snapshot_date"], "registry_manifest_sha256": checksum(registry.folder / "manifest.json"),
                                "review_event_revision": max((e["id"] for e in bundle["events"]), default=0),
                                "stale_group_count": len(bundle["stale_groups"]), "verified_identity_count": len(verified),
                                "association_review_count": len(bundle["reviews"]), "array_group_count": len(groups),
                                "automatic_acceptance": False, "battery_allocation": "Registry location relationships only; no exclusive allocation or repeated-capacity totals."}
        manifest["scope"] = "PV image review and versioned MaStR association proposals; only independently evidenced, current human identity decisions enter verified layers."
    for optional in ["buildings", "municipalities", "pixel_coverage"]:
        source = root / "runs" / run_id / (optional + ".geojson")
        if source.name in snapshot["manifest"]["artifacts"]:
            if checksum(source) != snapshot["manifest"]["artifacts"][source.name]:
                raise ValueError("A contextual export artifact changed.")
            datasets[optional] = json.loads(source.read_text())["features"]
    for variant in ["rgbi", "rgb"]:
        source = root / "runs" / run_id / f"{variant}.geojson"
        if checksum(source) != snapshot["manifest"]["artifacts"][source.name]:
            raise ValueError("Observation artifact changed; refusing an inconsistent export.")
        datasets[f"{variant}_observations"] = json.loads(source.read_text())["features"]
    try:
        manifest["geometry_repair_counts"] = {}
        for name, features in datasets.items():
            try:
                frame = layer(features)
            except ValueError as error:
                raise ValueError(f"Layer {name}: {error}") from error
            manifest["geometry_repair_counts"][name] = int(frame["geometry_repaired_for_export"].sum()) if "geometry_repaired_for_export" in frame else 0
            if name.endswith("_points"):
                frame.geometry = frame.geometry.representative_point()
                frame["point_method"] = "interior_representative_point_of_current_candidate_geometry"
            if not frame.empty and not frame.geometry.is_valid.all():
                raise ValueError("Export contains invalid geometry.")
            pyogrio.write_dataframe(frame, temporary, layer=name, driver="GPKG", geometry_type="Point" if name.endswith("_points") else "Unknown")
        with sqlite3.connect(temporary) as db:
            db.execute("CREATE TABLE export_metadata (id INTEGER PRIMARY KEY, metadata_json TEXT NOT NULL)")
            db.execute("INSERT INTO export_metadata(metadata_json) VALUES(?)", (json.dumps(manifest),))
            db.execute("CREATE TABLE inference_provenance (id INTEGER PRIMARY KEY, manifest_json TEXT NOT NULL)")
            db.execute("INSERT INTO inference_provenance(manifest_json) VALUES(?)", (json.dumps(snapshot["manifest"]),))
            db.execute("CREATE TABLE review_events (revision INTEGER PRIMARY KEY, item_id TEXT, kind TEXT, reviewer TEXT, created_at TEXT, payload TEXT)")
            db.executemany("INSERT INTO review_events VALUES(?,?,?,?,?,?)", [(e["revision"], e["item_id"], e["kind"], e["reviewer"], e["created_at"], e["payload"]) for e in snapshot["events"]])
            for table in ["export_metadata", "review_events", "inference_provenance"]:
                db.execute("INSERT INTO gpkg_contents(table_name,data_type,identifier,description,srs_id) VALUES(?,?,?,?,NULL)", (table, "attributes", table, "Version-pinned review metadata"))
            if registry_bundle is not None:
                def attribute_table(name, columns, values):
                    db.execute(f"CREATE TABLE {name} ({columns})")
                    if values:
                        db.executemany(f"INSERT INTO {name} VALUES({','.join('?' for _ in values[0])})", values)
                    db.execute("INSERT INTO gpkg_contents(table_name,data_type,identifier,description,srs_id) VALUES(?,?,?,?,NULL)", (name, "attributes", name, "Frozen public MaStR data or explicitly provisional association evidence"))
                attribute_table("mastr_solar", "solar_id TEXT PRIMARY KEY,ags TEXT,cohort TEXT,power_kw REAL,module_count REAL,commissioned TEXT,postcode TEXT,location_id TEXT,row_json TEXT", [(u["id"], u["ags"], u["cohort"], u["power_kw"], u["module_count"], u["commissioned"], u["postcode"], u["location_id"], json.dumps(u, ensure_ascii=False)) for u in registry.data["solar_units"]])
                for name, key in [("storage_units", "EinheitMastrNummer"), ("storage_systems", "MaStRNummer"), ("locations", "MastrNummer")]:
                    attribute_table("mastr_" + name, "id TEXT PRIMARY KEY,row_json TEXT", [(r[key], json.dumps(r, ensure_ascii=False)) for r in registry.data[name]])
                attribute_table("mastr_storage_relationships", "solar_id TEXT,storage_id TEXT,location_id TEXT,system_id TEXT,relationship_status TEXT,net_power_kw REAL,usable_capacity_kwh REAL,exclusive_allocation INTEGER,row_json TEXT", [(r["solar_id"], r["storage_id"], r["location_id"], r["system_id"], r["relationship_status"], r["net_power_kw"], r["usable_capacity_kwh"], 0, json.dumps(r)) for r in registry.data["storage_relationships"]])
                proposals = {a["proposal_id"]: a for entry in batch["rankings"]["units"].values() for a in entry["alternatives"]}
                proposals.update({a["proposal_id"]: a for items in batch["rankings"]["groups"].values() for a in items})
                attribute_table("mastr_association_proposals", "proposal_id TEXT PRIMARY KEY,solar_id TEXT,group_id TEXT,state TEXT,ranking_score REAL,stale INTEGER,evidence_json TEXT", [(a["proposal_id"], a["solar_id"], a["group_id"], a["state"], a["score"], int(a["group_id"] in bundle["stale_groups"]), json.dumps(a["evidence"])) for a in proposals.values()])
                attribute_table("mastr_unit_outcomes", "solar_id TEXT PRIMARY KEY,state TEXT,details_json TEXT", [(u["solar_id"], u["state"], json.dumps(u)) for u in batch["rankings"]["units"].values()])
                attribute_table("mastr_identity_reviews", "proposal_id TEXT PRIMARY KEY,solar_id TEXT,group_id TEXT,state TEXT,stale INTEGER,details_json TEXT", [(r["proposal_id"], r["solar_id"], r["group_id"], r["state"], int(r["group_id"] in bundle["stale_groups"]), json.dumps(r)) for r in bundle["reviews"]])
                attribute_table("mastr_review_events", "id INTEGER PRIMARY KEY,batch_id TEXT,proposal_id TEXT,created_at TEXT,reviewer TEXT,payload TEXT", [(r["id"], r["batch_id"], r["proposal_id"], r["created_at"], r["reviewer"], r["payload"]) for r in bundle["events"]])
                attribute_table("mastr_provenance", "id INTEGER PRIMARY KEY,registry_manifest_json TEXT,matching_manifest_json TEXT", [(1, json.dumps(registry.manifest), json.dumps(batch["manifest"]))])
        for name in datasets:
            frame = pyogrio.read_dataframe(temporary, layer=name)
            if frame.crs.to_epsg() != 3035 or len(frame) != len(datasets[name]):
                raise ValueError("GeoPackage round-trip validation failed.")
        os.replace(temporary, output)
        manifest["sha256"] = checksum(output)
        report = output.with_suffix(".html")
        make_report(snapshot, manifest, report)
        manifest["report_sha256"] = checksum(report)
        metadata_path = output.with_suffix(".json")
        metadata_path.write_text(json.dumps(manifest, indent=2) + "\n")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return manifest
