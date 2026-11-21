import argparse
import json
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

import numpy as np

from .dataset_audit import fingerprint, implementation_record, input_files, json_bytes, verify_manifest, within
from .geometry import MapPredictor, Scanner, observation_times
from .geometry_orbit import select_orbit
from .map_predictor import perturbed_model
from .pass_loader import load_pass
from .reference_map import load_coastline

STEPS = {
    "clock_s": 0.1,
    "roll_deg": 0.01,
    "pitch_deg": 0.01,
    "yaw_deg": 0.01,
    "mean_anomaly_deg": 0.01,
    "node_deg": 0.01,
}
PARAMETERS = [*STEPS, "scan_duration_s"]
NUISANCE = ["clock_s", "roll_deg", "pitch_deg", "yaw_deg", "scan_duration_s"]


def load_sheets(paths, dev_names, reference):
    records = []
    readers = set()
    for path in paths:
        data = json.loads(path.read_text())
        reader = data["reader"]
        if (
            not isinstance(reader, str)
            or not reader.strip()
            or reader in readers
            or data["reader_type"] not in ("ai", "human")
            or data["schema_version"] != 1
            or data["channel"] != 2
            or data["reference_sha256"] != reference
            or data["other_picks_hidden"] is not True
            or data["prediction_hidden"] is not True
        ):
            raise ValueError("expected distinct readers, concealed predictions/picks, raw channel 2 and pinned map")
        readers.add(reader)
        if data["map_sigma_m"] is not None and not (np.isfinite(data["map_sigma_m"]) and data["map_sigma_m"] > 0):
            raise ValueError("map uncertainty must be positive or null")
        if not data["map_error_basis"].strip():
            raise ValueError("describe the reference uncertainty")
        passes = data["passes"]
        names = [v["pass_name"] for v in passes]
        if not names or len(names) != len(set(names)) or not set(names) <= dev_names:
            raise ValueError("review sheets must identify distinct development passes")
        records.append(
            (
                {k: v for k, v in data.items() if k != "passes"} | {"file": fingerprint(path)},
                {v["pass_name"]: v for v in passes},
            )
        )
    return records


def checked_points(sheet, image_sha, vertices, image, usable):
    if sheet["image_sha256"] != image_sha:
        raise ValueError("review sheet refers to different image bytes")
    if not sheet["points"]:
        raise ValueError("empty landmark sheet")
    result = {}
    for point in sheet["points"]:
        v = point["vertex"]
        if (
            type(v) is not int
            or not 0 <= v < len(vertices)
            or v in result
            or not point["definition"].strip()
            or not point["reason"].strip()
        ):
            raise ValueError("each reference needs a unique valid vertex, definition and reason")
        entry = {key: point[key] for key in ("vertex", "definition", "status", "reason")}
        entry["lonlat"] = vertices[v].tolist()
        result[v] = entry
        if point["status"] == "rejected" and point.get("pixel") is None:
            continue
        if point["status"] != "picked":
            raise ValueError("finish all picks or give a rejection reason before analysis")
        xy = np.asarray(point["pixel"], dtype=float)
        radius = point["picking_radius_px"]
        if (
            xy.shape != (2,)
            or not np.isfinite(xy).all()
            or (xy < 0).any()
            or (xy > np.asarray(image.shape[::-1]) - 1).any()
            or not np.isfinite(radius)
            or radius <= 0
        ):
            raise ValueError("invalid original pixel or picking radius")
        x, y = np.rint(xy).astype(int)
        entry.update(pixel=xy.tolist(), picking_radius_px=radius, usable_pixel=bool(usable[y] and image[y, x] != 0))
    return result


def separation(design, orbit_parameter, free=NUISANCE):
    """Unweighted Euclidean projection, one nuisance vector per parameter for this pass."""
    a = design.reshape(-1, len(PARAMETERS))
    b = a[:, PARAMETERS.index(orbit_parameter)] * STEPS[orbit_parameter]
    x = a[:, [PARAMETERS.index(n) for n in free]]
    if not len(b):
        return None
    coeff, _, rank, _ = np.linalg.lstsq(x, b, rcond=None)
    unique = b - x @ coeff
    norm = np.linalg.norm(b)
    return {
        "points": len(design),
        "nuisance_rank": int(rank),
        "remaining_coordinate_dof": len(b) - int(rank),
        "orbital_step_deg": STEPS[orbit_parameter],
        "total_vector_rms_px": float(np.linalg.norm(b) / np.sqrt(len(design))),
        "remaining_vector_rms_px": float(np.linalg.norm(unique) / np.sqrt(len(design))),
        "remaining_norm_fraction": float(np.linalg.norm(unique) / norm) if norm else None,
        "equivalent_nuisance_change": dict(zip(free, coeff.tolist(), strict=True)),
    }


