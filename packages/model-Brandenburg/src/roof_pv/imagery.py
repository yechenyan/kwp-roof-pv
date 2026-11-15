"""Explicit sample preparation and georeferenced preview generation."""
from pathlib import Path
from datetime import date
import numpy as np
from PIL import Image
import rasterio
from rasterio.crs import CRS
from rasterio.windows import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling, transform_bounds
from .io import digest, write_json


def prepare_sample(source, output, bounds, acquired, source_crs=None, reason=None, published=None):
    """Create a portable four-band crop without modifying the official source."""
    output = Path(output)
    date.fromisoformat(acquired)
    if published:
        date.fromisoformat(published)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("Prepared output already exists; choose a new path.")
    if source_crs and not reason:
        raise ValueError("A CRS override requires an explicit evidence reason.")
    with rasterio.open(source) as src:
        crs = CRS.from_user_input(source_crs) if source_crs else src.crs
        if not crs or not crs.is_projected or src.count != 4:
            raise ValueError("Expected a verified projected four-band source.")
        window = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
        if window.col_off < 0 or window.row_off < 0 or window.col_off + window.width > src.width or window.row_off + window.height > src.height:
            raise ValueError("Requested crop is outside source coverage.")
        profile = src.profile.copy()
        profile.update(crs=crs, width=int(window.width), height=int(window.height), transform=src.window_transform(window), compress="deflate", tiled=True, blockxsize=256, blockysize=256)
        with rasterio.open(output, "w", **profile) as dst:
            dst.write(src.read(window=window))
            dst.write_mask(src.dataset_mask(window=window))
            dst.update_tags(acquisition_date=acquired, band_order="R,G,B,NIR")
            for i, label in enumerate(["R", "G", "B", "NIR"], 1):
                dst.set_band_description(i, label)
        provenance = {"schema_version": 1, "source_name": Path(source).name, "source_sha256": digest(source), "original_crs_wkt": src.crs.to_wkt() if src.crs else None, "assigned_crs": str(crs), "crs_override_reason": reason, "requested_bounds": list(bounds), "acquisition_date": acquired, "publication_date": published, "prepared_sha256": digest(output), "source_url": "https://data.geobasis-bb.de/geobasis/daten/dop/rgbi_tif/" + Path(source).with_suffix(".zip").name, "attribution": "GeoBasis-DE/LGB, dl-de/by-2-0; cropped and reprojected previews"}
    write_json(output.with_suffix(".provenance.json"), provenance)
    return provenance


def validate_image(path):
    with rasterio.open(path) as src:
        if src.count != 4 or src.dtypes != ("uint8",) * 4:
            raise ValueError("RGBI must be four uint8 bands; implicit 16-bit scaling is forbidden.")
        if not src.crs or not src.crs.to_epsg() or not src.crs.is_projected:
            raise ValueError("Image needs a verified projected EPSG CRS.")
        if not np.allclose(src.res, [0.2, 0.2]) or src.transform.b or src.transform.d:
            raise ValueError("Only north-up 20 cm imagery is supported by this sample pipeline.")
        if src.width < 256 or src.height < 256 or src.width * src.height > 16_000_000:
            raise ValueError("Sample pipeline requires dimensions >=256 and at most 16 million pixels.")
        order = src.tags().get("band_order") or ",".join(x or "" for x in src.descriptions)
        if order != "R,G,B,NIR" or not src.tags().get("acquisition_date"):
            raise ValueError("Explicit R,G,B,NIR order and acquisition_date tags are required.")
        date.fromisoformat(src.tags()["acquisition_date"])
        valid = np.all(src.read_masks() > 0, axis=0)
        if not valid.any():
            raise ValueError("Input has no valid pixels.")
        return {"width": src.width, "height": src.height, "crs": str(src.crs), "bounds": list(src.bounds), "transform": list(src.transform)[:6], "bands": ["R", "G", "B", "NIR"], "dtype": "uint8", "acquisition_date": src.tags()["acquisition_date"], "valid_pixels": int(valid.sum()), "missing_pixels": int(valid.size - valid.sum())}


def preview(source, output, bands=(1, 2, 3)):
    """Warp RGB to Web Mercator so a Leaflet image overlay is spatially exact."""
    with rasterio.open(source) as src:
        transform, width, height = calculate_default_transform(src.crs, "EPSG:3857", src.width, src.height, *src.bounds)
        rgba = np.zeros((4, height, width), np.uint8)
        for index in range(3):
            reproject(src.read(bands[index]), rgba[index], src_transform=src.transform, src_crs=src.crs, dst_transform=transform, dst_crs="EPSG:3857", resampling=Resampling.bilinear)
        valid = np.all(src.read_masks() > 0, axis=0).astype(np.uint8) * 255
        reproject(valid, rgba[3], src_transform=src.transform, src_crs=src.crs, dst_transform=transform, dst_crs="EPSG:3857", resampling=Resampling.nearest)
        Image.fromarray(rgba.transpose(1, 2, 0)).save(output)
        west, south, east, north = transform_bounds("EPSG:3857", "EPSG:4326", *rasterio.transform.array_bounds(height, width, transform))
    return [[south, west], [north, east]]
