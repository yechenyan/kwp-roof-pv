# Municipal delivery quality notes

The delivered survey covers the official Hohen Neuendorf and Birkenwerder boundary union (approximately 66.65 km²). It uses 99 original RGBI tiles, captures dated 2026-04-22 and 2026-05-01, and 25,813 official contextual building footprints.

There are 6,355 machine review candidates, not 6,355 validated installations. Initial review revision is zero. No MaStR matching, installed-power inference or storage allocation is performed. Independent human ground truth is not available; no municipal precision, recall or pixel-IoU claim is made.

The independent consumer verified all source/artifact hashes, all 471 block score/mask pairs, target coverage counts, real-data review persistence, stale-write rejection, merge/undo, RGB/CIR imagery, and GeoPackage/HTML downloads in an isolated test database. No test decisions entered production state.

All PV candidates, model observations and cadastral geometries were valid. One derived pixel-coverage geometry was self-intersecting. Export compares topology normalization methods, rejects substantive area changes, and records the chosen method, original validity reason and area delta. The delivered coverage used zero-buffer normalization with an area change of approximately 0.000095 m². Source rasters, model outputs and their hashes are unchanged.

All exported spatial layers are valid in EPSG:3035. Candidate and confirmed-rooftop point layers use interior representative points of current geometry. Roof mounting is a separate human classification; cadastral overlap is supporting evidence. Browser visual verification was not performed. Functional API, DOM-interaction, geospatial, build and HTTP checks passed.

See `municipal-verification.json`, `municipal-http-verification.json`, `accelerator-verification.json` and `municipal-delivery.json` for evidence and exact artifact identifiers.
