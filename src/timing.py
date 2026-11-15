import re
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

PASS_DIR_RE = re.compile(r"^(\d+)_(\d{8}T\d{6}Z)_(\d+)$")  # <aos unix>_<UTC>_<NORAD>

NOMINAL_PERIOD_S = 1.23114
WINDOW_BEFORE_S = 60.0
WINDOW_AFTER_S = 1200.0
TIME_OUTLIER_S = 0.05
MIN_SEGMENT_FRAMES = 8
MIN_CONSISTENT_FRACTION = 0.99
MAX_INTERNAL_RMS_S = 1e-5


@dataclass(frozen=True)
class TimeSegment:
    frames: np.ndarray
    t0: float  # t = t0 + period * frame_index
    rms_s: float


@dataclass(frozen=True)
class TimeModel:
    period: float
    segments: list[TimeSegment]
    n_candidates: int  # valid timestamps inside the pass window


def aos_from_folder(name: str) -> float:
    """Recording-window anchor, not an independently measured signal AOS."""
    m = PASS_DIR_RE.match(name)
    if m is None:
        raise ValueError(f"unexpected pass folder name: {name}")
    anchor = float(m.group(1))
    readable = datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp()
    if anchor != readable:
        raise ValueError(f"folder Unix and readable UTC timestamps disagree: {name}")
    return anchor


def build_time_model(raw_timestamps, aos: float) -> TimeModel:
    """Fit internal frame-time consistency; absolute UTC accuracy is unknown."""
    ts = np.asarray(raw_timestamps, dtype=float)
    if ts.ndim != 1 or not np.isfinite(aos):
        raise ValueError("timestamps must be one-dimensional and the recording anchor finite")
    k = np.arange(ts.size, dtype=float)  # frame indices

    # Empirical screening window around the recording anchor; not a UTC calibration.
    in_window = (ts >= aos - WINDOW_BEFORE_S) & (ts <= aos + WINDOW_AFTER_S)
    kk, tt = k[in_window], ts[in_window]
    if kk.size < MIN_SEGMENT_FRAMES:
        return TimeModel(float("nan"), [], int(kk.size))

    c = tt - NOMINAL_PERIOD_S * kk
    order = np.argsort(c)
    cuts = np.where(np.diff(c[order]) > TIME_OUTLIER_S)[0] + 1
    groups = [g for g in np.split(order, cuts) if g.size >= MIN_SEGMENT_FRAMES]
    if not groups:
        return TimeModel(float("nan"), [], int(kk.size))

    big = max(groups, key=lambda g: g.size)
    kc, tc = kk[big].mean(), tt[big].mean()
    period = float(np.polyfit(kk[big] - kc, tt[big] - tc, 1)[0])

    segments = []
    for g in groups:
        g = g[np.argsort(kk[g])]
        t0 = float(np.median(tt[g] - period * kk[g]))
        rms = float(np.sqrt(np.mean((tt[g] - (t0 + period * kk[g])) ** 2)))
        segments.append(TimeSegment(kk[g].astype(int), t0, rms))
    segments.sort(key=lambda s: s.frames[0])
    return TimeModel(period, segments, int(kk.size))


def timing_status(model: TimeModel) -> str:
    """Legacy consistency label. 'ok' does not imply full coverage or accurate UTC."""
    if not model.segments:
        return "no_timing"
    if len(model.segments) > 1:
        return "multi_segment"
    covered = model.segments[0].frames.size / model.n_candidates
    ok = covered >= MIN_CONSISTENT_FRACTION and model.segments[0].rms_s < MAX_INTERNAL_RMS_S
    if ok:
        return "ok"
    else:
        return "partial"


def line_times(model: TimeModel, n_frames: int, lines_per_frame: int = 8) -> np.ndarray:
    """Approximate Unix seconds per raw row; unsupported rows remain NaN.

    Assumes each frame timestamp anchors its first row and rows are uniformly
    spaced across the fitted frame period. No SatDump timestamp_offset is applied.
    """
    if lines_per_frame <= 0 or n_frames < 0:
        raise ValueError("lines_per_frame must be positive and n_frames nonnegative")
    out = np.full(n_frames * lines_per_frame, np.nan)
    for s in model.segments:
        lines = (s.frames[:, None] * lines_per_frame + np.arange(lines_per_frame)).ravel()
        out[lines] = s.t0 + model.period * (lines / lines_per_frame)
    return out
