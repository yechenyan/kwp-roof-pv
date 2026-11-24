"""Verify real delivery invariants, technical-field privacy and live WMS responses."""
import argparse
from collections import Counter
import io
import json
from pathlib import Path
import sqlite3
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

import geopandas as gpd
from PIL import Image, ImageStat
from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union
from pyproj import Transformer
import math
from collections import defaultdict
from fastapi.testclient import TestClient
import tempfile

from build import APP, RANKING_INDEX, TECHNICAL_FIELDS, VERIFIED_BUILD_FILES, digest, ranking_files, read, write
from building_evidence import AREA_MODES
from candidate_allocation import overlapping_groups, projected_demand, roof_budget
from candidate_scoring import FALLBACK_COUNT, MINIMUM_CANDIDATE_POINTS, POINT_SCALE
from serve import create_app
from scoring import LOCATION_DIAMETER_M


def verify(directory, check_wms=True):
    shards = ranking_files(directory)
    build_hashes = {name: digest(directory / name) for name in (*VERIFIED_BUILD_FILES, *shards)}
    records=read(directory/"records.json")
    context=read(directory/"context.json")
    metadata=read(directory/"summary.json")
    assert context["metadata"] == metadata
    summary=metadata["summary"]
    assert len(records)==summary["registry_records"]==3476
    assert len({r["solar_id"] for r in records})==len(records)
    assert sum(r["cohort"]=="active_building_under30" for r in records)==2318
    assert not any(r["identity_verified"] for r in records)
    assert not any(r["capacity_conflict"] for r in records)
    assert all(set(r["mastr_attributes"]) <= TECHNICAL_FIELDS for r in records)
    verify_building_evidence(records, context, summary)
    verify_rankings(directory, records, context, shards)
    verify_joint_proposals(records, context)
    assert read(directory / "postcode-totals.json") == context["registry_postcode_totals"]
    for name, key in (("observed-pv.geojson", "observed_pv_buildings"),
                      ("roof-faces.geojson", "observed_pv_roof_faces"), ("postcodes.geojson", "registry_by_postcode")):
        assert read(directory / name)["features"] == context[key]
    assert all((r["point"] is not None)==(r["association_status"] in {"inferred_image_association","statistical_building_allocation"}) for r in records)
    assert all(shape(r["geometry"]).covers(Point(r["point"])) for r in records if r["point"])
    for r in records:
        if r["point"]:
            assert r["alternatives"][0].get("group_id",r["alternatives"][0].get("building_id"))==r.get("group_id",r.get("building_id"))
            assert abs(r["score"]-r["alternatives"][0]["score"]) < 1e-6
        if r["association_status"]=="statistical_building_allocation":
            assert r["confidence"]=="very_low" and not r.get("group_id")
    inferred=[r for r in records if r["association_status"]=="inferred_image_association"]
    by_location = defaultdict(list)
    project = Transformer.from_crs(4326, 25833, always_xy=True).transform
    for r in records:
        if r["point"]:
            by_location[r["location_id"] or r["solar_id"]].append(project(*r["point"]))
    diameters = [max(math.dist(a, b) for a in points for b in points)
                 for points in by_location.values() if len(points) > 1]
    assert all(d <= LOCATION_DIAMETER_M + 1e-6 for d in diameters)
    assert round(max(diameters, default=0), 4) == summary["maximum_shared_location_diameter_m"]
    for gid in {r["group_id"] for r in inferred}:
        rows=[r for r in inferred if r["group_id"]==gid]
        assert len({r["location_id"] or r["solar_id"] for r in rows})==1
    with sqlite3.connect(directory/"roof-pv-results.gpkg") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        layers=[r[0] for r in db.execute("SELECT table_name FROM gpkg_geometry_columns")]
        assert db.execute("SELECT count(*) FROM registry_outcomes").fetchone()[0]==len(records)
        exported = {key: (building, rank, score, read_groups)
                    for key, building, rank, score, read_groups in db.execute(
                        "SELECT solar_id,building_id,candidate_rank,score,group_ids_json FROM registry_joint_proposals")}
        assert exported == {r["solar_id"]: (p["building_id"], p["rank"], p["score"], json.dumps(p["group_ids"]))
                            for r in records if (p := r.get("joint_proposal")) is not None}
        assert db.execute("SELECT count(*) FROM storage_relationships").fetchone()[0]==len(context["storage_relationships"])
        assert all(r[0]==3035 for r in db.execute("SELECT srs_id FROM gpkg_geometry_columns"))
    for name in layers:
        frame=gpd.read_file(directory/"roof-pv-results.gpkg",layer=name)
        assert frame.crs.to_epsg()==3035 and frame.geometry.is_valid.all()
        if name == "associated_pv_points":
            assert {"Bruttoleistung","Inbetriebnahmedatum","storage_power_kw","storage_capacity_kwh","confidence","reason"} <= set(frame.columns)
    wms=[]
    for code,layer in ([("dop20c","bebb_dop20c"),("dop20cir","bb_dop20cir")] if check_wms else []):
        base=f"https://isk.geobasis-bb.de/mapproxy/{code}/service/wms"
        with urllib.request.urlopen(base+"?SERVICE=WMS&REQUEST=GetCapabilities",timeout=45) as response:
            capabilities=response.read()
        names=[e.text for e in ET.fromstring(capabilities).iter() if e.tag.split("}")[-1]=="Name"]
        assert layer in names
        params={"SERVICE":"WMS","REQUEST":"GetMap","VERSION":"1.1.1","LAYERS":layer,"STYLES":"","FORMAT":"image/png",
                "SRS":"EPSG:4326","BBOX":"13.27679,52.67126,13.27922,52.67274","WIDTH":256,"HEIGHT":256}
        with urllib.request.urlopen(base+"?"+urllib.parse.urlencode(params),timeout=45) as response:
            image=Image.open(io.BytesIO(response.read())).convert("RGB")
        assert image.size==(256,256) and max(ImageStat.Stat(image).stddev)>1
        wms.append({"endpoint":base,"layer":layer,"capabilities_verified":True,"nonblank_getmap":True})
    with tempfile.TemporaryDirectory() as folder:
        client=TestClient(create_app(directory,Path(folder)/"annotations.sqlite3"))
        assert client.get("/").status_code==200
        data=client.get("/api/results").json()
        solar_id=next(r["solar_id"] for r in data if r["point"])
        annotation={"version":0,"confidence":"low","reason":"Isolated verification fixture; no production annotation."}
        assert client.put(f"/api/annotations/{solar_id}",json=annotation).status_code==200
        assert client.put(f"/api/annotations/{solar_id}",json=annotation).status_code==409
        assert client.put(f"/api/annotations/{solar_id}",json={**annotation,"version":1,"confidence":"verified"}).status_code==422
        assert client.put("/api/annotations/unknown",json=annotation).status_code==404
        response=client.get("/api/export")
        assert response.status_code==200
        path=Path(folder)/"roof-pv-results-edited.gpkg"
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT reason FROM registry_outcomes WHERE solar_id=?",(solar_id,)).fetchone()[0]==annotation["reason"]
            metadata=json.loads(db.execute("SELECT metadata_json FROM provenance").fetchone()[0])
            assert len(metadata["annotation_events"])==1
        changed=[dict(r) for r in data]
        changed_row=next(r for r in changed if r["solar_id"]==solar_id)
        changed_row["reason"]="Changed generated snapshot; previous annotations must not migrate."
        alternate=Path(folder)/"changed-delivery"
        alternate.mkdir()
        write(alternate/"records.json",changed)
        changed_client=TestClient(create_app(alternate,Path(folder)/"annotations.sqlite3"))
        current=next(r for r in changed_client.get("/api/results").json() if r["solar_id"]==solar_id)
        assert current["annotation_version"]==0 and current["reason"]==changed_row["reason"]
        changed_row["reason"]+=" Another rebuild."
        write(alternate/"records.json",changed)
        assert changed_client.get("/api/results").status_code==409
    if any(digest(directory / name) != checksum for name, checksum in build_hashes.items()):
        raise ValueError("Build files changed during verification.")
    result={"status":"passed","registry_completeness":True,"technical_allowlist":True,"point_inside_geometry":True,
            "building_evidence_and_postcode_reconciliation":True,
            "allocation_capacity_and_location_constraints":True,"joint_proposal_constraints":True,
            "geopackage_roundtrip":True,"spatial_layers":layers,
            "wms":wms,"annotation_persistence_and_stale_write_rejection":True,"edited_export":True,
            "annotation_snapshot_binding":True,
            "shared_location_consistency":True,
            "maximum_shared_location_diameter_m":round(max(diameters, default=0), 4),
            "test_database":"Isolated temporary database; production annotations unchanged.",
            "gpkg_sha256":build_hashes["roof-pv-results.gpkg"], "build_files_sha256":build_hashes}
    write(directory/"verification.json",result)
    print(json.dumps(result,indent=2))


