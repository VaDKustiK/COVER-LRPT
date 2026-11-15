# Decoded archive: audit and preparation results

Audit generated: **2026-11-15 11:25 UTC**. Policy: `decoded-inputs-v1`.
Reports use relative paths so another installation can supply a different archive root.

The decoded-input preparation is complete for the existing 36-pass selection:
inputs are identified, screened, preserved, and reproducibly recorded. This establishes
a basis for geometry experiments; it does not establish calibrated UTC, correct
image geolocation, or successful orbit refinement.

## Scope and measured inventory

The audit reads every discovered `*/satdump_output/MSU-MR/product.cbor` and its
available identity sidecars. It loads channel 2/4 image arrays for the selected passes.
It does not claim to have visually evaluated every image or decoded the CADU files.

| Property | Result |
|---|---|
| Discovered products | 412 |
| Products by folder NORAD | 206 for 57166; 206 for 59051 |
| Recording-anchor range, UTC | 2026-09-21 15:31:52 to 2026-10-11 14:35:59 |
| Product read errors | 0 |
| Consistent identity labels | 319 |
| Conflicting identity labels | 93 |
| Internal timing classifications | 323 `ok`, 80 `no_timing`, 5 `partial`, 4 `multi_segment` |
| Selected passes | 36; all admitted under the preparation policy |
| Selected consumed files verified by SHA-256 and size | 216 |

The satellite totals above describe folder labels. The identity check exists precisely
because a folder label is insufficient to establish which satellite produced a product.

| Original selection | Current role | M2-3 | M2-4 | Total |
|---|---|---:|---:|---:|
| `dev` | Development | 3 | 5 | 8 |
| `hard` | Stress development | 4 | 4 | 8 |
| `heldout` | Validation | 10 | 10 | 20 |

The original `dev_passes.json` names and memberships were preserved. The rationale for
these roles and the future final-test reservation is in `docs/experiment_protocol.md`.

## Findings that affect the research

### Conflicting identities require explicit exclusion

93 products have conflicting satellite labels, including folder versus embedded TLE
identifiers. Of these, 22 contain PNG files. A successful decode or a usable-looking
image therefore cannot substitute for identity checks. The audit preserves the evidence
and reports the conflict; normal loading rejects known conflicts by default.

All selected 36 have consistent interpreted labels. Agreement is an admission check,
not independent physical identification: some labels may originate from the same
configuration. No files were renamed, relabelled, or removed to resolve conflicts.

### Internally consistent timestamps still contain gaps and unknown offsets

Selected passes have finite modeled row-time coverage from **77.16% to 100%**, despite
all having timing status `ok`. Downstream work must use per-row masks rather than assume
the whole image is timed. Original timestamps, missing rows, channel transforms and
decoder configuration are retained.

A synthetic regression test shifts every valid timestamp by five seconds and retains
the `ok` classification. This demonstrates why internal fit consistency cannot establish
absolute UTC accuracy. It is not evidence that the actual archive has a five-second bias.
Timing conventions and unresolved offsets are described in `docs/input_contract.md`.

### Embedded elements are useful provenance, not independent orbit truth

The selected embedded TLE epochs precede recording anchors by approximately
**0.1464 to 4.0233 days**. An epoch is not the time the elements became available;
historical causal replay still needs that distinction.

Both TLE lines in every selected product have a nonstandard checksum field, with a
terminal `C`. The report preserves the strings and flags the format. This does not prove
that the numerical elements are wrong, and this preparation does not silently repair
them or certify them for propagation.

The archive metadata references 38 distinct recording OMM snapshot IDs, but the corresponding
OMM records were not recovered from this archive. The embedded decoder TLE must not be
silently substituted for the original recording OMM. Decoder version and command-line
provenance are also unresolved. These limitations remain explicit in the reports.

## Evidence and repeatable commands

- `research/archive_audit.json`: per-product identity, timing, embedded orbit and input evidence.
- `research/selected_passes.json`: selected inputs, hashes, configuration, roles, source hashes,
  environment versions, screening thresholds and companion-report hash.
- `research/dataset_policy.json`: machine-readable admission and evaluation policy.

Run the selected-input integrity check from the repository root:

```bash
.venv/bin/python -m src.dataset_audit verify \
  --archive "$HOME/Documents/ROSARP-OBS" \
  --manifest research/selected_passes.json
```

Result on this audit: `Input integrity: verified` for all 216 consumed files.
The recorded implementation, selection, policy and companion-report hashes were also
compared with the current files and matched. The CLI `verify` command itself checks
selected archive inputs only; those other comparisons were a separate verification.

The archive's 3,498-file inventory was compared before and after the work: no added or
removed files, and no changes to file size or modification time. This is an additional
preservation check, not a claim of a full before/after byte hash of unconsumed files.
Archive access was read-only. The existing external catalog was left unchanged.

## Software checks

| Check | Result |
|---|---|
| `python -m pytest -q` | 37 tests passed |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 21 files formatted |
| `ty check src tests` | Passed without diagnostics |
| `ty check .` | Exit 0; one existing warning in unchanged `get_latest_omms.py:27` |
| `git diff --check` | Passed |
| Import `src.pass_predictor` | Passed |

Tests cover inconsistent identity evidence, unavailable telemetry, preservation of raw
timing/metadata, missing-time masks, dimension checks, clock-shift ambiguity, selected
input admission, file tampering, missing files, path escapes and report overwrite refusal.
CI now runs the tests. Synthetic tests need neither the archive nor internet access.

## What this enables next

The next investigation can construct predicted viewing geometry in the original image
coordinates and compare it with manually identified landmarks. Its first question is
whether residuals respond distinguishably to time, pointing and orbit perturbations.
That evidence should determine which quantities may be fitted before automating CV.

The uncertainty register remains active. Completing preparation means these unknowns
are visible and cannot silently become calibrated measurements; resolving all of them
requires later geometry, timing and observation experiments.

## Basis for the preparation method

Sandve et al. (2013), *Ten Simple Rules for Reproducible Computational Research*, supports
recording exact inputs, versions and procedures. Our manifest format, checks and admission
thresholds are project engineering decisions, not a published METEOR calibration method.
[https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1003285]

Tashman (2000), *Out-of-sample tests of forecasting accuracy: an analysis and review*,
supports temporal evaluation and explicit separation of fitting and forecasting tests.
It supports the evaluation design, not any numerical orbit-accuracy claim.
[https://doi.org/10.1016/S0169-2070(00)00065-0]

See the input contract and uncertainty register for instrument/decoder references and
the experiment protocol for the later SGP4 estimation reference.
