# Architecture and lifecycle

The current version implements sample and municipal image recognition, manual review, and GIS export. It deliberately leaves MaStR identity relations and storage allocation outside the data model for this release.

## Inference boundary

`roof-pv-model` reads a prepared georeferenced four-band raster, verifies pinned weights, runs CPU inference and publishes immutable artifacts. Torch imports are isolated in inference functions. Completed run IDs derive from input bytes, provenance, source-code hash, parameters and runtime package versions. Run IDs are sample-scoped observation identities, not permanent physical-installation identifiers.

Processing uses overlapping native windows and disk-backed float32 sum/count arrays. Score and mask GeoTIFFs are written blockwise. Candidate vectorization is explicitly bounded to 16 million input pixels and is not advertised as a full-city connected-component algorithm. The separate municipal path implements official-tile mosaicking, a world-anchored window grid, bounded 2,048-pixel processing cores with context, and shared-edge component stitching before minimum-size filtering. Its results are invariant to processing partitions under the synthetic seam tests. Cross-run manual reconciliation is deliberately not automatic.

Each inference model stage is reused only if output checksums agree. A run is staged in `.partial-*`; only successful publication renames it into a visible completed run. An exclusive per-run lock prevents simultaneous writers. A stale lock after a hard kill requires process inspection before removal. Failed and partial runs are not imported into review state.

## Observation and review objects

RGBI and RGB observations are separate polygons. Candidate geometry defaults to RGBI where present; RGB-only objects become additional candidates. Intersections attach supporting observations without geometric union. Many-to-many observation overlap is a geometry conflict, not an inferred physical installation. This release has no facility or registry identity layer.

Manual review modifies a versioned overlay over immutable candidate geometry. A manually drawn candidate starts pending. A checked area records that someone inspected it; it does not assert PV absence. Manual merge/split creates pending descendants and retires its parents without removing their audit history. Undo restores parents while retaining the retired children and event history. Facility and registry identities are outside this release.

## API boundary

`roof-pv-review` reads completed artifacts and owns the SQLite database, review mutations and final export. It does not import `roof_pv`, torch, or segmentation-models-pytorch. A run manifest is hash-pinned at first import; a changed manifest is a conflict. Browser preview assets and exported observations are checked against their stored checksums.

SQLite schema v3 stores runs, candidates, active/retired state, human mounting classification, checked areas and append-only review events. Migrations add fields without overwriting existing decisions. Each write runs in a transaction. Candidate updates require the expected object version; a stale client receives HTTP 409. Review events store before/after values. Every mutation increments the run's review revision.

The default service is loopback-only, without user authentication. Two tabs still receive conflict protection. Do not expose this configuration directly as a shared public service.

## Export boundary

The API reads a single transactional snapshot, then writes and round-trip checks the GPKG outside the read transaction. Each export fixes run ID, run-manifest hash and review revision. Spatial layers are EPSG:3035. Confirmed PV is a separate layer; pending/rejected/uncertain candidates remain visible in the candidates layer. Non-spatial tables contain the source inference manifest, export metadata and review events. Existing exports never change when later reviews change.

Starting a new run does not silently migrate manual decisions or identities. Review data remains attached to its source run. There is no automatic cross-run inheritance in this release.
