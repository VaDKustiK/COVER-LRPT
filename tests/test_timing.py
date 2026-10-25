import numpy as np

from src.timing import (
    aos_from_folder,
    build_time_model,
    line_times,
    timing_status,
)

P = 1.231143
AOS = 1.79e9


def on_line(frames, t0):
    return t0 + P * np.asarray(frames, float)


def test_aos_from_folder():
    assert aos_from_folder("1791705353_20261011T075553Z_57166") == 1791705353.0


def test_clean_pass_is_ok():
    ts = on_line(np.arange(484), AOS + 107)
    ts[[5, 9, 200]] = -1
    m = build_time_model(ts, AOS)
    assert timing_status(m) == "ok"
    assert abs(m.period - P) < 1e-6
    assert len(m.segments) == 1


def test_two_lines_inside_window_is_multi_segment():
    a = on_line(np.arange(280), AOS + 393 - P)
    b = a - 406.739
    ts = -np.ones(280)
    ts[1:150], ts[165], ts[167] = a[1:150], a[165], a[167]
    ts[152:165], ts[166], ts[168:279] = b[152:165], b[166], b[168:279]
    ts[0] = AOS + 544474815
    m = build_time_model(ts, AOS)
    assert timing_status(m) == "multi_segment"
    assert [s.frames.size for s in m.segments] == [151, 125]


def test_garbage_epoch_and_frozen_clock_leave_a_partial_pass():
    rng = np.random.default_rng(0)
    ts = -np.ones(555)
    ts[:87] = on_line(np.arange(87), AOS + 47)
    ts[87:150] = AOS + 153.4 + rng.uniform(0, 0.9, 63)
    ts[155:] = on_line(np.arange(155, 555), AOS + 4692262727)
    m = build_time_model(ts, AOS)
    assert timing_status(m) == "partial"
    lt = line_times(m, 555)
    assert np.isfinite(lt).sum() == 87 * 8


def test_nothing_in_window_is_no_timing():
    m = build_time_model(np.full(100, AOS + 5000.0), AOS)
    assert timing_status(m) == "no_timing"
    assert not np.isfinite(line_times(m, 100)).any()


def test_line_times_step_by_one_eighth_period():
    m = build_time_model(on_line(np.arange(50), AOS + 60), AOS)
    lt = line_times(m, 50)
    assert np.allclose(np.diff(lt), P / 8, atol=1e-6)
