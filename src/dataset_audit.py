import argparse
import hashlib
import json
import platform
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from . import imagery, timing
from .pass_loader import PassData, load_pass

REPO = Path(__file__).resolve().parent.parent
REPORT_NAMES = ("archive_audit.json", "selected_passes.json")


def fingerprint(path: Path) -> dict[str, Any]:
    """Hash bytes actually used, without copying, altering, or trusting file dates."""
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"size_bytes": path.stat().st_size, "sha256": digest}


def within(root: Path, relative: str) -> Path:
    """Portable manifests only refer to files inside their declared root."""
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"expected a relative path without '..': {relative}")
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes root: {relative}")
    return path


def load_selection(path: Path, roles: dict[str, str]) -> dict[str, str]:
    groups = json.loads(path.read_text())
    if not isinstance(groups, dict) or set(groups) != set(roles):
        raise ValueError("selection groups must exactly match policy selection_roles")
    result: dict[str, str] = {}
    for group, names in groups.items():
        if not isinstance(names, list) or not names:
            raise ValueError(f"selection group {group} must be a nonempty list")
        for name in names:
            if not isinstance(name, str):
                raise ValueError("pass names must be strings")
            timing.aos_from_folder(name)
            if name in result:
                raise ValueError(f"duplicate pass in selection: {name}")
            result[name] = group
    return result


def orbit_record(p: PassData) -> dict[str, Any]:
    record: dict[str, Any] = {
        "recording_omm_snapshot_id": p.metadata.get("omm_snapshot_id"),
        "recording_omm_resolution": "not_resolved_from_this_archive",
        "decoder_version": None,
        "decoder_orbit_source": "product.cbor projection_cfg.tle",
        "decoder_orbit_availability_time_utc": None,
        "tle": list(p.tle) if p.tle else None,
        "epoch_utc": None,
        "age_at_recording_anchor_days": None,
        "checksum_status": [],
    }
    if p.tle:
        try:
            yy = int(p.tle[0][18:20])
            year = 1900 + yy if yy >= 57 else 2000 + yy
            epoch = datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=float(p.tle[0][20:32]) - 1)
            record["epoch_utc"] = epoch.isoformat()
            record["age_at_recording_anchor_days"] = (p.aos - epoch.timestamp()) / 86400
        except ValueError, OverflowError:
            record["epoch_parse_error"] = True
        for line in p.tle:
            if len(line) != 69 or not line[-1].isdigit():
                record["checksum_status"].append("nonstandard")
            else:
                total = sum(int(c) if c.isdigit() else int(c == "-") for c in line[:68])
                record["checksum_status"].append("valid" if total % 10 == int(line[-1]) else "invalid")
    return record


def timing_record(p: PassData) -> dict[str, Any]:
    model = p.time_model
    return {
        "status": p.status,
        "absolute_utc_accuracy": "unknown",
        "raw_frame_count": int(p.raw_timestamps.size),
        "candidate_frame_count": model.n_candidates,
        "modeled_frame_count": sum(int(s.frames.size) for s in model.segments),
        "image_row_count": p.n_lines,
        "timed_fraction": p.timed_fraction,
        "frame_period_s": model.period if np.isfinite(model.period) else None,
        "approximate_row_period_s": p.line_period if np.isfinite(p.line_period) else None,
        "segment_rms_s": [s.rms_s for s in model.segments],
        "timestamp_offset_applied": False,
        "row_time_convention": "frame_first_row_uniform_fitted_period",
    }


def pass_summary(p: PassData) -> dict[str, Any]:
    return {
        "name": p.name,
        "norad": p.norad,
        "recording_anchor_utc": datetime.fromtimestamp(p.aos, UTC).isoformat(),
        "identity": asdict(p.identity) if p.identity else None,
        "timing": timing_record(p),
        "channels_available": p.channels_available,
        "orbit": orbit_record(p),
    }


