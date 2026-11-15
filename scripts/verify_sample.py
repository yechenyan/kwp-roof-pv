"""Verify a real run and an isolated review/export round trip; never alter user reviews."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import roof_pv_review
from fastapi.testclient import TestClient
import geopandas as gpd
import jsonschema
import numpy as np
import rasterio
from roof_pv_review.app import create_app
from roof_pv_review.store import checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path("var"))
    parser.add_argument("--run")
    parser.add_argument("--output", type=Path, default=Path("docs/sample-verification.json"))
    args = parser.parse_args()
    choices = [folder for folder in (args.home / "runs").iterdir() if not folder.name.startswith(".") and (folder / "manifest.json").exists() and json.loads((folder / "manifest.json").read_text()).get("kind") != "municipal"]
    folder = args.home / "runs" / args.run if args.run else max(choices, key=lambda p: json.loads((p / "manifest.json").read_text())["created_at"])
    manifest = json.loads((folder / "manifest.json").read_text())
    jsonschema.validate(manifest, json.loads(Path("contracts/run-manifest.schema.json").read_text()))
    candidates = json.loads((folder / "candidates.geojson").read_text())["features"]
    schema = json.loads(Path("contracts/candidate.schema.json").read_text())
    for feature in candidates:
        jsonschema.validate(feature, schema)
    for name, sha in manifest["artifacts"].items():
        assert checksum(folder / name) == sha, name
    for variant in ["rgbi", "rgb"]:
        with rasterio.open(folder / f"{variant}_scores.tif") as scores, rasterio.open(folder / f"{variant}_mask.tif") as masks:
            score, mask = scores.read(1), masks.read(1)
            assert scores.dtypes == ("float32",)
            assert np.array_equal(np.where(score < 0, 255, score >= .5).astype(np.uint8), mask)
            assert scores.crs.to_epsg() == 25833
    with tempfile.TemporaryDirectory(prefix="roof-pv-verification-") as temporary:
        isolated = Path(temporary)
        shutil.copytree(folder, isolated / "runs" / manifest["run_id"])
        client = TestClient(create_app(isolated))
        base = "/api/runs/" + manifest["run_id"]
        initial = client.get(base).json()
        assert initial["revision"] == 0
        assert all(f["properties"]["status"] == "pending" for f in initial["candidates"]["features"])
        candidate = initial["candidates"]["features"][0]
        update = {"expected_version": 0, "status": "confirmed", "reviewer": "Isolated integration test", "note": "Automated persistence test, not human validation."}
        jsonschema.validate(update, json.loads(Path("contracts/review.schema.json").read_text()))
        path = base + "/candidates/" + candidate["properties"]["id"]
        assert client.patch(path, json=update).status_code == 200
        assert client.patch(path, json=update).status_code == 409
        data = {"geometry": candidate["geometry"], "reviewer": "Isolated integration test", "note": "Automated test only"}
        assert client.post(base + "/candidates", json=data).status_code == 201
        assert client.post(base + "/reviewed-areas", json=data).status_code == 201
        restarted = TestClient(create_app(isolated))
        snapshot = restarted.get(base).json()
        assert snapshot["revision"] == 3
        exported_response = restarted.post(base + "/exports")
        assert exported_response.status_code == 201, exported_response.text
        exported = exported_response.json()
        output = isolated / "exports" / (exported["export_id"] + ".gpkg")
        confirmed = gpd.read_file(output, layer="confirmed_pv")
        assert len(confirmed) == 1 and confirmed.crs.to_epsg() == 3035
        assert confirmed.geometry.is_valid.all()
        assert len(gpd.read_file(output, layer="candidates")) == len(candidates) + 1
        assert len(gpd.read_file(output, layer="reviewed_areas")) == 1
        assert exported["review_revision"] == 3
        assert restarted.get(exported["download_url"]).status_code == 200
        assert restarted.get(base + "/assets/preview.png").status_code == 200
        with sqlite3.connect(output) as db:
            assert db.execute("SELECT count(*) FROM review_events").fetchone()[0] == 3
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    report = {"status": "passed", "run_id": manifest["run_id"], "input_sha256": manifest["identity"]["input_sha256"], "input_pixels": [manifest["input"]["width"], manifest["input"]["height"]], "acquisition_date": manifest["input"]["acquisition_date"], "counts": manifest["counts"], "categories": dict(Counter(f["properties"]["category"] for f in candidates)), "checks": ["completed run and candidate JSON schemas", "all immutable artifact hashes", "float32 scores reproduce original threshold masks", "review persistence after service restart", "stale writes return HTTP 409", "manual candidate and checked area are independent", "consistent review revision in export", "valid confirmed polygons in EPSG:3035", "GeoPackage integrity and HTTP download"], "review_test_location": "temporary isolated database, deleted after verification", "production_decisions_added": 0, "browser_visual_verification": False, "scope": "Functional pipeline verification, not PV accuracy measurement."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