def finite_phase_check(model, grid, design, valid, usable):
    """Choose a clock/node equivalent on alternate grid targets and reproject the others."""
    idx = np.flatnonzero(valid)
    train, test = idx[::2], idx[1::2]
    if not len(test):
        return None
    free = ["clock_s", "node_deg"]
    fit = separation(design[train], "mean_anomaly_deg", free)
    if fit["nuisance_rank"] != len(free):
        return None
    changes = fit["equivalent_nuisance_change"]
    phase = perturbed_model(model, "mean_anomaly_deg", STEPS["mean_anomaly_deg"])
    node = perturbed_model(model, "node_deg", changes["node_deg"])
    alternative = MapPredictor(node.satellite, model.times + changes["clock_s"], model.scanner)
    ll = grid[test]
    first, second = (m.geo_to_pixel(ll[:, 1], ll[:, 0]) for m in (phase, alternative))
    good = np.isfinite(first).all(axis=1) & np.isfinite(second).all(axis=1)
    for xy in (first, second):
        y = np.where(good, xy[:, 1], 0)
        good &= usable[np.floor(y).astype(int)] & usable[np.ceil(y).astype(int)]
    distances = np.linalg.norm(first[good] - second[good], axis=1)
    return {
        "train_grid_indices": train.tolist(),
        "check_grid_indices": test.tolist(),
        "supported_check_indices": test[good].tolist(),
        "alternative": changes,
        "mean_anomaly_deg": STEPS["mean_anomaly_deg"],
        "max_forward_difference_px": float(distances.max()) if len(distances) else None,
    }


def design_at(model, ll, baseline, usable, scale):
    derivatives = []

    # A finite perturbation must stay on the same contiguous usable run as the base.
    run_id = np.cumsum(usable & ~np.r_[False, usable[:-1]])

    def support(xy):
        finite = np.isfinite(xy).all(axis=1)
        rows = np.rint(np.where(finite, xy[:, 1], 0)).astype(int)
        lo = np.floor(np.where(finite, xy[:, 1], 0)).astype(int)
        hi = np.ceil(np.where(finite, xy[:, 1], 0)).astype(int)
        return np.where(finite & usable[lo] & usable[hi], run_id[rows], 0)

    origin = support(baseline)
    valid = origin > 0
    for name, step in STEPS.items():
        delta = scale * step
        plus, minus = (perturbed_model(model, name, sign * delta).geo_to_pixel(ll[:, 1], ll[:, 0]) for sign in (1, -1))
        valid &= (support(plus) == origin) & (support(minus) == origin)
        derivatives.append((plus - minus) / (2 * delta))
    # First-order change from a linear acquisition-time ramp across the swath.
    phase = baseline[:, 0] / (model.scanner.width - 1) - 0.5
    derivatives.append(derivatives[0] * phase[:, None])
    return np.stack(derivatives, axis=-1), valid


def grid_targets(model, usable):
    # Interior samples across the usable rows; do not repeatedly count a single short run.
    rows = np.flatnonzero(usable)
    rows = np.unique(np.quantile(rows, np.linspace(0.05, 0.95, 7), method="nearest"))
    x, y = np.meshgrid(np.linspace(0.1, 0.9, 5) * (model.scanner.width - 1), rows)
    lat, lon = model.pixel_to_geo(x.ravel(), y.ravel())
    return np.column_stack((lon, lat))


def compare_readers(groups):
    comparisons = []
    for (a, first), (b, second) in combinations(groups.items(), 2):
        pairs = []
        both_rejected = 0
        status_disagreements = 0
        for v in sorted(first.keys() & second.keys()):
            p, q = first[v], second[v]
            both_rejected += int(p["status"] == q["status"] == "rejected")
            status_disagreements += int(p["status"] != q["status"])
            pair = {"vertex": v, "status": [p["status"], q["status"]]}
            if p.get("usable_pixel") and q.get("usable_pixel"):
                pair["second_minus_first_px"] = (np.asarray(q["pixel"]) - p["pixel"]).tolist()
            pairs.append(pair)
        differences = np.asarray([p["second_minus_first_px"] for p in pairs if "second_minus_first_px" in p])
        comparisons.append(
            {
                "readers": [a, b],
                "shared_references": pairs,
                "first_only_vertices": sorted(first.keys() - second.keys()),
                "second_only_vertices": sorted(second.keys() - first.keys()),
                "both_rejected": both_rejected,
                "pick_reject_disagreements": status_disagreements,
                "paired_count": len(differences),
                # Sample covariance of paired differences. Reader-specific variances are unknown.
                "sample_covariance_of_differences_px2": np.cov(differences.T, ddof=1).tolist()
                if len(differences) >= 2
                else None,
                "mean_difference_xy_px": differences.mean(axis=0).tolist() if len(differences) else None,
                "median_distance_px": float(np.median(np.linalg.norm(differences, axis=1)))
                if len(differences)
                else None,
            }
        )
    return comparisons


