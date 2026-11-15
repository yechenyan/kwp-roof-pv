"""RGBI display order and georeferenced XYZ tiles, independent of the model."""
import math
import roof_pv_review
import numpy as np
import pytest
from PIL import Image
from pyproj import Transformer
import rasterio
from rasterio.enums import ColorInterp
from rasterio.transform import from_origin
from roof_pv_review.tiles import render_tile


def test_rgb_and_cir_are_from_original_bands_and_black_is_valid(tmp_path):
    folder = tmp_path / "runs" / "test"
    folder.mkdir(parents=True)
    values = np.stack([np.full((512,512), value, np.uint8) for value in [0,100,150,240]])
    with rasterio.open(folder / "image.tif", "w", driver="GTiff", width=512, height=512, count=4, dtype="uint8", crs=25833, transform=from_origin(383000,5838000,.2,.2)) as dst:
        dst.write(values)
        dst.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.undefined)
        dst.write_mask(np.full((512,512),255,np.uint8))
    lon, lat = Transformer.from_crs(25833,4326,always_xy=True).transform(383051.2,5837948.8)
    z=20
    x=int((lon+180)/360*2**z)
    y=int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*2**z)
    for display, expected in [("rgb",[0,100,150,255]),("cir",[240,0,100,255])]:
        output=render_tile(tmp_path,"test",display,z,x,y)
        with Image.open(output) as image:
            assert image.size==(256,256) and image.mode=="RGBA"
            assert list(image.getpixel((128,128)))==expected


@pytest.mark.parametrize("display,z,x,y", [("wrong",10,1,1),("rgb",25,0,0),("cir",2,5,0),("rgb",1,-1,0)])
def test_invalid_tile_requests_are_rejected(tmp_path,display,z,x,y):
    with pytest.raises(ValueError):
        render_tile(tmp_path,"test",display,z,x,y)
