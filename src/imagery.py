from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

MIN_VALID_FRAC = 0.5
BRIDGE_LINES = 16
MIN_SEGMENT_LINES = 32
DAYLIGHT_MEDIAN = 30


def read_channel(product_dir: Path, product: dict, number: int) -> np.ndarray:
    """Channel PNG as a 2-D array; 'number' is the MSU-MR channel"""
    for img in product["images"]:
        if int(img["abs_index"]) + 1 == number:
            arr = cv2.imread(str(product_dir / img["file"]), cv2.IMREAD_UNCHANGED)
            if arr is None or arr.ndim != 2:
                raise ValueError(f"cannot read grayscale image {img['file']}")
            return arr
    raise KeyError(f"channel {number} not in this product")


def line_ok(img: np.ndarray, min_valid_frac: float = MIN_VALID_FRAC) -> np.ndarray:
    """True for lines where enough pixels carry data"""
    return (img > 0).mean(axis=1) >= min_valid_frac


def bool_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive (start, end) of each run of consecutive True values"""
    m = np.asarray(mask, dtype=bool)
    edges = np.diff(np.concatenate(([0], m.astype(np.int8), [0])))
    return list(zip(np.where(edges == 1)[0].tolist(), (np.where(edges == -1)[0] - 1).tolist(), strict=True))


def usable_segments(
    usable: np.ndarray, min_lines: int = MIN_SEGMENT_LINES, bridge: int = BRIDGE_LINES
) -> list[tuple[int, int]]:
    m = np.asarray(usable, dtype=bool).copy()
    for s, e in bool_runs(~m):
        if s > 0 and e < m.size - 1 and (e - s + 1) < bridge:
            m[s : e + 1] = True
    return [(s, e) for s, e in bool_runs(m) if e - s + 1 >= min_lines]


def daylight_fraction(ch2: np.ndarray, ok: np.ndarray) -> float:
    """Share of usable lines whose channel-2 median looks sunlit"""
    if not ok.any():
        return 0.0
    return float((np.median(ch2[ok], axis=1) > DAYLIGHT_MEDIAN).mean())


def classify_light(day_fraction: float) -> str:
    if day_fraction >= 0.9:
        return "day"
    if day_fraction <= 0.1:
        return "night"
    return "mixed"
