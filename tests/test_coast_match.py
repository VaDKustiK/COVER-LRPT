import json

import cbor2
import cv2
import numpy as np
import pytest

from src.coast_match import CATALOG, match_chip, match_pass


def shore_scene(dx, dy, dtype=np.uint8):
    # A stepped peninsula with a narrow head and two bays. Gray levels are
    # independent of the map geometry and the image polarity can be reversed.
    polygon = np.array(
        [[0, 40], [52, 40], [57, 48], [66, 37], [75, 55], [88, 56], [93, 77], [75, 87], [69, 77], [60, 90], [0, 90]],
        np.int32,
    )
    coast = np.zeros((160, 160), np.uint8)
    cv2.polylines(coast, [polygon], True, 255, 1)
    image = np.full(coast.shape, 150, dtype)
    cv2.fillPoly(image, [polygon + np.array((dx, dy), np.int32)], 50)
    return coast, image


@pytest.mark.parametrize(("dx", "dy", "invert"), [(9, -7, False), (-11, 5, True), (0, 0, False)])
def test_localizes_shifted_coast_in_original_coordinates(dx, dy, invert):
    coast, image = shore_scene(dx, dy)
    if invert:
        image = 255 - image
    result = match_chip(image, np.ones(160, bool), coast, np.array([75.25, 57.15]))
    assert result["accepted"], result
    np.testing.assert_allclose(result["best_shift_px"], [dx, dy], atol=1)


def test_missing_pixels_competing_edges_and_search_limits():
    coast, image = shore_scene(0, 0)
    pos = np.array([75, 62])
    usable = np.ones(160, bool)
    assert match_chip(image, usable, coast, [np.nan, np.nan])["reason"] == "outside_projection"
    assert match_chip(image, usable, coast, [1, 12])["reason"] == "incomplete_search_window"
    assert match_chip(image, ~usable, coast, pos)["reason"] == "insufficient_image_support"
    flat = np.full_like(image, 80)
    flat[70:76] = 0
    assert match_chip(flat, usable, coast, pos)["reason"] == "no_image_edges"
    straight = np.zeros_like(image)
    straight[:, 75] = 255
    flat[:] = 80
    flat[:, 75:] = 160
    res = match_chip(flat, usable, straight, pos)
    assert res["reason"] in ("ambiguous_peak", "search_boundary") and res["measured_pixel"] is None


def test_offline_catalog_and_pass_contract(decoded_pass, tmp_path):
    path = decoded_pass / "satdump_output/MSU-MR/product.cbor"
    product = cbor2.loads(path.read_bytes())
    product["projection_cfg"].update(type="normal_single_line", scan_angle=110.1, interpolate_timestamps_scantime=0.2)
    product["images"][0]["transform"].update(bx=0, by=0)
    path.write_bytes(cbor2.dumps(product))
    report = match_pass(decoded_pass, "embedded", allow_nonstandard=True)
    assert len(report["points"]) == len(json.loads(CATALOG.read_bytes())["landmarks"])
    assert all(not p["accepted"] and p["measured_pixel"] is None for p in report["points"])
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    invalid = tmp_path / "catalog.json"
    invalid.write_text(json.dumps(json.loads(CATALOG.read_bytes()) | {"reference_sha256": "other"}))
    with pytest.raises(ValueError, match="pinned map"):
        match_pass(decoded_pass, "embedded", invalid, True)


def test_high_bit_depth_preserves_shift_and_flat_tile_rejects():
    coast, image = shore_scene(-6, 8)
    high = image.astype(np.uint16) * 50
    valid = np.ones(160, bool)
    pos = np.array([75.25, 57.15])
    result = match_chip(high, valid, coast, pos)
    assert result["accepted"]
    np.testing.assert_allclose(result["best_shift_px"], [-6, 8], atol=1)
    assert match_chip(np.full_like(high, 1000), valid, coast, pos)["reason"] == "no_image_contrast"
