# COVER-LRPT

Experimental Python tools for METEOR-M2-3 and METEOR-M2-4 LRPT pass prediction and image geolocation. The coastline predictor runs offline with a local orbit and an existing decoded channel-2 product.

Install dependencies and run the checks:

```sh
uv sync --locked
uv run --locked pytest -q
uv run --locked python -m src.map_predictor --help
uv run --locked python -m src.dataset_audit build --help
```

For next-pass prediction, copy `.env.example` to `.env` and enter your station coordinates. Run `uv run python get_latest_omms.py` with internet access to create the local orbit file, then run `uv run python -m src.pass_predictor`.

For an offline coastline overlay, supply your own decoded pass folder and local OMM:

```sh
uv run python -m src.map_predictor /path/to/pass --orbit /path/to/orbit.json --timing fitted --output outputs/example
```

Use a new output directory. The dataset audit takes your own pass list via `--selection` and defaults to `config/dataset_policy.json`. Coastline data comes from Natural Earth.

To study which small orbital changes can be distinguished from clock and pointing offsets, run the offline diagnostic on the development group in your audit manifest.

```sh
uv run python -m src.measurement_study --archive /path/to/archive --manifest /path/to/selected_passes.json --orbit /path/to/orbits.json --output outputs/feasibility.json
```

This reports synthetic grid sensitivities per pass. Supply `--picks reader.json` once per independent reader to include raw channel-2 measurements and paired disagreements. Each sheet identifies the reader, original image bytes and reference vertices. The input fields and examples are in `tests/test_measurement_study.py`. Reported pixel shifts describe local sensitivity and the command writes a diagnostic report.

To locate known coastal features automatically in an original channel-2 image:

```sh
uv run python -m src.coast_match /path/to/pass --orbit /path/to/orbit.json --output outputs/coast-matches.json
```

The offline matcher projects the named vertices in `config/coastal_landmarks.json` using the fitted row times. It searches for a local coastline translation within ±24 pixels. The JSON records the predicted and measured column and row, a rejection reason for unsuccessful searches, and input hashes. Accepted positions are experimental image measurements. The input orbit stays fixed. Supply `--catalog` to use another list of named vertices on the same pinned map.
