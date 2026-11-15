# Recognition methodology

## Fixed inference recipe

Both models use SMP DeepLabV3Plus with the RegNetY-080 encoder, strict checkpoint state loading and pinned SHA-256 checks. RGBI uses `regnety_rgbi_s42`; RGB uses `regnety_rgb_s44`. The same prepared TIFF supplies both inputs, so RGB rendering is identical between tracks.

- Native window: 256 pixels; requested overlap: 24 pixels; final windows shift to the image boundary.
- Pixel size: 0.2 m; values: uint8 divided by 255.
- RGB mean/std: `[0.485, 0.456, 0.406]` / `[0.229, 0.224, 0.225]`.
- NIR mean/std: 0.5 / 0.25.
- Output: sigmoid of channel 1, **not** a softmax.
- Overlap scores: uniform arithmetic mean. This preserves the experiment's documented departure from publisher overlap overwrites.
- Pixel threshold: 0.5 from float32 scores.
- Four-neighbor components, at least 25 pixels (1 m² at 20 cm), for candidate generation only.
- Raw masks and float32 scores remain unchanged by small-component filtering.
- Invalid pixels are excluded from score accumulation; scores use -1 and masks use 255 as NoData.

## Model agreement

Every RGBI component keeps its own outline. Intersecting RGB components are attached as evidence. A unique one-to-one pair at IoU >=0.5 is `both`; other intersecting cases are `geometry_conflict`. Unmatched components are `rgbi_only` or `rgb_only`. The IoU cutoff is a review-routing rule, not a proven accuracy/confidence threshold. RGB-only candidates are not automatically accepted.

## Limits and acceptance

The municipal path acquires official administrative boundaries, 99 original RGBI context tiles, and paginated simplified ALKIS footprints. Source dates, archive/raster hashes and raw cadastral response hashes are retained. Capture dates in the current snapshot are 2026-04-22 and 2026-05-01; publication is 2026-10-09. Candidates carry their intersecting source tile dates.

Municipal output cores are 2,048 pixels with bounded context. Native windows share a world grid with origin easting 0 / northing 10,000,000 in EPSG:25833 and stride 232 pixels. This differs from the crop-relative sample grid. Full-context scores agree across processing partitions in the synthetic seam test. Components are stitched across shared edges, not diagonal point contacts, before filtering. RGB and RGBI masks remain separate.

MPS uses batches of eight on the current machine. Real-image probes compare both actual checkpoints with CPU inference; results are in `docs/accelerator-verification.json`. This is a numerical check, not accuracy evidence. CPU remains supported with batch size one.

Pixel coverage separates missing imagery and outside-area pixels. Cadastral overlap is contextual evidence. Current building geometry can postdate the orthophoto; absence of a footprint does not establish ground mounting. Human mounting classification is separate from PV presence.

The delivered Hohen Neuendorf scene is a retained experimental transfer sample. Its 14 candidates are not an accuracy statistic or 14 confirmed installations. Scores are uncalibrated. Areas are horizontal image-projected areas, not module surface areas. Arrays can be roof- or ground-mounted; a human must check context.

The source archive was updated on 2026-10-09 and imagery captured on 2026-04-22. Decisions are about visibility on that date. Current WMS imagery is not substituted for the frozen snapshot.

Before claiming municipal accuracy or enabling automatic acceptance, annotate separate spatial tuning and acceptance regions in both municipalities, including empty roofs, small arrays, shadows, missing imagery and real official-tile boundaries. Measure independent RGBI/RGB and combined candidate recall, false-review workload, outline quality, and checked-area coverage. The historical public test was small and dominated by one large PV image; it does not validate residential municipal performance.
