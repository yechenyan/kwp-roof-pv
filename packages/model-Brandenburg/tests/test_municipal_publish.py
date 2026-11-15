"""Exercise municipal publication and portable VRT references on synthetic data."""
from pathlib import Path
import json
import roof_pv
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window
from shapely.geometry import box, mapping
from shapely.ops import transform
from pyproj import Transformer
from roof_pv.io import digest
from roof_pv.pipeline import verify_complete


def test_full_publication_is_portable_and_preserves_coverage(tmp_path, monkeypatch):
    import torch
    import roof_pv.municipal as module
    class ConstantModel:
        def to(self, device):
            return self
        def __call__(self, inputs):
            return torch.ones((inputs.shape[0], 2, 256, 256), dtype=torch.float32)
    monkeypatch.setattr(module, "load_model", lambda *args: ConstantModel())
    monkeypatch.setattr(module, "CORE", 256)
    data = tmp_path / "data/municipal"
    (data / "tiles").mkdir(parents=True)
    source = data / "tiles/source.tif"
    with rasterio.open(source, "w", driver="GTiff", width=5000, height=5000, count=4, dtype="uint8", crs=25833, transform=from_origin(383000, 5838000, .2, .2), tiled=True, compress="deflate") as dst:
        dst.write(np.full((4, 256, 256), 128, np.uint8), window=Window(2300, 3600, 256, 256))
    tile = {"id": "test", "file": "data/municipal/tiles/source.tif", "bounds": [383000,5837000,384000,5838000], "sha256": digest(source), "aoi_area_m2": 100, "acquisition_date": "2026-04-22", "publication_date": "2026-10-09"}
    (data / "acquisition.json").write_text(json.dumps({"status":"completed", "tiles":[tile]}))
    aoi = box(383470,5837240,383490,5837260)
    (data / "municipalities_25833.geojson").write_text(json.dumps({"type":"FeatureCollection", "crs":{"type":"name","properties":{"name":"EPSG:25833"}}, "features":[{"type":"Feature","geometry":mapping(aoi),"properties":{"name":"Synthetic test region"}}]}))
    project = Transformer.from_crs(25833,4326,always_xy=True).transform
    boundary = {"type":"FeatureCollection", "features":[{"type":"Feature","geometry":mapping(transform(project,aoi)),"properties":{"name":"Synthetic test region"}}]}
    (data / "municipalities.geojson").write_text(json.dumps(boundary))
    (data / "gemeinden.xml").write_text("Synthetic boundary fixture.")
    building = {"type":"FeatureCollection", "features":[{"type":"Feature","id":"test-building","geometry":mapping(transform(project,aoi)),"properties":{"building_id":"test-building"}}]}
    (data / "buildings.geojson").write_text(json.dumps(building))
    (data / "buildings-source.json").write_text(json.dumps({"sha256":digest(data / "buildings.geojson")}))
    config = Path(__file__).resolve().parents[3] / "configs/brandenburg.json"
    result = module.municipal_run(tmp_path, config, "cpu", 1)
    folder = tmp_path / "runs" / result["run_id"]
    assert verify_complete(folder, result)
    assert result["input"]["missing_pixels"] == 0
    assert result["counts"]["candidates"] == 1
    assert (folder / "pixel_coverage.geojson").exists()
    candidates = json.loads((folder / "candidates.geojson").read_text())["features"]
    assert candidates[0]["properties"]["building_ids"] == ["test-building"]
    assert candidates[0]["properties"]["building_overlap_ratio"] > .99
    with rasterio.open(folder / "image.vrt") as image:
        assert image.crs.to_epsg() == 25833 and image.count == 4
    assert module.municipal_run(tmp_path, config, "cpu", 1)["run_id"] == result["run_id"]
