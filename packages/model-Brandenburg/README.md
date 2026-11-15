# Brandenburg batch processor

Python package `roof-pv-model`; command `roof-pv`. See the root README for setup and the real sample recipe. Inference dependencies are an optional `inference` extra. Model weights are external, revision-pinned and verified before strict loading. Neither the web API nor the comparison archive imports this package.

The sample path accepts at most 16 million pixels. The separate municipal path downloads verified source tiles and cadastral footprints, builds a relative VRT, runs both models on a world-anchored grid with bounded cores, stitches components before filtering, and preserves source-date evidence. See the root README for municipal acquisition, execution and independent verification commands.
