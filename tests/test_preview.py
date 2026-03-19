"""Tests for p1s_autoclear.preview path helpers."""

from p1s_autoclear.preview import get_pusher_path_segments


def test_part_center_sweep_same_column_two_passes_not_full_rake():
    """part_center_sweep matches injector: double central push at one X, not bed-wide rake."""
    bounds = (80.0, 200.0)
    segs = get_pusher_path_segments("part_center_sweep", bounds)
    xs = {s[0] for s in segs}
    assert len(xs) == 1
    assert len(segs) == 6


def test_part_center_three_segments():
    segs = get_pusher_path_segments("part_center", (60.0, 180.0))
    assert len(segs) == 3
    assert all(s[0] == segs[0][0] for s in segs)


def test_center_and_sweep_includes_double_rake():
    segs = get_pusher_path_segments("center_and_sweep", None)
    rake_cols = len({s[0] for s in segs if s[0] != 125.0})
    assert rake_cols == 7
    assert segs.count((220, 250.0, 220, 0.0)) == 2
