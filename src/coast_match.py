import argparse
import json
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path

import cv2
import numpy as np

from .dataset_audit import fingerprint, implementation_record, input_files, json_bytes
from .geometry import MapPredictor, Scanner, observation_times
from .geometry_orbit import select_orbit
from .pass_loader import load_pass
from .reference_map import load_coastline, render_coastline

CATALOG = Path(__file__).resolve().parents[1] / "config" / "coastal_landmarks.json"


@dataclass(frozen=True)
class MatchSettings:
    chip_radius: int = 32
    search_radius: int = 24
    center_radius: int = 8
    minimum_coast_pixels: int = 32
    minimum_valid_fraction: float = 0.8
    distance_sigma_px: float = 1.8
    minimum_score: float = 0.48
    minimum_center_score: float = 0.45
    competitor_distance_px: int = 6
    minimum_peak_margin: float = 0.08


SETTINGS = MatchSettings()


def normal_angles(coast):
    """Local tangent from the second moments of nearby map edge pixels, rotated to its normal."""
    y, x = np.indices(coast.shape, dtype=np.float32)
    mask = coast.astype(np.float32)

    def smooth(a):
        return cv2.GaussianBlur(a, (9, 9), 2, borderType=cv2.BORDER_CONSTANT)

    mass = np.maximum(smooth(mask), 1e-10)
    mx, my = smooth(x * mask) / mass, smooth(y * mask) / mass
    xx = smooth(x * x * mask) / mass - mx * mx
    yy = smooth(y * y * mask) / mass - my * my
    xy = smooth(x * y * mask) / mass - mx * my
    return 0.5 * np.arctan2(2 * xy, xx - yy) + np.pi / 2


def correlate(field, template):
    return cv2.matchTemplate(field.astype(np.float32), template.astype(np.float32), cv2.TM_CCORR)


