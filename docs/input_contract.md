# Decoded-pass input contract

This document defines what the current Python code measures, preserves, and assumes.
It describes **decoded input preparation**, not an orbit estimator. No clock calibration,
geographical matching, scanner projection, or orbit correction is performed yet.

## What is read

The archive is supplied at runtime; it is not copied into the repository. A pass has this layout:

```text
<integer Unix anchor>_<YYYYMMDDTHHMMSSZ>_<NORAD>/
    metadata.json
    <NORAD>_baseband_power.csv
    satdump_output/
        dataset.json
        telemetry.json
        meteor_m2-x_lrpt.cadu
        MSU-MR/
            product.cbor
            MSU-MR-*.png
```

`src/pass_loader.py` reads the CBOR product, optional metadata and identity sidecars,
and requested PNG channels. No input is rewritten. CADU and power CSV files are not
consumed by this loader. They remain available for later timing/identity investigations.
They are not included in the selected decoded-input integrity claim.

`load_pass(folder, channels=(2, 4))` returns `PassData`. Requesting an unavailable channel
does not invent an array: the channel is absent from `images`. With `channels=()` no
images are loaded. A `no_timing` pass also leaves `images` empty; available channel
descriptors are still reported. The research audit enforces its required-channel policy.

## Coordinates and pixel values

| Quantity | Convention |
|---|---|
| Array indexing | `images[channel][row, column]`, zero-based |
| Row order | Original decoded PNG order; no flip, rotation, crop, or resampling |
| Column order | Original decoded PNG order; no assumption that increasing columns mean east |
| Physical orientation | Requires the scanner model and orbit; a row is not a latitude |
| Channel number | `abs_index + 1`, preserving the instrument's one-based numbering |
| Pixel storage | Original PNG dtype; all selected channel 2/4 files currently load as uint8 |
| Radiometric meaning | No conversion to calibrated reflectance or temperature here |
| Width | Checked against `projection_cfg.image_width` when that field is present |
| Height | Must equal number of frame timestamps multiplied by rows per frame |

Descriptors can say `bit_depth=10` while the stored PNG arrays are uint8. Both facts are
preserved. Do not reinterpret those arrays as calibrated ten-bit measurements.

`channel_metadata[channel]` preserves each image descriptor, including filename,
declared bit depth, calibration metadata, and `transform`. The selected products store:

| Satellite | Channel | Stored transform coefficients |
|---|---|---|
| M2-3 | 2 | type 2; ax=ay=1; bx=by=0 |
| M2-3 | 4 | type 2; ax=ay=1; bx=by=-1.6 |
| M2-4 | 2 | type 2; ax=ay=1; bx=by=0 |
| M2-4 | 4 | type 2; ax=ay=1; bx=-2; by=0 |

These are **stored coefficients**, not an instruction to shift the arrays by a guessed
sign. They are not applied by the loader. Their mapping direction and pixel-centre
convention must be checked against the relevant SatDump implementation before combining
measurements across channels. Channel 2 is the declared initial geometry reference.

## Satellite identity

The supported folder IDs are 57166 (M2-3) and 59051 (M2-4). The loader cross-checks:

- Folder NORAD.
- `metadata.json`'s `norad_id`.
- Catalogue IDs on **both** embedded TLE lines.
- The product's `product_source` name, using explicit known aliases.
- The satellite name in `dataset.json`, when present.
- A strict majority of decoded `msu_mr_id` values, when present and recognized.

Null telemetry entries are ignored. A JSON-null telemetry document means no decoded
telemetry. Telemetry is optional corroboration; its absence is visible in the evidence
record. Multiple sources may have been produced by the same decoder and are not
independent physical identification.

| `identity.status` | Meaning | Default loader behavior |
|---|---|---|
| consistent | Required labels present and all interpreted evidence agrees | Return pass |
| incomplete | Required label missing/unknown, or optional evidence ambiguous | Return with explicit issues |
| conflict | A known label contradicts the folder, or folder ID is unsupported | Raise `IdentityConflict` |

`strict_identity=False` is provided for inventory and diagnosis. It does not make a
conflicting pass acceptable for scientific fitting. The selected-input policy requires
`consistent`. The catalog excludes conflicting/incomplete identities from day candidates.
Old catalog JSON files without identity checks must be regenerated before using that filter.

No label is automatically repaired and no external orbital record is substituted.

## Recording anchor versus observation time

`aos` is retained as a legacy field name. It is the integer Unix timestamp in the folder,
used to locate a recording window. The redundant readable UTC in the folder must agree.
It is **not an independently measured radio acquisition-of-signal event**. Metadata
may contain fractional seconds; the original metadata is retained separately.

`raw_timestamps` contains the numerical frame timestamps extracted from the CBOR product,
including invalid values. The product file remains the byte-exact source. `cfg` preserves
the other projection settings, including the embedded TLE and offsets.

The existing timing algorithm is retained:

1. Select frame timestamps inside `[anchor - 60 s, anchor + 1200 s]`.
2. Subtract `1.23114 s * frame_index` to find candidate intercept groups.
3. Split sorted intercepts at gaps above 0.05 s and retain groups with at least eight frames.
4. Fit a frame period to the largest group. Use that period with a separate intercept
   for each retained group.
