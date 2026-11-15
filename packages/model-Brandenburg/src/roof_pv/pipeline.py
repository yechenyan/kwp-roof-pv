"""Atomic, resumable sample runs with immutable completed artifacts."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import shutil
import traceback
from .io import digest, write_json, now
from .imagery import validate_image, preview
from .inference import run_model
from .postprocess import detections, compare, collection


def code_digest():
    result = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        result.update(path.name.encode())
        result.update(path.read_bytes())
    return result.hexdigest()


def verify_complete(directory, manifest):
    return manifest.get("status") == "completed" and all((directory / name).is_file() and digest(directory / name) == value for name, value in manifest["artifacts"].items())


def run(source, config_path, home, name="hn-center"):
    config = json.loads(Path(config_path).read_text())
    if (config["window_px"], config["overlap_px"], config["connectivity"], config["activation"], config["fusion"]) != (256, 24, 4, "sigmoid_output_channel_1", "uniform_mean"):
        raise ValueError("Unsupported inference contract; update implementation before changing it.")
    metadata = validate_image(source)
    versions = {package: importlib.metadata.version(package) for package in ["torch", "torchvision", "segmentation-models-pytorch", "rasterio", "numpy", "scipy"]}
    provenance_path = Path(source).with_suffix(".provenance.json")
    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else None
    if provenance and provenance.get("prepared_sha256") != digest(source):
        raise ValueError("Prepared image differs from its provenance checksum.")
    identity = {"input_sha256": digest(source), "input_provenance": provenance, "config": config, "code_sha256": code_digest(), "versions": versions}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in name):
        raise ValueError("Run name must contain only lowercase ASCII letters, digits, hyphens or underscores.")
    run_id = f"{name}-{fingerprint[:12]}"
    root = Path(home) / "runs"
    root.mkdir(parents=True, exist_ok=True)
    final = root / run_id
    work = root / (".partial-" + run_id)
    if final.exists():
        previous = json.loads((final / "manifest.json").read_text())
        if not verify_complete(final, previous):
            raise ValueError("Completed run was changed or corrupted; refusing silent reuse.")
        return previous
    lock = root / (".lock-" + run_id)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise ValueError("Run is locked. If its process ended, inspect and remove the stale lock before retrying.") from error
    os.close(descriptor)
    try:
        work.mkdir(exist_ok=True)
        manifest_path = work / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"schema_version": 1, "run_id": run_id, "fingerprint": fingerprint, "created_at": now(), "identity": identity, "input": metadata, "stages": {}, "limitations": ["Local 160 m sample; not municipal coverage or an accuracy evaluation.", "No MaStR matching, capacity inference or roof-versus-ground classification.", "Scores are uncalibrated; candidates require human review.", "Global planning is relative to this prepared raster; full municipal mosaics are a later stage.", "Candidate vectorization is sample-bounded to 16 million pixels."]}
        manifest["status"] = "running"
        write_json(manifest_path, manifest)
        snapshot = work / "image.tif"
        if not snapshot.exists() or digest(snapshot) != identity["input_sha256"]:
            shutil.copyfile(source, snapshot)
        for variant in ["rgbi", "rgb"]:
            stage = manifest["stages"].get(variant, {})
            files = [f"{variant}_scores.tif", f"{variant}_mask.tif"]
            valid = stage.get("status") == "completed" and all((work / file).exists() and digest(work / file) == stage.get("artifacts", {}).get(file) for file in files)
            if not valid:
                print(f"Running {variant}: {run_id}", flush=True)
                checkpoint = Path(home) / "models" / config["models"][variant]["checkpoint"]
                result = run_model(snapshot, checkpoint, config, variant, work)
                manifest["stages"][variant] = {"status": "completed", "runtime": result, "artifacts": {file: digest(work / file) for file in files}}
                write_json(manifest_path, manifest)
        rgbi = detections(work, "rgbi", run_id, config)
        rgb = detections(work, "rgb", run_id, config)
        candidates = compare(rgbi, rgb, run_id, config["comparison_iou"])
        write_json(work / "rgbi.geojson", collection(rgbi))
        write_json(work / "rgb.geojson", collection(rgb))
        write_json(work / "candidates.geojson", collection(candidates))
        manifest["preview_bounds"] = preview(snapshot, work / "preview.png")
        preview(snapshot, work / "preview_cir.png", bands=(4, 1, 2))
        manifest["counts"] = {"rgbi": len(rgbi), "rgb": len(rgb), "candidates": len(candidates)}
        manifest["artifacts"] = {path.name: digest(path) for path in sorted(work.iterdir()) if path.is_file() and path.name not in ["manifest.json", "manifest.json.tmp"] and not path.name.endswith(".tmp")}
        manifest.update(status="completed", completed_at=now())
        write_json(manifest_path, manifest)
        os.replace(work, final)
        print(f"Completed {run_id}: {len(candidates)} review candidates", flush=True)
        return manifest
    except Exception:
        if work.exists() and (work / "manifest.json").exists():
            failed = json.loads((work / "manifest.json").read_text())
            failed.update(status="failed", error=traceback.format_exc())
            write_json(work / "manifest.json", failed)
        raise
    finally:
        lock.unlink(missing_ok=True)
