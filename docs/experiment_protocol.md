# Experiment and dataset protocol

Protocol ID: `decoded-inputs-v1`. The machine-readable companion is
`research/dataset_policy.json`. This is an input and evaluation specification, not a
claim that image-based orbit maintenance has already been demonstrated.

## Research question and scope

Starting with an initial orbit estimate, can decoded METEOR-M2-3/M2-4 imagery provide
corrections that improve subsequent predictions without downloading updated orbits?

The present deliverable is a checked, reproducible set of decoded inputs for geometry
and measurement development. It does not require a functioning map predictor, an orbit
solver, a prospective test campaign, or calibrated clock uncertainty yet. Those open
requirements are explicit in `docs/uncertainties.md`.

## Dataset roles

Keep `dev_passes.json` unchanged as the original selection. Resolve its names against
the local archive; never silently drop a missing or rejected selected pass.

| Original group | Research role | Permitted use |
|---|---|---|
| dev (8) | development | Design geometry and matching, annotate landmarks, investigate errors |
| hard (8) | stress_development | Tune rejection behavior and explore difficult conditions |
| heldout (20) | validation | Compare choices on separate passes; report any tuning performed |

These sets are disjoint by name but overlap in time. The historical selection screened
daytime passes with consistent timing and substantial candidate windows. It is not a
representative sample of all reception conditions. The full archive inventory must be
kept when reporting failure rates or designing a later all-pass replay.

The original `heldout` label is preserved for traceability. The set is **not** an untouched
chronological final test. Repeatedly using it to choose parameters makes it validation data.
Metadata auditing and previously viewed contact sheets must not be concealed as pristine
blind testing.

### Prospective final-test reservation

No suitable untouched final-test campaign is claimed to exist in this archive. The policy
contains an empty reservation, rather than falsely assigning already inspected images.

Before collecting/evaluating that campaign, freeze code, parameters, calibration,
initial orbit bytes and their availability time, campaign boundaries, update rules,
metrics, and reception tolerances. The campaign must be later than all inspected data
and after this experiment freeze. Choose its consecutive date interval before inspecting
the images, and include every scheduled attempt, failure, and rejected observation.

If tuning becomes necessary, label that campaign development/validation and reserve a
new final campaign. Do not move inconvenient observations between groups.

## Admission for current geometry development

The policy requires consistent interpreted satellite labels, the existing `ok` timing
classification, loaded channels 2 and 4, a parsable embedded TLE epoch, and at least one
row passing the existing joint time/nonzero screen. The PNG dimensions must agree with
the declared frame structure and width. The selected 36 are audited individually.

These are engineering admission criteria, not scientific accuracy guarantees. All other
records remain on disk. The inventory records conflicts and timing failures for later
investigation; it does not delete or relabel them. Admission does not certify cloud-free
landmarks, accurate UTC, exact radiometry, or a physically correct orbit.

## Reproducibility and data integrity

`python -m src.dataset_audit build` reads the archive and creates two reports:

- `archive_audit.json`: all discovered pass products, identity evidence, timing summaries,
  original embedded orbit strings, and hashes of the evidence files actually read.
  Images are inspected only for the selected passes. Nonselected read errors are recorded.
- `selected_passes.json`: the admitted selection, input hashes, configuration and channel
  metadata, roles, policy, dependency versions, and hashes of implementation files.

SHA-256 and byte size identify the consumed CBOR, JSON sidecars, and channel 2/4 PNGs.
Paths are relative to the archive so it can be relocated. CADU, power CSVs, and other PNGs
are not consumed or hashed by this preparation step. If a future experiment reads them,
its manifest must include them then.

The manifest also binds the archive report by hash. Git HEAD is recorded only for context;
source hashes capture uncommitted changes as well. `uv.lock` is included. This is a versioned
record, not a promise that an arbitrarily changed environment will reproduce results.

The build command refuses output inside the archive and refuses to overwrite reports.
For a new dataset or implementation version, choose a new output directory and review
the differences. The `verify` command checks the consumed selected archive files against
the saved manifest. It does not certify their scientific correctness, compare the current
source/environment with the recorded versions, or validate the nonselected inventory.

Do not hand-edit generated reports to make a failure disappear. Record a new audit with
an explained selection or policy change. Keep the old report as the prior experiment's record.

## Later experiment records

Each future experiment needs a run record containing:

