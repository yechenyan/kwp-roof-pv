# Roof PV result atlas

This local delivery scores municipal rooftop-PV observations against technical MaStR records, selects the highest feasible association, exports EPSG:3035 GIS results and produces a PDF report. It is separate from the original human comparison application.

## Open the delivered result

From the repository root:

```sh
python3 -m http.server 8791 --bind 127.0.0.1 --directory packages/result-web-app/delivery
```

Open <http://127.0.0.1:8791/web/>. This included static delivery is ready after cloning and needs only Python's standard HTTP server. It does not require model weights, fresh imagery downloads, inference or the sibling lab checkout. Live WMS imagery requires internet access.

For persistent confidence/reason editing, first follow the rebuild instructions below. The generated `dist/` runtime is local and is not tracked. Then start the annotation server:

```sh
uv run --package roof-pv-review python packages/result-web-app/serve.py --port 8790
```

Open <http://127.0.0.1:8790>. Static hosting cannot save annotations. The annotation server stores edits in `packages/result-web-app/state/annotations.sqlite3`, separate from the production image-review database. Confidence edits never establish verified identity. Each save requires the current annotation version and creates an audit event; personal reviewer data is not collected. The edited GeoPackage download includes current annotations. The original PDF and static GeoPackage remain the original generated snapshot.

Restart the result server after rebuilding. Annotations are bound to the result-record checksum and do not silently migrate to changed associations. Historical edit events remain retained.

## Deliverables

- `dist/roof-pv-report-zh.pdf`: Chinese report from a separate localization resource.
- `dist/roof-pv-report.pdf`: nine-page English data, methodology, model-comparison and quality report.
- `dist/roof-pv-results.gpkg`: EPSG:3035 points, image arrays, statistical building allocations, scored groups, unselected groups, municipality boundaries, technical MaStR tables and normalized storage relationships.
- `dist/index.html`: result atlas entry point; serve over HTTP.
- `dist/results.geojson`: result points for interoperability, in EPSG:4326.
- `dist/summary.json`, `verification.json`, `model-comparison.json`, `browser-verification.json` and `delivery-manifest.json`: metrics, provenance and verification evidence.
- `delivery/`: uncompressed client delivery containing the English project report, `geodata/`, and a standalone read-only `web/` directory. The Chinese report is kept separately under `dist/` and is not included.

The result directory is an explicitly local deliverable. No cloud hosting or publication is required.

## Interpretation

The continuous technical score uses registered module count, power, era and categorical inclination to estimate projected area; building overlap and RGB/RGBI agreement add image evidence. Public coordinates constrain the search when present. No measured facing azimuth, roof-plane slope or postcode geometry is invented. Scores range from 0 to 100 and are not identity probabilities.

Selection uses a disclosed greedy allocation policy: public-coordinate records first, then descending power and stable ID order. The highest-scoring available group is selected, with one registry location per group, shared-location units allowed, a broad 0.5 kW/m2 combined-power screen and minimum score 30. All selected points at the same registry location must have pairwise distances no greater than 100 m, across image and statistical stages. The first selected position anchors the local cluster. Missing location IDs use separate solar IDs. No compatible local candidate means unlocated rather than a distant substitution. The unconstrained best score and feasible alternatives remain available. This is a conservative planning policy, not a global optimizer or independently validated identity match. Some real multi-group installations can remain unmatched.

Later records receive a separate statistical building allocation using remaining roof capacity and compactness, or remain unlocated. An anchored site can reuse its associated building after deducting observed PV area and previous statistical allocations. Allocated building footprints are not observed PV geometry. The rule is more selective than unrestricted random placement, but improvement in exact-location accuracy is unmeasured. Anonymous image links are low confidence; statistical allocations are very low confidence; no automatic result is a verified identity.

Common technical MaStR attributes are expanded into feature columns. Original allowlisted technical fields are also retained in `mastr_attributes` and normalized tables. Operator IDs, installation names, street addresses, source XML and personal reviewer information are excluded. Public installation, technical-location and storage identifiers remain for traceability.

Storage power comes from the storage unit; usable capacity comes from the storage system. Within a PV row, equipment/system IDs are deduplicated. Across rows, storage totals must be deduplicated again by equipment/system ID: a shared registry location does not establish exclusive storage allocation to one PV unit.

## Rebuild

From the repository root, with the frozen survey/registry artifacts and a grouping batch matching the current image-review revision:

```sh
uv run --package roof-pv-review --with reportlab python packages/result-web-app/deliver.py
```

The builder verifies immutable input hashes and opens the source review database read-only. A changed image-review revision is rejected; rebuild the source proposal batch before adapting the pinned batch identifier. Public benchmark results come from the included model archive. The local pilot summary comes from the sibling `lab/review-app` when available, or a previous local `dist/model-comparison.json`; reproducing that section after a clean clone requires the sibling pilot data. The included English PDF already contains the frozen comparison. No LLM call or model inference is used by the result delivery.

Individual operations:

```sh
uv run --package roof-pv-review python packages/result-web-app/build.py
uv run --package roof-pv-review python packages/result-web-app/verify.py
uv run --package roof-pv-review --with reportlab python packages/result-web-app/report.py
uv run --package roof-pv-review --with reportlab python packages/result-web-app/report_zh.py
uv run --package roof-pv-review python packages/result-web-app/package_delivery.py
```

`verify.py` checks real record completeness, point geometry, allocation constraints, GIS round trips, technical field filtering, nonblank live WMS requests and versioned edits/exports in an isolated temporary database. Network/service failures fail verification. The PDF must also be rendered and visually inspected after meaningful report changes. Browser verification is a recorded manual QA snapshot, not an automatically rerun browser test.

## Sources and licence

Imagery and cadastral context: GeoBasis-DE/LGB, dl-de/by-2-0. Registry: Bundesnetzagentur / Marktstammdatenregister, dl-de/by-2-0. Brandenburg model weights: CC-BY-4.0. Optional OpenStreetMap basemap: OpenStreetMap contributors. Leaflet's original licence is retained in `vendor/LEAFLET-LICENSE.txt`.

Chinese report text is stored in `locales/zh-CN-report.json`. Its renderer embeds a Chinese TrueType font discovered at runtime; set `ROOF_PV_REPORT_FONT` when font discovery is unavailable. The 100 m shared-location limit is an allocation consistency rule, not a measured geolocation accuracy. The report notes the separately reviewed manual-geometry rebuild and concurrent-export snapshot limitations; this change addresses location consistency only.
