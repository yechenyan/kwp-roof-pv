"""Acquire pinned official imagery without model dependencies or inference."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen
import zipfile

import rasterio

from .delivery import web_delivery
from .store import checksum


def download_tile(home, tile):
    home = Path(home).resolve()
    output = (home / tile["file"]).resolve()
    if not output.is_relative_to(home / "data"):
        raise ValueError("Source imagery must be stored inside the data directory.")
    if output.is_file() and checksum(output) == tile["sha256"]:
        return "reused"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".imagery-", dir=output.parent) as temporary:
        archive = Path(temporary) / "source.zip"
        with urlopen(tile["url"], timeout=120) as response, archive.open("wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
        if checksum(archive) != tile["archive_sha256"]:
            raise ValueError(f"Official archive changed for {tile['id']}; refusing to replace frozen imagery.")
        extracted = Path(temporary) / "source.tif"
        with zipfile.ZipFile(archive) as package:
            with package.open(f"dop_{tile['id']}.tif") as source, extracted.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
        if checksum(extracted) != tile["sha256"]:
            raise ValueError(f"Source imagery checksum differs for {tile['id']}.")
        with rasterio.open(extracted) as source:
            if source.count != 4 or source.dtypes != ("uint8",) * 4:
                raise ValueError("Expected four uint8 RGBI bands.")
            if not all(math.isclose(a, b, abs_tol=.001) for a, b in zip(source.bounds, tile["bounds"])):
                raise ValueError("Source imagery bounds differ from the frozen run.")
            if not all(math.isclose(value, .2, abs_tol=.000001) for value in source.res):
                raise ValueError("Expected the original 20 cm source resolution.")
        extracted.replace(output)
    return "downloaded"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path("var"))
    parser.add_argument("--run", default="municipal-27320a7f3fb1")
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 9))
    args = parser.parse_args()
    if Path(args.run).name != args.run:
        parser.error("Run must be a directory name, not a path.")
    folder = args.home / "runs" / args.run
    manifest = json.loads((folder / "manifest.json").read_text())
    delivery = web_delivery(folder, manifest)
    if not delivery:
        parser.error("Run must include a pinned web-delivery profile.")
    if checksum(folder / "image.vrt") != manifest["artifacts"]["image.vrt"]:
        raise ValueError("Original mosaic failed integrity verification.")
    tiles = manifest["source_tiles"]
    print(f"Acquiring {len(tiles)} official tiles ({sum(t['bytes'] for t in tiles) / 1e9:.2f} GB). Verified local tiles are reused.", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_tile, args.home, tile): tile for tile in tiles}
        for count, future in enumerate(as_completed(futures), 1):
            tile = futures[future]
            print(f"{count}/{len(tiles)} {tile['id']}: {future.result()}", flush=True)
    print("Original imagery verified. Restart the web service and reload the page to enable native RGB/CIR tiles.")