def measurement_coverage(group, usable, times, width):
    """Describe where usable observations fall, independently of inverse-model success."""
    rows = np.flatnonzero(usable)
    t0, t1 = np.min(times[rows]), np.max(times[rows])
    pixels = np.asarray([pt["pixel"] for pt in group.values() if pt.get("usable_pixel")]).reshape(-1, 2)
    result = {
        "usable_picks": len(pixels),
        "usable_time_extent_s": float(t1 - t0),
        "column_fraction_range": None,
        "time_fraction_range": None,
        "observed_time_span_s": None,
    }
    if len(pixels):
        col, row = pixels.T
        # Use only the screened native sample associated with each pick.
        sample_times = times[np.rint(row).astype(int)]
        result["column_fraction_range"] = (np.array([col.min(), col.max()]) / (width - 1)).tolist()
        result["observed_time_span_s"] = float(np.ptp(sample_times))
        if t1 > t0:
            result["time_fraction_range"] = (
                (np.array([sample_times.min(), sample_times.max()]) - t0) / (t1 - t0)
            ).tolist()
    return result


def analyze_pass(p, satellite, groups):
    usable = p.usable_lines((2,))
    fitted = observation_times(p, "fitted")
    centered = observation_times(p, "satdump")
    model = MapPredictor(satellite, fitted, Scanner.from_pass(p))
    grid = grid_targets(model, usable)
    references = {v: pt["lonlat"] for g in groups.values() for v, pt in g.items() if pt.get("usable_pixel")}
    lookup = {v: i for i, v in enumerate(references, len(grid))}
    ll = np.vstack((grid, np.asarray(list(references.values())).reshape(-1, 2)))
    base = model.geo_to_pixel(ll[:, 1], ll[:, 0])
    design, valid = design_at(model, ll, base, usable, 1)
    half, half_valid = design_at(model, ll, base, usable, 0.5)
    valid &= half_valid

    def summarize(indices):
        ix = np.asarray(indices, dtype=int)
        chosen = ix[valid[ix]]
        out = {"requested": len(ix), "supported": len(chosen)}
        for name in ("mean_anomaly_deg", "node_deg"):
            out[name] = separation(design[chosen], name)
            out[name + "_half_step_check"] = separation(half[chosen], name)
            out[name + "_clock_only"] = separation(design[chosen], name, ["clock_s"])
            other = "node_deg" if name == "mean_anomaly_deg" else "mean_anomaly_deg"
            out[name + "_with_other_orbit_free"] = separation(design[chosen], name, [*NUISANCE, other])
        return out

    annotations = []
    for reader, group in groups.items():
        use = []
        for v, pt in group.items():
            if pt.get("usable_pixel"):
                i = lookup[v]
                pt["projection_supported"] = bool(valid[i])
                use.append(i)
                if valid[i]:
                    pt["observed_minus_predicted_px"] = (pt["pixel"] - base[i]).tolist()
        residuals = np.array(
            [pt["observed_minus_predicted_px"] for pt in group.values() if "observed_minus_predicted_px" in pt]
        ).reshape(-1, 2)
        supported_indices = [i for i in use if valid[i]]
        discrepancy = None
        if len(residuals):
            nuisance = design[supported_indices].reshape(-1, len(PARAMETERS))[
                :, [PARAMETERS.index(n) for n in NUISANCE]
            ]
            coeff, _, rank, _ = np.linalg.lstsq(nuisance, residuals.ravel(), rcond=None)
            unexplained = residuals.ravel() - nuisance @ coeff
            discrepancy = {
                "median_raw_distance_px": float(np.median(np.linalg.norm(residuals, axis=1))),
                "nuisance_only_in_sample_vector_rms_px": float(np.linalg.norm(unexplained) / np.sqrt(len(residuals))),
                "remaining_coordinate_dof": int(residuals.size - rank),
            }
        annotations.append(
            {
                "reader": reader,
                "coverage": measurement_coverage(group, usable, fitted, model.scanner.width),
                "points": list(group.values()),
                "design": summarize(use),
                "observed_discrepancy": discrepancy,
            }
        )
    diff = (centered - fitted)[usable]
    scan_shifts = design[: len(grid), :, -1][valid[: len(grid)]] * p.line_period
    return {
        "timing": {
            "configured_offset_s": p.cfg.get("timestamp_offset", 0),
            "fitted_row_period_s": p.line_period,
            "decoder_row_step_s": p.cfg["interpolate_timestamps_scantime"],
            "centered_minus_fitted_s_min_median_max": np.quantile(diff, [0, 0.5, 1]).tolist(),
            "centered_nonincreasing_adjacent_rows": int(np.sum(np.diff(centered) <= 0)),
            "fitted_nonincreasing_adjacent_rows": int(np.sum(np.diff(fitted) <= 0)),
            "linear_scan_scenario_edge_offset_s": p.line_period / 2,
            "linear_scan_scenario_max_sample_shift_px": float(np.linalg.norm(scan_shifts, axis=1).max())
            if len(scan_shifts)
            else None,
        },
        "grid_design": summarize(range(len(grid))),
        "finite_phase_check": finite_phase_check(model, grid, design[: len(grid)], valid[: len(grid)], usable),
        "annotations": annotations,
        "reader_agreement": compare_readers(groups),
        "jacobian_columns": PARAMETERS,
        "targets": [
            {
                "kind": "grid" if i < len(grid) else "reference",
                "lonlat": ground.tolist() if np.isfinite(ground).all() else None,
                "predicted_pixel": base[i].tolist() if np.isfinite(base[i]).all() else None,
                "px_per_unit": design[i].tolist() if valid[i] else None,
            }
            for i, ground in enumerate(ll)
        ],
    }


