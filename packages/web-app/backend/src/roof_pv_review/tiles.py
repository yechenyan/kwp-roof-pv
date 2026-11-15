"""XYZ image tiles derived from frozen RGBI sources, never a changing WMS."""
from pathlib import Path
import numpy as np
from PIL import Image
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.transform import from_bounds
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from xml.sax.saxutils import escape


def spectral_view(src):
    """Override misleading RGBA color tags without changing spectral samples."""
    path = escape(str(Path(src.name).resolve()))
    geotransform = ','.join(str(value) for value in src.transform.to_gdal())
    rectangle = f'<SrcRect xOff="0" yOff="0" xSize="{src.width}" ySize="{src.height}"/><DstRect xOff="0" yOff="0" xSize="{src.width}" ySize="{src.height}"/>'
    source = f'<SourceFilename relativeToVRT="0">{path}</SourceFilename>'
    bands = ''.join(f'<VRTRasterBand dataType="Byte" band="{band}"><ColorInterp>Undefined</ColorInterp><SimpleSource>{source}<SourceBand>{band}</SourceBand>{rectangle}</SimpleSource></VRTRasterBand>' for band in range(1,5))
    mask = f'<MaskBand><VRTRasterBand dataType="Byte"><ComplexSource>{source}<SourceBand>1</SourceBand>{rectangle}<UseMaskBand>true</UseMaskBand><ScaleOffset>255</ScaleOffset><ScaleRatio>0</ScaleRatio></ComplexSource></VRTRasterBand></MaskBand>'
    xml = f'<VRTDataset rasterXSize="{src.width}" rasterYSize="{src.height}"><SRS>{escape(src.crs.to_wkt())}</SRS><GeoTransform>{geotransform}</GeoTransform>{bands}{mask}</VRTDataset>'
    return MemoryFile(xml.encode())


def render_tile(root, run_id, display, z, x, y):
    if display not in ["rgb", "cir"] or not 0 <= z <= 24 or not 0 <= x < 2 ** z or not 0 <= y < 2 ** z:
        raise ValueError("Invalid XYZ tile coordinates or display.")
    root = Path(root)
    output = root / "cache/tiles-v2" / run_id / display / str(z) / str(x) / f"{y}.png"
    if output.exists():
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    folder = root / "runs" / run_id
    source = folder / ("image.vrt" if (folder / "image.vrt").exists() else "image.tif")
    extent = 20037508.342789244
    size = 2 * extent / (2 ** z)
    west, north = -extent + x * size, extent - y * size
    target = from_bounds(west, north - size, west + size, north, 256, 256)
    with rasterio.Env(GDAL_CACHEMAX=64 * 1024 * 1024), rasterio.open(source) as src:
        with spectral_view(src) as memory, memory.open() as spectral, WarpedVRT(spectral, crs="EPSG:3857", transform=target, width=256, height=256, resampling=Resampling.bilinear, add_alpha=True) as warped:
            values = warped.read([1, 2, 3] if display == "rgb" else [4, 1, 2])
            rgba = np.concatenate([values, warped.read(warped.count)[None]], axis=0).transpose(1, 2, 0)
    temporary = output.with_suffix(".tmp.png")
    # Each request has an independent temporary name; concurrent tile reads are safe.
    import uuid
    temporary = temporary.with_name(uuid.uuid4().hex + ".png")
    Image.fromarray(rgba).save(temporary)
    temporary.replace(output)
    return output