def verify_rankings(directory, records, context, shards):
    index = read(directory / RANKING_INDEX)
    assert index["snapshot"] == context["metadata"]["created_at"]
    assert index["score_scale"] == POINT_SCALE
    assert index["threshold"] == MINIMUM_CANDIDATE_POINTS
    assert index["fallback_count"] == FALLBACK_COUNT
    assert tuple(index["area_modes"]) == AREA_MODES
    assert index["building_ids"] == [f["properties"]["building_id"] for f in context["observed_pv_buildings"]]
    pools = defaultdict(set)
    for i, feature in enumerate(context["observed_pv_buildings"]):
        row = feature["properties"]
        option, = index["array_options"][i]
        assert option["groups"] == row["group_ids"]
        assert option["projected_m2"] == round(row["projected_area_m2"], 2)
        parallel = row["roof_parallel_area_m2"]
        assert option["parallel_m2"] == (round(parallel, 2) if parallel is not None else None)
        for municipality in row["municipalities"]:
            pools[municipality, row["postcode"]].add(i)
    expected = {r["solar_id"]: r for r in records if r["association_status"] == "building_candidates"}
    for name in shards:
        shard = read(directory / name)
        assert shard["snapshot"] == index["snapshot"]
        for key, ranking in shard["records"].items():
            row = expected.pop(key)
            assert row["ranking_shard"] == Path(name).name
            assert len(ranking) == row["candidate_pool_size"]
            indices, areas, directions, tilts, options, modes = zip(*ranking)
            totals = [area + direction + tilt for area, direction, tilt in zip(areas, directions, tilts)]
            assert len(set(indices)) == len(indices)
            assert set(indices) == pools[row["municipality"], row["postcode"]]
            assert all(-40 * POINT_SCALE <= points <= 40 * POINT_SCALE for points in areas)
            assert all(-24 * POINT_SCALE <= points <= 12 * POINT_SCALE for points in directions)
            assert all(-20 * POINT_SCALE <= points <= 6 * POINT_SCALE for points in tilts)
            assert all(option == 0 for option in options)
            assert all(0 <= mode < len(AREA_MODES) for mode in modes)
            order = [(-total, -area, i) for total, area, i in zip(totals, areas, indices)]
            assert all(a <= b for a, b in zip(order, order[1:]))
            assert row["candidate_qualified_count"] == sum(total >= index["threshold"] * POINT_SCALE for total in totals)
            proposal = row["joint_proposal"]
            if proposal is not None:
                rank = proposal["rank"] - 1
                assert 0 <= rank < row["candidate_qualified_count"]
                b = indices[rank]
                assert proposal["building_id"] == index["building_ids"][b]
                assert proposal["group_ids"] == index["array_options"][b][0]["groups"]
                assert proposal["array_option"] == options[rank]
                assert proposal["points"] == [areas[rank], directions[rank], tilts[rank]]
                assert proposal["area_method"] == AREA_MODES[modes[rank]]
                assert MINIMUM_CANDIDATE_POINTS <= proposal["score"] == totals[rank] / POINT_SCALE
            for i, alternative in enumerate(row["alternatives"]):
                assert index["building_ids"][indices[i]] == alternative["building_id"]
                assert totals[i] / POINT_SCALE == alternative["score"]
                assert AREA_MODES[modes[i]] == alternative["area_method"]
    assert not expected


