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
    return paths


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
                print(f"Verified {run_id}: {len(data['candidates']['features'])} candidates; fresh database: {fresh}", flush=True)
    print(f"Verified compact delivery: {len(paths) + 1} data files, all below 100 MB.")


if __name__ == "__main__":
    main()