5. For a retained frame `k` and row-within-frame `j`, assign:

   `t(k, j) = intercept + fitted_period * (k + j / rows_per_frame)`.

Unsupported frame rows stay `NaN`; they are not filled by extrapolation. Multiple
groups can describe inconsistent clocks: their assigned row times need not be globally
monotonic. They are inventoried, but excluded by the current selected-input policy.

This row formula assumes a frame timestamp anchors its first row and uniformly
distributes rows across the fitted period. The selected data give about 0.153893 s/row.
The stored `interpolate_timestamps_scantime=0.2` and frame/filter settings do not establish
the same interpretation by themselves. Their semantics must be reconciled before
claiming accurate scanner observation times. Within-row acquisition timing is not modeled.

`timestamp_offset` is **preserved but not applied**. It must not be applied twice later.
Neither spacecraft-clock bias, station-clock bias, nor absolute observation latency is
estimated here. Unix seconds are the working numerical time representation; the
original decoder's absolute timing accuracy and exact conventions remain unverified.

## Timing status and coverage are different

| Status | Current definition |
|---|---|
| no_timing | No retained intercept group |
| multi_segment | More than one retained group |
| ok | One group contains at least 99% of window candidates and its internal RMS is below 1e-5 s |
| partial | One retained group fails the preceding consistency criterion |

The denominator for the 99% check is **window candidates**, not all image frames.
`timed_fraction` explicitly reports finite line times divided by all rows.
A pass can be `ok` with missing rows, and an otherwise consistent clock shifted by five
seconds can remain `ok`. Tests demonstrate both behaviors.

The RMS is a residual of an internal model fitted to decoded timestamps. It is not an
absolute UTC uncertainty, and must not become an orbit-estimation measurement sigma.
These thresholds are inherited empirical heuristics, not confidence bounds derived
from a paper. The audit records their actual values and the source-code hashes.

## Missing data and candidate windows

`line_ok` requires at least half of a row's pixels to be nonzero. This is a useful
screening heuristic for these products, not a decoder-provided validity mask: darkness
can produce legitimate zeros, and corrupt pixels can be nonzero. There is no cloud mask.

`usable_lines(channels)` intersects finite modeled times with this row heuristic for
every requested channel. It is the row-level mask to preserve downstream.

`segments()` returns inclusive candidate `(start, end)` windows after bridging internal
holes shorter than 16 rows and rejecting windows shorter than 32 rows. A returned window
does **not** certify every enclosed row. Apply `usable_lines()` inside it. Bridging does
not restore observations or fill their time values.

The day/night classifier uses channel-2 row medians greater than 30, then thresholds
their fraction at 0.9 and 0.1. This is a brightness classifier used for selection, not
solar-geometry validation. It depends on stored pixel scale.

## Orbit provenance

The audit keeps three concepts separate:

| Record | What is known now |
|---|---|
| Recording orbit | `omm_snapshot_id` is preserved; its OMM bytes are not resolved from this archive |
| Decoder orbit | The two original TLE strings are preserved from `product.cbor` |
| Experiment's initial orbit | Must be explicitly chosen and frozen by the later experiment runner |

The decoder version and its orbital-data retrieval time are unknown. The selected
decoder TLE epochs precede their recordings, but an epoch is not a publication or
availability time. This does not establish a strictly causal offline experiment.

The inspected decoder TLEs end in `C` instead of a numeric checksum. The audit records
`nonstandard`, preserving the bytes without repairing them. This does not itself prove
the numeric orbital fields are wrong, but these strings must not be described as
checksum-validated official TLEs. Their interpretation needs a propagator check before use.

Do not use the mutable `latest_omms.json` as an implicit historical reference. A historical
experiment must name its initial orbit explicitly. OMM is a representation, not a source
of independent truth.

## Research and implementation sources

Wu, Naegeli and Wunderle (2020), *Geometric accuracy assessment of coarse-resolution
satellite datasets*, introduction and methodology: motivates separating geolocation
error sources. It does not validate our timing thresholds or transfer its accuracy to MSU-MR.
[https://essd.copernicus.org/articles/12/539/2020/]

Baldwin and Emery (1993), *A systematized approach to AVHRR image navigation*, geometry
and correction discussion: explains why timing and pointing can imitate displacement.
It is an analogue, not an MSU-MR calibration.
[https://doi.org/10.3189/S0260305500013185]

SatDump implementation references: the instrument decoder and projection configuration
provide the field definitions to investigate. These links follow the current branch;
they do not identify the version that produced the archive. Pin the verified implementation
before adopting its transform or timestamp equations.
[https://github.com/SatDump/SatDump/blob/master/plugins/meteor_support/meteor/instruments/msumr/module_meteor_msumr_lrpt.cpp]
[https://github.com/SatDump/SatDump/blob/master/plugins/meteor_support/meteor/instruments/msumr/lrpt_msumr_reader.cpp]
[https://github.com/SatDump/SatDump/blob/master/resources/projections_settings/meteor_m2-3_msumr_lrpt.json]
[https://github.com/SatDump/SatDump/blob/master/resources/projections_settings/meteor_m2-4_msumr_lrpt.json]
