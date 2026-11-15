"""Export API schemas without importing production runs or review state."""
import argparse
import json
from pathlib import Path
import tempfile
from roof_pv_review.app import create_app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=Path("var/openapi.json"))
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix="roof-pv-openapi-") as directory:
    schema = create_app(Path(directory), Path(directory) / "no-frontend").openapi()
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(schema, indent=2) + "\n")
