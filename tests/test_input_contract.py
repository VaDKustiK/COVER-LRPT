import json

import cbor2
import numpy as np
import pytest

from src.catalog import analyse_pass, day_candidates
from src.identity import IdentityConflict
from src.pass_loader import load_pass, read_product
from src.timing import aos_from_folder, build_time_model, timing_status


def change_product(folder, edit):
    path = folder / "satdump_output/MSU-MR/product.cbor"
    product = read_product(path)
    edit(product)
    path.write_bytes(cbor2.dumps(product))


def test_identity_agrees_across_labels_and_nullable_telemetry(decoded_pass):
    p = load_pass(decoded_pass)
    assert p.identity is not None
    assert p.identity.status == "consistent"
    assert set(p.identity.evidence.values()) == {57166}
    assert p.identity.telemetry_counts == {3: 1}


@pytest.mark.parametrize("line", ["line1", "line2"])
def test_conflicting_tle_is_rejected_but_auditable(decoded_pass, line):
    def edit(product):
        product["projection_cfg"]["tle"][line] = product["projection_cfg"]["tle"][line].replace("57166", "59051")

    change_product(decoded_pass, edit)
    with pytest.raises(IdentityConflict, match="59051"):
        load_pass(decoded_pass)
    p = load_pass(decoded_pass, strict_identity=False)
    assert p.identity is not None
    assert p.identity.status == "conflict"
    row = analyse_pass(decoded_pass)
    assert row["skip"] == "identity_conflict"
    assert not day_candidates([row], 57166, min_longest_s=0)


def test_conflicting_metadata_and_telemetry_are_not_ignored(decoded_pass):
    path = decoded_pass / "metadata.json"
    path.write_text(json.dumps({"norad_id": 59051}))
    with pytest.raises(IdentityConflict, match="metadata"):
        load_pass(decoded_pass)
    path.write_text(json.dumps({"norad_id": 57166}))
    (decoded_pass / "satdump_output/telemetry.json").write_text(json.dumps([{"msu_mr_id": 4}]))
    with pytest.raises(IdentityConflict, match="telemetry"):
        load_pass(decoded_pass)


def test_unknown_source_is_incomplete_not_silently_trusted(decoded_pass):
    change_product(decoded_pass, lambda p: p.update(product_source="unknown"))
    identity = load_pass(decoded_pass).identity
    assert identity is not None and identity.status == "incomplete"


def test_missing_evidence_is_incomplete(decoded_pass):
    (decoded_pass / "metadata.json").write_text("{}")
    identity = load_pass(decoded_pass).identity
    assert identity is not None and identity.status == "incomplete"


def test_null_telemetry_is_missing_optional_evidence(decoded_pass):
    (decoded_pass / "satdump_output/telemetry.json").write_text("null")
    identity = load_pass(decoded_pass).identity
    assert identity is not None and identity.status == "consistent"
    assert identity.telemetry_counts == {}
    assert "telemetry_majority" not in identity.evidence


def test_raw_times_transforms_and_holes_are_preserved(decoded_pass):
    p = load_pass(decoded_pass, channels=(2, 4))
    assert p.raw_timestamps[4] == -1
    assert np.isnan(p.times[32:40]).all()
    assert not p.usable_lines()[32:40].any()
    assert p.timed_fraction == 19 / 20
    assert p.status == "ok"  # consistency is deliberately separate from coverage
    assert p.times[0] == pytest.approx(p.raw_timestamps[0], abs=1e-5, rel=0)
    assert p.cfg["timestamp_offset"] == 1.0  # retained, not applied
    assert p.channel_metadata[4]["transform"]["bx"] == -1.6
    assert p.images[4].shape == (160, 16)  # no resampling/cropping


def test_constant_clock_bias_is_not_detected_by_consistency_fit(decoded_pass):
    p = load_pass(decoded_pass)
    shifted = np.where(p.raw_timestamps > 0, p.raw_timestamps + 5, p.raw_timestamps)
    assert timing_status(build_time_model(shifted, p.aos)) == p.status == "ok"


def test_folder_dates_must_agree():
    with pytest.raises(ValueError, match="disagree"):
        aos_from_folder("1791705353_20261012T075553Z_57166")


@pytest.mark.parametrize("value", [0, -1, 2.5, True])
def test_invalid_frame_row_count_rejected(decoded_pass, value):
    change_product(decoded_pass, lambda p: p["projection_cfg"].update(interpolate_timestamps=value))
    with pytest.raises(ValueError, match="positive integer"):
        load_pass(decoded_pass)


def test_declared_width_and_duplicate_channels_are_checked(decoded_pass):
    change_product(decoded_pass, lambda p: p["projection_cfg"].update(image_width=17))
    with pytest.raises(ValueError, match="width"):
        load_pass(decoded_pass, channels=(2,))
    change_product(decoded_pass, lambda p: p["images"].append(p["images"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        load_pass(decoded_pass)


def test_legacy_catalog_is_not_treated_as_identity_checked():
    row = {"norad": 57166, "light": "day", "status": "ok", "longest_s": 400, "aos_utc": "2026"}
    assert day_candidates([row], 57166) == []