def run(archive, manifest, picks, output, orbit, allow_nonstandard):
    archive, output = archive.expanduser().resolve(), output.expanduser().resolve()
    if output.is_relative_to(archive) or output.exists():
        raise ValueError("choose a new report file outside the archive")
    data = json.loads(manifest.read_text())
    dev = [row for row in data["passes"] if row["selected_group"] == "dev"]
    names = {row["name"] for row in dev}
    if not dev or len(names) != len(dev):
        raise ValueError("need unique audited development passes")
    selected = {"schema_version": data["schema_version"], "passes": dev}
    failures = verify_manifest(archive, selected)
    if failures:
        raise ValueError(f"development inputs changed: {failures}")
    lines, meta = load_coastline()
    vertices = np.concatenate(lines)
    readers = load_sheets(picks, names, meta["asset"]["sha256"])
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "manifest": fingerprint(manifest),
        "implementation": implementation_record(),
        "map": meta,
        "readers": [r for r, _ in readers],
        "finite_difference_steps": STEPS,
        "free_nuisance_per_pass": NUISANCE,
        "orbit_candidates_tested_separately": ["mean_anomaly_deg", "node_deg"],
        "weighting": "equal pixel-coordinate weights, descriptive only",
        "calibrated_measurement_covariance": None,
        "passes": [],
    }
    for row in dev:
        folder = within(archive, row["name"])
        p = load_pass(folder, channels=(2,))
        if not p.identity or p.identity.status != "consistent" or p.status != "ok" or not p.usable_lines((2,)).any():
            raise ValueError(f"ineligible development pass {p.name}")
        inputs = input_files(archive, p)
        if any(row["files"].get(k) != value for k, value in inputs.items()):
            raise ValueError(f"unverified pass inputs {p.name}")
        image_path = p.product_dir / p.channel_metadata[2]["file"]
        image_sha = inputs[str(image_path.relative_to(archive))]["sha256"]
        groups = {
            reader["reader"]: checked_points(sheets[p.name], image_sha, vertices, p.images[2], p.usable_lines((2,)))
            for reader, sheets in readers
            if p.name in sheets
        }
        sat, source = select_orbit(p, orbit, allow_nonstandard=allow_nonstandard)
        report["passes"].append(
            {"name": p.name, "norad": p.norad, "orbit": source, "inputs": inputs, **analyze_pass(p, sat, groups)}
        )
    if verify_manifest(archive, selected):
        raise ValueError("development inputs changed during analysis")
    payload = json_bytes(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as f:
        f.write(payload)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True, help="dataset audit selected-pass manifest")
    parser.add_argument("--picks", type=Path, action="append", default=[], help="one raw-first JSON sheet per reader")
    parser.add_argument("--orbit", required=True, help="embedded or a local OMM JSON file")
    parser.add_argument("--allow-nonstandard-tle", action="store_true")
    parser.add_argument("--output", type=Path, required=True, help="new local JSON report")
    args = parser.parse_args()
    try:
        report = run(args.archive, args.manifest, args.picks, args.output, args.orbit, args.allow_nonstandard_tle)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"Study failed: {exc}\n")
    print(f"Recorded {len(report['passes'])} development passes in {args.output}")


if __name__ == "__main__":
    main()