def verify_joint_proposals(records, context):
    meta = context["metadata"]
    allocation = meta["joint_allocation"]
    roofs = {f["properties"]["building_id"]: f for f in context["observed_pv_buildings"]}
    owners = {gid: b for b, f in roofs.items() for gid in f["properties"]["group_ids"]}
    project = Transformer.from_crs(4326, 25833, always_xy=True).transform
    groups = {f["properties"]["id"]: f for f in context["groups"]}
    book_watts, book_area, use_watts, use_area = (Counter() for _ in range(4))
    claims = {}
    booked_groups, active_sites, anchors = set(), defaultdict(list), defaultdict(list)
    selected = []
    for record in records:
        site = record["location_id"] or record["solar_id"]
        if record["association_status"] == "inferred_image_association":
            group_id = record["group_id"]
            booked_groups.add(group_id)
            roof_id = owners.get(group_id)
            if roof_id is not None:
                book_watts[roof_id] += round(record["power_kw"] * 1000)
                group = transform(project, shape(groups[group_id]["geometry"]))
                book_area[roof_id] += round(group.area * 10)
            anchors[site].append(project(*record["point"]))
        if (proposal := record.get("joint_proposal")) is None:
            continue
        selected.append(proposal)
        assert record["association_status"] == "building_candidates" and record["point"] is None
        assert not record["identity_verified"]
        b = proposal["building_id"]
        assert proposal["group_ids"] == roofs[b]["properties"]["group_ids"]
        for gid in proposal["group_ids"]:
            assert gid not in claims
            claims[gid] = record["solar_id"]
        use_watts[b] += round(record["power_kw"] * 1000)
        use_area[b] += projected_demand({"raw": record["mastr_attributes"], "flags": record["registry_flags"],
                                          **{k: record[k] for k in ("power_kw", "module_count", "commissioned")}})
        geometry = transform(project, shape(roofs[b]["geometry"]))
        active_sites[site].append(tuple(geometry.representative_point().coords[0]))
    assert len(selected) == allocation["proposals"] == meta["summary"]["joint_proposal_count"]
    score = sum(sum(p["points"]) - MINIMUM_CANDIDATE_POINTS * POINT_SCALE for p in selected)
    assert allocation["surplus"] == score <= allocation["bound"]
    if allocation["optimal_components"] == allocation["components"]:
        assert allocation["surplus"] == allocation["bound"]
    assert not booked_groups.intersection(claims)
    for a, b in overlapping_groups({gid: transform(project, shape(groups[gid]["geometry"]))
                                     for gid in claims.keys() | booked_groups}):
        assert (a in booked_groups and b in booked_groups) or (a in claims and claims[a] == claims.get(b))
    for b, power in use_watts.items():
        budget = roof_budget(roofs[b]["properties"]["projected_area_m2"], (0, 0), book_watts[b], book_area[b])
        assert power <= budget.watts and use_area[b] <= budget.area
    for site, points in active_sites.items():
        assert all(math.dist(point, other) <= LOCATION_DIAMETER_M + .001 for point in points for other in points + anchors[site])


