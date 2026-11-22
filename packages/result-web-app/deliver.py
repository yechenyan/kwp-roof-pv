"""Rebuild, verify, report and package the local delivery using one interpreter."""
import argparse
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-wms", action="store_true", help="Verify without contacting live basemap services.")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    for name in ("build.py", "verify.py", "report.py", "report_zh.py", "package_delivery.py"):
        flags = ["--skip-wms"] if name == "verify.py" and args.skip_wms else []
        subprocess.run([sys.executable, str(folder / name), *flags], check=True)
