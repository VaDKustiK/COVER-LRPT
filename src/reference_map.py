import json
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

from .dataset_audit import fingerprint

if TYPE_CHECKING:
    from .geometry import MapPredictor

DEFAULT_MAP = Path(__file__).resolve().parent.parent / "assets" / "ne_10m_coastline.npz"


def load_coastline(path: Path = DEFAULT_MAP) -> tuple[list[np.ndarray], dict[str, Any]]:
    metadata = json.loads(path.with_suffix(".json").read_text())
    if fingerprint(path) != metadata["asset"]:
        raise ValueError("coastline asset does not match its provenance record")
    with np.load(path, allow_pickle=False) as data:
        points, offsets = data["lonlat"], data["offsets"]
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError("invalid coastline coordinates")
    if offsets.ndim != 1 or len(offsets) < 2 or offsets[0] != 0 or offsets[-1] != len(points):
        raise ValueError("invalid coastline offsets")
    if not np.issubdtype(offsets.dtype, np.integer) or (np.diff(offsets) < 2).any():
        raise ValueError("each coastline must contain at least two vertices")
    return [points[a:b] for a, b in pairwise(offsets)], metadata


def render_coastline(predictor: MapPredictor, lines: list[np.ndarray], usable_rows: np.ndarray) -> np.ndarray:
    """Return an 8-bit edge map; do not connect across gaps, misses or the far hemisphere."""
    edge = np.zeros((predictor.height, predictor.scanner.width), dtype=np.uint8)
    offsets = np.cumsum([0] + [len(line) for line in lines])
    points = np.concatenate(lines)
    xy = predictor.geo_to_pixel(points[:, 1], points[:, 0])
    for start, end in pairwise(offsets):
        line = xy[start:end]
        good = np.isfinite(line).all(axis=1)
        adjacent = good[:-1] & good[1:] & (np.linalg.norm(np.diff(line, axis=0), axis=1) < 128)
        # Each piece has finite, consecutive vertices. Never draw through a rejected vertex.
        for i in np.flatnonzero(adjacent):
            a, b = np.rint(line[i : i + 2]).astype(int)
            cv2.line(edge, tuple(a), tuple(b), 255, 1, cv2.LINE_8)
    edge[~usable_rows] = 0
    return edge
