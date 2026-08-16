"""Unit tests for `app.services.geo` — pure, no network, no DB.

Douglas-Peucker simplification is tested against synthetic tracks built directly in
this file (no shared fixtures/conftest — see module scope note in the task). A final
test loads a real GPS track from `backend/tests/fixtures/*.fit` via
`app.services.fit_import.parse_fit`, if that module is available and imports/parses
cleanly; otherwise it's skipped with a clear reason rather than failing on another
agent's in-progress work.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.services.geo import (
    TrackPoint,
    downsample_to,
    simplify_track,
    track_bounds,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _pt(lat: float, lng: float, elevation: float | None = 0.0, timestamp: int = 0) -> TrackPoint:
    return {"lat": lat, "lng": lng, "elevation": elevation, "timestamp": timestamp}


def _straight_line(
    n: int, *, lat: float = 48.0, lng_start: float = 8.0, lng_end: float = 8.02
) -> list[TrackPoint]:
    return [
        _pt(lat, lng_start + (lng_end - lng_start) * i / (n - 1), elevation=float(i), timestamp=i)
        for i in range(n)
    ]


def _sharp_corner(n_per_leg: int = 20) -> list[TrackPoint]:
    """Two straight legs meeting at a real right-angle vertex, ~1.1 km each leg."""
    lat0, lng0 = 48.0, 8.0
    # Leg 1: due east along the same latitude.
    leg1 = [_pt(lat0, lng0 + 0.01 * i / (n_per_leg - 1), timestamp=i) for i in range(n_per_leg)]
    # Leg 2: due north from the corner (leg1's last point == corner == leg2's first).
    corner_lng = leg1[-1]["lng"]
    leg2 = [
        _pt(lat0 + 0.01 * i / (n_per_leg - 1), corner_lng, timestamp=n_per_leg + i)
        for i in range(1, n_per_leg)
    ]
    return leg1 + leg2


def _wiggly_track(n: int) -> list[TrackPoint]:
    """A long, genuinely wiggly path — enough real shape that simplification has
    actual work to do at a range of tolerances (used for monotonicity/downsample
    tests, where a plain straight line would collapse at any tolerance > 0)."""
    points = []
    for i in range(n):
        t = i / (n - 1)
        lat = 48.0 + 0.05 * t + 0.01 * math.sin(t * 40)
        lng = 8.0 + 0.08 * t + 0.01 * math.cos(t * 27)
        elevation = 100.0 + 50.0 * math.sin(t * 13)
        points.append(_pt(lat, lng, elevation=elevation, timestamp=i))
    return points


# --------------------------------------------------------------------------- #
# Core behaviour
# --------------------------------------------------------------------------- #


def test_straight_line_collapses_to_two_points():
    points = _straight_line(200)
    result = simplify_track(points, tolerance_m=1.0)
    assert len(result) == 2
    assert result[0] == points[0]
    assert result[-1] == points[-1]


def test_sharp_corner_is_preserved():
    points = _sharp_corner()
    result = simplify_track(points, tolerance_m=5.0)
    corner = points[19]  # last point of leg 1 == first point of leg 2
    assert any(p["lat"] == corner["lat"] and p["lng"] == corner["lng"] for p in result)
    # Exactly the three real vertices are needed to represent this path faithfully.
    assert len(result) == 3


def test_endpoints_always_kept_regardless_of_tolerance():
    points = _wiggly_track(300)
    for tolerance in (0.0, 1.0, 100.0, 100_000.0, 10_000_000.0):
        result = simplify_track(points, tolerance_m=tolerance)
        assert result[0] == points[0], f"first point dropped at tolerance={tolerance}"
        assert result[-1] == points[-1], f"last point dropped at tolerance={tolerance}"


def test_higher_tolerance_yields_fewer_or_equal_points():
    points = _wiggly_track(500)
    tolerances = [0.5, 2.0, 10.0, 50.0, 200.0, 1000.0]
    counts = [len(simplify_track(points, tolerance_m=t)) for t in tolerances]
    for earlier, later in zip(counts, counts[1:], strict=False):
        assert later <= earlier, f"counts not monotonic non-increasing: {counts}"
    # Sanity: the extremes actually differ (the track isn't trivially degenerate).
    assert counts[0] > counts[-1]
    assert counts[-1] >= 2


def test_downsample_to_lands_near_target_and_never_exceeds_it():
    points = _wiggly_track(3000)
    for target in (10, 50, 300):
        result = downsample_to(points, target)
        assert len(result) <= target
        assert len(result) >= 2
        # "Near" the target, not collapsed to just the endpoints — the binary search
        # should find a tolerance that uses most of the point budget for a track this
        # rich in shape.
        assert len(result) >= target * 0.5, (
            f"downsample_to({target}) only reached {len(result)} points"
        )


def test_downsample_to_is_a_noop_when_already_under_target():
    points = _wiggly_track(20)
    result = downsample_to(points, 1000)
    assert result == points


def test_null_elevation_survives_simplification_without_being_coerced_to_zero():
    points = _sharp_corner()
    points[19]["elevation"] = None  # the corner vertex itself has no altitude reading
    result = simplify_track(points, tolerance_m=5.0)
    corner_result = next(p for p in result if p["timestamp"] == 19)
    assert corner_result["elevation"] is None


# --------------------------------------------------------------------------- #
# Degenerate inputs
# --------------------------------------------------------------------------- #


def test_empty_input():
    assert simplify_track([], tolerance_m=10.0) == []
    assert track_bounds([]) is None
    assert downsample_to([], 100) == []


def test_single_point():
    points = [_pt(48.0, 8.0)]
    assert simplify_track(points, tolerance_m=10.0) == points
    assert downsample_to(points, 100) == points
    bounds = track_bounds(points)
    assert bounds.n_lat == bounds.s_lat == 48.0
    assert bounds.e_lng == bounds.w_lng == 8.0


def test_two_points():
    points = [_pt(48.0, 8.0), _pt(48.1, 8.1)]
    assert simplify_track(points, tolerance_m=10.0) == points
    assert downsample_to(points, 100) == points


def test_all_identical_points():
    points = [_pt(48.0, 8.0, timestamp=i) for i in range(50)]
    result = simplify_track(points, tolerance_m=0.0)
    assert len(result) == 2
    assert result[0] == points[0]
    assert result[-1] == points[-1]
    # Downsampling identical points shouldn't error on a zero-diagonal bounding box.
    result = downsample_to(points, 10)
    assert 2 <= len(result) <= 10


def test_negative_tolerance_rejected():
    with pytest.raises(ValueError):
        simplify_track(_straight_line(5), tolerance_m=-1.0)


def test_max_points_below_two_rejected():
    with pytest.raises(ValueError):
        downsample_to(_straight_line(5), 1)


# --------------------------------------------------------------------------- #
# track_bounds
# --------------------------------------------------------------------------- #


def test_track_bounds():
    points = [_pt(48.0, 8.0), _pt(48.2, 8.05), _pt(47.9, 8.3)]
    bounds = track_bounds(points)
    assert bounds.n_lat == 48.2
    assert bounds.s_lat == 47.9
    assert bounds.e_lng == 8.3
    assert bounds.w_lng == 8.0


# --------------------------------------------------------------------------- #
# Real data
# --------------------------------------------------------------------------- #


def test_simplifies_a_real_fixture_track():
    """Sanity-checks the algorithm against a genuine 9k-point GPS recording, not just
    synthetic shapes. Skips cleanly if `fit_import` isn't available yet or a fixture
    fails to parse — this module's own tests (above) already cover correctness."""
    try:
        from app.services.fit_import import parse_fit
    except ImportError as exc:
        pytest.skip(f"app.services.fit_import not importable yet: {exc}")

    fit_path = FIXTURES_DIR / "multisport_2026-07-11.fit"
    if not fit_path.exists():
        pytest.skip(f"fixture not found: {fit_path}")

    try:
        parsed = parse_fit(fit_path)
    except Exception as exc:  # noqa: BLE001 - any parse failure here is "skip", not "fail"
        pytest.skip(f"fixture did not parse cleanly: {exc}")

    # The bike session is the longest, richest-shaped track in these fixtures.
    session = max(parsed.sessions, key=lambda s: len(s.gps_track))
    track = session.gps_track
    assert len(track) > 1000, "expected a real multi-thousand-point session track"

    result = downsample_to(track, 300)

    assert len(result) <= 300
    assert result[0] == track[0]
    assert result[-1] == track[-1]
    # Elevation nulls (~20% of records, §3 Traps 9) must not have been coerced to 0 —
    # every kept point's elevation is either None or an actual numeric reading.
    assert all(p["elevation"] is None or isinstance(p["elevation"], int | float) for p in result)

    original_size = len(track)
    simplified_size = len(result)
    ratio = simplified_size / original_size
    print(
        f"\nreal fixture track simplification: {original_size} -> {simplified_size} points "
        f"({ratio:.1%} of original)"
    )
    assert ratio < 0.5, "expected a meaningful reduction on a real multi-thousand-point track"
