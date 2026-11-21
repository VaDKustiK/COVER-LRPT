import json
from dataclasses import replace
from pathlib import Path

import cbor2
import numpy as np
import pytest
from sgp4.api import jday
from skyfield.api import EarthSatellite, wgs84
from skyfield.sgp4lib import TEME_to_ITRF

from src.geometry import (
    A_KM,
    AXES_KM,
    MapPredictor,
    Scanner,
    ecef_to_geodetic,
    geodetic_to_ecef,
    intersect_ellipsoid,
    observation_times,
    timescale,
    viewing_direction,
)
from src.geometry_orbit import select_orbit, tle_fields
from src.map_predictor import build_prediction, landmark_report, sensitivity_report
from src.pass_loader import load_pass
from src.reference_map import load_coastline, render_coastline


@pytest.fixture
def geometry_pass(decoded_pass):
    path = decoded_pass / "satdump_output/MSU-MR/product.cbor"
    product = cbor2.loads(path.read_bytes())
    product["projection_cfg"].update(
        type="normal_single_line", scan_angle=110.1, interpolate_timestamps_scantime=0.2, roll_offset=-0.31
    )
    product["images"][0]["transform"].update(bx=0, by=0)
    path.write_bytes(cbor2.dumps(product))
    return load_pass(decoded_pass, channels=(2,))


@pytest.fixture
def model(geometry_pass):
    sat, _ = select_orbit(geometry_pass, "embedded", allow_nonstandard=True)
    return MapPredictor(sat, observation_times(geometry_pass, "fitted"), Scanner.from_pass(geometry_pass))


def test_geodetic_roundtrip_and_independent_skyfield():
    lat = np.array([-90, -70, -20, 0, 40, 89.9, 90.0])
    lon = np.array([-180, -179.99, -10, 0, 179.99, 180, 10.0])
    for height in (0, 820):
        xyz = geodetic_to_ecef(lat, lon, height)
        reference = wgs84.latlon(lat, lon, elevation_m=height * 1000).itrs_xyz.km.T
        np.testing.assert_allclose(xyz, reference, atol=1e-9)
        out_lat, out_lon = ecef_to_geodetic(xyz)
        np.testing.assert_allclose(out_lat, lat, atol=1e-10)
        np.testing.assert_allclose(out_lon, lon, atol=1e-10)


def test_analytic_equatorial_nadir_and_ray_misses():
    p = np.array([A_KM + 820, 0, 0])
    np.testing.assert_allclose(intersect_ellipsoid(p, np.array([-1, 0, 0])), [A_KM, 0, 0], atol=1e-9)
    for direction in ([1, 0, 0], [0, 1, 0], [-0.1, 1, 0]):
        assert np.isnan(intersect_ellipsoid(p, np.array(direction))).all()
    assert np.isnan(intersect_ellipsoid(np.array([1, 1, 1]), np.array([1, 0, 0]))).all()


def test_separately_compiled_satdump_reference():
    cases = json.loads((Path(__file__).parent / "fixtures/satdump_geometry_reference.json").read_text())["cases"]
    for case in cases:
        p, v = np.array(case["position_ecef_km"]), np.array(case["inertial_velocity_in_ecef_km_s"])
        scanner = Scanner(1568, 110.1, case["roll_deg"], case["pitch_deg"], case["yaw_deg"])
        ground = intersect_ellipsoid(p, viewing_direction(p, v, 784, scanner))
        if case["miss"]:
            assert np.isnan(ground).all()
        else:
            lat, lon = case["expected_lat_lon_deg"]
            # 1 mm numerical agreement, not a claimed measurement accuracy.
            assert np.linalg.norm(ground - geodetic_to_ecef(lat, lon)) < 1e-6


def test_scan_direction_and_boresight():
    p, v = np.array([A_KM + 820, 0, 0]), np.array([0.0, 0.0, 7.0])
    scanner = Scanner(1568, 110.1)
    left = viewing_direction(p, v, 0, scanner)
    middle = viewing_direction(p, v, 784, scanner)
    np.testing.assert_allclose(middle, [-1, 0, 0], atol=1e-12)
    assert left[1] > 0
    np.testing.assert_allclose(viewing_direction(p, v, 1568, replace(scanner, invert_scan=True)), left)


