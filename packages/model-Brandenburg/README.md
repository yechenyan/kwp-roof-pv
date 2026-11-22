# Brandenburg batch processor

The `roof-pv-model` package provides the `roof-pv` command. Follow the [model setup instructions](../../README.md#set-up-the-brandenburg-model) to install the inference extra and acquire the two verified checkpoints. The root README also includes a real sample recipe. Neither the web API nor the comparison archive imports this package.

The sample path accepts at most 16 million pixels. The separate municipal path downloads verified source tiles and cadastral footprints, builds a relative VRT, runs both models on a world-anchored grid with bounded cores, stitches components before filtering, and preserves source-date evidence. See the root README for municipal acquisition, execution and independent verification commands.
