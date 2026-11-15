# Roof PV

Production repository for Brandenburg PV image recognition and human review. The repository includes the real Hohen Neuendorf sample and the full image-survey pipeline for Hohen Neuendorf and Birkenwerder.

## Delivered scope

- Python inference using pinned Brandenburg DeepLabV3+ / RegNetY-080 RGBI and RGB weights.
- Official municipal boundaries, paginated ALKIS footprints, original RGBI tile acquisition, and resumable municipal inference.
- Independent model observations and explicit agreement/conflict candidates; no automatic geometry union.
- React + TypeScript + Leaflet map, with RGB/CIR displays of the actual inference image snapshot.
- Confirm, reject, uncertain, reset, vertex editing, missed-PV polygon drawing, checked-area recording, manual merge/split and undo.
- SQLite review persistence, optimistic version checks, and before/after audit events.
- Version-pinned GeoPackage exports in EPSG:3035, including confirmed PV, confirmed rooftop PV, interior-point layers and an offline HTML report.
- English archive of the completed multi-model experiment in `packages/model-diff`.

No MaStR unit matching, power estimation, battery association, LLM calls, or model retraining is included. Detection does not itself establish roof mounting. The sample CLI accepts verified four-band samples up to 16 million pixels. The municipal CLI uses bounded processing cores, a shared world grid, cross-source context and cross-core component stitching. Independent local accuracy annotation remains a separate human task.

## Current local delivery

The completed run is recorded in [municipal-delivery.json](docs/municipal-delivery.json): 6,355 candidates, 471 processing blocks, and zero missing target pixels. The initial GeoPackage and offline HTML report are in `var/exports/`. The review workspace is served at <http://127.0.0.1:8780>. See [quality notes](docs/quality-notes.md) and [independent verification](docs/municipal-verification.json). All initial candidates remain unreviewed.

## Setup

Use Python 3.12, uv, Node.js 22 or later, and pnpm 9.0.2. Run commands from the repository root. Paths are relative; `--home`, `--config`, `--frontend`, and `ROOF_PV_HOME` allow explicit locations from another working directory. `.env.example` documents variables; no automatic dotenv loading is assumed.

```sh
uv sync --all-packages --all-extras
pnpm install --frozen-lockfile
pnpm build
uv run --package roof-pv-review roof-pv-review --port 8780
```

Open <http://127.0.0.1:8780>. The English experiment archive is at <http://127.0.0.1:8780/comparison/>. Initial candidates are all pending; test decisions are never inserted into production state.

For frontend development, start the API as above and run `pnpm dev` in a second terminal. The Vite development server proxies `/api` to port 8780. The service binds only to loopback and has no multi-user authentication. Reviewer names are attribution, not authenticated identities. SQLite is intended for local/small-scale review.

## Full municipal survey

The official target boundaries cover approximately 66.65 km². The current frozen plan has 99 one-kilometre RGBI source tiles, including context beyond the administrative boundary. The cadastral extraction contains 25,813 building/construction footprints intersecting the target municipalities, after paginating 45,829 source features from the bounding rectangle. Source metadata and hashes are in `var/data/municipal/`.

```sh
uv run --package roof-pv-model roof-pv weights
uv run --package roof-pv-model roof-pv acquire-municipal --workers 2
uv run --package roof-pv-model roof-pv acquire-buildings
uv run --package roof-pv-model --extra inference roof-pv run-municipal --device auto --batch-size 8
```

`auto` selects MPS when available and CPU otherwise. The effective CPU batch size is one. Parameters and device are part of the run identity. The workspace shows source acquisition and inference progress. Only complete runs appear in the review selector. Refresh the run list after processing finishes; existing review decisions stay attached to their original run.

Municipal imagery is served as local RGB/CIR XYZ tiles from the frozen source mosaic. The overview shows candidate grid clusters; zoom to level 16 or closer for outlines and cadastral footprints. Rendering is bounded to the viewport and the queue loads 100 rows at a time. The source mosaic uses relative file references; preserve `var/data/municipal/tiles/` with its run artifacts.

Treat an acquired source namespace as immutable. For a future imagery epoch, use a separate `--home` or `ROOF_PV_HOME` rather than overwriting files referenced by an older run. No automatic migration of review decisions is performed.

