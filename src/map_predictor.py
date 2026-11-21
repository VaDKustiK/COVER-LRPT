import argparse
import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from sgp4.exporter import export_omm
from skyfield.api import EarthSatellite

from .dataset_audit import fingerprint, implementation_record, input_files, json_bytes
from .geometry import MapPredictor, Scanner, observation_times
from .geometry_orbit import select_orbit
from .pass_loader import PassData, load_pass
from .reference_map import DEFAULT_MAP, load_coastline, render_coastline


def landmark_report(model: MapPredictor, data: dict[str, Any], p: PassData) -> dict[str, Any]:
    if data.get("pass_name") != p.name or data.get("channel") != 2:
        raise ValueError("landmarks must identify this pass and raw channel 2")
    points = data["points"]
    if not points or len({point["id"] for point in points}) != len(points):
        raise ValueError("landmarks must be nonempty with distinct IDs")
    ll = np.asarray([[v["latitude"], v["longitude"]] for v in points], dtype=float)
    observed = np.asarray([[v["column"], v["row"]] for v in points], dtype=float)
    if not np.isfinite(ll).all() or not np.isfinite(observed).all() or (np.abs(ll[:, 0]) > 90).any():
        raise ValueError("invalid landmark coordinates")
    if (
        (observed < 0).any()
        or (observed[:, 0] > model.scanner.width - 1).any()
        or (observed[:, 1] > model.height - 1).any()
    ):
        raise ValueError("observed landmarks must lie in the original image")
    predicted = model.geo_to_pixel(ll[:, 0], ll[:, 1])
    residual = observed - predicted
    usable = p.usable_lines((2,))[np.rint(observed[:, 1]).astype(int)]
    valid = np.isfinite(predicted).all(axis=1) & usable
    rows = []
    for i, point in enumerate(points):
        rows.append(
            {
                **point,
                "accepted_for_residual": bool(valid[i]),
                "predicted_column": float(predicted[i, 0]) if valid[i] else None,
                "predicted_row": float(predicted[i, 1]) if valid[i] else None,
                "observed_minus_predicted_px": residual[i].tolist() if valid[i] else None,
            }
        )
    norms = np.linalg.norm(residual[valid], axis=1)
    return {
        "annotation_provenance": {k: v for k, v in data.items() if k != "points"},
        "points": rows,
        "accepted": int(valid.sum()),
        "total": len(points),
        "median_residual_px": float(np.median(norms)) if len(norms) else None,
        "maximum_residual_px": float(np.max(norms)) if len(norms) else None,
        "interpretation": "image/map discrepancy, not satellite position error",
    }


def sensitivity_report(model: MapPredictor, p: PassData) -> dict[str, Any]:
    """Finite differences at fixed ground targets, not an orbit fit or error covariance."""
    good = np.flatnonzero(p.usable_lines((2,)))
    rows = good[np.linspace(0, len(good) - 1, min(9, len(good))).astype(int)]
    # Avoid boundary points leaving the image under perturbation.
    rows = rows[(rows > 5) & (rows < model.height - 6)]
    x, y = np.meshgrid(np.array([0.2, 0.5, 0.8]) * (model.scanner.width - 1), rows)
    x, y = x.ravel(), y.ravel()
    lat, lon = model.pixel_to_geo(x, y)
    base = np.column_stack((x, y))
    experiments = []
    columns = []
    for name, step in (
        ("clock_s", 0.1),
        ("roll_deg", 0.01),
        ("pitch_deg", 0.01),
        ("yaw_deg", 0.01),
        ("mean_anomaly_deg", 0.01),
    ):
        positions = []
        for sign in (-1, 1):
            scanner, satellite, times = model.scanner, model.satellite, model.times
            if name == "clock_s":
                times = times + sign * step
            elif name == "mean_anomaly_deg":
                fields = export_omm(satellite.model, "sensitivity experiment")
                fields["MEAN_ANOMALY"] = (fields["MEAN_ANOMALY"] + sign * step) % 360
                satellite = EarthSatellite.from_omm(model.ts, fields)
            else:
                scanner = replace(scanner, **{name: getattr(scanner, name) + sign * step})
            changed = MapPredictor(satellite, times, scanner)
            positions.append(changed.geo_to_pixel(lat, lon))
        derivative = (positions[1] - positions[0]) / (2 * step)
        valid = np.isfinite(derivative).all(axis=1)
        columns.append(derivative.ravel())
        experiments.append(
            {
                "parameter": name,
                "symmetric_step": step,
                "supported_targets": int(valid.sum()),
                "median_pixel_shift_for_positive_step": (
                    np.nanmedian(positions[1] - base, axis=0).tolist() if valid.any() else None
                ),
                "derivative_px_per_unit": [
                    v.tolist() if ok else None for v, ok in zip(derivative, valid, strict=True)
                ],
            }
        )
    matrix = np.asarray(columns).T
    complete = np.isfinite(matrix).all(axis=1)
    matrix = matrix[complete]
    norms = np.linalg.norm(matrix, axis=0)
    correlation = None
    singular_values = None
    if len(matrix) and (norms > 0).all():
        normalized = matrix / norms
        correlation = (normalized.T @ normalized).tolist()
        singular_values = np.linalg.svd(normalized, compute_uv=False).tolist()
    return {
        "targets_raw_pixels": base.tolist(),
        "experiments": experiments,
        "normalized_derivative_cosines": correlation,
        "normalized_singular_values": singular_values,
        "interpretation": "local sensitivity; parallel columns indicate confounding, not identifiability proof",
    }


