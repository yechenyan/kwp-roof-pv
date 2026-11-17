"""Verify real delivery invariants, technical-field privacy and live WMS responses."""
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
from pyproj import Transformer
import math
from collections import defaultdict
from fastapi.testclient import TestClient
import tempfile

from build import APP, read, write, TECHNICAL_FIELDS, digest
from serve import create_app
from scoring import LOCATION_DIAMETER_M


def verify(directory):
    records=read(directory/"records.json")
    context=read(directory/"context.json")
    summary=read(directory/"summary.json")["summary"]
    assert len(records)==summary["registry_records"]==3476
    assert len({r["solar_id"] for r in records})==len(records)
    assert sum(r["cohort"]=="active_building_under30" for r in records)==2318
    assert not any(r["identity_verified"] for r in records)
    assert not any(r["capacity_conflict"] for r in records)
    assert all(set(r["mastr_attributes"]) <= TECHNICAL_FIELDS for r in records)
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
        assert db.execute("SELECT count(*) FROM storage_relationships").fetchone()[0]==len(context["storage_relationships"])
        assert all(r[0]==3035 for r in db.execute("SELECT srs_id FROM gpkg_geometry_columns"))
    for name in layers:
        frame=gpd.read_file(directory/"roof-pv-results.gpkg",layer=name)
        assert frame.crs.to_epsg()==3035 and frame.geometry.is_valid.all()
        if name == "associated_pv_points":
            assert {"Bruttoleistung","Inbetriebnahmedatum","storage_power_kw","storage_capacity_kwh","confidence","reason"} <= set(frame.columns)
    wms=[]
    for code,layer in [("dop20c","bebb_dop20c"),("dop20cir","bb_dop20cir")]:
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
    result={"status":"passed","registry_completeness":True,"technical_allowlist":True,"point_inside_geometry":True,
            "allocation_capacity_and_location_constraints":True,"geopackage_roundtrip":True,"spatial_layers":layers,
            "wms":wms,"annotation_persistence_and_stale_write_rejection":True,"edited_export":True,
            "annotation_snapshot_binding":True,
            "shared_location_consistency":True,
            "maximum_shared_location_diameter_m":round(max(diameters, default=0), 4),
            "test_database":"Isolated temporary database; production annotations unchanged.",
            "gpkg_sha256":digest(directory/"roof-pv-results.gpkg")}
    write(directory/"verification.json",result)
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    verify(APP/"dist")
