"""Verify a compact web checkout without using uncommitted local imagery."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from fastapi.testclient import TestClient
from roof_pv_review.app import create_app


ROOT = Path(__file__).resolve().parents[1]
RUN_IDS = ["hn-center-eb242d954721", "municipal-27320a7f3fb1"]
REGISTRY_ID = "mastr-20261115-99075610"
BATCH_ID = "matching-1077534f5c31f8b6"


def delivery_paths():
    paths = []
    for run_id in RUN_IDS:
        folder = ROOT / "var/runs" / run_id
        manifest = json.loads((folder / "manifest.json").read_text())
        if (folder / "web-delivery.json").exists():
            profile = json.loads((folder / "web-delivery.json").read_text())
            names = profile["artifacts"] + ["web-delivery.json", "buildings-source.json", "device-check.json"]
        else:
            names = list(manifest["artifacts"])
        paths.extend(folder / name for name in ["manifest.json", *names])
    paths.extend(registry_delivery_paths())
    return paths


def registry_delivery_paths():
    folder = ROOT / "var/registry" / REGISTRY_ID
    manifest = json.loads((folder / "manifest.json").read_text())
    names = ["manifest.json", *manifest["input_artifacts"], *manifest["artifacts"]]
    paths = [folder / name for name in names]
    folder = ROOT / "var/matching" / RUN_IDS[1] / BATCH_ID
    manifest = json.loads((folder / "manifest.json").read_text())
    paths.extend(folder / name for name in ["manifest.json", *manifest["artifacts"]])
    return paths


def verify_registry(client, base, candidate_id):
    response = client.get(base + "/matching")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["available"] and summary["registry_ready"]
    assert summary["registry"]["snapshot_id"] == REGISTRY_ID
    assert summary["batch"]["batch_id"] == BATCH_ID
    assert not summary["registry_snapshot_changed"] and summary["stale_groups"] == 0
    assert summary["batch"]["summary"]["array_groups"] == 5038
    response = client.get(base + "/matching/units", params={"cohort": "all", "limit": 30})
    assert response.status_code == 200, response.text
    units = response.json()
    assert units["total"] == 3476 and len(units["items"]) == 30
    response = client.get(base + "/matching/units", params={"cohort": "target", "limit": 1})
    assert response.status_code == 200 and response.json()["total"] == 2320
    unit_id = units["items"][0]["id"]
    response = client.get(base + f"/matching/units/{unit_id}")
    assert response.status_code == 200, response.text
    assert response.json()["snapshot_id"] == REGISTRY_ID
    response = client.get(base + f"/matching/candidates/{candidate_id}")
    assert response.status_code == 200, response.text
    print("Verified MaStR: 3476 solar records, 2320 target records, 5038 matching groups.", flush=True)


def main():
    paths = delivery_paths()
    assert all(p.stat().st_size < 100_000_000 for p in paths)
    with tempfile.TemporaryDirectory(prefix="roof-pv-web-check-") as temporary:
        checkout = Path(temporary)
        for source in paths:
            target = checkout / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
        target = checkout / "var/state/review.sqlite3"
        target.parent.mkdir(parents=True)
        with sqlite3.connect(f"file:{ROOT / 'var/state/review.sqlite3'}?mode=ro", uri=True) as original:
            with sqlite3.connect(target) as snapshot:
                original.backup(snapshot)
                assert snapshot.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        for fresh in [False, True]:
            if fresh:
                for sidecar in target.parent.glob("review.sqlite3*"):
                    sidecar.unlink()
            client = TestClient(create_app(checkout / "var", frontend=ROOT / "packages/web-app/frontend/dist"))
            assert client.get("/").status_code == 200
            assert client.get("/api/health").status_code == 200
            runs = client.get("/api/runs").json()
            assert len(runs) == 2
            for run in runs:
                run_id = run["run_id"]
                base = f"/api/runs/{run_id}"
                response = client.get(base + "/workspace")
                assert response.status_code == 200, response.text
                data = response.json()
                assert len(data["candidates"]["features"]) == run["manifest"]["counts"]["candidates"]
                candidate = data["candidates"]["features"][0]
                assert client.get(base + f"/candidates/{candidate['id']}").status_code == 200
                for asset in ["preview.png", "preview_cir.png", "rgbi.geojson", "rgb.geojson"]:
                    response = client.get(base + f"/assets/{asset}")
                    assert response.status_code == 200, response.text
                bounds = data["manifest"]["preview_bounds"]
                bbox = f"{bounds[0][1]},{bounds[0][0]},{bounds[1][1]},{bounds[1][0]}"
                response = client.get(base + "/view", params={"bbox": bbox, "zoom": 18, "layers": "buildings,rgbi,rgb"})
                assert response.status_code == 200, response.text
                if run_id.startswith("municipal"):
                    assert data["manifest"]["delivery"]["imagery"] == "preview"
                    assert client.get(base + "/tiles/rgb/18/140688/85750.png").status_code == 404
                    verify_registry(client, base, candidate["id"])
                print(f"Verified {run_id}: {len(data['candidates']['features'])} candidates; fresh database: {fresh}", flush=True)
    print(f"Verified compact delivery: {len(paths) + 1} data files, all below 100 MB.")


if __name__ == "__main__":
    main()
