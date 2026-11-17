"""Rebuild, verify, report and package the local delivery using one interpreter."""
import subprocess
import sys
from pathlib import Path

folder=Path(__file__).resolve().parent
for name in ["build.py","verify.py","report.py","report_zh.py","package_delivery.py"]:
    subprocess.run([sys.executable,str(folder/name)],check=True)