def build_prediction(
    folder: Path,
    *,
    orbit: str,
    timing: str,
    output: Path,
    allow_nonstandard: bool = False,
    coastline: Path = DEFAULT_MAP,
    landmarks: Path | None = None,
    time_offset_s: float = 0,
    roll_delta_deg: float = 0,
    pitch_delta_deg: float = 0,
    yaw_delta_deg: float = 0,
    grid_step: int = 32,
    sensitivity: bool = False,
) -> dict[str, Any]:
    folder, output = folder.expanduser().resolve(), output.expanduser().resolve()
    if output.is_relative_to(folder.parent):
        raise ValueError("outputs must be outside the archive root")
    if output.exists():
        raise FileExistsError("choose a new output directory; previous results are not overwritten")
    if grid_step < 1:
        raise ValueError("grid step must be positive")
    started = time.perf_counter()
    p = load_pass(folder, channels=(2,))
    if not p.identity or p.identity.status != "consistent" or p.status != "ok" or 2 not in p.images:
        raise ValueError("prediction requires consistent identity, ok timing and channel 2")
    usable = p.usable_lines((2,))
    if not usable.any():
        raise ValueError("no usable channel-2 rows")
    satellite, orbit_record = select_orbit(p, orbit, allow_nonstandard=allow_nonstandard)
    scanner = Scanner.from_pass(p)
    scanner = replace(
        scanner,
        roll_deg=scanner.roll_deg + roll_delta_deg,
        pitch_deg=scanner.pitch_deg + pitch_delta_deg,
        yaw_deg=scanner.yaw_deg + yaw_delta_deg,
    )
    times = observation_times(p, timing, time_offset_s)
    model = MapPredictor(satellite, times, scanner)
    lines, map_record = load_coastline(coastline)
    edges = render_coastline(model, lines, usable)
    x, y = np.meshgrid(
        np.unique(np.r_[np.arange(0, scanner.width, grid_step), scanner.width - 1]),
        np.unique(np.r_[np.arange(0, p.n_lines, grid_step), p.n_lines - 1]),
    )
    lat, lon = model.pixel_to_geo(x, y)
    overlay = cv2.cvtColor(p.images[2], cv2.COLOR_GRAY2BGR)
    overlay[edges != 0] = (0, 0, 255)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "pass_name": p.name,
        "norad": p.norad,
        "channel": 2,
        "coordinate_convention": "original PNG, zero-based (column,row), no flips/resampling",
        "orbit": orbit_record,
        "scanner": asdict(scanner),
        "timing": {
            "convention": timing,
            "configured_offset_s": p.cfg.get("timestamp_offset", 0),
            "additional_offset_s": time_offset_s,
            "offset_applied_once": True,
            "within_scan_timing": "instantaneous row approximation",
            "absolute_utc_accuracy": "unknown",
            "finite_rows": int(np.isfinite(times).sum()),
            "usable_rows": int(usable.sum()),
            "nonincreasing_adjacent_finite_times": int(np.sum(np.diff(times) <= 0)),
        },
        "map": map_record,
        "inputs": input_files(folder.parent, p),
        "implementation": implementation_record(),
        "time_tables": "Skyfield bundled leap-second and delta-T tables; no external polar motion table",
        "shape": list(p.images[2].shape),
        "coastline_pixels": int(np.count_nonzero(edges)),
        "grid_step_px": grid_step,
        "geometry_validity": "finite times and forward ellipsoid intersection",
        "limitations": [
            "reference map not surveyed truth",
            "no DEM or atmospheric refraction",
            "row state interpolation between adjacent finite rows",
            "attitude offsets uncalibrated",
            "decoder TLE is not the missing recording OMM",
            "no orbit correction performed",
        ],
    }
    report["implementation"]["packages"]["sgp4"] = version("sgp4")
    if landmarks:
        report["landmarks_input"] = fingerprint(landmarks)
        report["landmarks"] = landmark_report(model, json.loads(landmarks.read_text()), p)
    if sensitivity:
        report["sensitivity"] = sensitivity_report(model, p)
    report["runtime_seconds_before_writing"] = time.perf_counter() - started
    # Serialize before creating any outputs; reject nonfinite JSON rather than hide it.
    json_bytes(report)
    output.mkdir(parents=True, exist_ok=False)
    for name, img in (("coastline.png", edges), ("overlay.png", overlay)):
        if not cv2.imwrite(str(output / name), img):
            raise OSError(f"could not write {name}")
    np.savez_compressed(
        output / "geometry.npz",
        columns=x,
        rows=y,
        latitude=lat,
        longitude=lon,
        observation_times=times,
        usable_rows=usable,
    )
    report["artifacts"] = {
        name: fingerprint(output / name) for name in ("coastline.png", "overlay.png", "geometry.npz")
    }
    (output / "report.json").write_bytes(json_bytes(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pass_folder", type=Path)
    parser.add_argument("--orbit", required=True, help="embedded, or an explicit local OMM JSON path")
    parser.add_argument("--timing", required=True, choices=("fitted", "satdump"))
    parser.add_argument("--allow-nonstandard-tle", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--coastline", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--landmarks", type=Path)
    parser.add_argument("--time-offset-s", type=float, default=0)
    parser.add_argument("--roll-delta-deg", type=float, default=0)
    parser.add_argument("--pitch-delta-deg", type=float, default=0)
    parser.add_argument("--yaw-delta-deg", type=float, default=0)
    parser.add_argument("--grid-step", type=int, default=32)
    parser.add_argument("--sensitivity", action="store_true")
    args = vars(parser.parse_args())
    folder = args.pop("pass_folder")
    args["allow_nonstandard"] = args.pop("allow_nonstandard_tle")
    try:
        report = build_prediction(folder, **args)
        print(
            json.dumps(
                {k: report[k] for k in ("pass_name", "shape", "coastline_pixels", "runtime_seconds_before_writing")}
            )
        )
        print(f"Saved prediction to {args['output']}")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Prediction failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
