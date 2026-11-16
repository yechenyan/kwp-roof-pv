# PV array grouping and registry proposals

This package creates immutable proposal batches. It does not automatically verify registry identity or estimate installation power from imagery.

The compact checkout includes the latest municipal batch `matching-1077534f5c31f8b6`, pinned to the delivered run and MaStR snapshot. Its manifest, groups and rankings total about 72 MB, with every file below 100 MB. Superseded batches and acquisition caches are omitted; no rebuild is needed to inspect the included proposals in the web app.

From the repository root, after registry acquisition:

```sh
uv run --package roof-pv-review python scripts/build_mastr_proposals.py
```

For a separately exported full review snapshot:

```sh
uv run --package roof-pv-matching roof-pv-match --snapshot var/review-snapshot.json
```

Groups conservatively connect nearby regions on the same cadastral geometric part. The initial 2 m distance and 20% building-overlap rules are experimental proposal settings, not validated physical-installation boundaries. Manual candidate merges/splits remain available in the review app. Region membership, versions and original observations are retained.

Ranking compares public coordinates when available, broad projected-area intervals derived from registered module count/power and uncertain slope, municipality membership and image/commissioning dates. Correlated area features are disclosed. Facing azimuth and postcode geometry are not currently measured. Unknown, suspect or absent inputs retain uncertainty. No postcode-centroid coordinates are invented.

Each record retains alternatives and an unmatched outcome. Ties remain explicit; scores are not probabilities. Coordinate conflicts, later commissioning, context records and unavailable evidence remain visible. There is no one-to-one assignment constraint and no automatic acceptance threshold. Small-unit identity accuracy is unmeasured until independently verified cases exist.

The review backend owns human identity decisions separately from PV-presence review. Independently verified identity requires supported evidence and rooftop confirmation of the proposed group members. Image edits make affected proposals stale; new batches preserve historical identity decisions without automatically migrating them. GeoPackage exports distinguish image groups, proposals, reviewed associations and current verified identities.