- Question and method, with paper section/equation or explicit adaptation rationale.
- Exact input-manifest hash, source hashes/commit, dependency versions, and random seeds.
- Initial orbit bytes, epoch, origin, availability time, and propagation conventions.
- Any station coordinates and time source used, with their uncertainties.
- Model parameters, nuisance-parameter assumptions, and fitted versus fixed quantities.
- Observation availability, accepted/rejected measurements, and reasons.
- Results, denominators, uncertainty estimates, failures, and conclusions.

Do not treat a decoder timestamp-fit residual as the uncertainty of a geographical
observation. Derive a measurement error model from the relevant calibration experiments.

## Baselines and causal replay rules

Use the same initial orbit and observation opportunities for the appropriate comparisons:

1. **Unchanged initial orbit:** propagate without updates.
2. **Timing/pointing-only adjustment:** improve image alignment without changing the orbit;
   this tests whether apparent gains need an orbital explanation.
3. **Restricted orbit correction:** fit only parameters supported by sensitivity experiments.
4. Later, compare additional orbit parameters or longer observation windows against those baselines.

At an update, only previously available observations may be used. Acceptance cannot
depend on the later pass that will score the prediction. A future observation can update
the system only after that prediction has been scored. The update interval is an experiment
parameter, not an instruction to emit a new orbit when no useful observations exist.

For historical experiments, an embedded TLE with an earlier epoch is not sufficient proof
that it was available at recording time. If availability cannot be recovered, label the
experiment a conditional retrospective study. Later external elements may be evaluation
references, not estimator inputs in an offline trial.

## Metrics and interpretation

| Question | Measurements | Limitation |
|---|---|---|
| Does the geometry explain the image? | Median and upper-percentile landmark residuals in raw pixels, separated along/across the swath; error versus scan position | Image alignment alone does not prove orbit accuracy |
| Is matching reliable? | Manually checked match errors, false accepted matches, accepted-pass fraction, rejection reasons | Count failures as well as successful matches |
| Do corrections predict later observations? | Paired residual changes against the unchanged-orbit and nuisance-only baselines at declared prediction horizons | Later images may share calibration biases |
| Does the station still receive passes? | Independent reception/capture evidence, recording-window coverage, missed attempts, pointing error if independently measured | Scheduled AOS/LOS is not truth; RF detection depends on reception conditions |
| Is the physical orbit more accurate? | Position/velocity differences against a suitable independent reference | Public GP/TLE/OMM records are comparison references, not exact truth |
| Can the target device keep up? | Runtime, peak memory, storage growth, rejected updates, restart behavior | Must be measured on the intended device |

The initial planned prediction horizons are 24, 48, and 72 hours, reflecting the proposed
update cadence; they are project design choices, not literature-mandated thresholds.
Define the exact scoring window around each horizon before running that experiment.
If no observation is available there, report missing support rather than invent a score.

Use pass- or time-block-level comparisons when estimating uncertainty; neighboring pixels
and passes can be correlated. Report per-satellite results, sample sizes, selection effects,
and the duration since the last usable observation. Agree the statistical method before
final testing; do not treat thousands of neighboring pixels as independent experiments.

The current policy leaves reception and pointing tolerances as `null`. Their meaning is
"not yet specified", not unlimited tolerance. Derive them from actual recording margins
and antenna requirements before the field campaign. No absolute station-success claim
is permitted without those definitions. For development, report measured errors and
paired changes instead of inventing an unsupported success threshold.

## Methodological sources

Sandve et al. (2013), *Ten Simple Rules for Reproducible Computational Research*, especially
recording procedures, exact inputs and versions: supports preserving reproducible evidence.
The particular JSON schemas and CLI are our engineering choices.
[https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1003285]

Tashman (2000), *Out-of-sample tests of forecasting accuracy: an analysis and review*:
supports defining temporal fit/test periods and rolling evaluation. It is forecasting
methodology, not evidence of METEOR orbit accuracy. Detailed statistical choices remain
to be specified for the eventual experiment.
[https://doi.org/10.1016/S0169-2070(00)00065-0]

Vallado and Crawford (2008), *SGP4 Orbit Determination*: supports the later connection
between an observation model and SGP4-compatible differential correction. No fitting
algorithm from this paper is implemented in the input-preparation code.
[https://celestrak.org/publications/AIAA/2008-6770/]