def input_files(root: Path, p: PassData) -> dict[str, dict[str, Any]]:
    relative = Path(p.name)
    paths = [
        relative / "metadata.json",
        relative / "satdump_output" / "dataset.json",
        relative / "satdump_output" / "telemetry.json",
        relative / "satdump_output" / "MSU-MR" / "product.cbor",
    ]
    paths.extend(relative / "satdump_output" / "MSU-MR" / p.channel_metadata[ch]["file"] for ch in p.images)
    return {str(rel): fingerprint(within(root, str(rel))) for rel in paths if within(root, str(rel)).is_file()}


def implementation_record() -> dict[str, Any]:
    paths = [
        *sorted((REPO / "src").glob("*.py")),
        REPO / "pyproject.toml",
        REPO / "uv.lock",
    ]
    packages = ("numpy", "cbor2", "opencv-python-headless", "skyfield")
    result: dict[str, Any] = {
        "python": platform.python_version(),
        "packages": {name: version(name) for name in packages},
        "files": {str(p.relative_to(REPO)): fingerprint(p) for p in paths},
    }
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=False)
    result["git_head_for_context_only"] = git.stdout.strip() if git.returncode == 0 else None
    result["authoritative_version"] = "file hashes include uncommitted source; git HEAD alone is insufficient"
    return result


def screening_parameters() -> dict[str, Any]:
    return {
        "timing": {
            key: getattr(timing, key)
            for key in (
                "NOMINAL_PERIOD_S",
                "WINDOW_BEFORE_S",
                "WINDOW_AFTER_S",
                "TIME_OUTLIER_S",
                "MIN_SEGMENT_FRAMES",
                "MIN_CONSISTENT_FRACTION",
                "MAX_INTERNAL_RMS_S",
            )
        },
        "imagery": {
            key: getattr(imagery, key)
            for key in ("MIN_VALID_FRAC", "BRIDGE_LINES", "MIN_SEGMENT_LINES", "DAYLIGHT_MEDIAN")
        },
        "threshold_origin": "existing empirical heuristics, not literature-derived confidence bounds",
    }


