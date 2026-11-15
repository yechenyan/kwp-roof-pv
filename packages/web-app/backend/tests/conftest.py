"""Portable synthetic artifacts, not fabricated production review decisions."""
import json
import roof_pv_review
import pytest
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform
from roof_pv_review.app import create_app
from roof_pv_review.store import checksum
from fastapi.testclient import TestClient


@pytest.fixture
def review(tmp_path):
    run_id = "test-run"
    folder = tmp_path / "runs" / run_id
    folder.mkdir(parents=True)
    project = Transformer.from_crs(25833, 4326, always_xy=True).transform
    geometry = mapping(transform(project, box(383500, 5837120, 383510, 5837130)))
    feature = {"type": "Feature", "id": "test-candidate", "geometry": geometry, "properties": {"id": "test-candidate", "category": "both", "score_mean": .8, "default_model": "rgbi", "observation_ids": ["a", "b"]}}
    for name in ["candidates", "rgbi", "rgb"]:
        (folder / f"{name}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    manifest = {"schema_version": 1, "status": "completed", "run_id": run_id, "input": {"bounds": [383487, 5837096, 383647, 5837256], "crs": "EPSG:25833", "acquisition_date": "2026-04-22"}, "artifacts": {p.name: checksum(p) for p in folder.iterdir()}}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    manifest["identity"] = {"config": {"models": {}}}
    manifest["input"].update(valid_pixels=640000, missing_pixels=0)
    (folder / "manifest.json").write_text(json.dumps(manifest))
    app = create_app(tmp_path, tmp_path / "no-frontend")
    return TestClient(app), app.state.store, tmp_path, run_id, feature