def match_chip(image, usable_rows, coast, predicted):
    """Return the translated geographic anchor only if image evidence supports the match."""
    cfg = SETTINGS
    result = {"predicted_pixel": None, "measured_pixel": None, "accepted": False}
    predicted = np.asarray(predicted)
    if not np.isfinite(predicted).all():
        return result | {"reason": "outside_projection"}
    result["predicted_pixel"] = np.asarray(predicted).tolist()
    x, y = np.rint(predicted).astype(int)
    half, search = cfg.chip_radius, cfg.search_radius
    pad = half + search
    if x < pad or y < pad or x + pad >= image.shape[1] or y + pad >= image.shape[0]:
        return result | {"reason": "incomplete_search_window"}
    template = coast[y - half : y + half + 1, x - half : x + half + 1] != 0
    ty, tx = np.ogrid[-half : half + 1, -half : half + 1]
    central = template & (ty**2 + tx**2 <= cfg.center_radius**2)
    if template.sum() < cfg.minimum_coast_pixels or not central.any():
        return result | {"reason": "insufficient_reference_coast"}
    tile = image[y - pad : y + pad + 1, x - pad : x + pad + 1]
    valid = (tile != 0) & usable_rows[y - pad : y + pad + 1, None]
    # The blur, Sobel and nonmaximum suppression must not see a missing-data boundary.
    safe = cv2.erode(valid.astype(np.uint8), np.ones((9, 9), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
    support = correlate(safe, template) / template.sum()
    centers = safe[half:-half, half:-half] != 0
    allowed = centers & (support >= cfg.minimum_valid_fraction)
    if not allowed.any():
        return result | {"reason": "insufficient_image_support"}
    # PNGs may carry higher-bit-depth detector counts. Scale only valid
    # samples for the 8-bit edge detector. Constant tiles remain edgeless.
    if tile.dtype == np.uint16:
        low, high = np.percentile(tile[safe != 0], [1, 99])
        if high <= low:
            return result | {"reason": "no_image_contrast"}
        tile = np.clip((tile.astype(np.float32) - low) * (255 / (high - low)), 0, 255).astype(np.uint8)
    smooth = cv2.GaussianBlur(tile, (5, 5), 1)
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1)
    high = max(12.0, float(np.percentile(np.hypot(gx, gy)[safe != 0], 75)))
    edges = (cv2.Canny(smooth, 0.5 * high, high, L2gradient=True) != 0) & (safe != 0)
    if not edges.any():
        return result | {"reason": "no_image_edges"}
    observed_angle = np.arctan2(gy, gx)
    bins = np.rint(normal_angles(template) * 4 / np.pi).astype(int) % 4
    scores = np.zeros(allowed.shape, dtype=np.float32)
    center_scores = np.zeros_like(scores)
    for k in range(4):
        model_edges = template & (bins == k)
        compatible = edges & (np.abs(np.cos(observed_angle - k * np.pi / 4)) >= np.cos(np.pi / 4))
        if not model_edges.any() or not compatible.any():
            continue
        distance = cv2.distanceTransform((~compatible).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        evidence = safe * np.exp(-0.5 * (distance / cfg.distance_sigma_px) ** 2)
        scores += correlate(evidence, model_edges)
        center_scores += correlate(evidence, model_edges & central)
    scores /= template.sum()
    center_scores /= central.sum()
    scores[~allowed] = -np.inf
    iy, ix = np.unravel_index(np.argmax(scores), scores.shape)
    score = float(scores[iy, ix])
    sy, sx = np.indices(scores.shape)
    rivals = allowed & ((sx - ix) ** 2 + (sy - iy) ** 2 >= cfg.competitor_distance_px**2)
    margin = float(score - np.max(scores[rivals])) if rivals.any() else None
    shift = np.array([ix - search, iy - search])
    result.update(
        score=score,
        center_score=float(center_scores[iy, ix]),
        peak_margin=margin,
        valid_coast_fraction=float(np.clip(support[iy, ix], 0, 1)),
        best_shift_px=shift.tolist(),
    )
    if np.abs(shift).max() == search:
        reason = "search_boundary"
    elif score < cfg.minimum_score or center_scores[iy, ix] < cfg.minimum_center_score:
        reason = "weak_edge_support"
    elif margin is None or margin < cfg.minimum_peak_margin:
        reason = "ambiguous_peak"
    else:
        return result | {"accepted": True, "reason": "matched", "measured_pixel": (predicted + shift).tolist()}
    return result | {"reason": reason}


def match_pass(folder, orbit, catalog=CATALOG, allow_nonstandard=False):
    folder = folder.expanduser().resolve()
    p = load_pass(folder, channels=(2,))
    if not p.identity or p.identity.status != "consistent" or p.status != "ok" or 2 not in p.images:
        raise ValueError("matching requires consistent identity, ok timing and channel 2")
    usable = p.usable_lines((2,))
    lines, meta = load_coastline()
    targets = json.loads(catalog.read_text())
    vertices = np.concatenate(lines)
    entries = targets["landmarks"]
    ids = [entry["vertex"] for entry in entries]
    if (
        targets["reference_sha256"] != meta["asset"]["sha256"]
        or not ids
        or any(type(i) is not int or not 0 <= i < len(vertices) for i in ids)
        or len(set(ids)) != len(ids)
        or any(not isinstance(e["name"], str) or not e["name"].strip() for e in entries)
    ):
        raise ValueError("catalog must identify the pinned map and distinct named vertices")
    sat, source = select_orbit(p, orbit, allow_nonstandard=allow_nonstandard)
    model = MapPredictor(sat, observation_times(p, "fitted"), Scanner.from_pass(p))
    ll = vertices[ids]
    xy = model.geo_to_pixel(ll[:, 1], ll[:, 0])
    # This is a geographic template. Missing imagery is screened at every trial translation.
    coast = render_coastline(model, lines, np.isfinite(model.times)) if np.isfinite(xy).all(axis=1).any() else None
    points = []
    for entry, pos in zip(entries, xy, strict=True):
        measurement = match_chip(p.images[2], usable, coast, pos)
        points.append({"vertex": entry["vertex"], "name": entry["name"], **measurement})
    provenance = implementation_record()
    provenance["packages"]["sgp4"] = version("sgp4")
    return {
        "schema_version": 1,
        "pass_name": p.name,
        "norad": p.norad,
        "channel": 2,
        "coordinates": "zero-based original PNG column, row",
        "timing": "fitted",
        "orbit": source,
        "map": meta["asset"],
        "catalog": fingerprint(catalog),
        "settings": asdict(SETTINGS),
        "implementation": provenance,
        "inputs": input_files(folder.parent, p),
        "points": points,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pass_folder", type=Path)
    parser.add_argument("--orbit", required=True, help="local OMM JSON file or embedded")
    parser.add_argument("--allow-nonstandard-tle", action="store_true")
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--output", required=True, type=Path, help="new JSON file outside the archive")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    try:
        if output.exists() or output.is_relative_to(args.pass_folder.expanduser().resolve().parent):
            raise ValueError("choose a new output file outside the archive")
        report = match_pass(args.pass_folder, args.orbit, args.catalog, args.allow_nonstandard_tle)
        data = json_bytes(report)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as f:
            f.write(data)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Matching failed: {exc}\n")
    print(f"Accepted {sum(p['accepted'] for p in report['points'])} of {len(report['points'])} landmarks. {output}")


if __name__ == "__main__":
    main()
