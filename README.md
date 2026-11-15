# COVER-LRPT

Research software for investigating orbit refinement from decoded METEOR-M2-3 and
METEOR-M2-4 LRPT imagery using CV. The intended system uses known ground features to constrain
an orbit and improve future pass predictions without downloading new orbital elements.
Reception and decoding are external, the current input is an existing SatDump product.

**Implemented:** local OMM loading, pass prediction, decoded-pass loading, internal timing
screening, satellite-identity checks, dataset auditing, and reproducible input manifests.
The map predictor, CV matcher, and orbit estimator are not implemented yet.

## Read the project in this order

1. `docs/input_contract.md` — what each input means, the existing timing mathematics,
   original image coordinates, identity checks, and limits of the validity masks.
2. `docs/uncertainties.md` — what is established, assumed, or still unknown, and why.
3. `docs/experiment_protocol.md` — dataset roles, baselines, evaluation and future-test rules.
4. `docs/archive_audit.md` — measured findings from the available archive and verification results.

Research references are included as full URLs in square brackets in those documents.
Generated evidence is in `research/archive_audit.json` and `research/selected_passes.json`;
the policy is `research/dataset_policy.json`. Original recordings remain outside the repo.
`logs.md` is a local, Git-ignored work journal, not the only record of scientific decisions.

## Environment

Python 3.14+ is currently required. With uv installed:

```bash
uv sync --all-groups
uv run pytest -q
```

The commands below also work using the existing `.venv/bin/python`. No new dependencies
are required by the audit. Tests use temporary synthetic products; they do not require
the private archive or network access.

## Inspect and verify the decoded dataset

The checked-in reports refer to the 36 names in `dev_passes.json`. Verify the consumed
archive files without writing into the archive:

```bash
.venv/bin/python -m src.dataset_audit verify \
  --archive "$HOME/Documents/ROSARP-OBS" \
  --manifest research/selected_passes.json
```

Create a fresh audit after a deliberate input or implementation change:

```bash
.venv/bin/python -m src.dataset_audit build \
  --archive "$HOME/Documents/ROSARP-OBS" \
  --selection dev_passes.json \
  --policy research/dataset_policy.json \
  --output-dir research/review-02
```

Choose a new output directory for each run. Existing reports are not overwritten, and
output inside the archive is refused. Inputs are only read. Admission failures in selected
passes stop the build; errors in other passes are reported. Exit code 0 means completion;
1 means an audit/integrity failure. A verified hash does not establish orbit or clock accuracy.

Example loader inspection:

```python
from pathlib import Path
from src.pass_loader import load_pass

folder = Path.home() / "Documents/ROSARP-OBS/1791030115_20261003T122155Z_59051"
p = load_pass(folder, channels=(2, 4))
print(p.identity)
print(p.status, p.timed_fraction)
print(p.channel_metadata[4]["transform"])
rows = p.usable_lines((2,))
```

`strict_identity=False` is reserved for diagnosis of conflicting products. It is not a
way to admit them into an experiment. `status == "ok"` is an internal timing classification,
not an absolute UTC or image-quality guarantee.

## Local orbit loading and pass prediction

`latest_omms.json` contains local SGP4 mean-element records. `get_latest_omms.py` is an
explicit network download helper that replaces that local file. Do not run it to prepare
a historical experiment: preserve and identify the orbit appropriate to that experiment.

Set station coordinates using `.env.example` and run the predictor as a module:

```bash
.venv/bin/python -m src.pass_predictor
```

The predictor's current satellite selection and elevation/preparation settings are in
`src/pass_predictor.py`. It searches for a future complete pass; it does not identify which
spacecraft actually produced an archived recording.

## Code map

| File | Responsibility |
|---|---|
| `src/omm.py` | Load local records and construct Skyfield satellites |
| `src/pass_predictor.py` | Predict the next qualifying pass for the configured station |
| `src/pass_loader.py` | Preserve decoded arrays, raw times, metadata, and identity evidence |
| `src/identity.py` | Compare available identity labels without automatically repairing them |
| `src/timing.py` | Fit internal frame-time consistency and approximate row times |
| `src/imagery.py` | Read raw channels and apply empirical row/brightness screens |
| `src/catalog.py` | Summarize passes and select identity-checked day candidates |
| `src/dataset_audit.py` | Audit the archive, freeze selected input hashes, verify integrity |
| `tests/` | Synthetic regression and contract tests |
| `testing/` | Existing local exploratory scripts; Git-ignored, not the reproducible interface |
| `main.py` | Existing placeholder; use the module entry points above |

## Checks

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ty check .
.venv/bin/python -m pytest -q
```