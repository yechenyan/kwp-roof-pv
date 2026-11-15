"""Independent municipal artifact, real-image tile and review/export verification."""
import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import roof_pv_review
from fastapi.testclient import TestClient
import geopandas as gpd
import jsonschema
import numpy as np
import pyogrio
import rasterio
from shapely.geometry import shape
from roof_pv_review.app import create_app


def link_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path("var"))
    parser.add_argument("--run")
    parser.add_argument("--output", type=Path, default=Path("docs/municipal-verification.json"))
    args = parser.parse_args()
    root = args.home
    run_id = args.run or json.loads((root / "jobs/municipal.json").read_text())["run_id"]
    folder = root / "runs" / run_id
    manifest = json.loads((folder / "manifest.json").read_text())
    jsonschema.validate(manifest, json.loads(Path("contracts/run-manifest.schema.json").read_text()))
    assert manifest["kind"] == "municipal" and manifest["status"] == "completed"
    source_tiles = manifest["source_tiles"]
    candidates = json.loads((folder / "candidates.geojson").read_text())["features"]
    schema = json.loads(Path("contracts/candidate.schema.json").read_text())
    for candidate in candidates:
        jsonschema.validate(candidate, schema)
    counts = {"valid": 0, "aoi": 0}
    origins = set()
    for stage_file in sorted((folder / "blocks").glob("*/stage.json")):
        stage = json.loads(stage_file.read_text())
        origin = (stage["x"], stage["y"])
        assert origin not in origins
        origins.add(origin)
        for variant in ["rgbi", "rgb"]:
            with rasterio.open(stage_file.parent / f"{variant}_scores.tif") as scores, rasterio.open(stage_file.parent / f"{variant}_mask.tif") as masks:
                score, mask = scores.read(1), masks.read(1)
                assert scores.dtypes == ("float32",)
                assert np.array_equal(np.where(score < 0, 255, score >= .5).astype(np.uint8), mask)
                assert scores.crs.to_epsg() == 25833
                assert np.count_nonzero(score >= 0) == stage["runtime"][variant]["valid_pixels"]
        counts["valid"] += stage["runtime"]["rgbi"]["valid_pixels"]
        counts["aoi"] += stage["runtime"]["rgbi"]["aoi_pixels"]
    assert len(origins) == manifest["processing"]["completed_blocks"]
    assert counts["valid"] == manifest["input"]["valid_pixels"]
    assert counts["aoi"] - counts["valid"] == manifest["input"]["missing_pixels"]
    print(f"Verified {len(origins)} block score/mask pairs.", flush=True)
    with tempfile.TemporaryDirectory(prefix="roof-pv-municipal-") as temporary:
        isolated = Path(temporary)
        for name in ["manifest.json", *manifest["artifacts"]]:
            link_file(folder / name, isolated / "runs" / run_id / name)
        for tile in source_tiles:
            link_file(root / tile["file"], isolated / tile["file"])
        # The review consumer independently checks every artifact and source hash.
        client = TestClient(create_app(isolated))
        base = "/api/runs/" + run_id
        initial = client.get(base).json()
        assert len(initial["candidates"]["features"]) == len(candidates) > 0
        assert all(f["properties"]["status"] == "pending" for f in initial["candidates"]["features"])
        first = initial["candidates"]["features"][0]
        reviewer = "Isolated automated integration check"
        update = {"expected_version": 0, "status": "confirmed", "mounting": "roof", "reviewer": reviewer, "note": "Functional simulation only; not a human judgment."}
        assert client.patch(base + "/candidates/" + first["id"], json=update).status_code == 200
        assert client.patch(base + "/candidates/" + first["id"], json=update).status_code == 409
        addition = {"geometry": first["geometry"], "reviewer": reviewer, "note": "Isolated test only"}
        added = client.post(base + "/candidates", json=addition).json()["id"]
        assert client.post(base + "/reviewed-areas", json=addition).status_code == 201
        merge = {"operation": "merge", "expected_versions": {first["id"]: 1, added: 0}, "reviewer": reviewer}
        assert client.post(base + "/transform", json=merge).status_code == 201
        assert client.post(base + "/undo", json={"expected_revision": 4, "reviewer": reviewer}).status_code == 200
        restarted = TestClient(create_app(isolated))
        assert restarted.get(base).json()["revision"] == 5
        response = restarted.post(base + "/exports")
        assert response.status_code == 201, response.text
        exported = response.json()
        output = isolated / "exports" / (exported["export_id"] + ".gpkg")
        spatial_layers = []
        for name, geometry_type in pyogrio.list_layers(output):
            if geometry_type is not None:
                frame = gpd.read_file(output, layer=name)
                assert frame.crs.to_epsg() == 3035 and frame.geometry.is_valid.all()
                spatial_layers.append(name)
        assert len(gpd.read_file(output, layer="confirmed_rooftop_pv")) == 1
        assert len(gpd.read_file(output, layer="candidates")) == len(candidates) + 1
        assert restarted.get(exported["download_url"]).status_code == 200
        assert restarted.get(exported["report_url"]).status_code == 200
        point = shape(first["geometry"]).representative_point()
        z = 19
        x = int((point.x + 180) / 360 * 2**z)
        y = int((1 - math.asinh(math.tan(math.radians(point.y))) / math.pi) / 2 * 2**z)
        for display in ["rgb", "cir"]:
            assert restarted.get(f"{base}/tiles/{display}/{z}/{x}/{y}.png").status_code == 200
        with sqlite3.connect(output) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    result = {"status": "passed", "run_id": run_id, "source_tiles": len(source_tiles), "area_km2": manifest["input"]["area_m2"]/1e6, "acquisition_dates": manifest["input"]["acquisition_dates"], "verified_blocks": len(origins), "valid_target_pixels": counts["valid"], "missing_target_pixels": counts["aoi"] - counts["valid"], "counts": manifest["counts"], "candidate_categories": dict(Counter(f["properties"]["category"] for f in candidates)), "spatial_layers_epsg3035": spatial_layers, "checks": ["all source and artifact hashes checked by the review consumer", "all float32 scores reproduce raw masks", "target coverage counts agree with block masks", "candidate JSON schemas", "real-data review persistence and stale writes", "manual addition, merge, undo, inspection area", "all exported spatial geometry valid in EPSG:3035", "GPKG and HTML report downloads", "original RGB and CIR XYZ tiles"], "production_decisions_added": 0, "browser_visual_verification": False, "accuracy_assessment": "No independent local ground truth; this is functional and geospatial verification."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
