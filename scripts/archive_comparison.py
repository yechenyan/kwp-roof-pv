"""Import frozen experiment evidence as an English, offline comparison archive."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("packages/model-diff"))
    args = parser.parse_args()
    lab, output = args.lab, args.output
    for folder in ["results", "protocol", "provenance", "report/assets"]:
        (output / folder).mkdir(parents=True, exist_ok=True)
    source = lab / "results/benchmark/evaluation.json"
    evaluation = json.loads(source.read_text())
    shutil.copyfile(source, output / "results/evaluation.json")
    for name in ["evaluation_protocol.json", "experiment-plan.json"]:
        shutil.copyfile(lab / "benchmark" / name, output / "protocol" / name)
    for name in ["final_result_audit.json", "evaluator_review.json"]:
        shutil.copyfile(lab / "results/benchmark/qa" / name, output / "provenance" / name)
    (output / "provenance/archive.json").write_text(json.dumps({"schema_version": 1, "source_file": "evaluation.json", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "dataset_sha256": evaluation["dataset_sha256"], "scope": "Frozen historical experiment. GPT entrants are archived evidence only; production does not call LLMs."}, indent=2) + "\n")
    entrants = [{key: value for key, value in entrant.items() if key != "images"} for entrant in evaluation["entrants"]]
    images = []
    for image in evaluation["images"]:
        if image["split"] != "public_test":
            continue
        image_id = image["id"]
        shutil.copyfile(lab / image["rgb_path"], output / f"report/assets/{image_id}.png")
        shutil.copyfile(lab / f"benchmark/reference/masks/{image_id}.png", output / f"report/assets/{image_id}_reference.png")
        records = {}
        for entrant in evaluation["entrants"]:
            record = next((r for r in entrant.get("images", []) if r["id"] == image_id), None)
            if not record:
                continue
            records[entrant["entrant_id"]] = {"boxes": record.get("boxes", []), "status": record["status"]}
            if record.get("mask_path"):
                filename = f"{image_id}_{entrant['entrant_id']}.png"
                shutil.copyfile(lab / record["mask_path"], output / "report/assets" / filename)
                records[entrant["entrant_id"]]["mask"] = filename
        images.append({"id": image_id, "records": records})
    (output / "report/data.js").write_text("window.BENCHMARK = " + json.dumps({"entrants": entrants, "images": images}, separators=(",", ":")) + ";\n")


if __name__ == "__main__":
    main()
