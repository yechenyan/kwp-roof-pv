"""Compact checkouts retain verified detections without full inference rasters."""
import json
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from roof_pv_review.app import create_app
from roof_pv_review.store import checksum
from roof_pv_review.source_download import download_tile
from test_source_download import source_archive


def compact_checkout(review, tmp_path):
    _, _, source, run_id, _ = review
    root = tmp_path / "compact"
    folder = root / "runs" / run_id
    shutil.copytree(source / "runs" / run_id, folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    for name in ["preview.png", "preview_cir.png"]:
        Image.new("RGBA", (16, 16), (20, 80, 120, 255)).save(folder / name)
        manifest["artifacts"][name] = checksum(folder / name)
    manifest["kind"] = "municipal"
    manifest["preview_bounds"] = [[52.6, 13.2], [52.7, 13.3]]
    manifest["source_tiles"] = [{"file": "data/missing.tif", "sha256": "0" * 64}]
    manifest["artifacts"]["blocks/unused_scores.tif"] = "0" * 64
    (folder / "manifest.json").write_text(json.dumps(manifest))
    profile = {"version": 1, "imagery": "preview",
               "manifest_sha256": checksum(folder / "manifest.json"),
               "artifacts": sorted(n for n in manifest["artifacts"] if "/" not in n)}
    (folder / "web-delivery.json").write_text(json.dumps(profile))
    return root, folder, run_id


def test_compact_checkout_supports_review_and_previews_without_source_imagery(review, tmp_path):
    root, _, run_id = compact_checkout(review, tmp_path)
    client = TestClient(create_app(root))
    workspace = client.get(f"/api/runs/{run_id}/workspace")
    assert workspace.status_code == 200
    assert workspace.json()["manifest"]["delivery"] == {"imagery": "preview"}
    assert len(workspace.json()["candidates"]["features"]) == 1
    assert client.get("/api/runs").json()[0]["manifest"]["delivery"]["imagery"] == "preview"
    assert client.get(f"/api/runs/{run_id}/assets/preview.png").status_code == 200
    assert client.get(f"/api/runs/{run_id}/assets/preview_cir.png").status_code == 200
    response = client.get(f"/api/runs/{run_id}/tiles/rgb/18/140688/85750.png")
    assert response.status_code == 404
    assert "Original imagery is not included" in response.json()["detail"]
    response = client.patch(f"/api/runs/{run_id}/candidates/test-candidate", json={
        "expected_version": 0, "status": "confirmed", "reviewer": "Test reviewer"})
    assert response.status_code == 200
    restarted = TestClient(create_app(root))
    assert restarted.get(f"/api/runs/{run_id}/workspace").json()["revision"] == 1


@pytest.mark.parametrize("failure", ["missing_profile", "wrong_manifest", "omitted_result", "corrupt_result"])
def test_compact_delivery_cannot_skip_or_corrupt_required_results(review, tmp_path, failure):
    root, folder, _ = compact_checkout(review, tmp_path)
    path = folder / "web-delivery.json"
    profile = json.loads(path.read_text())
    if failure == "missing_profile":
        path.unlink()
    elif failure == "wrong_manifest":
        profile["manifest_sha256"] = "0" * 64
        path.write_text(json.dumps(profile))
    elif failure == "omitted_result":
        profile["artifacts"].remove("candidates.geojson")
        path.write_text(json.dumps(profile))
    else:
        (folder / "candidates.geojson").write_text("corrupt")
    with pytest.raises((ValueError, FileNotFoundError)):
        create_app(root)


def test_compact_delivery_rechecks_results_with_retained_database(review, tmp_path):
    root, folder, _ = compact_checkout(review, tmp_path)
    create_app(root)
    (folder / "rgb.geojson").write_text("corrupt")
    with pytest.raises(ValueError, match="integrity verification"):
        create_app(root)


def test_downloading_sources_enables_native_tiles_without_inference_intermediates(review, tmp_path, monkeypatch):
    import io
    from roof_pv_review import source_download
    root, folder, run_id = compact_checkout(review, tmp_path)
    tile, content = source_archive(tmp_path)
    shutil.copyfile(tmp_path / "source.tif", folder / "image.vrt")
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["source_tiles"] = [tile]
    manifest["artifacts"]["image.vrt"] = checksum(folder / "image.vrt")
    path.write_text(json.dumps(manifest))
    profile_path = folder / "web-delivery.json"
    profile = json.loads(profile_path.read_text())
    profile["manifest_sha256"] = checksum(path)
    profile["artifacts"].append("image.vrt")
    profile_path.write_text(json.dumps(profile))
    client = TestClient(create_app(root))
    assert client.get(f"/api/runs/{run_id}/workspace").json()["manifest"]["delivery"]["imagery"] == "preview"
    monkeypatch.setattr(source_download, "urlopen", lambda *a, **kw: io.BytesIO(content))
    assert download_tile(root, tile) == "downloaded"
    restarted = TestClient(create_app(root))
    assert restarted.get(f"/api/runs/{run_id}/workspace").json()["manifest"]["delivery"]["imagery"] == "original"
    response = restarted.get(f"/api/runs/{run_id}/tiles/rgb/18/140688/85750.png")
    assert response.status_code == 200
    assert not (folder / "blocks").exists()