def test_orbit_parsing_is_explicit_and_matches_omm(geometry_pass):
    with pytest.raises(ValueError, match="checksum"):
        select_orbit(geometry_pass, "embedded")
    sat, record = select_orbit(geometry_pass, "embedded", allow_nonstandard=True)
    assert not record["checksum_valid"]
    fields = tle_fields(geometry_pass.tle, geometry_pass.norad)
    reconstructed = EarthSatellite.from_omm(timescale(), fields)
    ts = timescale()
    t = ts.utc(2026, 10, [6, 9, 11])
    np.testing.assert_allclose(sat.at(t).position.km, reconstructed.at(t).position.km, atol=1e-5, rtol=0)
    assert record["availability_time_utc"] is None


def test_omm_identity_and_frame_rejected(geometry_pass, tmp_path):
    fields = tle_fields(geometry_pass.tle, geometry_pass.norad)
    path = tmp_path / "orbit.json"
    path.write_text(json.dumps([fields]))
    sat, _ = select_orbit(geometry_pass, str(path))
    assert sat.model.satnum == geometry_pass.norad
    fields["REF_FRAME"] = "ITRF"
    path.write_text(json.dumps([fields]))
    with pytest.raises(ValueError, match="REF_FRAME"):
        select_orbit(geometry_pass, str(path))
    fields["NORAD_CAT_ID"] = 59051
    path.write_text(json.dumps([fields]))
    with pytest.raises(ValueError, match="exactly one"):
        select_orbit(geometry_pass, str(path))


def test_itrs_position_against_teme_reference(model):
    ts = timescale()
    t = ts.utc(2026, 10, 3, 7, 45, 0)
    jd, fraction = jday(2026, 10, 3, 7, 45, 0)
    error, position, velocity = model.satellite.model.sgp4(jd, fraction)
    assert error == 0
    reference, _ = TEME_to_ITRF(t.ut1, np.array(position), np.array(velocity))
    actual, _ = model.propagate(np.array([1791013500.0]))
    np.testing.assert_allclose(actual[0], reference, atol=2e-5, rtol=0)


def test_timing_conventions_and_gaps(geometry_pass):
    p = geometry_pass
    fitted = observation_times(p, "fitted", 0.25)
    np.testing.assert_allclose((fitted - p.times)[np.isfinite(p.times)], 1.25)
    assert np.array_equal(np.isnan(fitted), np.isnan(p.times))


def test_centered_expansion_applies_offset_once(geometry_pass):
    p = geometry_pass
    times = observation_times(p, "satdump")
    assert times[0] == pytest.approx(p.raw_timestamps[0] - 0.8 + 1, abs=1e-6, rel=0)
    assert times[7] == pytest.approx(p.raw_timestamps[0] + 0.6 + 1, abs=1e-6, rel=0)
    assert np.isnan(times[32:40]).all()
    assert (np.diff(times) < 0).any()  # explicit diagnostic of overlapping expansions


def test_forward_inverse_and_ellipsoid(model):
    x = np.array([0.5, 3.0, 8.0, 13.0, 14.5])
    y = np.array([10.1, 25.6, 71.2, 100.5, 148.7])
    xyz = model.pixel_to_ecef(x, y)
    np.testing.assert_allclose(np.sum((xyz / AXES_KM) ** 2, axis=1), 1, atol=1e-12)
    lat, lon = ecef_to_geodetic(xyz)
    xy = model.geo_to_pixel(lat, lon)
    np.testing.assert_allclose(xy, np.column_stack((x, y)), atol=1e-5)
    exact = model.pixel_to_ecef(x, y, exact=True)
    assert np.max(np.linalg.norm(xyz - exact, axis=1)) < 0.001


def test_gaps_bounds_and_far_hemisphere(model):
    for x, y in ((8, 32), (8, 39.5), (8, -1), (-1, 50), (16, 50), (8, 160), (np.nan, 50)):
        assert np.isnan(model.pixel_to_ecef(x, y)).all()
    lat, lon = model.pixel_to_geo(8, 80)
    assert np.isnan(model.geo_to_pixel(-lat, (lon + 360) % 360 - 180)).all()
    assert np.isnan(model.geo_to_pixel(100, 0)).all()


