"""Official municipal boundaries and checksum-verified RGBI tile acquisition."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import html
import json
import math
import re
import shutil
import time
from urllib.parse import urlencode
from urllib.request import urlopen
import zipfile
import xml.etree.ElementTree as ET
import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import box, mapping
from .io import digest, write_json, now
from shapely import make_valid
from pyproj import Transformer
from shapely.ops import transform

BOUNDARY_URL = "https://isk.geobasis-bb.de/ows/vg_wfs"
TILE_URL = "https://data.geobasis-bb.de/geobasis/daten/dop/rgbi_tif/"
MUNICIPALITIES = {"12065144": "Hohen Neuendorf", "12065036": "Birkenwerder"}


def plan(home, boundary_file=None):
    root = Path(home) / "data" / "municipal"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "gemeinden.xml"
    params = dict(SERVICE="WFS", VERSION="2.0.0", REQUEST="GetFeature", TYPENAMES="app:gem", SRSNAME="EPSG:25833", BBOX="375000,5830000,393000,5850000,EPSG:25833", COUNT=100)
    url = BOUNDARY_URL + "?" + urlencode(params)
    if boundary_file:
        if Path(boundary_file).resolve() != source.resolve():
            shutil.copyfile(boundary_file, source)
    elif not source.exists():
        source.write_bytes(urlopen(url, timeout=60).read())
    frame = gpd.read_file(source)
    if frame.crs is None:
        declared = {e.attrib["srsName"] for e in ET.fromstring(source.read_bytes()).iter() if "srsName" in e.attrib}
        if declared != {"EPSG:25833"}:
            raise ValueError("Boundary CRS is not unambiguously declared as EPSG:25833.")
        frame = frame.set_crs(25833)
    frame = frame[frame.ags.isin(MUNICIPALITIES)].to_crs(25833)
    if len(frame) != 2 or not frame.geometry.is_valid.all():
        raise ValueError("Official boundary response does not contain both valid target municipalities.")
    features = []
    for _, row in frame.iterrows():
        features.append({"type": "Feature", "geometry": mapping(row.geometry), "properties": {"ags": row.ags, "name": row.gem_name, "boundary_date": str(row.gem_aktualitaet), "area_m2": row.geometry.area}})
    write_json(root / "municipalities_25833.geojson", {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:25833"}}, "features": features})
    frame.to_crs(4326).to_file(root / "municipalities.geojson", driver="GeoJSON")
    geom = frame.geometry.union_all()
    buffered = geom.buffer(60)
    west, south, east, north = buffered.bounds
    tiles = []
    for x in range(math.floor(west / 1000), math.ceil(east / 1000)):
        for y in range(math.floor(south / 1000), math.ceil(north / 1000)):
            cell = box(x * 1000, y * 1000, (x + 1) * 1000, (y + 1) * 1000)
            if not buffered.intersects(cell):
                continue
            tile = f"33{x}-{y}"
            tiles.append({"id": tile, "bounds": list(cell.bounds), "url": TILE_URL + f"dop_{tile}.zip", "aoi_area_m2": cell.intersection(geom).area})
    result = {"schema_version": 1, "created_at": now(), "boundary_url": url, "boundary_sha256": digest(source), "crs": "EPSG:25833", "context_buffer_m": 60, "area_m2": geom.area, "bounds": list(geom.bounds), "municipalities": [{"ags": f["properties"]["ags"], "name": f["properties"]["name"], "area_m2": f["properties"]["area_m2"]} for f in features], "tiles": tiles}
    write_json(root / "plan.json", result)
    return result


def download_tile(root, spec, reuse=None):
    tile = spec["id"]
    output = root / "tiles" / f"dop_{tile}.tif"
    metadata = output.with_suffix(".json")
    if output.exists() and metadata.exists():
        old = json.loads(metadata.read_text())
        if old["sha256"] == digest(output):
            return old
    output.parent.mkdir(parents=True, exist_ok=True)
    archive = output.with_suffix(".zip.part")
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(spec["url"], timeout=60) as response, archive.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
            archive_hash = digest(archive)
            with zipfile.ZipFile(archive) as package:
                article = package.read(f"dop_{tile}.html").decode("utf-8", errors="replace")
                text = html.unescape(article)
                acquired = re.search(r"Bildflugdatum:.*?(\d{4}-\d{2}-\d{2})", text, re.S)
                published = re.search(r"Veröffentlichung:.*?(\d{4}-\d{2}-\d{2})", text, re.S)
                if not acquired or not published:
                    raise ValueError("Official tile article lacks acquisition or publication date.")
                temporary = output.with_suffix(".tif.part")
                with package.open(f"dop_{tile}.tif") as src, temporary.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                (output.with_suffix(".html")).write_text(article)
            with rasterio.open(temporary) as src:
                if src.count != 4 or src.dtypes != ("uint8",) * 4 or not np.allclose(src.bounds, spec["bounds"], atol=.001) or not np.allclose(src.res, [.2, .2]):
                    raise ValueError("Downloaded raster differs from the official 1 km RGBI tile grid.")
            temporary.replace(output)
            record = {**spec, "status": "completed", "file": "data/municipal/tiles/" + output.name, "sha256": digest(output), "archive_sha256": archive_hash, "acquisition_date": acquired[1], "publication_date": published[1], "bytes": output.stat().st_size, "band_order": ["R", "G", "B", "NIR"], "assigned_crs": "EPSG:25833", "crs_evidence": "Official RGBI tile grid, source transform and LGB product definition.", "downloaded_at": now()}
            write_json(metadata, record)
            archive.unlink(missing_ok=True)
            return record
        except Exception as error:
            last_error = str(error)
            archive.unlink(missing_ok=True)
            time.sleep(attempt + 1)
    return {**spec, "status": "failed", "error": last_error}


def acquire(home, workers=4):
    root = Path(home) / "data" / "municipal"
    specification = plan(home)
    records = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download_tile, root, tile) for tile in specification["tiles"]]
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            print(f"Tile {record['id']}: {record['status']} ({len(records)}/{len(futures)})", flush=True)
            write_json(root / "acquisition.json", {"schema_version": 1, "status": "running", "expected": len(futures), "tiles": sorted(records, key=lambda r: r["id"])})
    failed = [r for r in records if r["status"] != "completed"]
    result = {"schema_version": 1, "status": "failed" if failed else "completed", "expected": len(specification["tiles"]), "tiles": sorted(records, key=lambda r: r["id"]), "completed_at": now()}
    write_json(root / "acquisition.json", result)
    if failed:
        raise ValueError(f"{len(failed)} source tiles failed. Retry acquisition; verified tiles will be reused.")
    return result


def acquire_buildings(home, page_size=2000):
    """Fetch paginated official building footprints and retain raw response hashes."""
    root = Path(home) / "data/municipal"
    specification = plan(home)
    metadata = root / "buildings-source.json"
    output = root / "buildings.geojson"
    if metadata.exists() and output.exists():
        saved = json.loads(metadata.read_text())
        if saved["sha256"] == digest(output):
            return saved
    aoi = gpd.read_file(root / "municipalities_25833.geojson").geometry.union_all()
    pages, frames, identifiers = [], [], set()
    start = 0
    for _ in range(200):
        params = dict(SERVICE="WFS", VERSION="2.0.0", REQUEST="GetFeature", TYPENAMES="ave:GebaeudeBauwerk", SRSNAME="EPSG:25833", BBOX=','.join(str(v) for v in specification["bounds"]) + ",EPSG:25833", COUNT=page_size, STARTINDEX=start)
        url = "https://isk.geobasis-bb.de/ows/alkis_vereinf_wfs?" + urlencode(params)
        file = root / "buildings" / f"page-{start:06d}.gml"
        file.parent.mkdir(exist_ok=True)
        if not file.exists():
            with urlopen(url, timeout=60) as response:
                file.write_bytes(response.read())
        tree = ET.fromstring(file.read_bytes())
        returned = int(tree.attrib.get("numberReturned", "0"))
        if returned == 0:
            # Some servers omit numberReturned; GDAL still exposes the members.
            try:
                frame = gpd.read_file(file)
            except Exception:
                if tree.tag.endswith("FeatureCollection"):
                    break
                raise ValueError("Building service returned an exception rather than features.")
        else:
            frame = gpd.read_file(file)
        if frame.empty:
            break
        if frame.crs is None:
            declared = {e.attrib["srsName"] for e in tree.iter() if "srsName" in e.attrib}
            if declared != {"EPSG:25833"}:
                raise ValueError("Building response CRS is ambiguous.")
            frame = frame.set_crs(25833)
        frame = frame.to_crs(25833)
        ids = set(frame.gml_id.astype(str))
        if ids.issubset(identifiers):
            raise ValueError("Building service repeated a page; pagination may be unsupported.")
        identifiers.update(ids)
        frames.append(frame)
        pages.append({"file": "data/municipal/buildings/" + file.name, "sha256": digest(file), "url": url, "returned": len(frame)})
        print(f"Buildings: {len(identifiers)} source features", flush=True)
        start += len(frame)
        matched = tree.attrib.get("numberMatched", "unknown")
        if matched.isdigit() and start >= int(matched):
            break
    else:
        raise ValueError("Building pagination exceeded the declared safety bound.")
    features = []
    project = Transformer.from_crs(25833, 4326, always_xy=True).transform
    for frame in frames:
        for _, row in frame.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty or not geom.intersects(aoi):
                continue
            repaired = not geom.is_valid
            geom = make_valid(geom) if repaired else geom
            if geom.geom_type not in ["Polygon", "MultiPolygon"]:
                continue
            features.append({"type": "Feature", "id": str(row.gml_id), "geometry": mapping(transform(project, geom)), "properties": {"building_id": str(row.gml_id), "source": "ALKIS simplified WFS", "geometry_repaired": repaired}})
    unique = {f["id"]: f for f in features}
    if not unique:
        raise ValueError("The official building inventory is unexpectedly empty.")
    write_json(output, {"type": "FeatureCollection", "features": list(unique.values())})
    result = {"schema_version": 1, "status": "completed", "fetched_at": now(), "features": len(unique), "source_features": len(identifiers), "pages": pages, "sha256": digest(output), "date_note": "Current cadastral footprints; not asserted contemporaneous with the aerial image.", "attribution": "GeoBasis-DE/LGB, dl-de/by-2-0"}
    write_json(metadata, result)
    return result
