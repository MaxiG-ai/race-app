"""GPS track simplification for the `/races/{id}/track` endpoint (§4 Payload note).

A multisport race carries ~9k GPS points per session across up to 5 sessions — multi-MB
of JSON if served naively. This module reduces a `gps_track` (a list of
``{lat, lng, elevation, timestamp}`` dicts, exactly the shape stored in
`RecordedActivity.gps_track`) to a few hundred points using the Ramer-Douglas-Peucker
algorithm, while preserving the track's visual shape closely enough for map rendering.

Distance metric
----------------
Douglas-Peucker needs a perpendicular point-to-line distance, and comparing that
distance against a real-world tolerance (metres) requires a metric coordinate space —
comparing raw lat/lng degrees would treat 1° of longitude as equally "wide" as 1° of
latitude, which is wrong everywhere except the equator, and increasingly wrong at higher
latitudes (1° of longitude shrinks by cos(latitude)).

We use an **equirectangular projection** centred on the track's mean latitude:

    x = R * radians(lng) * cos(radians(mean_lat))
    y = R * radians(lat)

This is a linear approximation of the Earth's curvature, accurate to well under a metre
of distortion over a bounding box the size of a single race course (a few hundred metres
to, at most, a couple hundred kilometres for a full-distance bike leg) — the curvature of
the Earth over that extent is negligible. It is *not* valid for spans of continental
scale or for tracks that cross the poles or the antimeridian, neither of which occurs for
a single recorded activity. Haversine (or a full geodesic) would be more "correct" but is
unnecessary work per point-pair when this approximation's error is already far below the
simplification tolerances used here (metres, not degrees).

Iterative, not recursive
-------------------------
A naive Douglas-Peucker recurses one stack frame per split; a 9k-point pathological
track (e.g. a zig-zagging trail run) can exceed Python's default recursion limit. This
implementation keeps an explicit stack instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, TypedDict

_EARTH_RADIUS_M = 6_371_000.0


class TrackPoint(TypedDict, total=False):
    lat: float
    lng: float
    elevation: float | None
    timestamp: Any


# `?precision=` on `/races/{id}/track` maps directly onto this table. Values are
# Douglas-Peucker tolerances in metres — the maximum a simplified segment is allowed to
# deviate from the original polyline. Chosen to comfortably fit a full-distance bike
# course (tens of km) into a few hundred points while keeping corners recognisable.
PRECISION_TOLERANCES_M: dict[str, float] = {
    "low": 30.0,
    "medium": 10.0,
    "high": 3.0,
}
DEFAULT_PRECISION = "medium"


@dataclass(frozen=True, slots=True)
class TrackBounds:
    """Matches `RecordedActivity.bounds_*` naming for a direct assignment on read."""

    n_lat: float
    s_lat: float
    e_lng: float
    w_lng: float


def track_bounds(points: list[TrackPoint]) -> TrackBounds | None:
    """Bounding box for map-fitting. `None` for an empty track."""
    if not points:
        return None
    lats = [p["lat"] for p in points]
    lngs = [p["lng"] for p in points]
    return TrackBounds(n_lat=max(lats), s_lat=min(lats), e_lng=max(lngs), w_lng=min(lngs))


def _project(points: list[TrackPoint]) -> list[tuple[float, float]]:
    """Equirectangular-project points to metres, scaled by the track's mean latitude."""
    mean_lat_rad = math.radians(sum(p["lat"] for p in points) / len(points))
    cos_mean_lat = math.cos(mean_lat_rad)
    projected = []
    for p in points:
        x = _EARTH_RADIUS_M * math.radians(p["lng"]) * cos_mean_lat
        y = _EARTH_RADIUS_M * math.radians(p["lat"])
        projected.append((x, y))
    return projected