def test_inverse_near_timed_run_boundaries(model):
    wide = MapPredictor(model.satellite, model.times, Scanner(1568, 110.1, roll_deg=-0.31))
    x, y = np.array([78.35, 1488.65, 100.1, 1400.2]), np.array([0.3, 31.7, 40.01, 158.99])
    # 31.7 has no support because its next row is missing; use the last exact row.
    y[1] = 31
    lat, lon = wide.pixel_to_geo(x, y)
    np.testing.assert_allclose(wide.geo_to_pixel(lat, lon), np.column_stack((x, y)), atol=1e-4)


def test_dateline_and_polar_inverse(geometry_pass):
    sat, _ = select_orbit(geometry_pass, "embedded", allow_nonstandard=True)
    ts = timescale()
    start = ts.utc(2026, 10, 11).utc_datetime().timestamp()
    # Select orbit samples by location without using the inverse as reference.
    coarse = MapPredictor(sat, start + np.arange(600) * 10, Scanner(1568, 110.1))
    lat, lon = coarse.pixel_to_geo(np.full(600, 784), np.arange(600))
    indices = [int(np.nanargmin(np.abs(np.abs(lon) - 180))), int(np.nanargmax(np.abs(lat)))]
    for index in indices:
        times = coarse.times[index] + (np.arange(200) - 100) * 0.154
        m = MapPredictor(sat, times, Scanner(1568, 110.1))
        x, y = np.array([500.0, 784.0, 1000.0]), np.array([40.3, 100.4, 170.1])
        lat, lon = m.pixel_to_geo(x, y)
        result = m.geo_to_pixel(lat, lon)
        np.testing.assert_allclose(result, np.column_stack((x, y)), atol=1e-4)


def test_reject_unsupported_channel_transform(geometry_pass):
    geometry_pass.channel_metadata[2]["transform"]["bx"] = 1
    with pytest.raises(ValueError, match="identity transform"):
        Scanner.from_pass(geometry_pass)


def test_render_does_not_bridge_missing_rows(model, geometry_pass):
    rows = np.arange(geometry_pass.n_lines)
    lat, lon = model.pixel_to_geo(np.full(len(rows), 8), rows)
    line = np.column_stack((lon, lat))
    # A vector crossing an image timing gap must leave that gap blank.
    edge = render_coastline(model, [line[np.isfinite(line).all(axis=1)]], geometry_pass.usable_lines((2,)))
    assert np.count_nonzero(edge) > 20
    assert not edge[32:40].any()


def test_packaged_map_integrity():
    lines, metadata = load_coastline()
    assert len(lines) == 4133
    assert sum(len(x) for x in lines) == metadata["vertex_count"]


def test_landmark_residual_sign_and_identity(model, geometry_pass):
    lat, lon = model.pixel_to_geo(8, 80)
    data = {
        "pass_name": geometry_pass.name,
        "channel": 2,
        "points": [{"id": "synthetic", "latitude": float(lat), "longitude": float(lon), "column": 9, "row": 82}],
    }
    report = landmark_report(model, data, geometry_pass)
    np.testing.assert_allclose(report["points"][0]["observed_minus_predicted_px"], [1, 2], atol=1e-5)
    data["pass_name"] = "different pass"
    with pytest.raises(ValueError, match="this pass"):
        landmark_report(model, data, geometry_pass)


def test_sensitivity_records_confounding(model, geometry_pass):
    report = sensitivity_report(model, geometry_pass)
    assert len(report["experiments"]) == 5
    assert abs(report["normalized_derivative_cosines"][0][4]) > 0.9


def test_cli_workflow_preserves_archive_and_refuses_overwrite(geometry_pass, tmp_path):
    folder = geometry_pass.product_dir.parents[1]
    output = tmp_path / "results"
    before = {str(p): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    report = build_prediction(folder, orbit="embedded", timing="fitted", output=output, allow_nonstandard=True)
    assert report["shape"] == [160, 16]
    assert {p.name for p in output.iterdir()} == {"report.json", "geometry.npz", "overlay.png", "coastline.png"}
    assert before == {str(p): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        build_prediction(folder, orbit="embedded", timing="fitted", output=output, allow_nonstandard=True)
    with pytest.raises(ValueError, match="archive"):
        build_prediction(folder, orbit="embedded", timing="fitted", output=folder / "result")
