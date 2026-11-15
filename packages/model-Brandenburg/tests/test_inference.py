"""Tests for boundary blending, NoData, and raw-score preservation."""
from pathlib import Path
import roof_pv
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from roof_pv.inference import starts, run_model
from roof_pv.imagery import validate_image
from roof_pv.postprocess import compare


def image(path, width=800, height=800, dtype="uint8"):
    values = np.full((4, height, width), 128, dtype=dtype)
    profile = dict(driver="GTiff", width=width, height=height, count=4, dtype=dtype, crs="EPSG:25833", transform=from_origin(383487, 5837256, .2, .2), tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(values)
        valid = np.full((height, width), 255, np.uint8)
        valid[:10, :10] = 0
        dst.write_mask(valid)
        dst.update_tags(band_order="R,G,B,NIR", acquisition_date="2026-04-22")


@pytest.mark.parametrize("length", [256, 257, 488, 800, 1024])
def test_window_coverage(length):
    coverage = np.zeros(length, np.uint8)
    for start in starts(length):
        coverage[start:start + 256] += 1
    assert coverage.min() > 0
    assert starts(length)[-1] == length - 256


def test_streamed_blending_preserves_nodata_and_float32(tmp_path, monkeypatch):
    import torch
    import roof_pv.inference as module
    class Constant:
        def __call__(self, value):
            return torch.zeros((1, 2, 256, 256), dtype=torch.float32)
    monkeypatch.setattr(module, "load_model", lambda *args: Constant())
    source = tmp_path / "input.tif"
    image(source)
    config = {"models": {"rgbi": {"channels": ["R", "G", "B", "NIR"], "sha256": "unused"}}, "mean": [.485, .456, .406, .5], "std": [.229, .224, .225, .25], "scale": 255, "pixel_threshold": .5}
    result = run_model(source, Path("unused"), config, "rgbi", tmp_path)
    assert result["windows"] == 16
    with rasterio.open(tmp_path / "rgbi_scores.tif") as scores, rasterio.open(tmp_path / "rgbi_mask.tif") as masks:
        assert scores.dtypes == ("float32",)
        assert scores.read(1)[0, 0] == -1
        assert masks.read(1)[0, 0] == 255
        assert np.all(scores.read(1)[10:, 10:] == .5)
        assert np.all(masks.read(1)[10:, 10:] == 1)
    assert not list(tmp_path.glob("*.tmp"))


def test_input_rejects_uint16(tmp_path):
    source = tmp_path / "image.tif"
    image(source, dtype="uint16")
    with pytest.raises(ValueError, match="uint8"):
        validate_image(source)


def test_comparison_retains_primary_geometry_and_shared_evidence():
    def feature(identifier, left, right):
        return {"id": identifier, "geometry": {"type": "Polygon", "coordinates": [[[left, 52], [right, 52], [right, 52.001], [left, 52.001], [left, 52]]]}, "properties": {"score_mean": .8}}
    first, second = feature("a", 13, 13.001), feature("b", 13.002, 13.003)
    broad = feature("c", 13, 13.003)
    result = compare([first, second], [broad], "test")
    assert len(result) == 2
    assert all(f["properties"]["category"] == "geometry_conflict" for f in result)
    assert result[0]["geometry"] == first["geometry"]
    assert result[1]["geometry"] == second["geometry"]
