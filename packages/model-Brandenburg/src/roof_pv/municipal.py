"""Bounded-memory municipal inference on one world-anchored window grid."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import math
import os
import shutil
from xml.sax.saxutils import escape
import geopandas as gpd
import numpy as np
from PIL import Image
from pyproj import Transformer
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.transform import from_origin
from rasterio.windows import Window
from rasterio.warp import reproject, calculate_default_transform, transform_bounds, Resampling
from scipy import ndimage
from shapely import STRtree, set_precision
from shapely.geometry import box, shape, mapping
from shapely.ops import transform, unary_union
from .io import digest, write_json, now
from .inference import load_model
from .pipeline import code_digest, verify_complete
from .postprocess import compare, collection

RESOLUTION = .2
CORE = 2048
STEP = 232
WORLD_NORTH = 10_000_000


def native_starts(start, stop):
    """Every globally anchored 256-pixel window intersecting [start, stop)."""
    first = math.ceil((start - 255) / STEP) * STEP
    return range(first, stop, STEP)


def write_vrt(path, width, height, transform_value, sources, count=4, dtype="Byte", nodata=None, masks=True):
    crs = rasterio.crs.CRS.from_epsg(25833).to_wkt()
    rows = [f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">', f'<SRS>{escape(crs)}</SRS>', '<GeoTransform>' + ','.join(str(v) for v in transform_value.to_gdal()) + '</GeoTransform>']
    def source_xml(source, band, mask=False):
        filename, src_width, src_height, x, y = source
        relative = os.path.relpath(filename, path.parent)
        tag = "ComplexSource" if mask else "SimpleSource"
        scaling = '<UseMaskBand>true</UseMaskBand><ScaleOffset>255</ScaleOffset><ScaleRatio>0</ScaleRatio>' if mask else ''
        return f'<{tag}><SourceFilename relativeToVRT="1">{escape(relative)}</SourceFilename><SourceBand>{band}</SourceBand><SrcRect xOff="0" yOff="0" xSize="{src_width}" ySize="{src_height}"/><DstRect xOff="{x}" yOff="{y}" xSize="{src_width}" ySize="{src_height}"/>{scaling}</{tag}>'
    for band in range(1, count + 1):
        rows.append(f'<VRTRasterBand dataType="{dtype}" band="{band}">')
        if nodata is not None:
            rows.append(f'<NoDataValue>{nodata}</NoDataValue>')
        rows.extend(source_xml(source, band) for source in sources)
        rows.append('</VRTRasterBand>')
    if masks:
        rows.append('<MaskBand><VRTRasterBand dataType="Byte">')
        rows.extend(source_xml(source, 1, True) for source in sources)
        rows.append('</VRTRasterBand></MaskBand>')
    rows.append('</VRTDataset>')
    path.write_text('\n'.join(rows))


def image_mosaic(home, output, tiles):
    west = min(t["bounds"][0] for t in tiles)
    south = min(t["bounds"][1] for t in tiles)
    east = max(t["bounds"][2] for t in tiles)
    north = max(t["bounds"][3] for t in tiles)
    transform_value = from_origin(west, north, .2, .2)
    width, height = round((east - west) / .2), round((north - south) / .2)
    sources = [(Path(home) / t["file"], 5000, 5000, round((t["bounds"][0] - west) / .2), round((north - t["bounds"][3]) / .2)) for t in tiles]
    write_vrt(output, width, height, transform_value, sources)


def overview(source, output, bands=(1, 2, 3), limit=2048):
    with rasterio.open(source) as src:
        target, width, height = calculate_default_transform(src.crs, 3857, src.width, src.height, *src.bounds)
        factor = max(width, height) / limit
        if factor > 1:
            target = target * rasterio.Affine.scale(factor)
            width, height = math.ceil(width / factor), math.ceil(height / factor)
        image = np.zeros((4, height, width), np.uint8)
        for i, band in enumerate(bands):
            reproject(rasterio.band(src, band), image[i], src_transform=src.transform, src_crs=src.crs, dst_transform=target, dst_crs=3857, resampling=Resampling.bilinear)
        # Alpha is derived from the source coverage, never from RGB black pixels.
        from rasterio.vrt import WarpedVRT
        with WarpedVRT(src, crs=3857, transform=target, width=width, height=height) as warped:
            image[3] = warped.dataset_mask()
        Image.fromarray(image.transpose(1, 2, 0)).save(output)
        west, south, east, north = transform_bounds(3857, 4326, *rasterio.transform.array_bounds(height, width, target))
    return [[south, west], [north, east]]


def fragments(score, valid, transform_value):
    labels, _ = ndimage.label((score >= .5) & valid)
    sizes = np.bincount(labels.ravel())
    sums = np.bincount(labels.ravel(), weights=np.maximum(score, 0).ravel())
    return [{"geometry": mapping(set_precision(shape(geometry), .0001)), "pixels": int(sizes[int(label)]), "score_sum": float(sums[int(label)])} for geometry, label in shapes(labels.astype(np.int32), mask=labels > 0, transform=transform_value, connectivity=4)]


def stitch(items, minimum_pixels=25):
    """Merge edge-connected fragments, preserving four-neighbor semantics."""
    geometries = [set_precision(shape(item["geometry"]), .0001) for item in items]
    tree = STRtree(geometries)
    parents = list(range(len(items)))
    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    for i, geom in enumerate(geometries):
        for j in tree.query(geom, predicate="intersects"):
            j = int(j)
            if j <= i:
                continue
            shared = geom.intersection(geometries[j])
            if shared.area > 0 or shared.length > .0001:
                parents[find(j)] = find(i)
    groups = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    result = []
    for indices in groups.values():
        pixels = sum(items[i]["pixels"] for i in indices)
        if pixels < minimum_pixels:
            continue
        result.append({"geometry": unary_union([geometries[i] for i in indices]), "pixels": pixels, "score_mean": sum(items[i]["score_sum"] for i in indices) / pixels})
    return result


def infer_core(src, models, config, aoi, x, y, transform_value, output, device, batch_size):
    """Accumulate global windows into a disjoint core, including neighboring sources."""
    import torch
    core_transform = transform_value * rasterio.Affine.translation(x, y)
    inside = rasterize([(mapping(aoi), 1)], out_shape=(CORE, CORE), transform=core_transform, dtype="uint8") > 0
    west, south, east, north = rasterio.transform.array_bounds(CORE, CORE, core_transform)
    world_x, world_y = round(west / .2), round((WORLD_NORTH - north) / .2)
    locations = []
    for global_y in native_starts(world_y, world_y + CORE):
        for global_x in native_starts(world_x, world_x + CORE):
            wx, ny = global_x * .2, WORLD_NORTH - global_y * .2
            if not aoi.intersects(box(max(west, wx), max(south, ny - 51.2), min(east, wx + 51.2), min(north, ny))):
                continue
            sx, sy = round((wx - src.bounds.left) / .2), round((src.bounds.top - ny) / .2)
            locations.append((global_x - world_x, global_y - world_y, Window(sx, sy, 256, 256)))
    profile = dict(driver="GTiff", width=CORE, height=CORE, count=1, crs="EPSG:25833", transform=core_transform, tiled=True, blockxsize=256, blockysize=256, compress="deflate")
    # Read bounded context once; both models use the same original pixel values.
    context_x = min((int(item[2].col_off) for item in locations), default=0)
    context_y = min((int(item[2].row_off) for item in locations), default=0)
    context_right = max((int(item[2].col_off) + 256 for item in locations), default=256)
    context_bottom = max((int(item[2].row_off) + 256 for item in locations), default=256)
    context = Window(context_x, context_y, context_right - context_x, context_bottom - context_y)
    pixels = src.read(window=context, boundless=True, fill_value=0)
    coverage = np.all(src.read_masks(window=context, boundless=True) > 0, axis=0)
    result = {}
    for variant, model in models.items():
        channels = 4 if variant == "rgbi" else 3
        means = np.asarray(config["mean"][:channels], np.float32)[:, None, None]
        stds = np.asarray(config["std"][:channels], np.float32)[:, None, None]
        total = np.zeros((CORE, CORE), np.float32)
        counts = np.zeros((CORE, CORE), np.uint16)
        with torch.inference_mode():
            for start in range(0, len(locations), batch_size):
                chunk = locations[start:start + batch_size]
                inputs, masks = [], []
                for _, _, window in chunk:
                    cx, cy = int(window.col_off) - context_x, int(window.row_off) - context_y
                    values = pixels[:channels, cy:cy + 256, cx:cx + 256].copy()
                    valid = coverage[cy:cy + 256, cx:cx + 256]
                    values[:, ~valid] = 0
                    inputs.append((values.astype(np.float32) / 255 - means) / stds)
                    masks.append(valid)
                if not inputs:
                    continue
                tensor = torch.from_numpy(np.stack(inputs)).to(device)
                predictions = torch.sigmoid(model(tensor)[:, 1]).cpu().numpy()
                for (px, py, _), prediction, valid in zip(chunk, predictions, masks):
                    left, top, right, bottom = max(px, 0), max(py, 0), min(px + 256, CORE), min(py + 256, CORE)
                    if left >= right or top >= bottom:
                        continue
                    window = np.s_[top:bottom, left:right]
                    crop = np.s_[top - py:bottom - py, left - px:right - px]
                    total[window] += prediction[crop] * valid[crop]
                    counts[window] += valid[crop]
        valid = (counts > 0) & inside
        score = np.divide(total, counts, out=np.full(total.shape, -1, np.float32), where=valid)
        mask = np.where(valid, score >= config["pixel_threshold"], 255).astype(np.uint8)
        for suffix, array, dtype, nodata in [("scores", score, "float32", -1), ("mask", mask, "uint8", 255)]:
            with rasterio.open(output / f"{variant}_{suffix}.tif", "w", dtype=dtype, nodata=nodata, **profile) as dst:
                dst.write(array, 1)
        write_json(output / f"{variant}_fragments.json", fragments(score, valid, core_transform))
        if variant == "rgbi":
            kinds = np.where(inside, np.where(valid, 1, 2), 0).astype(np.uint8)
            coverage_parts = [{"type": "Feature", "geometry": geometry, "properties": {"kind": "valid" if value == 1 else "missing"}} for geometry, value in shapes(kinds, mask=inside, transform=core_transform)]
            write_json(output / "coverage_fragments.json", collection(coverage_parts))
        result[variant] = {"windows": len(locations), "valid_pixels": int(valid.sum()), "aoi_pixels": int(inside.sum())}
    return result


def validate_device(model, device, values, config):
    """Record CPU/MPS numerical agreement without claiming bitwise equality."""
    import torch
    if device == "cpu":
        return {"reference": "cpu", "device": "cpu", "max_absolute_score_difference": 0.0, "threshold_pixel_differences": 0}
    means = np.asarray(config["mean"], np.float32)[:, None, None]
    stds = np.asarray(config["std"], np.float32)[:, None, None]
    inputs = torch.from_numpy((values.astype(np.float32) / 255 - means) / stds).unsqueeze(0)
    with torch.inference_mode():
        model.to("cpu")
        reference = torch.sigmoid(model(inputs)[:, 1]).numpy()
        model.to(device)
        accelerated = torch.sigmoid(model(inputs.to(device))[:, 1]).cpu().numpy()
    delta = float(np.max(np.abs(reference - accelerated)))
    differences = int(np.count_nonzero((reference >= .5) != (accelerated >= .5)))
    if delta > .001 or differences > reference.size * .001:
        raise ValueError("Accelerated inference differs excessively from CPU on the recorded probe.")
    return {"reference": "cpu", "device": device, "max_absolute_score_difference": delta, "threshold_pixel_differences": differences, "probe_shape": list(values.shape)}


def municipal_run(home, config_path, device="auto", batch_size=8):
    import torch
    root = Path(home)
    data = root / "data/municipal"
    acquisition = json.loads((data / "acquisition.json").read_text())
    if acquisition["status"] != "completed":
        raise ValueError("All planned official source tiles must be acquired before inference.")
    tiles = acquisition["tiles"]
    for tile in tiles:
        if digest(root / tile["file"]) != tile["sha256"]:
            raise ValueError("Official source tile checksum changed.")
    boundaries = gpd.read_file(data / "municipalities_25833.geojson").to_crs(25833)
    aoi = boundaries.geometry.union_all()
    config = json.loads(Path(config_path).read_text())
    device = "mps" if device == "auto" and torch.backends.mps.is_available() else "cpu" if device == "auto" else device
    if not 1 <= batch_size <= 32:
        raise ValueError("Batch size must be between 1 and 32.")
    if (config["window_px"], config["overlap_px"], config["scale"], config["connectivity"], config["pixel_threshold"], config["activation"], config["fusion"]) != (256, 24, 255, 4, .5, "sigmoid_output_channel_1", "uniform_mean"):
        raise ValueError("Municipal processing requires the validated inference contract.")
    if device == "cpu":
        batch_size = 1
    identity = {"config": config, "code_sha256": code_digest(), "boundary_sha256": digest(data / "gemeinden.xml"), "source_tiles": [{key: t[key] for key in ["id", "file", "sha256", "acquisition_date", "publication_date", "bounds"]} for t in tiles], "device": device, "batch_size": batch_size, "core_px": CORE, "versions": {p: importlib.metadata.version(p) for p in ["torch", "torchvision", "numpy", "rasterio", "segmentation-models-pytorch", "scipy", "shapely", "pyproj", "geopandas"]}}
    building_path = data / "buildings.geojson"
    if not building_path.exists() or not (data / "buildings-source.json").exists():
        raise ValueError("Acquire official buildings before starting the municipal run.")
    building_source = json.loads((data / "buildings-source.json").read_text())
    if digest(building_path) != building_source["sha256"]:
        raise ValueError("Building inventory checksum differs from its acquisition metadata.")
    identity["buildings_sha256"] = building_source["sha256"]
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    identity["input_sha256"] = hashlib.sha256(json.dumps(identity["source_tiles"], sort_keys=True).encode()).hexdigest()
    run_id = "municipal-" + fingerprint[:12]
    destination = root / "runs" / run_id
    if destination.exists():
        saved = json.loads((destination / "manifest.json").read_text())
        if not verify_complete(destination, saved):
            raise ValueError("Completed municipal artifacts changed.")
        return saved
    work = root / "runs" / (".partial-" + run_id)
    work.mkdir(parents=True, exist_ok=True)
    lock = work.parent / (".lock-" + run_id)
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(descriptor)
    job = root / "jobs" / "municipal.json"
    progress = {"schema_version": 1, "run_id": run_id, "status": "running", "started_at": now(), "completed_blocks": 0}
    try:
        west, south, east, north = aoi.bounds
        size = CORE * .2
        west, south, east, north = math.floor(west / size) * size, math.floor(south / size) * size, math.ceil(east / size) * size, math.ceil(north / size) * size
        width, height = round((east - west) / .2), round((north - south) / .2)
        transform_value = from_origin(west, north, .2, .2)
        blocks = [(x, y) for y in range(0, height, CORE) for x in range(0, width, CORE) if aoi.intersects(box(west + x * .2, north - (y + CORE) * .2, west + (x + CORE) * .2, north - y * .2))]
        progress["total_blocks"] = len(blocks)
        write_json(job, progress)
        image_mosaic(root, work / "image.vrt", tiles)
        models = {variant: load_model(root / "models" / config["models"][variant]["checkpoint"], config["models"][variant]["sha256"], 4 if variant == "rgbi" else 3).to(device) for variant in ["rgbi", "rgb"]}
        records = []
        with rasterio.Env(GDAL_CACHEMAX=128 * 1024 * 1024), rasterio.open(work / "image.vrt") as src:
            probe = next(t for t in tiles if t["aoi_area_m2"] > 0)
            probe_x = round((probe["bounds"][0] - src.bounds.left) / .2) + 2000
            probe_y = round((src.bounds.top - probe["bounds"][3]) / .2) + 2000
            device_check = validate_device(models["rgbi"], device, src.read(window=Window(probe_x, probe_y, 256, 256)), config)
            write_json(work / "device-check.json", device_check)
            for index, (x, y) in enumerate(blocks):
                block = work / "blocks" / f"b{index:04d}"
                block.mkdir(parents=True, exist_ok=True)
                stage_path = block / "stage.json"
                stage = json.loads(stage_path.read_text()) if stage_path.exists() else None
                if not stage or not all((block / name).exists() and digest(block / name) == sha for name, sha in stage["artifacts"].items()):
                    print(f"Block {index + 1}/{len(blocks)}: ({x},{y}) on {device}", flush=True)
                    result = infer_core(src, models, config, aoi, x, y, transform_value, block, device, batch_size if device != "cpu" else 1)
                    stage = {"x": x, "y": y, "runtime": result, "artifacts": {p.name: digest(p) for p in block.iterdir() if p.name != "stage.json" and not p.name.endswith(".tmp")}}
                    write_json(stage_path, stage)
                records.append((block, stage))
                progress.update(completed_blocks=index + 1, updated_at=now())
                write_json(job, progress)
        del models
        project = Transformer.from_crs(25833, 4326, always_xy=True).transform
        observations = {}
        for variant in ["rgbi", "rgb"]:
            parts = []
            for block, _ in records:
                parts.extend(json.loads((block / f"{variant}_fragments.json").read_text()))
            observations[variant] = []
            for index, item in enumerate(stitch(parts, config["minimum_pixels"])):
                geom = item["geometry"].intersection(aoi)
                if geom.is_empty or geom.area < .02:
                    continue
                if geom.geom_type not in ["Polygon", "MultiPolygon"]:
                    geom = unary_union([g for g in geom.geoms if g.geom_type in ["Polygon", "MultiPolygon"]])
                observation_id = f"{run_id}:{variant}:{index + 1}"
                sources = [t for t in tiles if geom.intersects(box(*t["bounds"]))]
                towns = [row.gem_name if "gem_name" in row else row["name"] for _, row in boundaries.iterrows() if geom.intersects(row.geometry)]
                observations[variant].append({"type": "Feature", "id": observation_id, "geometry": mapping(transform(project, geom)), "properties": {"id": observation_id, "run_id": run_id, "model": variant, "pixels": item["pixels"], "projected_area_m2": geom.area, "score_mean": item["score_mean"], "score_is_calibrated": False, "source_tiles": [t["id"] for t in sources], "acquisition_dates": sorted({t["acquisition_date"] for t in sources}), "municipalities": towns, "boundary_clipped": geom.intersects(aoi.boundary)}})
            write_json(work / f"{variant}.geojson", collection(observations[variant]))
            for suffix, dtype, nodata in [("scores", "Float32", -1), ("mask", "Byte", 255)]:
                sources = [(block / f"{variant}_{suffix}.tif", CORE, CORE, stage["x"], stage["y"]) for block, stage in records]
                write_vrt(work / f"{variant}_{suffix}.vrt", width, height, transform_value, sources, count=1, dtype=dtype, nodata=nodata, masks=False)
        candidates = compare(observations["rgbi"], observations["rgb"], run_id, config["comparison_iou"])
        buildings = json.loads(building_path.read_text())["features"]
        to_native = Transformer.from_crs(4326, 25833, always_xy=True).transform
        building_shapes = [transform(to_native, shape(f["geometry"])) for f in buildings]
        building_tree = STRtree(building_shapes)
        lookup = {f["id"]: f for group in observations.values() for f in group}
        for feature in candidates:
            evidence = [lookup[item]["properties"] for item in feature["properties"]["observation_ids"]]
            feature["properties"].update(acquisition_dates=sorted({date for e in evidence for date in e["acquisition_dates"]}), source_tiles=sorted({tile for e in evidence for tile in e["source_tiles"]}), municipalities=sorted({town for e in evidence for town in e["municipalities"]}), boundary_clipped=any(e["boundary_clipped"] for e in evidence))
            geom = transform(to_native, shape(feature["geometry"]))
            matches = [int(i) for i in building_tree.query(geom, predicate="intersects") if geom.intersection(building_shapes[int(i)]).area > 0]
            covered = geom.intersection(unary_union([building_shapes[i] for i in matches])).area if matches else 0
            feature["properties"].update(building_ids=[buildings[i]["id"] for i in matches], building_overlap_ratio=covered / geom.area, roof_support="building_overlap" if matches else "no_building_overlap", roof_support_note="Cadastral overlap is review evidence, not a roof/ground classification.")
        write_json(work / "candidates.geojson", collection(candidates))
        coverage_groups = {"valid": [], "missing": []}
        for block, _ in records:
            for feature in json.loads((block / "coverage_fragments.json").read_text())["features"]:
                coverage_groups[feature["properties"]["kind"]].append(shape(feature["geometry"]))
        coverage_features = []
        for kind, geometries in coverage_groups.items():
            if geometries:
                geom = unary_union(geometries).intersection(aoi)
                coverage_features.append({"type": "Feature", "geometry": mapping(transform(project, geom)), "properties": {"kind": kind, "run_id": run_id, "meaning": "Target pixel coverage; not a PV absence label."}})
        write_json(work / "pixel_coverage.geojson", collection(coverage_features))
        shutil.copyfile(data / "municipalities.geojson", work / "municipalities.geojson")
        shutil.copyfile(building_path, work / "buildings.geojson")
        shutil.copyfile(data / "buildings-source.json", work / "buildings-source.json")
        preview_bounds = overview(work / "image.vrt", work / "preview.png")
        overview(work / "image.vrt", work / "preview_cir.png", bands=(4, 1, 2))
        valid_pixels = sum(s["runtime"]["rgbi"]["valid_pixels"] for _, s in records)
        aoi_pixels = sum(s["runtime"]["rgbi"]["aoi_pixels"] for _, s in records)
        dates = sorted({t["acquisition_date"] for t in tiles})
        manifest = {"schema_version": 1, "kind": "municipal", "run_id": run_id, "fingerprint": fingerprint, "status": "completed", "created_at": progress["started_at"], "completed_at": now(), "identity": identity, "source_tiles": tiles, "aoi_geometry": mapping(transform(project, aoi)), "input": {"name": "Hohen Neuendorf + Birkenwerder", "crs": "EPSG:25833", "bounds": [west, south, east, north], "width": width, "height": height, "bands": ["R", "G", "B", "NIR"], "acquisition_date": dates[0] if len(dates) == 1 else f"{dates[0]} / {dates[-1]}", "acquisition_dates": dates, "valid_pixels": valid_pixels, "missing_pixels": aoi_pixels - valid_pixels, "area_m2": aoi.area}, "preview_bounds": preview_bounds, "counts": {"rgbi": len(observations["rgbi"]), "rgb": len(observations["rgb"]), "candidates": len(candidates)}, "processing": {"device": device, "batch_size": batch_size, "core_px": CORE, "global_step_px": STEP, "global_origin": [0, WORLD_NORTH], "completed_blocks": len(records)}, "limitations": ["Image observations only; human confirmation is pending.", "No MaStR matching, power estimates or storage allocation.", "Scores are uncalibrated; roof mounting is not established by this detector."]}
        manifest["artifacts"] = {str(p.relative_to(work)): digest(p) for p in sorted(work.rglob("*")) if p.is_file() and p.name != "manifest.json" and not p.name.endswith(".tmp")}
        write_json(work / "manifest.json", manifest)
        os.replace(work, destination)
        progress.update(status="completed", completed_at=now(), counts=manifest["counts"])
        write_json(job, progress)
        return manifest
    except Exception as error:
        progress.update(status="failed", error=str(error), updated_at=now())
        write_json(job, progress)
        raise
    finally:
        lock.unlink(missing_ok=True)