def _perpendicular_distance_m(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Distance from point P to the infinite line through A and B, in projected metres.

    Falls back to point-to-point distance when A and B coincide (a degenerate
    zero-length "line" — e.g. duplicate GPS fixes), since the perpendicular-distance
    formula divides by the segment length.
    """
    dx = bx - ax
    dy = by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    numerator = abs(dy * px - dx * py + bx * ay - by * ax)
    denominator = math.hypot(dx, dy)
    return numerator / denominator


def simplify_track(points: list[TrackPoint], *, tolerance_m: float) -> list[TrackPoint]:
    """Ramer-Douglas-Peucker simplification, iterative (see module docstring).

    The first and last points are always kept. A point survives simplification
    untouched — including a `None` elevation, which is never coerced to 0 (~20% of
    records lack altitude, PLAN §3 Traps 9; the point is a shape-decision, not an
    elevation-decision).
    """
    if tolerance_m < 0:
        raise ValueError("tolerance_m must be >= 0")

    n = len(points)
    if n <= 2:
        return list(points)

    projected = _project(points)
    keep = bytearray(n)
    keep[0] = 1
    keep[-1] = 1

    stack: list[tuple[int, int]] = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        ax, ay = projected[start]
        bx, by = projected[end]
        max_dist = -1.0
        max_idx = -1
        for i in range(start + 1, end):
            px, py = projected[i]
            d = _perpendicular_distance_m(px, py, ax, ay, bx, by)
            if d > max_dist:
                max_dist = d
                max_idx = i
        if max_dist > tolerance_m:
            keep[max_idx] = 1
            stack.append((start, max_idx))
            stack.append((max_idx, end))

    return [p for p, k in zip(points, keep, strict=True) if k]


def simplify_for_precision(
    points: list[TrackPoint], precision: str | None = None
) -> list[TrackPoint]:
    """Convenience wrapper for the `?precision=` query param — `low`/`medium`/`high`
    map onto `PRECISION_TOLERANCES_M`; anything else (including `None`) falls back to
    `DEFAULT_PRECISION`."""
    tolerance = PRECISION_TOLERANCES_M.get(
        precision or DEFAULT_PRECISION, PRECISION_TOLERANCES_M[DEFAULT_PRECISION]
    )
    return simplify_track(points, tolerance_m=tolerance)


def _diagonal_m(bounds: TrackBounds) -> float:
    """Bounding-box diagonal in metres — an upper bound on any perpendicular distance
    within the track, so a tolerance this large is guaranteed to collapse the track to
    its two endpoints."""
    mean_lat_rad = math.radians((bounds.n_lat + bounds.s_lat) / 2)
    cos_mean_lat = math.cos(mean_lat_rad)
    dx = _EARTH_RADIUS_M * math.radians(bounds.e_lng - bounds.w_lng) * cos_mean_lat
    dy = _EARTH_RADIUS_M * math.radians(bounds.n_lat - bounds.s_lat)
    return math.hypot(dx, dy)


def downsample_to(
    points: list[TrackPoint], max_points: int, *, max_iterations: int = 24
) -> list[TrackPoint]:
    """Binary-search the Douglas-Peucker tolerance so the simplified track lands at or
    near (never far above) `max_points`.

    Tolerance and point count move in opposite directions but not linearly or even
    monotonically-smoothly (adjacent tolerances can yield the same count), so this is a
    bisection over tolerance rather than a direct formula — `max_iterations` bounds the
    work; 24 halvings narrow a metre-to-kilometre starting range to sub-millimetre
    precision, which is always enough to settle.
    """
    if max_points < 2:
        raise ValueError("max_points must be >= 2")

    n = len(points)
    if n <= max_points or n <= 2:
        return list(points)

    bounds = track_bounds(points)
    assert bounds is not None  # n > 2 above guarantees a non-empty track
    hi = _diagonal_m(bounds) or 1.0  # all-identical points: diagonal is 0, still need hi > 0
    lo = 0.0
    best = simplify_track(points, tolerance_m=hi)

    for _ in range(max_iterations):
        mid = (lo + hi) / 2
        candidate = simplify_track(points, tolerance_m=mid)
        if len(candidate) > max_points:
            lo = mid
        else:
            best = candidate
            hi = mid
        if len(candidate) == max_points:
            break

    return best
