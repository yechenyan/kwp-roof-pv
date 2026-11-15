"""Global grid, physical source seams, NoData, and component stitching."""
import roof_pv
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping
from roof_pv.municipal import native_starts, stitch, write_vrt, infer_core


def test_global_windows_only_intersect_and_cover_target():
    for start in [0, 1, 232, 2048, 1_900_000]:
        values = list(native_starts(start, start + 512))
        assert all(x % 232 == 0 and x + 256 > start and x < start + 512 for x in values)
        assert all(any(x <= pixel < x + 256 for x in values) for pixel in range(start, start + 512))


def test_stitch_filters_after_merging_and_does_not_join_diagonal_pixels():
    left = {"geometry": mapping(box(0, 0, 1, 1)), "pixels": 12, "score_sum": 9.6}
    edge = {"geometry": mapping(box(1, 0, 2, 1)), "pixels": 13, "score_sum": 10.4}
    diagonal = {"geometry": mapping(box(1, 1, 2, 2)), "pixels": 13, "score_sum": 10.4}
    result = stitch([left, edge], 25)
    assert len(result) == 1 and result[0]["pixels"] == 25
    assert result[0]["geometry"].area == 2
    assert stitch([left, diagonal], 25) == []


def test_two_source_mosaic_and_two_processing_cores_match_one_core(tmp_path, monkeypatch):
    import torch
    import roof_pv.municipal as module
    class PositionalModel:
        def __call__(self, inputs):
            # Position-dependent scores make a shifted window grid observable.
            grid = torch.arange(256, dtype=torch.float32)[None, None, None, :].expand(inputs.shape[0], 2, 256, 256) / 255 - .25
            return grid
    transform_value = from_origin(383000, 5838000, .2, .2)
    sources = []
    for index in range(2):
        source = tmp_path / f"source-{index}.tif"
        profile = dict(driver="GTiff", count=4, dtype="uint8", width=512, height=512, crs="EPSG:25833", transform=transform_value * rasterio.Affine.translation(index * 512, 0))
        with rasterio.open(source, "w", **profile) as dst:
            dst.write(np.full((4, 512, 512), 128, np.uint8))
        sources.append((source, 512, 512, index * 512, 0))
    vrt = tmp_path / "mosaic.vrt"
    write_vrt(vrt, 1024, 512, transform_value, sources)
    config = {"mean": [.485, .456, .406, .5], "std": [.229, .224, .225, .25], "pixel_threshold": .5}
    aoi = box(383000, 5838000 - 102.4, 383000 + 204.8, 5838000)
    with rasterio.open(vrt) as src:
        assert np.all(src.read_masks() == 255)
        monkeypatch.setattr(module, "CORE", 512)
        parts = []
        for index in range(2):
            folder = tmp_path / f"part-{index}"
            folder.mkdir()
            infer_core(src, {"rgbi": PositionalModel()}, config, aoi, index * 512, 0, transform_value, folder, "cpu", 8)
            with rasterio.open(folder / "rgbi_scores.tif") as image:
                parts.append(image.read(1))
        monkeypatch.setattr(module, "CORE", 1024)
        together = tmp_path / "together"
        together.mkdir()
        infer_core(src, {"rgbi": PositionalModel()}, config, aoi, 0, 0, transform_value, together, "cpu", 8)
        with rasterio.open(together / "rgbi_scores.tif") as image:
            expected = image.read(1)
        assert np.allclose(np.concatenate(parts, axis=1), expected[:512], atol=1e-7)
        assert np.all(expected[512:] == -1)
