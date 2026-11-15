"""Build a proposal batch from current image review without creating human decisions."""
import argparse
import json
from pathlib import Path
from roof_pv_review.store import Store
from roof_pv_review.matching import MatchingService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path("var"))
    parser.add_argument("--run", default="municipal-27320a7f3fb1")
    parser.add_argument("--output", type=Path, default=Path("docs/matching-delivery.json"))
    args = parser.parse_args()
    store = Store(args.home)
    store.import_runs()
    service = MatchingService(store)
    manifest = service.rebuild(args.run)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
