"""Compare the actual RGB/RGBI checkpoints on CPU and MPS using real pixels."""
import argparse
import json
from pathlib import Path
import roof_pv
import numpy as np
import rasterio
from rasterio.windows import Window
import torch
from roof_pv.inference import load_model
from roof_pv.io import digest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--input", type=Path, default=Path("var/data/hn-center-v1.tif"))
parser.add_argument("--home", type=Path, default=Path("var"))
parser.add_argument("--output", type=Path, default=Path("docs/accelerator-verification.json"))
args = parser.parse_args()
if not torch.backends.mps.is_available():
    raise SystemExit("MPS is not available; retain CPU execution on this machine.")
config = json.loads(Path("configs/brandenburg.json").read_text())
with rasterio.open(args.input) as source:
    window = Window((source.width - 256)//2, (source.height - 256)//2, 256, 256)
    pixels = source.read(window=window)
records = []
for variant, spec in config["models"].items():
    channels = len(spec["channels"])
    model = load_model(args.home / "models" / spec["checkpoint"], spec["sha256"], channels)
    mean = np.asarray(config["mean"][:channels], np.float32)[:, None, None]
    std = np.asarray(config["std"][:channels], np.float32)[:, None, None]
    tensor = torch.from_numpy((pixels[:channels].astype(np.float32)/255 - mean)/std).unsqueeze(0)
    with torch.inference_mode():
        reference = torch.sigmoid(model(tensor)[:, 1]).numpy()
        model.to("mps")
        accelerated = torch.sigmoid(model(tensor.repeat(8,1,1,1).to("mps"))[:, 1]).cpu().numpy()
    delta = float(np.max(np.abs(accelerated - reference)))
    changed = int(np.count_nonzero((accelerated >= .5) != (reference >= .5)))
    assert delta < .001 and changed <= accelerated.size * .001
    records.append({"variant": variant, "checkpoint_sha256": spec["sha256"], "cpu_batch": 1, "mps_batch": 8, "max_absolute_score_difference": delta, "threshold_pixel_differences": changed, "compared_pixels": int(accelerated.size)})
    del model
report = {"status": "passed", "source_sha256": digest(args.input), "window": [int(window.col_off),int(window.row_off),256,256], "checks": records, "scope": "Numerical agreement on a real-image probe, not a detection accuracy evaluation."}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
