"""Build the local result delivery from immutable survey and registry artifacts."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, shape, mapping
from shapely.ops import transform
from shapely.strtree import STRtree
from roof_pv_registry.snapshot import RegistrySnapshot
from roof_pv_matching.engine import load_batch
from building_evidence import (AREA_MODES, BuildingShortlists, assemble_buildings, load_roofs, observation_features,
                               postal_summary, postcode_fabric)
from candidate_allocation import joint_proposals
from candidate_scoring import FALLBACK_COUNT, MINIMUM_CANDIDATE_POINTS, POINT_SCALE
from scoring import LOCATION_DIAMETER_M, LocationConstraints, rank_unit, score_arrays, flag_competition

ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parent
RUN = "municipal-27320a7f3fb1"
SNAPSHOT = "mastr-20261115-99075610"
BATCH = "matching-1077534f5c31f8b6"
VERSION = "building-postcode-lod2-evidence-v10"
RANKING_INDEX = "candidate-rankings/index.json"
RANKING_SHARD_SIZE = 40
WEB_DATA = ("records.json", "summary.json", "buildings.geojson", "municipalities.geojson",
            "observed-pv.geojson", "roof-faces.geojson", "postcodes.geojson", "postcode-totals.json", RANKING_INDEX)
GIS_DATA = ("roof-pv-results.gpkg", "results.geojson")
REPORT_INPUTS = ("summary.json", "context.json")
VERIFIED_BUILD_FILES = (*WEB_DATA, *GIS_DATA, "context.json", "index.html",
                        "static/index.html", "static/app.js", "static/style.css")
TECHNICAL_FIELDS = {
    "EinheitMastrNummer", "DatumLetzteAktualisierung", "LokationMaStRNummer", "Gemeinde", "Gemeindeschluessel",
    "Postleitzahl", "Registrierungsdatum", "Inbetriebnahmedatum", "EinheitSystemstatus", "EinheitBetriebsstatus",
    "Energietraeger", "Bruttoleistung", "Nettonennleistung", "ZugeordneteWirkleistungWechselrichter", "AnzahlModule",
    "ArtDerSolaranlage", "Leistungsbegrenzung", "EinheitlicheAusrichtungUndNeigungswinkel", "Hauptausrichtung",
    "HauptausrichtungNeigungswinkel", "Nebenausrichtung", "NebenausrichtungNeigungswinkel", "Nutzungsbereich",
    "EegMaStRNummer", "SpeicherAmGleichenOrt", "SolaranlagenKategorie", "SpeMastrNummer", "MaStRNummer",
    "NutzbareSpeicherkapazitaet", "Batterietechnologie", "AcDcKoppelung", "Technologie", "MastrNummer",
    "Spannungsebene", "VerknuepfteEinheitenMaStRNummern"}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ranking_files(directory):
    names = read(directory / RANKING_INDEX)["shards"]
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[0-9]{4}\.json", name) for name in names):
        raise ValueError("Invalid candidate ranking shard names.")
    return tuple(f"candidate-rankings/{name}" for name in names)


def write_rankings(output, rankings, shortlists, snapshot):
    folder = output / "candidate-rankings"
    folder.mkdir(exist_ok=True)
    shards = []
    for offset in range(0, len(rankings), RANKING_SHARD_SIZE):
        name = f"{offset // RANKING_SHARD_SIZE:04d}.json"
        rows = rankings[offset:offset + RANKING_SHARD_SIZE]
        write(folder / name, {"snapshot": snapshot, "records": {row["solar_id"]: scores for row, scores in rows}})
        for row, _ in rows:
            row["ranking_shard"] = name
        shards.append(name)
    write(output / RANKING_INDEX, {"snapshot": snapshot, "building_ids": shortlists.building_ids, "score_scale": POINT_SCALE,
                                  "threshold": MINIMUM_CANDIDATE_POINTS, "fallback_count": FALLBACK_COUNT,
                                  "area_modes": AREA_MODES,
                                  "array_options": [[{"groups": option.groups, "projected_m2": round(option.projected, 2),
                                                       "parallel_m2": round(option.parallel, 2) if option.parallel is not None else None}]
                                                     for option in shortlists.measurements],
                                  "shards": shards})


def clean(row):
    return {k: v for k, v in row.items() if k in TECHNICAL_FIELDS}


def public(row):
    return {k: v for k, v in row.items() if not k.startswith("_")}


def feature(geometry, properties):
    return {"type": "Feature", "geometry": mapping(geometry) if geometry is not None else None, "properties": properties}


def collection(features):
    return {"type": "FeatureCollection", "features": features}


def export_gpkg(output, records, context):
    """Export a convenient flat feature layer plus normalized technical tables."""
    output = Path(output)
    temporary = output.with_name("." + output.stem + ".gpkg")
    temporary.unlink(missing_ok=True)
    point_rows, image_rows, fallback_rows = [], [], []
    for row in records:
        properties = {k: json.dumps(v, ensure_ascii=True) if isinstance(v, (dict, list)) else v
                      for k, v in row.items() if k not in {"point", "geometry"}}
        properties.update(row["mastr_attributes"])
        if row.get("point"):
            point_rows.append(feature(Point(row["point"]), properties))
        if row.get("geometry"):
            target = fallback_rows if row["association_status"] == "statistical_building_allocation" else image_rows
            target.append({"type": "Feature", "geometry": row["geometry"], "properties": properties})
    datasets = {"associated_pv_points": point_rows, "inferred_pv_arrays": image_rows,
                "statistical_building_allocations": fallback_rows, "scored_array_groups": context["groups"],
                "unselected_image_groups": [g for g in context["groups"] if g["properties"]["selected_by_count"] == 0],
                "municipalities": context["municipalities"]}
    for name in ("observed_pv_buildings", "observed_pv_roof_faces", "registry_by_postcode"):
        if name in context:
            datasets[name] = context[name]
    for name, features in datasets.items():
        if features:
            frame = gpd.GeoDataFrame.from_features(features, crs=4326)
            for column in frame.columns:
                if column != "geometry" and any(isinstance(v, (dict, list)) for v in frame[column]):
                    frame[column] = frame[column].map(lambda v: json.dumps(v) if isinstance(v, (dict, list)) else v)
            frame = frame.to_crs(3035)
        else:
            frame = gpd.GeoDataFrame({"solar_id": [], "geometry": []}, geometry="geometry", crs=3035)
        if not frame.geometry.is_valid.all():
            raise ValueError(f"Invalid geometry in {name}.")
        frame.to_file(temporary, layer=name, driver="GPKG", engine="pyogrio")
    with sqlite3.connect(temporary) as db:
        def table(name, columns, rows):
            db.execute(f'CREATE TABLE "{name}" ({columns})')
            if rows:
                db.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" for _ in rows[0])})', rows)
            db.execute("INSERT INTO gpkg_contents(table_name,data_type,identifier,description) VALUES(?,?,?,?)",
                       (name, "attributes", name, "Technical MaStR data and explicitly inferred associations"))
        table("registry_outcomes", "solar_id TEXT PRIMARY KEY,association_status TEXT,confidence TEXT,reason TEXT,details_json TEXT",
              [(r["solar_id"], r["association_status"], r["confidence"], r["reason"], json.dumps({k:v for k,v in r.items() if k not in {"point","geometry"}})) for r in records])
        table("mastr_solar_attributes", "solar_id TEXT PRIMARY KEY,attributes_json TEXT", [(r["solar_id"], json.dumps(r["mastr_attributes"])) for r in records])
        table("storage_relationships", "solar_id TEXT,storage_id TEXT,system_id TEXT,location_id TEXT,net_power_kw REAL,usable_capacity_kwh REAL,relationship_status TEXT,exclusive_allocation INTEGER",
              [(r["solar_id"],r["storage_id"],r["system_id"],r["location_id"],r["net_power_kw"],r["usable_capacity_kwh"],r["relationship_status"],0) for r in context["storage_relationships"]])
        table("storage_units", "storage_id TEXT PRIMARY KEY,attributes_json TEXT", [(r["EinheitMastrNummer"],json.dumps(clean(r))) for r in context["storage_units"]])
        table("storage_systems", "system_id TEXT PRIMARY KEY,attributes_json TEXT", [(r["MaStRNummer"],json.dumps(clean(r))) for r in context["storage_systems"]])
        if "registry_postcode_totals" in context:
            table("registry_postcode_totals", "municipality TEXT,postcode TEXT,has_geometry INTEGER,registry_count INTEGER,registry_power_kw REAL,target_count INTEGER,target_power_kw REAL,post_capture_count INTEGER,PRIMARY KEY(municipality,postcode)",
                  [tuple(r[k] for k in ("municipality", "postcode", "has_geometry", "registry_count", "registry_power_kw",
                                        "target_count", "target_power_kw", "post_capture_count")) for r in context["registry_postcode_totals"]])
            table("registry_building_candidates", "solar_id TEXT,rank INTEGER,building_id TEXT,score REAL,projected_score REAL,area_method TEXT,orientation_compared INTEGER,PRIMARY KEY(solar_id,rank)",
                  [(r["solar_id"], rank, a["building_id"], a["score"], a["projected_score"], a["area_method"], a["orientation_compared"])
                   for r in records if r["association_status"] == "building_candidates"
                   for rank, a in enumerate(r["alternatives"], 1)])
            table("registry_joint_proposals", "solar_id TEXT PRIMARY KEY,building_id TEXT,candidate_rank INTEGER,score REAL,group_ids_json TEXT",
                  [(r["solar_id"], proposal["building_id"], proposal["rank"], proposal["score"], json.dumps(proposal["group_ids"]))
                   for r in records if (proposal := r.get("joint_proposal")) is not None])
        table("provenance", "id INTEGER PRIMARY KEY,metadata_json TEXT", [(1,json.dumps(context["metadata"]))])
    with sqlite3.connect(temporary) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("GeoPackage integrity check failed.")
        if any(r[0] != 3035 for r in db.execute("SELECT srs_id FROM gpkg_geometry_columns")):
            raise ValueError("Unexpected spatial CRS.")
    temporary.replace(output)


def build(home, output, evidence_dir=APP / "evidence"):
    output.mkdir(parents=True, exist_ok=True)
    registry = RegistrySnapshot(home / "registry" / SNAPSHOT)
    batch_folder = home / "matching" / RUN / BATCH
    batch = load_batch(batch_folder)
    run_folder = home / "runs" / RUN
    manifest = read(run_folder / "manifest.json")
    if digest(registry.folder / "manifest.json") != batch["manifest"]["identity"]["registry_manifest_sha256"]:
        raise ValueError("Registry snapshot differs from matching input.")
    if digest(run_folder / "manifest.json") != batch["manifest"]["identity"]["run_manifest_sha256"]:
        raise ValueError("Run manifest differs from matching input.")
    for name in ["buildings.geojson", "municipalities.geojson", "candidates.geojson"]:
        if digest(run_folder / name) != manifest["artifacts"][name]:
            raise ValueError(f"Source checksum mismatch: {name}")
    project = Transformer.from_crs(4326, 25833, always_xy=True).transform
    restore = Transformer.from_crs(25833, 4326, always_xy=True).transform
    municipalities = read(run_folder / "municipalities.geojson")["features"]
    boundaries = {m["properties"]["gem_name"]: transform(project,shape(m["geometry"])) for m in municipalities}
    candidates = {r["id"]: r for r in read(run_folder / "candidates.geojson")["features"]}
    with sqlite3.connect((home / "state" / "review.sqlite3").resolve().as_uri()+"?mode=ro", uri=True) as db:
        revision = db.execute("SELECT revision FROM runs WHERE id=?",(RUN,)).fetchone()[0]
        statuses = {r[0]: r[1:] for r in db.execute("SELECT id,status,mounting,version FROM candidates WHERE run_id=? AND active=1",(RUN,))}
    if revision != batch["manifest"]["identity"]["review_revision"]:
        raise ValueError("Review changed: rebuild the source grouping batch before delivery.")
    groups = []
    for f in batch["groups"]["features"]:
        p = f["properties"]
        members = [candidates[i]["properties"] for i in p["member_ids"]]
        if any(i not in statuses or statuses[i][0] == "rejected" or statuses[i][1] in ["ground","other"] or statuses[i][2] != p["member_versions"][i] for i in p["member_ids"]):
            continue
        overlap = float(np.mean([min(1,m.get("building_overlap_ratio",0) or 0) for m in members]))
        both = sum(m["category"] == "both" for m in members)/len(members)
        quality = .55*overlap + .45*both
        groups.append({**p, "image_quality": round(quality,4), "building_overlap": round(overlap,4),
                       "roof_supported": bool(p["building_parts"]) and overlap >= .5,
                       "_geometry": transform(project,shape(f["geometry"])), "_geojson": f["geometry"]})
    by_group = {g["id"]: g for g in groups}
    group_tree = STRtree([g["_geometry"] for g in groups])
    buildings = []
    cadastral = {}
    for f in read(run_folder / "buildings.geojson")["features"]:
        geometry = transform(project,shape(f["geometry"]))
        bid = str(f["properties"].get("building_id",f.get("id")))
        cadastral[bid] = geometry
        point = geometry.representative_point()
        names = [name for name,boundary in boundaries.items() if boundary.covers(point)]
        if not names:
            continue
        buildings.append({"id": bid,"_geometry":geometry})
    def location_point(item):
        point = shape(item["_geojson"]).representative_point()
        return project(point.x, point.y)
    for item in groups:
        item["_location_point"] = location_point(item)
    postcode_path = evidence_dir / "lgb-postcodes.geojson"
    lod2_path = evidence_dir / "lod2-roof-faces.json.gz"
    postcodes, postcode_provenance = postcode_fabric(postcode_path, project)
    postal_scopes = {(municipality, code) for municipality, boundary in boundaries.items()
                     for code, polygon in postcodes.items() if boundary.intersection(polygon).area > 0}
    lod2 = load_roofs(lod2_path, manifest["artifacts"]["buildings.geojson"], digest(batch_folder / "groups.geojson"))
    observed = assemble_buildings(groups, cadastral, postcodes, lod2["buildings"])
    inventory, roof_faces = observation_features(observed, restore)
    shortlists = BuildingShortlists(observed)
    storage_by_solar = defaultdict(list)
    for link in registry.data["storage_relationships"]:
        if link["relationship_status"] == "supported":
            storage_by_solar[link["solar_id"]].append(link)
    records, rankings = [], []
    latest_capture = max(t["acquisition_date"] for t in manifest["source_tiles"])
    reservations = {}
    locations = LocationConstraints()
    raw_group_best = {}
    ordered_units = sorted(registry.data["solar_units"], key=lambda u: (u["longitude"] is None, -(u["power_kw"] or 0), u["id"]))
    for unit in ordered_units:
        links = storage_by_solar[unit["id"]]
        storage = {r["storage_id"]:r for r in links}
        systems = {r["system_id"]:r for r in links if r["system_id"]}
        row = {"solar_id":unit["id"],"municipality":unit["municipality"],"ags":unit["ags"],"postcode":unit["postcode"],
               "location_id":unit["location_id"],"power_kw":unit["power_kw"],"module_count":unit["module_count"],
               "commissioned":unit["commissioned"],"cohort":unit["cohort"],"mastr_attributes":clean(unit["raw"]),
               "main_direction":unit["decoded"].get("Hauptausrichtung"),"secondary_direction":unit["decoded"].get("Nebenausrichtung"),
               "main_tilt":unit["decoded"].get("HauptausrichtungNeigungswinkel"),"registry_flags":unit["flags"],
               "storage_unit_ids":sorted(storage),"storage_system_ids":sorted(systems),
               "storage_power_kw":sum(r["net_power_kw"] or 0 for r in storage.values()) if storage else None,
               "storage_capacity_kwh":sum(r["usable_capacity_kwh"] or 0 for r in systems.values()) if systems else None,
               "storage_shared":any(sum(x["storage_id"]==r["storage_id"] for x in registry.data["storage_relationships"])>1 for r in storage.values()),
               "identity_verified":False,"confidence":"unlocated","association_status":"context_unlocated",
               "reason":"Outside active building-PV matching scope; retained in registry outcomes.",
               "point":None,"geometry":None,"alternatives":[],"score":None,"competing_registrations":0,"capacity_conflict":False}
        records.append(row)
        if unit["raw"].get("EinheitBetriebsstatus") != "35" or unit["raw"].get("ArtDerSolaranlage") != "853":
            continue
        if unit["longitude"] is not None:
            point = transform(project,Point(unit["longitude"],unit["latitude"]))
            if not boundaries.get(unit["municipality"]).covers(point):
                row.update(association_status="source_conflict",reason="Public coordinate lies outside the declared municipality.")
                continue
            unit = {**unit,"_point":point}
            pool = [groups[int(i)] for i in group_tree.query(point.buffer(100),predicate="intersects") if groups[int(i)]["roof_supported"] and unit["municipality"] in groups[int(i)]["municipalities"]]
        else:
            if unit.get("commissioned") and unit["commissioned"] > latest_capture:
                row.update(association_status="post_capture_unlocated", post_capture=True, candidate_status="post_capture",
                           reason="Commissioned after the survey. Included in its registry postcode totals.")
            elif unit["cohort"] == "active_building_under30":
                postcode = unit["postcode"]
                if postcode and postcode not in postcodes:
                    row.update(shortlists.unlocated("outside_lgb_extract", "The registered postcode is outside the LGB extract."))
                elif postcode and (unit["municipality"], postcode) not in postal_scopes:
                    row.update(shortlists.unlocated("municipality_postcode_conflict", "The LGB postcode has no area in the registry municipality."))
                else:
                    evidence = shortlists.rank(unit, full=True)
                    ranking = evidence.pop("_ranking", None)
                    row.update(evidence)
                    if ranking is not None:
                        rankings.append((row, ranking))
            continue
        original_ranked, original_alternatives = rank_unit(unit,pool)
        if pool:
            scores, _, _, _, _ = score_arrays(unit,pool)
            for group, score in zip(pool,scores):
                previous = raw_group_best.get(group["id"],(0,None))
                if float(score)*100 > previous[0]:
                    raw_group_best[group["id"]] = (round(float(score)*100,4),unit["id"])
        location_key = unit["location_id"] or unit["id"]
        location_pool = [g for g in pool if locations.allows(location_key, g["_location_point"])]
        eligible = [g for g in location_pool if (g["id"] not in reservations or reservations[g["id"]]["location"] == location_key)
                    and (reservations.get(g["id"],{}).get("power",0) + (unit["power_kw"] or 0)) <= g["area_m2"]*.5]
        ranked, alternatives = rank_unit(unit,eligible)
        if ranked:
            row.update(ranked,alternatives=alternatives)
            if row["score"] < 30:
                row.update(association_status="unmatched_registry",confidence="unlocated",score=None,
                           group_id=None,reason="No remaining image group reaches the minimum 30/100 technical compatibility screen.",
                           alternatives=original_alternatives)
                if locations.points.get(location_key):
                    row["registry_flags"] = [*row["registry_flags"], "location_constraint_unresolved"]
                    row["reason"] += " The shared registry-location cluster is retained; no compatible local array is available and no distant substitution is allowed."
                continue
            reservation = reservations.setdefault(row["group_id"],{"location":location_key,"power":0})
            reservation["power"] += unit["power_kw"] or 0
            locations.reserve(location_key, by_group[row["group_id"]]["_location_point"])
            row.update(unconstrained_top_group_id=original_ranked["group_id"],unconstrained_top_score=original_ranked["score"],
                       original_pool_size=len(pool),allocation_order="Public coordinates first, descending power, then solar ID")
            row["reason"] += (" Selected the highest-scoring available group after a conservative allocation policy: one registry location per group, "
                              "shared-location units allowed, total power at most 0.5 kW/m2. Allocation order prioritizes public coordinates, "
                              "then descending power and solar ID. Every selected point at a registry location must remain within "
                              f"{LOCATION_DIAMETER_M:.0f} m of every other selected point at that location, including statistical allocations. "
                              "The first inferred position anchors this bounded roof cluster; it does not verify identity.")
            if row["group_id"] != original_ranked["group_id"]:
                row["reason"] += f" Unconstrained best score {original_ranked['score']:.2f} was unavailable under the allocation policy."
                row["confidence"] = "low"
            g=by_group[row["group_id"]]
            row.update(point=list(shape(g["_geojson"]).representative_point().coords)[0],
                       geometry=g["_geojson"],observed_area_m2=g["area_m2"],capture_dates=g["capture_dates"],
                       building_parts=g["building_parts"])
        else:
            row.update(association_status="unmatched_registry",reason="No building-supported image group remains under the location/capacity allocation policy.",alternatives=original_alternatives)
            if locations.points.get(location_key):
                row["reason"] += " The shared registry-location cluster is retained; no local candidate satisfies the remaining constraints."
                row["registry_flags"] = [*row["registry_flags"], "location_constraint_unresolved"]
    allocation = joint_proposals(rankings, {u["id"]: u for u in ordered_units}, shortlists, observed, by_group, records)
    shared_location_diameters = {}
    for location_key, points in locations.points.items():
        if len(points) > 1:
            diameter = max(math.dist(a, b) for a in points for b in points)
            if diameter > LOCATION_DIAMETER_M:
                raise ValueError("A shared registry location exceeds its permitted diameter.")
            shared_location_diameters[location_key] = diameter
    for row in records:
        key = row["location_id"] or row["solar_id"]
        row["location_cluster_diameter_m"] = round(shared_location_diameters.get(key, 0), 4) if row["point"] else None
        row["location_cluster_limit_m"] = LOCATION_DIAMETER_M
    confidence=flag_competition(records,by_group)
    selected=Counter(r.get("group_id") for r in records if r.get("group_id"))
    postal_features, postal_totals = postal_summary(records, postcodes, boundaries, restore)
    group_features=[{"type":"Feature","geometry":g["_geojson"],"properties":{
        **public(g),"selected_by_count":selected[g["id"]],"best_registry_score":raw_group_best.get(g["id"],(None,None))[0],
        "best_registry_id":raw_group_best.get(g["id"],(None,None))[1]}} for g in groups]
    unique_storage={r["storage_id"]:r for r in registry.data["storage_relationships"] if r["relationship_status"]=="supported"}
    unique_systems={r["system_id"]:r for r in unique_storage.values() if r["system_id"]}
    summary={"registry_records":len(records),"image_candidates":len(candidates),"array_groups":len(groups),
             "roof_supported_groups":sum(g["roof_supported"] for g in groups),"strict_under30_targets":sum(r["cohort"]=="active_building_under30" for r in records),
             "states":dict(Counter(r["association_status"] for r in records)),"confidence":confidence,
             "located_records":sum(r["point"] is not None for r in records),"verified_identities":0,
             "competing_groups":sum(v>1 for v in selected.values()),"capacity_conflict_records":sum(r["capacity_conflict"] for r in records),
             "unique_supported_storage_units":len(unique_storage),"unique_supported_storage_systems":len(unique_systems),
             "unique_storage_power_kw":round(sum(r["net_power_kw"] or 0 for r in unique_storage.values()),4),
             "unique_storage_capacity_kwh":round(sum(r["usable_capacity_kwh"] or 0 for r in unique_systems.values()),4),
             "unselected_image_groups":sum(selected[g["id"]]==0 for g in groups)}
    summary.update(shared_located_locations=len(shared_location_diameters),
                   maximum_shared_location_diameter_m=round(max(shared_location_diameters.values(), default=0), 4),
                   shared_locations_over_limit=0,
                   location_constraint_unresolved=sum("location_constraint_unresolved" in r["registry_flags"] for r in records))
    summary.update(observed_buildings=len(inventory), observed_roof_faces=len(roof_faces),
                   roof_area_estimates=sum(b["roof_parallel_area_m2"] is not None for b in observed),
                   projected_pv_area_m2=round(sum(b["projected_area_m2"] for b in observed), 2),
                   postcode_rows=len(postal_totals), mapped_postcode_rows=len(postal_features),
                   candidate_statuses=dict(Counter(r["candidate_status"] for r in records if "candidate_status" in r)),
                   candidate_threshold=MINIMUM_CANDIDATE_POINTS, candidate_fallback_count=FALLBACK_COUNT,
                   joint_proposal_count=allocation["proposals"])
    metadata={"algorithm":VERSION,"created_at":datetime.now(timezone.utc).isoformat(),"run_id":RUN,"registry_snapshot":SNAPSHOT,
              "registry_snapshot_date":registry.manifest["snapshot_date"],"source_batch":BATCH,"review_revision":revision,
              "capture_dates":sorted({t["acquisition_date"] for t in manifest["source_tiles"]}),"summary":summary,
              "source_hashes":{"run_manifest":digest(run_folder/"manifest.json"),"registry_manifest":digest(registry.folder/"manifest.json"),
                               "source_batch_manifest":digest(batch_folder/"manifest.json"),"scoring_code":digest(APP/"scoring.py"),"build_code":digest(Path(__file__)),
                               "postcode_subset":digest(postcode_path),"lod2_roofs":digest(lod2_path),"building_evidence_code":digest(APP / "building_evidence.py"),
                               "candidate_scoring_code":digest(APP / "candidate_scoring.py"),
                               "candidate_allocation_code":digest(APP / "candidate_allocation.py")},
              "geometry_sources":{"postcode":postcode_provenance,"lod2_attribution":lod2["attribution"],"lod2_tiles":lod2["sources"]},
              "score_meaning":"Anonymous candidates use signed evidence points. Public-coordinate proposals use 0–100 technical compatibility. Neither is an identity probability.",
              "identity_policy":"Anonymous candidates and joint proposals are unverified. Post-capture units are included in postcode statistics. Public-coordinate associations remain provisional.",
              "allocation_policy":"Anonymous candidates share their municipality and LGB postcode. Each building's full PV union is scored once per record and can be assigned to one anonymous record. Joint proposals observe the score threshold, roof capacity, existing reservations and a shared-location distance limit. Public-coordinate proposals use their original workflow.",
              "joint_allocation":allocation,
              "storage_policy":"Per-row totals deduplicate IDs within the row; cross-row totals must deduplicate storage/system IDs.",
              "personal_data_policy":"Technical allowlist only; no operator IDs, names, addresses, original XML or reviewer personal details."}
    context={"groups":group_features,"municipalities":municipalities,"storage_relationships":registry.data["storage_relationships"],
             "storage_units":[clean(r) for r in registry.data["storage_units"]],"storage_systems":[clean(r) for r in registry.data["storage_systems"]],"metadata":metadata,
             "observed_pv_buildings":inventory,"observed_pv_roof_faces":roof_faces,"registry_by_postcode":postal_features,
             "registry_postcode_totals":postal_totals}
    write_rankings(output, rankings, shortlists, metadata["created_at"])
    write(output/"records.json",records)
    write(output/"context.json",context)
    write(output/"summary.json",metadata)
    export_gpkg(output/"roof-pv-results.gpkg",records,context)
    write(output/"results.geojson",collection([feature(Point(r["point"]),{k:v for k,v in r.items() if k not in {"geometry","point"}}) for r in records if r["point"]]))
    write(output/"arrays.geojson",collection([feature(transform(restore,g["_geometry"].simplify(.25,preserve_topology=True)),
          {"id":g["id"],"selected_by_count":selected[g["id"]],"roof_supported":g["roof_supported"]}) for g in groups]))
    write(output/"buildings.geojson",collection([feature(transform(restore,b["_geometry"].simplify(.5,preserve_topology=True)),{"id":b["id"]}) for b in buildings]))
    write(output/"municipalities.geojson",collection(municipalities))
    write(output/"observed-pv.geojson",collection(inventory))
    write(output/"roof-faces.geojson",collection(roof_faces))
    write(output/"postcodes.geojson",collection(postal_features))
    write(output/"postcode-totals.json",postal_totals)
    for source in [APP/"static",APP/"vendor"]:
        if source.exists():
            shutil.copytree(source,output/source.name,dirs_exist_ok=True)
    if (APP/"static"/"index.html").exists():
        shutil.copy2(APP/"static"/"index.html",output/"index.html")
    comparison=read(ROOT/"packages"/"model-diff"/"results"/"evaluation.json")
    pilot_path=ROOT.parent/"lab"/"review-app"/"dist"/"data"/"project.json"
    pilot=read(pilot_path) if pilot_path.exists() else None
    retained_model_summary = read(output/"model-comparison.json") if (output/"model-comparison.json").exists() else {}
    model_summary={"public": [{"id":e["entrant_id"],"model":e.get("model"),"eligible":e.get("rank_eligible"),
                    "track":e.get("track"),"objects_50":e.get("objects_50"),"pixels":e.get("pixels"),"mask_metrics":e.get("mask_metrics")} for e in comparison["entrants"]],
                   "interpretation":comparison["interpretation"],"pilot":pilot["metrics"] if pilot else retained_model_summary.get("pilot"),
                   "source_hashes":{"public_evaluation":digest(ROOT/"packages/model-diff/results/evaluation.json"),
                                    "local_pilot":digest(pilot_path) if pilot else retained_model_summary.get("source_hashes",{}).get("local_pilot")}}
    write(output/"model-comparison.json",model_summary)
    print(json.dumps(summary,indent=2))
    return metadata


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home",type=Path,default=ROOT/"var")
    parser.add_argument("--output",type=Path,default=APP/"dist")
    parser.add_argument("--evidence",type=Path,default=APP/"evidence")
    args=parser.parse_args()
    build(args.home.resolve(),args.output.resolve(),args.evidence.resolve())
