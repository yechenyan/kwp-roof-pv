"""Pinned source acquisition, restart safety, and refusal of changed imagery."""
import hashlib
import io
from pathlib import Path
import zipfile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from roof_pv_review import source_download


def source_archive(tmp_path):
    raster = tmp_path / "source.tif"
    with rasterio.open(raster, "w", driver="GTiff", count=4, width=32, height=32,
                       dtype="uint8", crs=25833, transform=from_origin(383000, 5838000, .2, .2)) as dst:
        dst.write(np.full((4, 32, 32), 120, dtype=np.uint8))
    payload = raster.read_bytes()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("dop_test.tif", payload)
    content = archive.getvalue()
    tile = {"id": "test", "file": "data/municipal/tiles/dop_test.tif",
            "url": "https://example.test/dop_test.zip",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "archive_sha256": hashlib.sha256(content).hexdigest(),
            "bounds": [383000, 5837993.6, 383006.4, 5838000]}
    return tile, content


def test_download_verifies_source_and_reuses_existing_tiles_without_network(tmp_path, monkeypatch):
    tile, content = source_archive(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setattr(source_download, "urlopen", lambda *a, **kw: io.BytesIO(content))
    assert source_download.download_tile(home, tile) == "downloaded"
    assert source_download.checksum(home / tile["file"]) == tile["sha256"]
    def unexpected_network(*args, **kwargs):
        pytest.fail("Verified local tiles must not be downloaded again")
    monkeypatch.setattr(source_download, "urlopen", unexpected_network)
    assert source_download.download_tile(home, tile) == "reused"


@pytest.mark.parametrize("failure", ["archive", "raster", "bounds", "unsafe_path"])
def test_changed_sources_never_replace_an_existing_file(tmp_path, monkeypatch, failure):
    tile, content = source_archive(tmp_path)
    home = tmp_path / "home"
    target = home / tile["file"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing unrelated data")
    if failure == "archive":
        tile["archive_sha256"] = "0" * 64
    elif failure == "raster":
        tile["sha256"] = "0" * 64
    elif failure == "bounds":
        tile["bounds"] = [0, 0, 1, 1]
    else:
        tile["file"] = "../outside.tif"
    monkeypatch.setattr(source_download, "urlopen", lambda *a, **kw: io.BytesIO(content))
    with pytest.raises(ValueError):
        source_download.download_tile(home, tile)
    assert target.read_bytes() == b"existing unrelated data"
    assert not list(target.parent.glob(".imagery-*"))
