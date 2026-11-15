"""A failed RGB stage must not invalidate a verified completed RGBI stage."""
from pathlib import Path
import json
import roof_pv
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from roof_pv.pipeline import run


def test_partial_stage_resume_and_completed_artifact_integrity(tmp_path, monkeypatch):
    source = tmp_path / "input.tif"
    profile = dict(driver="GTiff", width=300, height=300, count=4, dtype="uint8", crs="EPSG:25833", transform=from_origin(383487, 5837256, .2, .2), tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(source, "w", **profile) as dst:
        dst.write(np.full((4, 300, 300), 128, np.uint8))
        dst.update_tags(acquisition_date="2026-04-22", band_order="R,G,B,NIR")
    calls = []
    fail = True
    def execute(source, checkpoint, config, variant, output):
        calls.append(variant)
        if variant == "rgb" and fail:
            raise RuntimeError("Simulated interruption")
        with rasterio.open(source) as src:
            output_profile = src.profile.copy()
            output_profile.update(count=1, dtype="float32", nodata=-1)
            score = np.zeros((300, 300), np.float32)
            score[100:110, 100:110] = .8
            with rasterio.open(output / f"{variant}_scores.tif", "w", **output_profile) as dst:
                dst.write(score, 1)
            output_profile.update(dtype="uint8", nodata=255)
            with rasterio.open(output / f"{variant}_mask.tif", "w", **output_profile) as dst:
                dst.write((score >= .5).astype(np.uint8), 1)
        return {"device": "test stub"}
    monkeypatch.setattr("roof_pv.pipeline.run_model", execute)
    config = Path(__file__).resolve().parents[3] / "configs/brandenburg.json"
    with pytest.raises(RuntimeError, match="Simulated"):
        run(source, config, tmp_path, "resume")
    assert not list((tmp_path / "runs").glob("resume-*"))
    fail = False
    manifest = run(source, config, tmp_path, "resume")
    assert calls == ["rgbi", "rgb", "rgb"]
    assert run(source, config, tmp_path, "resume")["run_id"] == manifest["run_id"]
    assert len(calls) == 3
    completed = tmp_path / "runs" / manifest["run_id"]
    (completed / "rgb_scores.tif").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="corrupted"):
        run(source, config, tmp_path, "resume")
