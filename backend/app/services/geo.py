"""Track simplification for /races/{id}/track (docs/features/race-detail.md#api)."""

from __future__ import annotations

import math
from typing import Any

_EARTH_RADIUS_M = 6_371_000.0

# Douglas-Peucker tolerance in metres per `?precision=`. Visitors only ever get these;
# the owner may also ask for `full`.
PRECISION_M = {"low": 30.0, "medium": 10.0, "high": 3.0}


def simplify(points: list[dict[str, Any]], tolerance_m: float) -> list[dict[str, Any]]:
    """Ramer-Douglas-Peucker on an equirectangular projection around the mean latitude
    (accurate to well under a metre at race-course scale). Iterative, so a 9 000-point
    track can't hit the recursion limit."""
    n = len(points)
    if n <= 2:
        return list(points)
    cos_lat = math.cos(math.radians(sum(p["lat"] for p in points) / n))
    xy = [
        (
            _EARTH_RADIUS_M * math.radians(p["lng"]) * cos_lat,
            _EARTH_RADIUS_M * math.radians(p["lat"]),
        )
        for p in points
    ]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        best, best_i = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            d = (
                abs(dy * px - dx * py + bx * ay - by * ax) / length
                if length
                else math.hypot(px - ax, py - ay)
            )
            if d > best:
                best, best_i = d, i
        if best > tolerance_m:
            keep[best_i] = True
            stack += [(a, best_i), (best_i, b)]
    return [p for p, k in zip(points, keep, strict=True) if k]
