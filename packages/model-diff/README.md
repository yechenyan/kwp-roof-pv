# Frozen model comparison

This package is an English archive of the completed experiment, not a production dependency.
Open `report/index.html` directly, or visit `/comparison/` on the local review server.

- `results/evaluation.json`: unchanged machine-readable scores and individual predictions.
- `protocol/`: original frozen evaluation rules and experiment plan.
- `provenance/`: independent audits and source checksums.
- `report/`: offline English tables and visual comparison for all eight public crops.

The historical GPT Sol/Terra results used PI subscription access. They are retained for transparency. No GPT or other LLM is installed, connected, or called by the production pipeline.

There are 10 reference components on eight public crops. One image contains 89.28% of positive pixels. Public source scenes were selected around plants of at least 30 kWp. These scores do not establish accuracy on small residential PV in the two municipalities. RGBI is an enhanced-input track and is reported separately from RGB-only inputs. Equal weighting of positive images reverses the RGB/RGBI pixel-IoU ordering. SAM 3 was blocked, not measured as zero.

Regenerate the archive from the original local experiment, if available:

```sh
uv run python scripts/archive_comparison.py --lab ../lab
```

Imagery: GeoBasis-DE/LGB, dl-de/by-2-0. Displayed masks and overlays are derived data. Refer to each model's pinned source and license in the archived evaluation.
