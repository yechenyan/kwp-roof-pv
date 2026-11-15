"""Explicit preview-only delivery of unchanged, checksum-verified run results."""
import hashlib
import json
from pathlib import Path


def web_delivery(folder, manifest):
    profile = folder / "web-delivery.json"
    if not profile.is_file():
        return None
    delivery = json.loads(profile.read_text())
    expected = hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest()
    if delivery.get("version") != 1 or delivery.get("manifest_sha256") != expected:
        raise ValueError("Web delivery does not match the original run manifest.")
    required = {name for name in manifest["artifacts"]
                if "/" not in name and (Path(name).suffix in {".geojson", ".png"} or name == "image.vrt")}
    if delivery.get("imagery") != "preview" or set(delivery.get("artifacts", [])) != required:
        raise ValueError("Web delivery must retain every result geometry and preview.")
    # Original imagery can be acquired independently of inference intermediates.
    sources = manifest.get("source_tiles", [])
    if sources and (folder / "image.vrt").is_file() and all(
        (folder.parent.parent / tile["file"]).is_file() for tile in sources
    ):
        return {**delivery, "imagery": "original"}
    return delivery
