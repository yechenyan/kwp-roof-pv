"""Command-line entry points; no network inference or LLM integration."""
import argparse
import json
from pathlib import Path
import shutil
import urllib.request
from .io import digest, home
from .imagery import prepare_sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=Path("configs/brandenburg.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan-municipal")
    commands.add_parser("acquire-buildings")
    acquire = commands.add_parser("acquire-municipal")
    acquire.add_argument("--workers", type=int, default=4)
    municipal = commands.add_parser("run-municipal")
    municipal.add_argument("--device", choices=["auto", "cpu", "mps"], default="auto")
    municipal.add_argument("--batch-size", type=int, default=8)
    weights = commands.add_parser("weights")
    weights.add_argument("--from-dir", type=Path)
    sample = commands.add_parser("prepare-sample")
    sample.add_argument("--source", type=Path, required=True)
    sample.add_argument("--output", type=Path, required=True)
    sample.add_argument("--bounds", type=float, nargs=4, required=True)
    sample.add_argument("--acquired", required=True)
    sample.add_argument("--published")
    sample.add_argument("--source-crs")
    sample.add_argument("--crs-reason")
    execute = commands.add_parser("run")
    execute.add_argument("--input", type=Path, required=True)
    execute.add_argument("--name", default="hn-center")
    args = parser.parse_args()
    data_home = (args.home or home()).resolve()
    if args.command == "acquire-buildings":
        from .acquire import acquire_buildings
        print(json.dumps(acquire_buildings(data_home), indent=2))
    elif args.command in ["plan-municipal", "acquire-municipal"]:
        from .acquire import plan, acquire
        result = plan(data_home) if args.command == "plan-municipal" else acquire(data_home, args.workers)
        print(json.dumps({"status": result.get("status", "planned"), "tiles": len(result["tiles"]), "area_m2": result.get("area_m2")}, indent=2))
    elif args.command == "run-municipal":
        from .municipal import municipal_run
        result = municipal_run(data_home, args.config, args.device, args.batch_size)
        print(json.dumps({"run_id": result["run_id"], "counts": result["counts"]}, indent=2))
    elif args.command == "prepare-sample":
        print(json.dumps(prepare_sample(args.source, args.output, args.bounds, args.acquired, args.source_crs, args.crs_reason, args.published), indent=2))
    elif args.command == "weights":
        config = json.loads(args.config.read_text())
        folder = data_home / "models"
        folder.mkdir(parents=True, exist_ok=True)
        for spec in config["models"].values():
            output = folder / spec["checkpoint"]
            if output.exists() and digest(output) == spec["sha256"]:
                print(f"Verified {output.name}")
                continue
            temporary = output.with_suffix(".download")
            if args.from_dir:
                shutil.copyfile(args.from_dir / output.name, temporary)
            else:
                url = f"https://huggingface.co/{config['source']}/resolve/{config['revision']}/{spec['remote_path']}"
                urllib.request.urlretrieve(url, temporary)
            if digest(temporary) != spec["sha256"]:
                temporary.unlink()
                raise ValueError("Downloaded checkpoint SHA-256 mismatch.")
            temporary.replace(output)
            print(f"Verified {output.name}")
    else:
        from .pipeline import run
        result = run(args.input, args.config, data_home, args.name)
        print(result["run_id"])


if __name__ == "__main__":
    main()
