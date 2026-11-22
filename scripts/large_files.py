"""Split large var artifacts for Git, or restore their exact original bytes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "var/large-files.json"
CHUNK_SIZE = 95_000_000


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def split():
    if MANIFEST.exists():
        raise FileExistsError("Split manifest already exists; restore or review it first")
    paths = sorted(p for p in (ROOT / "var").rglob("*")
                   if p.is_file() and p.suffix != ".pt" and p.stat().st_size > 100_000_000)
    records = []
    for path in paths:
        parts = []
        with path.open("rb") as source:
            while chunk := source.read(CHUNK_SIZE):
                part = path.with_name(f"{path.name}.part{len(parts) + 1:03d}")
                with part.open("xb") as output:
                    output.write(chunk)
                parts.append({"path": part.relative_to(ROOT).as_posix(),
                              "size": len(chunk), "sha256": digest(part)})
        combined = hashlib.sha256()
        for part in parts:
            with (ROOT / part["path"]).open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    combined.update(chunk)
        original_hash = digest(path)
        if combined.hexdigest() != original_hash:
            raise ValueError(f"Split verification failed: {path.relative_to(ROOT)}")
        records.append({"path": path.relative_to(ROOT).as_posix(),
                        "size": path.stat().st_size, "sha256": original_hash,
                        "parts": parts})
        print(f"Verified {path.relative_to(ROOT)}: {len(parts)} parts")
    MANIFEST.write_text(json.dumps({"version": 1, "chunk_size": CHUNK_SIZE,
                                    "files": records}, indent=2) + "\n", encoding="utf-8")


def local_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to((ROOT / "var").resolve()):
        raise ValueError(f"Artifact path must stay inside var: {relative}")
    return path


def restore():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for record in manifest["files"]:
        target = local_path(record["path"])
        if target.exists():
            if target.stat().st_size != record["size"] or digest(target) != record["sha256"]:
                raise ValueError(f"Existing file differs; refusing to overwrite: {record['path']}")
            print(f"Verified existing {record['path']}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                for part in record["parts"]:
                    path = local_path(part["path"])
                    part_hash = hashlib.sha256()
                    size = 0
                    with path.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                            part_hash.update(chunk)
                            size += len(chunk)
                    if size != part["size"] or part_hash.hexdigest() != part["sha256"]:
                        raise ValueError(f"Part verification failed: {part['path']}")
            if temporary.stat().st_size != record["size"] or digest(temporary) != record["sha256"]:
                raise ValueError(f"Restored file verification failed: {record['path']}")
            os.link(temporary, target)
            print(f"Restored {record['path']}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["split", "restore"])
    args = parser.parse_args()
    {"split": split, "restore": restore}[args.action]()