## Reproduce the real sample

The sample is 800 × 800 pixels, 160 × 160 m, from tile `33383-5837`. The imagery was captured on 2026-04-22 and published on 2026-10-09. See `configs/hn-center.json` and the prepared image's provenance sidecar.

The current workspace has local weights and input files in ignored `var/`. To set up another checkout, download and extract the official [LGB RGBI product](https://data.geobasis-bb.de/geobasis/daten/dop/rgbi_tif/dop_33383-5837.zip) into `var/data/source/`, then:

```sh
uv run --package roof-pv-model roof-pv weights
uv run --package roof-pv-model roof-pv prepare-sample \
  --source var/data/source/dop_33383-5837.tif \
  --output var/data/hn-center-v1.tif \
  --bounds 383487 5837096 383647 5837256 \
  --acquired 2026-04-22 --published 2026-10-09 \
  --source-crs EPSG:25833 \
  --crs-reason 'ETRS89 / UTM 33N verified against the LGB article, tile coordinates and world file; source CRS metadata is assigned explicitly.'
uv run --package roof-pv-model --extra inference roof-pv run \
  --input var/data/hn-center-v1.tif --name hn-center
```

`weights` can also take `--from-dir` to copy previously downloaded checkpoints after SHA-256 validation. Existing prepared outputs are not overwritten. Identical completed runs are verified and reused. Interrupted runs resume only checksum-verified completed model stages. After a hard process kill, inspect the run process before removing any stale `.lock-*` file in `var/runs/`.

The repository's wheel-bundled PROJ database is selected at runtime to avoid conflicts with unrelated GIS installations. The original TIFF is never modified. Input must be uint8 R,G,B,NIR at 20 cm with an explicit acquisition date. CIR is not accepted as four-band RGBI.

## Storage and ownership

```text
packages/model-Brandenburg  Python batch inference; immutable run artifacts
packages/model-diff        English frozen experiment archive; no runtime dependency
packages/web-app/backend   FastAPI, SQLite review state, final GeoPackage exports
packages/web-app/frontend  React review interface; no inference execution
contracts/                 Versioned JSON schemas
configs/                   Pinned model parameters and sample definition
var/data/                  Prepared inputs and provenance
var/models/                Checksum-verified model checkpoints
var/runs/                  Immutable completed runs; partial runs are hidden
var/state/                 SQLite database (back up with SQLite backup tools)
var/exports/               Immutable GPKG snapshots and checksum manifests
```

The API has no dependency on the model package or PyTorch. It consumes the run-file contract. `localStorage` holds only an optional reviewer-name preference; all review decisions are server-side. Back up `var/state/` consistently and preserve the referenced immutable run directories together.

## Validation

```sh
uv run --all-packages --all-extras pytest -q
pnpm test
pnpm build
uv run --no-sync python scripts/verify_sample.py
uv run --no-sync python scripts/verify_municipal.py
```

Python tests cover blending boundaries/NoData, input validation, model-evidence grouping, review persistence, stale writes, geometry editing, manual additions, inspected areas, export snapshots and all spatial layer CRS values. React tests exercise user decisions, error handling, language/filtering, and export actions in a DOM environment. They do not constitute browser visual verification or a local accuracy evaluation.

API request types are generated from OpenAPI with `pnpm api:types`.

See `docs/architecture.md`, `docs/methodology.md`, and `docs/review-guide.md` for object semantics and review instructions.

## Sources

- [Brandenburg model and weights](https://huggingface.co/Muemmel/solar-pv-segmentation-brandenburg), revision pinned in `configs/brandenburg.json`, CC-BY-4.0.
- Imagery © GeoBasis-DE/LGB, [dl-de/by-2-0](https://www.govdata.de/dl-de/by-2-0). Crops, previews and overlays are derived.
- Optional basemap © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright). The core imagery review works without external map tiles.
- Python workspace follows [uv workspace documentation](https://docs.astral.sh/uv/concepts/projects/workspaces/); the API serves the built frontend using [FastAPI StaticFiles](https://fastapi.tiangolo.com/tutorial/static-files/).