def build_audit(root: Path, selection_path: Path, policy_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    policy = json.loads(policy_path.read_text())
    selection = load_selection(selection_path, policy["selection_roles"])
    paths = sorted(root.glob("*/satdump_output/MSU-MR/product.cbor"))
    if not paths:
        raise ValueError("no pass products found in archive")
    missing = set(selection) - {p.parents[2].name for p in paths}
    if missing:
        raise ValueError(f"selected passes missing from archive: {sorted(missing)}")
    rows, selected_rows = [], []
    for path in paths:
        name = path.parents[2].name
        try:
            # Reject linked inputs outside the archive, including optional identity evidence.
            for rel in (
                "metadata.json",
                "satdump_output/dataset.json",
                "satdump_output/telemetry.json",
                "satdump_output/MSU-MR/product.cbor",
            ):
                within(root, f"{name}/{rel}")
            p = load_pass(
                path.parents[2],
                channels=policy["required_channels"] if name in selection else (),
                strict_identity=False,
            )
            row = pass_summary(p)
            row["selected_group"] = selection.get(name)
            row["evidence_files"] = input_files(root, p)
            rows.append(row)
            if name in selection:
                reasons = []
                if not p.identity or p.identity.status != policy["required_identity_status"]:
                    reasons.append("identity_not_consistent")
                if p.status not in policy["accepted_timing_statuses"]:
                    reasons.append("timing_not_accepted")
                if row["orbit"]["epoch_utc"] is None:
                    reasons.append("decoder_orbit_epoch_unavailable")
                if not set(policy["required_channels"]) <= p.images.keys():
                    reasons.append("required_images_not_loaded")
                elif not p.usable_lines(policy["required_channels"]).any():
                    reasons.append("no_screened_rows")
                selected_rows.append(
                    {
                        **row,
                        "role": policy["selection_roles"][selection[name]],
                        "admitted_for": policy["allowed_use"] if not reasons else None,
                        "exclusion_reasons": reasons,
                        "projection_cfg": {k: v for k, v in p.cfg.items() if k != "tle"},
                        "channel_metadata": p.channel_metadata,
                        "images": {
                            ch: {"shape": list(img.shape), "dtype": str(img.dtype)} for ch, img in p.images.items()
                        },
                        "files": row["evidence_files"],
                    }
                )
        except Exception as exc:
            if name in selection:
                raise ValueError(f"selected pass {name} could not be audited: {exc}") from exc
            rows.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
    if any(r["exclusion_reasons"] for r in selected_rows):
        failed = {r["name"]: r["exclusion_reasons"] for r in selected_rows if r["exclusion_reasons"]}
        raise ValueError(f"selection violates input policy: {failed}")
    valid = [r for r in rows if "error" not in r]
    generated = datetime.now(UTC).isoformat()
    summary = {
        "product_count": len(rows),
        "read_error_count": len(rows) - len(valid),
        "identity_statuses": dict(Counter(r["identity"]["status"] for r in valid)),
        "timing_statuses": dict(Counter(r["timing"]["status"] for r in valid)),
        "selection_counts": dict(Counter(selection.values())),
        "first_recording_anchor_utc": min(r["recording_anchor_utc"] for r in valid) if valid else None,
        "last_recording_anchor_utc": max(r["recording_anchor_utc"] for r in valid) if valid else None,
    }
    report = {
        "schema_version": 1,
        "generated_at_utc": generated,
        "summary": summary,
        "passes": rows,
        "scope": "metadata inventory of all products; image arrays inspected only for selected passes",
    }
    manifest = {
        "schema_version": 1,
        "generated_at_utc": generated,
        "archive_root": "supplied_at_runtime; paths below are relative",
        "policy": policy,
        "selection_sha256": fingerprint(selection_path)["sha256"],
        "policy_sha256": fingerprint(policy_path)["sha256"],
        "implementation": implementation_record(),
        "screening_parameters": screening_parameters(),
        "archive_audit_sha256": hashlib.sha256(json_bytes(report)).hexdigest(),
        "unconsumed_inputs": "CADU, power CSVs, and unrequested PNGs are not hashed or used in this experiment",
        "passes": selected_rows,
    }
    return report, manifest


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def verify_manifest(root: Path, manifest: dict[str, Any]) -> list[str]:
    """Check recorded consumed files; integrity does not imply scientific validity."""
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("passes"), list) or not manifest["passes"]:
        raise ValueError("expected a version-1 manifest with a nonempty pass list")
    failures = []
    for row in manifest["passes"]:
        if not isinstance(row, dict) or not isinstance(row.get("files"), dict) or not row["files"]:
            raise ValueError("each manifest pass must have a nonempty file inventory")
        for rel, expected in row["files"].items():
            try:
                actual = fingerprint(within(root, rel))
                if actual != expected:
                    failures.append(f"changed: {rel}")
            except (OSError, ValueError) as exc:
                failures.append(f"unavailable: {rel}: {exc}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="inventory archive and freeze selected decoded inputs")
    build.add_argument("--archive", type=Path, required=True)
    build.add_argument("--selection", type=Path, required=True)
    build.add_argument("--policy", type=Path, default=REPO / "config/dataset_policy.json")
    build.add_argument("--output-dir", type=Path, required=True)
    verify = sub.add_parser("verify", help="verify selected input bytes against a saved manifest")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        root = args.archive.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"archive directory does not exist: {root}")
        if args.command == "verify":
            failures = verify_manifest(root, json.loads(args.manifest.read_text()))
            for failure in failures:
                print(failure, file=sys.stderr)
            print("Input integrity: " + ("FAILED" if failures else "verified"))
            return int(bool(failures))
        output = args.output_dir.expanduser().resolve()
        if output.is_relative_to(root):
            raise ValueError("output directory must be outside the read-only archive")
        for name in REPORT_NAMES:
            if (output / name).exists() or (output / name).is_symlink():
                raise ValueError(f"report already exists; choose a new output directory: {output / name}")
        report, manifest = build_audit(root, args.selection, args.policy)
        payloads = (json_bytes(report), json_bytes(manifest))
        output.mkdir(parents=True, exist_ok=True)
        for name, data in zip(REPORT_NAMES, payloads, strict=True):
            with (output / name).open("xb") as stream:
                stream.write(data)
        print(json.dumps(report["summary"], indent=2))
        print(f"Saved {output / REPORT_NAMES[0]} and {output / REPORT_NAMES[1]}")
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"Audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
