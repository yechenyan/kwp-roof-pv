"""Build provisional associations from an explicit image-review snapshot."""
import argparse
import json
from pathlib import Path
from roof_pv_registry.snapshot import latest_snapshot
from .engine import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path("var"))
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.home, json.loads(args.snapshot.read_text()), latest_snapshot(args.home))
    print(json.dumps(result["manifest"], indent=2))
