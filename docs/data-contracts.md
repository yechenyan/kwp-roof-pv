# File and API contracts

Version 1 source schemas are in `contracts/`. A completed inference run contains `manifest.json`, the input raster snapshot, float32 score GeoTIFFs, uint8 raw mask GeoTIFFs, separate RGB/RGBI observation GeoJSON, candidate GeoJSON, and RGB/CIR Web-Mercator RGBA previews. Municipal runs also include relative image/score VRT mosaics, per-core score/mask rasters, pixel-coverage geometry, official buildings and boundary context. Source imagery is referenced by portable paths and verified hashes. Manifest artifact paths are run-relative file names with SHA-256 checksums; machine-specific absolute paths are not persisted.

GeoJSON follows RFC 7946 longitude/latitude order. IDs are scoped to a run. Candidate properties include source observation IDs and routing category. Review-only properties include status, reasons, notes, reviewer, version, updated time, original geometry and whether geometry was overridden. No candidate is a claimed MaStR unit or physical facility identity.

The API publishes machine-readable OpenAPI at `/openapi.json` and interactive documentation at `/docs`. API request types are generated into the frontend from OpenAPI using `pnpm api:types`. GeoJSON artifact types follow the versioned file contracts.

- `GET /api/runs`: completed imported runs.
- `GET /api/runs/{run_id}`: a transactional state snapshot.
- `GET /api/runs/{run_id}/assets/{asset}`: checksum-verified preview or model GeoJSON.
- `PATCH /api/runs/{run_id}/candidates/{candidate_id}`: expected-version review update.
- `POST /api/runs/{run_id}/candidates`: add a pending manual candidate.
- `POST /api/runs/{run_id}/reviewed-areas`: record an inspection polygon.
- `POST /api/runs/{run_id}/transform`: manual merge or line-based split, with parent versions.
- `POST /api/runs/{run_id}/undo`: revision-checked undo of the latest supported action.
- `GET /api/runs/{run_id}/tiles/{display}/{z}/{x}/{y}.png`: frozen RGB/CIR imagery.
- `GET /api/jobs`: acquisition and inference progress.
- `POST /api/runs/{run_id}/exports`: create a snapshot GeoPackage.
- `GET /api/exports/{filename}`: checksum-verified download.

Schemas are checked against the actual delivered run by `scripts/verify_sample.py` and `scripts/verify_municipal.py`. The API additionally validates geometry, review status, reason requirements and expected versions. The backend is the sole owner of export semantics.
