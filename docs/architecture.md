# Architecture and lifecycle

The current version implements sample and municipal image recognition, manual PV review, frozen MaStR imports, provisional association rankings, independent identity review and GIS export. Exclusive storage allocation and automatic identity acceptance are outside the implementation.

## Inference boundary

`roof-pv-model` reads a prepared georeferenced four-band raster, verifies pinned weights, runs CPU inference and publishes immutable artifacts. Torch imports are isolated in inference functions. Completed run IDs derive from input bytes, provenance, source-code hash, parameters and runtime package versions. Run IDs are sample-scoped observation identities, not permanent physical-installation identifiers.

Processing uses overlapping native windows and disk-backed float32 sum/count arrays. Score and mask GeoTIFFs are written blockwise. Candidate vectorization is explicitly bounded to 16 million input pixels and is not advertised as a full-city connected-component algorithm. The separate municipal path implements official-tile mosaicking, a world-anchored window grid, bounded 2,048-pixel processing cores with context, and shared-edge component stitching before minimum-size filtering. Its results are invariant to processing partitions under the synthetic seam tests. Cross-run manual reconciliation is deliberately not automatic.

Each inference model stage is reused only if output checksums agree. A run is staged in `.partial-*`; only successful publication renames it into a visible completed run. An exclusive per-run lock prevents simultaneous writers. A stale lock after a hard kill requires process inspection before removal. Failed and partial runs are not imported into review state.

## Observation and review objects

RGBI and RGB observations are separate polygons. Candidate geometry defaults to RGBI where present; RGB-only objects become additional candidates. Intersections attach supporting observations without geometric union. Many-to-many observation overlap is a geometry conflict, not an inferred physical installation. Proposed array groups reference versioned candidate members; they do not replace source observations or assert a physical installation boundary.

Manual review modifies a versioned overlay over immutable candidate geometry. A manually drawn candidate starts pending. A checked area records that someone inspected it; it does not assert PV absence. Manual merge/split creates pending descendants and retires its parents without removing their audit history. Undo restores parents while retaining the retired children and event history. Registry identity requires a separate evidence-bearing decision.

## API boundary

`roof-pv-review` reads completed artifacts and owns the SQLite database, review mutations and final export. It does not import `roof_pv`, torch, or segmentation-models-pytorch. A run manifest is hash-pinned at first import; a changed manifest is a conflict. Browser preview assets and exported observations are checked against their stored checksums.

SQLite schema v4 preserves the v3 image tables and adds independent registry identity reviews and append-only identity events. Migrations do not overwrite existing decisions. Each write runs in a transaction. Candidate and identity updates require their own expected versions; a stale client receives HTTP 409. PV mutations increment the image review revision. Identity decisions have a separate event sequence and cannot mark image candidates confirmed.

The UI initially receives a compact overview containing explicitly labelled bounding envelopes. These are navigation geometry, not edit geometry. Exact candidate detail is fetched on selection. STRtree queries return bounded candidate/building/model geometry for the viewport; hidden model layers are not downloaded. The overview uses the frozen image preview; native XYZ tiles appear at closer zoom. Revision caches, cancellation, move-end debouncing, Canvas paths and in-place opacity updates avoid repeated full-city downloads and redraws.

## Registry and matching boundary

`roof-pv-registry` reads dated official ZIP members, selects by AGS, verifies compressed data and retains original fields, catalogs and source provenance. Empty location IDs never join to one another. Supported storage relationships require reverse links and remain non-exclusive. External units referenced by a retained location but outside the selected AGS scope are not silently assumed present in the imported unit tables.

`roof-pv-matching` writes immutable batches under `var/matching/`. Each batch pins the image manifest, review revision, registry manifest, matcher source hash and experimental configuration. Region groups and ranking alternatives remain proposals; tied scores and full search-pool sizes are explicit. Changing image members invalidates affected groups. New batches do not inherit old identity decisions automatically. Verified identity requires independent evidence, a reviewer confirmation of grouping and rooftop-PV confirmation for every member.

The default service is loopback-only, without user authentication. Two tabs still receive conflict protection. Do not expose this configuration directly as a shared public service.

## Export boundary

The API reads a version-consistent image/identity snapshot, then writes and round-trip checks the GPKG. Exports fix run ID, image manifest/revision, registry snapshot, matching batch and identity event revision. Spatial layers are EPSG:3035. Confirmed PV and verified MaStR identity are separate layers. Stale identity decisions remain in audit tables and are excluded from verified geometry. Registry, unmatched outcomes, proposals and storage relationships are retained as separate attribute tables. Existing exports never change when later reviews change.

Starting a new run does not silently migrate manual decisions or identities. Review data remains attached to its source run. There is no automatic cross-run inheritance in this release.
