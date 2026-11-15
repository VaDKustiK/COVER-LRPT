import json
from pathlib import Path

import pytest

from src.dataset_audit import REPO, build_audit, json_bytes, load_selection, main, verify_manifest, within


def audit_inputs(folder, tmp_path):
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"dev": [folder.name]}))
    policy = json.loads((REPO / "research/dataset_policy.json").read_text())
    policy["selection_roles"] = {"dev": "development"}
    policy_path = tmp_path / "policy.json"
    policy_path.write_bytes(json_bytes(policy))
    return selection, policy_path


def test_audit_records_and_verifies_input_identity(decoded_pass, tmp_path):
    selection, policy = audit_inputs(decoded_pass, tmp_path)
    report, manifest = build_audit(decoded_pass.parent, selection, policy)
    assert report["summary"]["product_count"] == 1
    row = manifest["passes"][0]
    assert row["admitted_for"] == "geometry_and_measurement_development"
    assert row["orbit"]["recording_omm_resolution"] == "not_resolved_from_this_archive"
    assert row["orbit"]["decoder_orbit_availability_time_utc"] is None
    assert row["orbit"]["checksum_status"] == ["nonstandard", "nonstandard"]
    assert row["timing"]["absolute_utc_accuracy"] == "unknown"
    assert row["timing"]["timed_fraction"] == 0.95
    assert len(row["files"]) == 6
    assert all(not Path(path).is_absolute() for path in row["files"])
    assert not verify_manifest(decoded_pass.parent, manifest)
    (decoded_pass / "metadata.json").write_text("{}")
    assert any("metadata.json" in issue for issue in verify_manifest(decoded_pass.parent, manifest))


def test_missing_selected_pass_fails_instead_of_dropping_it(decoded_pass, tmp_path):
    selection, policy = audit_inputs(decoded_pass, tmp_path)
    selection.write_text(json.dumps({"dev": ["1791620351_20261010T081911Z_57166"]}))
    with pytest.raises(ValueError, match="missing from archive"):
        build_audit(decoded_pass.parent, selection, policy)


@pytest.mark.parametrize(
    "manifest", [{}, {"schema_version": 1, "passes": []}, {"schema_version": 1, "passes": [{"files": {}}]}]
)
def test_empty_manifest_cannot_report_success(tmp_path, manifest):
    with pytest.raises(ValueError):
        verify_manifest(tmp_path, manifest)


def test_missing_consumed_file_is_reported(tmp_path):
    manifest = {"schema_version": 1, "passes": [{"files": {"absent.png": {"size_bytes": 1, "sha256": "0" * 64}}}]}
    assert "unavailable: absent.png" in verify_manifest(tmp_path, manifest)[0]


def test_duplicate_membership_rejected(tmp_path):
    selection = tmp_path / "selection.json"
    name = "1791705353_20261011T075553Z_57166"
    selection.write_text(json.dumps({"dev": [name], "heldout": [name]}))
    with pytest.raises(ValueError, match="duplicate"):
        load_selection(selection, {"dev": "development", "heldout": "validation"})


@pytest.mark.parametrize("relative", ["../outside", "/tmp/outside"])
def test_manifest_paths_cannot_escape_archive(tmp_path, relative):
    with pytest.raises(ValueError, match="relative"):
        within(tmp_path, relative)


def test_symlink_cannot_escape_archive(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    (root / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="escapes"):
        within(root, "link")


def test_cli_never_writes_reports_into_archive(decoded_pass, monkeypatch):
    output = decoded_pass.parent / "reports"
    monkeypatch.setattr(
        "sys.argv", ["audit", "build", "--archive", str(decoded_pass.parent), "--output-dir", str(output)]
    )
    assert main() == 1
    assert not output.exists()


def test_cli_build_verify_and_refuse_overwrite(decoded_pass, tmp_path, monkeypatch):
    selection, policy = audit_inputs(decoded_pass, tmp_path)
    output = tmp_path / "reports"
    argv = [
        "audit",
        "build",
        "--archive",
        str(decoded_pass.parent),
        "--selection",
        str(selection),
        "--policy",
        str(policy),
        "--output-dir",
        str(output),
    ]
    monkeypatch.setattr("sys.argv", argv)
    assert main() == 0
    before = (output / "selected_passes.json").read_bytes()
    assert main() == 1
    assert (output / "selected_passes.json").read_bytes() == before
    monkeypatch.setattr(
        "sys.argv",
        ["audit", "verify", "--archive", str(decoded_pass.parent), "--manifest", str(output / "selected_passes.json")],
    )
    assert main() == 0
