# MaStR proposal workflow delivery

The local application is available at <http://127.0.0.1:8780>. Open **MaStR comparison** to inspect registry values, image alternatives, public coordinate markers, proposed array outlines and separate identity-review forms. Existing PV decisions and source inference artifacts have been preserved.

## Frozen data and initial results

| Measure | Value |
| --- | ---: |
| Registry source date | 2026-11-15 |
| Solar/storage/location/system/catalog source members | 198, all acquired |
| Solar records | 3,476 |
| Storage units / systems | 1,971 / 1,971 |
| Technical locations | 3,476 |
| Strict-under-30 active anonymous building PV | 2,318 |
| Exactly-30 additional targets | 2 |
| Image candidate regions | 6,355 |
| Proposed array groups | 5,038 |
| Groups containing multiple regions | 870 |
| Supported solar/storage location relationships | 1,795 |
| Unique storage units in supported relationships | 1,688 |
| Human PV confirmations / identity decisions | 0 / 0 |

The source has one additional solar record and one additional storage record relative to the retained 2026-11-14 baseline. Retained source CRC metadata, compressed-member hashes, XML fragments and normalized tables were checked. The current normalized records also agree with the current normalization code.

The baseline diagnostic's naive missing-location grouping created 2,750 false pair combinations: 55 solar records times 50 storage records without a location ID. Missing IDs are now excluded before joining. The corrected baseline contains 1,794 supported pairs; the new snapshot has 1,795. This was an audit-join issue, not evidence of thousands of conflicting physical locations. No spurious pairs were allocated to buildings.

## What the proposals establish

The initial grouping uses nearby regions on the same cadastral geometric part, retaining member IDs and versions. It is a proposal for review, not 5,038 independently verified physical installations. Original RGBI/RGB outputs remain intact. No LLM or new recognition model was introduced.

For the 2,320 anonymous targets including exactly 30 kWp, the best displayed proposal states are 2,167 ambiguous and 153 post-capture. The latter is relative to the proposed group's capture date, not a known installation position. All 2,320 targets have tied best scores under the current broad area/spatial/temporal evidence. Five displayed alternatives are examples, not exhaustive unique answers; the UI exposes the full search-pool and tied-best counts.

This result does **not** establish successful building-level geolocation of anonymous units. No automatic identity was accepted. Panel-facing azimuth, actual roof-plane slope and postcode boundaries are not available as measured image evidence, and the public target records lack addresses/coordinates. Stronger independently verified location information or additional measured features is needed to establish unique identities. Small-unit matching accuracy remains unmeasured.

The workflow nevertheless supports inspecting coordinate-bearing anchors, correcting array groups and images, comparing registry records, saving versioned human evidence, retaining uncertainty and exporting source-consistent results. Ground/other mounting reviews are excluded from rooftop registry ranking. Later commissioning remains an evidence queue rather than forced old-image assignment or a declaration of absence.

## Application behavior

- Enter the reviewer name in the top context bar.
- Open the registry comparison and filter target, coordinate-bearing or all records. Search by ID, municipality or postcode.
- Select an image alternative or a map candidate. Proposed array boundaries and existing candidate outlines stay distinct. The public registry point has its own marker and locate button.
- Confirm image PV presence and rooftop mounting separately. Correct group membership with candidate merge/split/edit operations as needed, then rebuild proposals.
- Save an ambiguous/probable identity judgement with a reason, or a verified identity with independent evidence and confirmed grouping. The server enforces versions and rejects stale source/member states.
- Export the current snapshot using the existing GPKG button. Unmatched units are retained; shared storage is not allocated repeatedly to PV records.

## GIS and report artifacts

Initial export: `var/exports/municipal-27320a7f3fb1-r0-925fad1b.gpkg` (109,494,272 bytes).

SHA-256: `f1d49c71ea84abe439779c77deb3d59cffefe5eb4fcba90ef816190e9aba17e6`.

The same basename `.html` is the offline English report. The `.json` contains export metadata. The running service exposes the [report](http://127.0.0.1:8780/api/exports/municipal-27320a7f3fb1-r0-925fad1b/report) and [GeoPackage download](http://127.0.0.1:8780/api/exports/municipal-27320a7f3fb1-r0-925fad1b.gpkg).

All 16 spatial layers round-trip as valid EPSG:3035 geometries. Registry unit tables, unmatched outcomes, proposals, identity review/audit tables, source provenance and non-exclusive storage relationships are separate attribute tables. Verified MaStR geometry layers are empty because no real identity review has been completed. One derived pixel-coverage geometry undergoes the previously documented tiny-area topology normalization; PV/group geometries require no export repair.

The GPKG, source imagery, weights, registry data, SQLite state and intermediate batches remain under ignored `var/`. No Git-eligible file exceeds 100 MB, and machine-specific paths are not embedded in project files.

## Performance and verification

The frontend now loads labelled navigation envelopes and fetches original geometry only when selected. Candidate/building/model layers use bounded spatial queries after move-end debouncing; hidden model layers are not prefetched. Overviews use the frozen preview image, closer views use XYZ tiles, Canvas renders geometry, and opacity changes do not recreate the image layer. Requests are cancelled when superseded and background polling pauses in hidden tabs.

Local HTTP measurements: initial geometry payload falls from 117.27 MB across the previous eager requests to 3.83 MB for the overview (96.73% reduction). The overview is approximately 578 KB on the wire with gzip. Three-sample warm median response time is approximately 108 ms for the overview and 10 ms for the example viewport. These are local API measurements; they exclude image transfers and actual browser rendering and do not promise end-user page-load times.

Validation passed: 43 Python tests, 10 React interaction tests, TypeScript/Vite build, retained source/XML checks, proposal coverage and version checks, GIS round trips, and live HTTP endpoints. Human decisions in tests are synthetic fixtures in isolated temporary stores; production remains pending. A full visual browser review has not been performed.

Evidence: [matching batch](matching-delivery.json), [real-data verification](mastr-workflow-verification.json), [HTTP checks](mastr-http-verification.json), [export metadata](mastr-export-delivery.json), [performance measurements](web-performance.json). The accepted design is in [matching plan](mastr-matching-plan.md); package usage is in [registry-mastr](../packages/registry-mastr/README.md) and [matching](../packages/matching/README.md).
