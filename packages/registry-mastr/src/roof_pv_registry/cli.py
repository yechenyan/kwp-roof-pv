"""Acquire and normalize an explicit dated MaStR export."""
import argparse
import json
from pathlib import Path
from .source import acquire, DEFAULT_AGS
from .snapshot import normalize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["acquire", "normalize"])
    parser.add_argument("--home", type=Path, default=Path("var"))
    parser.add_argument("--url")
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--ags", nargs="+", default=list(DEFAULT_AGS))
    parser.add_argument("--workers", type=int, choices=[1, 2, 3, 4], default=2)
    args = parser.parse_args()
    if args.command == "acquire":
        if not args.url:
            parser.error("--url is required for a frozen acquisition.")
        folder = acquire(args.home, args.url, args.ags, args.workers)
    else:
        if not args.folder:
            parser.error("--folder is required for normalization.")
        folder = args.folder
    print(json.dumps(normalize(folder)["summary"], indent=2))
