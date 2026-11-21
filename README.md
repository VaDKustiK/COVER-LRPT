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