def verify_building_evidence(records, context, summary):
    if "observed_pv_buildings" not in context:
        return
    roofs = {feature["properties"]["building_id"]: feature for feature in context["observed_pv_buildings"]}
    assert len(roofs) == len(context["observed_pv_buildings"]) == summary["observed_buildings"]
    group_geometries = {g["properties"]["id"]: shape(g["geometry"]) for g in context["groups"] if g["properties"]["roof_supported"]}
    groups = Counter(key for feature in roofs.values() for key in feature["properties"]["group_ids"])
    assert groups == Counter(group_geometries.keys())
    project = Transformer.from_crs(4326, 25833, always_xy=True).transform
    for feature in roofs.values():
        p = feature["properties"]
        merged = unary_union([transform(project, group_geometries[key]) for key in p["group_ids"]])
        assert abs(merged.area - p["projected_area_m2"]) < .001
    grouped_faces = defaultdict(list)
    for feature in context["observed_pv_roof_faces"]:
        face = feature["properties"]
        grouped_faces[face["building_id"]].append(face)
    for building_id, feature in roofs.items():
        p = feature["properties"]
        faces = grouped_faces[building_id]
        assert len(faces) == p["roof_face_count"]
        assert sum(f["projected_area_m2"] for f in faces) <= p["projected_area_m2"] + .03
        if p["area_method"] == "roof_parallel_estimate":
            difference = p["roof_parallel_area_m2"] - p["projected_area_m2"]
            assert abs(difference - sum(f["roof_parallel_area_m2"] - f["projected_area_m2"] for f in faces)) < .03
        else:
            assert p["roof_parallel_area_m2"] is None
    totals = {(r["municipality"], r["postcode"]): r for r in context["registry_postcode_totals"]}
    expected = defaultdict(Counter)
    for r in records:
        key = (r["municipality"], r["postcode"] or "")
        watts = round((r["power_kw"] or 0) * 1000)
        expected[key]["registry_count"] += 1
        expected[key]["registry_watts"] += watts
        if r["cohort"] != "active_building_under30":
            continue
        assert r["point"] is None and r["geometry"] is None
        expected[key]["target_count"] += 1
        expected[key]["target_watts"] += watts
        expected[key]["post_capture_count"] += r["association_status"] == "post_capture_unlocated"
        if r["association_status"] == "building_candidates":
            assert 1 <= len(r["alternatives"]) <= min(FALLBACK_COUNT, r["candidate_pool_size"])
            assert len({item["building_id"] for item in r["alternatives"]}) == len(r["alternatives"])
            scores = [item["score"] for item in r["alternatives"]]
            assert all(-84 <= score <= 58 for score in scores)
            assert r["score"] == scores[0]
            assert r["candidate_status"] in {"ranked", "low_compatibility"}
            if r["candidate_status"] == "low_compatibility":
                assert r["score"] < MINIMUM_CANDIDATE_POINTS and r["candidate_qualified_count"] == 0
            else:
                assert r["score"] >= MINIMUM_CANDIDATE_POINTS and r["candidate_qualified_count"] > 0
            for item in r["alternatives"]:
                roof = roofs[item["building_id"]]["properties"]
                assert r["postcode"] == roof["postcode"] and r["municipality"] in roof["municipalities"]
        else:
            assert not r["alternatives"] and r["candidate_status"]
    assert set(totals) == set(expected)
    for key, row in totals.items():
        assert all(row[f] == expected[key][f] for f in ("registry_count", "target_count", "post_capture_count"))
        assert round(row["registry_power_kw"] * 1000) == expected[key]["registry_watts"]
        assert round(row["target_power_kw"] * 1000) == expected[key]["target_watts"]


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-wms", action="store_true", help="Verify the delivery without contacting live basemap services.")
    verify(APP/"dist", check_wms=not parser.parse_args().skip_wms)
