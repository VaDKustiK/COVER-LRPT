# Assumptions, uncertainties, and decisions

Status recorded for the decoded-input preparation on 2026-11-15. "Unknown" is deliberate;
it does not mean zero uncertainty or a calibrated probability distribution.

## Uncertainty register

| ID | Quantity / assumption | Current evidence | Consequence and next check |
|---|---|---|---|
| U01 | Absolute UTC of decoded timestamps | Internal consistency only; a constant bias survives the fit | Do not use internal RMS as UTC sigma; investigate decoder semantics and any independent station/packet timing |
| U02 | Timestamp-to-row relationship | Fitted period / 8 is about 0.153893 s; config also contains a 0.2 scantime field | Preserve the current row model as an approximation; reconcile scan/frame/packet timing before precision projection |
| U03 | Per-pixel acquisition time within a scan | Not modeled | Quantify its contribution when constructing the scanner model |
| U04 | Stored timestamp_offset | 0 for selected M2-3 products, 1 for M2-4; retained and unapplied | Determine convention and calibration before applying exactly once |
| U05 | Channel transform direction | Raw coefficients retained; selected channel 4 is not an identity transform | Begin with channel 2; verify mapping direction and sampling convention before multi-channel registration |
| U06 | PNG zero as missing data | Existing nonzero-fraction heuristic; no decoder validity mask | Dark valid pixels and corrupt nonzero pixels are possible; do not use the screen as a calibrated quality probability |
| U07 | Pixel radiometry | Selected PNGs uint8; descriptors report 10-bit source depth | Do not infer temperature/reflectance from these numbers without calibration and decoder-version evidence |
| U08 | Satellite identity | Labels and decoded serials agree for selected passes; archive includes contradictions | Cross-check by default; inventory conflicts without repair; agreement from one decoder is not independent identification |
| U09 | Recording orbit provenance | 38 omm_snapshot_id references; snapshot bytes absent in the inspected archive | Recover original records if available; do not equate decoder TLE with recording OMM |
| U10 | Decoder provenance | Archived projection settings exist; decoder version/command not recovered | Preserve source bytes and settings; pin a verified decoder implementation before importing its formulas |
| U11 | Embedded TLE representation | Selected TLE strings have nonnumeric terminal checksums | Preserve and flag; verify numeric parsing/propagation before geometry use, without silently repairing records |
| U12 | Orbit availability time | Embedded epochs precede selected passes; publication/retrieval time unknown | Historical offline claims must disclose this limitation; freeze availability prospectively |
| U13 | Attitude and boresight | Configuration offsets exist; no independent attitude time series established | Treat as calibration/nuisance assumptions, not independently measured orientation |
| U14 | Shoreline position and terrain | Reference dataset not selected/calibrated yet | Include map/terrain/seasonal water-boundary uncertainty in the later observation model |
| U15 | Final-test independence | Current sets interleave and have been screened | Reserve a prospective campaign after model/protocol freeze; do not relabel current data as blind |
| U16 | Station reception tolerances | Not specified | Set from actual recording margins/antenna requirements before claiming operational success |

## Why the timing questions are left explicit

An error budget should eventually propagate time uncertainty through motion and pointing
uncertainty through viewing geometry. The first-order dependencies are displacement
approximately proportional to velocity times timing error, and viewing displacement
dependent on range, scan angle, and angular error. These relations are sensitivity guides,
not numerical bounds for this archive. We have not measured the necessary error distributions.

Clock, attitude, and orbit changes can explain similar landmark shifts. Current checks
establish consistent input labels and reproducible bytes. The planned sensitivity experiment
must determine which physical parameters can be separated.

Wu, Naegeli and Wunderle (2020) motivates treating timing, position, orientation, and
terrain as separate geolocation error sources; its AVHRR results are not imported as
METEOR accuracy bounds.
[https://essd.copernicus.org/articles/12/539/2020/]

## Decision record

| Decision | Reason | Evidence / validation |
|---|---|---|
| Preserve raw times and channel descriptors | Avoid discarding information needed by the scanner model | Loader tests check raw holes, arrays, and transforms survive unchanged |
| Reject known identity conflicts by default | A plausible image does not identify the correct propagator | Conflicting metadata/TLE-line/decoded-serial tests; archive inventory |
| Separate status from timed_fraction | Consistent retained timestamps do not imply complete coverage | Synthetic missing-frame and constant-bias tests |
| Retain empirical timing/brightness thresholds | No physical calibration justifies replacement yet | Original algorithm unchanged apart from input validation and explicit constants |
| Hash consumed decoded files, not every recording byte | Current geometry inputs should be identifiable without copying or processing CADU | SHA-256 verification and tamper/missing-file tests; scope explicit |
| Preserve selection names and define roles separately | Maintain the original curation record without claiming test independence | Dataset policy and duplicate/missing-selection checks |
| Keep future final-test reservation empty | No new untouched campaign is present | Protocol specifies collection/freeze requirements rather than fabricating data |
| Keep generated reports immutable by default | Prevent accidental replacement of experiment evidence | CLI rejects existing outputs and output paths inside the archive |

The evidence for hashes and provenance is computational reproducibility guidance, not
a claim that a hash validates physics: Sandve et al. (2013).
[https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1003285]

## How to change a decision

Record the problem, supporting source or experiment, changed assumption, expected effect,
and validation result. Create a new policy/report version when admission or dataset roles
change. Preserve the previous experiment records. A later resolved uncertainty should
name the measurement or implementation version that resolved it.
