# Roof PV result atlas

The building inventory uses the frozen Hohen Neuendorf and Birkenwerder survey and MaStR snapshot. The delivered atlas opens on published joint matches and public-coordinate associations. The original image-review service keeps its own database and workflow.

## Open the delivered atlas

Run from the repository root:

```sh
python3 -m http.server 8791 --bind 127.0.0.1 --directory packages/result-web-app/delivery
```

Open <http://127.0.0.1:8791/web/>. Search by MaStR ID or postcode and open a result to see its matched panels and evidence. The tracked `delivery/` contains the German method guide, the standalone `web/` atlas, the EPSG:3035 GeoPackage in `geodata/`, and the verification and checksum manifests. Live Brandenburg imagery and optional OpenStreetMap tiles require network access.

## Rebuild the delivery

Use the sibling `lab-map` workspace with the frozen guide inputs, including the example's aerial tile and LoD2 archive. Run from this repository root.

```sh
uv run --locked --package roof-pv-review python packages/result-web-app/build.py
uv run --locked --package roof-pv-review python packages/result-web-app/verify.py
uv run --locked --package roof-pv-review --with reportlab --with jinja2 --with rasterio python ../lab-map/roof-pv-guide/render_pdf.py --output packages/result-web-app/dist/roof-pv-guide-de.pdf
uv run --locked --package roof-pv-review python packages/result-web-app/package_delivery.py --tracked
```

Packaging checks that the guide, rankings and GIS data come from the verified build. Pass `--skip-wms` to `verify.py` for verification without live imagery checks.

For a local candidate-review preview with the English and Chinese inventory PDFs, run `uv run --locked --package roof-pv-review --with reportlab python packages/result-web-app/deliver.py`. It writes `delivery-v4/`. Set `ROOF_PV_REPORT_FONT` to an embeddable Chinese TrueType font if automatic font discovery fails.

### Local annotations

After the build, run the annotation service and open <http://127.0.0.1:8790>:

```sh
uv run --locked --package roof-pv-review python packages/result-web-app/serve.py --port 8790
```

Annotations are stored in `state/annotations.sqlite3` and bound to the generated record checksum. Restart the service after a rebuild. Changes to confidence or reasons retain version history. An annotation changes neither the candidate rank nor verified identity.

## Inventory, ranking and export

- `observed_pv_buildings` contains one union of roof-supported detection geometry per ALKIS ID. Source group IDs, capture dates and projected area are retained.
- `observed_pv_roof_faces` contains each usable LoD2 face intersection. The estimated surface area assumes panel alignment with the roof and uses a projected-area fallback when the roof geometry or mounting is uncertain. The building layer records the applicable area method. Tilt and azimuth describe the roof face.
- `registry_by_postcode` maps registry totals by municipality and official LGB postcode. `registry_postcode_totals` retains totals even where a postcode has no mapped area. The review atlas and inventory reports default to the active anonymous building-PV cohort below 30 kW. The GeoPackage also gives the full registry counts and power. Later records enter the postcode figures.
- The atlas scores observed buildings in the registered municipality and assigned LGB postcode. Each building's full PV union is scored once for area, direction and tilt. A module count and power estimate support the area comparison. The frozen evidence lacks an independent installation window for the full inventory, so timing adds zero points. Projected comparisons account for the available registry tilt range.

- Within the geographic pool, all roofs scoring at least 42 points appear as candidates. The total is the only selection criterion. Strong direction and tilt discrepancies reduce the score, including when one of two comparable faces disagrees. When no roof reaches the threshold, a collapsed review panel can show and map up to five highest-scoring roofs. An empty geographic pool stays unlocated. The GIS table `registry_building_candidates` keeps the first five ranks, and `registry_outcomes` carries eligible and qualified counts. Its `projected_score` column retains the prior engineering comparison for reference. Only coordinate-bearing records retain selected point proposals.

The technical allowlist, original group evidence and non-exclusive storage relationships remain in the GeoPackage. Storage may be shared by registry records and must be deduplicated by equipment or system ID before totals are calculated across PV rows.

### Joint proposals

The candidate review highlights at most one joint proposal per eligible anonymous registration. The purple marker and list badge identify that proposal, while candidate ranks retain their individual scores. The CP-SAT model maximizes combined evidence above the threshold, with proposal count as a tie-breaker. Each building's PV union can serve at most one joint proposal. The allocation observes roof power and projected-area budgets and limits shared locations to a 100-metre span. An existing coordinate-bearing claim blocks any building PV union that would reuse its arrays. Solver diagnostics are recorded in the summary. The GeoPackage exposes the proposals in `registry_joint_proposals` for joining to building polygons.

## Inputs and verification

The build verifies the pinned image and registry manifests and reads the source review database in read-only mode. The bundled [geometry evidence](evidence/README.md) identifies the official LGB inputs, LoD2 archives and frozen group/footprint hashes. The map data, GeoPackage and inventory reports can be built from the bundled inputs.

`verify.py` checks each roof-supported group is counted once, building and roof areas reconcile, candidate postcodes and municipalities agree, registry counts and capacity roll up exactly, and GIS layers round-trip in EPSG:3035. The annotation and edited-export checks run against an isolated database. The live imagery checks can be run separately from the frozen data checks. Packaging checks the verified web and GIS files and the fingerprint of each PDF before releasing the new directory.
