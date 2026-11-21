import json

import cbor2
import numpy as np
import pytest

from src.dataset_audit import fingerprint, input_files
from src.measurement_study import (
    PARAMETERS,
    checked_points,
    compare_readers,
    load_sheets,
    measurement_coverage,
    run,
    separation,
)
from src.pass_loader import load_pass
from src.reference_map import DEFAULT_MAP


def test_separation_detects_known_degeneracy_and_orthogonal_signal():
    design = np.zeros((4, 2, len(PARAMETERS)))
    a = design.reshape(8, -1)
    for i in range(4):
        a[i, i] = 1
    a[:, PARAMETERS.index("mean_anomaly_deg")] = 2 * a[:, 0] - a[:, 1]
    a[-1, PARAMETERS.index("node_deg")] = 1
    phase = separation(design, "mean_anomaly_deg")
    assert phase["remaining_norm_fraction"] < 1e-12
    assert phase["equivalent_nuisance_change"]["clock_s"] == pytest.approx(0.02)
    assert separation(design, "node_deg")["remaining_norm_fraction"] == pytest.approx(1)
    assert separation(design[:0], "node_deg") is None
    # A candidate can be distinct from pointing and still coincide with another orbit parameter.
    a[:, PARAMETERS.index("mean_anomaly_deg")] += a[:, PARAMETERS.index("node_deg")]
    assert (
        separation(design, "node_deg", ["clock_s", "roll_deg", "mean_anomaly_deg"])["remaining_norm_fraction"] < 1e-12
    )


def test_annotation_provenance_and_missing_pixels(tmp_path):
    image = np.ones((5, 5))
    image[2, 2] = 0
    vertices = np.array([[40.0, 29.0], [41.0, 29.0]])
    picked = {
        "vertex": 0,
        "status": "picked",
        "reason": "clear",
        "definition": "tip",
        "pixel": [2, 2],
        "picking_radius_px": 2,
    }
    rejected = {
        "vertex": 1,
        "status": "rejected",
        "definition": "second cape",
        "reason": "cloud",
        "usable_pixel": True,
    }
    sheet = {"pass_name": "dev", "image_sha256": "first", "points": [picked, rejected]}
    valid = np.ones(5, dtype=bool)
    group = checked_points(sheet, "first", vertices, image, valid)
    assert not group[0]["usable_pixel"] and not group[1].get("usable_pixel")
    with pytest.raises(ValueError, match="different image"):
        checked_points(sheet, "second", vertices, image, valid)
    with pytest.raises(ValueError, match="unique"):
        checked_points(sheet | {"points": [picked, picked]}, "first", vertices, image, valid)
    data = {
        "schema_version": 1,
        "reader": "A",
        "reader_type": "human",
        "prediction_hidden": True,
        "other_picks_hidden": True,
        "reference_sha256": "map",
        "channel": 2,
        "map_sigma_m": None,
        "map_error_basis": "unknown",
        "passes": [sheet],
    }
    path = tmp_path / "sheet.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="development"):
        load_sheets([path], {"other"}, "map")
    with pytest.raises(ValueError, match="distinct readers"):
        load_sheets([path, path], {"dev"}, "map")
    path.write_text(json.dumps(data | {"prediction_hidden": False}))
    with pytest.raises(ValueError, match="concealed"):
        load_sheets([path], {"dev"}, "map")
    assert compare_readers({"A": group, "B": group})[0]["paired_count"] == 0
    image[2, 2] = 1
    first = checked_points(sheet, "first", vertices, image, valid)
    second = checked_points(
        sheet | {"points": [picked | {"pixel": [3, 2]}, rejected]}, "first", vertices, image, valid
    )
    assert compare_readers({"A": first, "B": second})[0]["mean_difference_xy_px"] == [1, 0]


def test_offline_study_uses_only_dev_and_refuses_changed_inputs(decoded_pass, tmp_path):
    path = decoded_pass / "satdump_output/MSU-MR/product.cbor"
    product = cbor2.loads(path.read_bytes())
    product["projection_cfg"].update(type="normal_single_line", scan_angle=110.1, interpolate_timestamps_scantime=0.2)
    product["images"][0]["transform"].update(bx=0, by=0)
    path.write_bytes(cbor2.dumps(product))
    p = load_pass(decoded_pass, channels=(2,))
    files = input_files(decoded_pass.parent, p)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "passes": [
                    {"name": p.name, "selected_group": "dev", "files": files},
                    {"name": "untouched", "selected_group": "heldout", "files": {"missing": {}}},
                ],
            }
        )
    )
    out = tmp_path / "result.json"
    args = (decoded_pass.parent, manifest, [])
    with pytest.raises(ValueError, match="outside"):
        run(*args, decoded_pass / "unsafe.json", "embedded", True)
    report = run(*args, out, "embedded", True)
    assert json.loads(out.read_bytes()) == report
    assert report["map"]["asset"] == fingerprint(DEFAULT_MAP)
    assert len(report["passes"]) == 1
    row = report["passes"][0]
    assert row["grid_design"]["supported"] > 0 and row["annotations"] == []
    check = row["finite_phase_check"]
    assert set(check["train_grid_indices"]).isdisjoint(check["check_grid_indices"])
    assert check["supported_check_indices"] == check["check_grid_indices"]
    assert 0.1 < check["alternative"]["clock_s"] < 0.2
    assert 0 < check["alternative"]["node_deg"] < 0.001
    assert check["max_forward_difference_px"] < 1e-4
    assert row["timing"]["centered_nonincreasing_adjacent_rows"] > 0
    with pytest.raises(ValueError, match="new report"):
        run(*args, out, "embedded", True)
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="inputs changed"):
        run(*args, tmp_path / "new.json", "embedded", True)


def test_coverage_and_pair_scatter_use_actual_time_and_supported_picks():
    first = {i: {"pixel": [i, i], "status": "picked", "usable_pixel": i != 1} for i in range(4)}
    second = {
        0: first[0] | {"pixel": [1, 1]},
        2: first[2] | {"pixel": [5, 1]},
        3: {"status": "rejected"},
        4: {"status": "rejected"},
    }
    valid = np.array([True, False, True, True])
    cov = measurement_coverage(first, valid, np.array([10.0, np.nan, 15.0, 22.0]), 7)
    assert cov["observed_time_span_s"] == 12 and cov["time_fraction_range"] == [0, 1]
    assert cov["column_fraction_range"] == [0, 0.5]
    empty = measurement_coverage({}, valid, np.array([10.0, np.nan, 15.0, 22.0]), 7)
    assert empty["observed_time_span_s"] is None
    result = compare_readers({"A": first, "B": second})[0]
    assert result["first_only_vertices"] == [1] and result["second_only_vertices"] == [4]
    assert result["pick_reject_disagreements"] == 1 and result["paired_count"] == 2
    np.testing.assert_allclose(result["sample_covariance_of_differences_px2"], [[2, -2], [-2, 2]])
    assert compare_readers({"A": {0: first[0]}, "B": second})[0]["sample_covariance_of_differences_px2"] is None
