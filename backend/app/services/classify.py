"""Infer a race's category from its measured distances (§9).

Pure functions over a lightweight protocol, so this is testable without a database and
reusable against either ORM rows or the seed tuples.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models.base import SCORING_SEGMENTS, CategoryKind, Segment


class CategoryLike(Protocol):
    key: str
    kind: CategoryKind
    tolerance_pct: float
    is_active: bool

    def bounds(self, segment: Segment) -> tuple[float | None, float | None]: ...


@dataclass(frozen=True, slots=True)
class Miss:
    """Why a category was rejected — the discipline that was furthest outside."""

    segment: Segment
    measured_m: float
    delta_pct: float


@dataclass(frozen=True, slots=True)
class Classification:
    category_key: str | None
    #: Nearest category when nothing matched, so the UI can say what it almost was.
    nearest_key: str | None = None
    worst_miss: Miss | None = None

    @property
    def matched(self) -> bool:
        return self.category_key is not None

    def as_detail(self) -> dict:
        if self.matched:
            return {"matched": self.category_key}
        detail: dict = {"matched": None, "nearest": self.nearest_key}
        if self.worst_miss:
            detail |= {
                "worst_segment": self.worst_miss.segment.value,
                "worst_delta_pct": round(self.worst_miss.delta_pct, 1),
                "measured_m": round(self.worst_miss.measured_m, 1),
            }
        return detail


def _widen(
    bounds: tuple[float | None, float | None], tolerance_pct: float
) -> tuple[float | None, float | None]:
    """Widen published bounds outward.

    The published numbers stay in the table exactly as written; the fudge factor is
    applied here so it stays visible and tunable rather than baked into the data.
    """
    lo, hi = bounds
    factor = tolerance_pct / 100.0
    return (
        lo * (1 - factor) if lo is not None else None,
        hi * (1 + factor) if hi is not None else None,
    )


def _miss(measured: float, bounds: tuple[float | None, float | None]) -> float:
    """Signed percentage outside the widened bounds; 0.0 when inside."""
    lo, hi = bounds
    if lo is not None and measured < lo:
        return (measured - lo) / lo * 100.0
    if hi is not None and measured > hi:
        return (measured - hi) / hi * 100.0
    return 0.0


def _width(category: CategoryLike) -> float:
    """Total span of a category's three ranges — smaller means more specific.

    An open bound counts as very wide, which is what makes Sprintdistanz lose to
    Supersprintdistanz for a race that matches both.
    """
    total = 0.0
    for segment in SCORING_SEGMENTS:
        lo, hi = category.bounds(segment)
        if lo is None or hi is None:
            total += 1e9
        else:
            total += hi - lo
    return total


def classify(
    distances: dict[Segment, float | None],
    categories: list[CategoryLike],
) -> Classification:
    """Pick the category whose ranges contain all three measured distances.

    Matches are common rather than exceptional — Supersprintdistanz sits entirely inside
    Sprintdistanz — so the tie-break is *narrowest range wins*: the most specific category
    containing the measurement is the right answer.
    """
    measured = {seg: distances.get(seg) for seg in SCORING_SEGMENTS}
    if any(value is None for value in measured.values()):
        # No recorded distances: stay unclassified rather than guess a bucket (§10).
        return Classification(category_key=None)

    eligible = [c for c in categories if c.is_active and c.kind == CategoryKind.DISTANCE]

    matches: list[CategoryLike] = []
    best_miss: tuple[float, str, Miss] | None = None

    for category in eligible:
        worst: Miss | None = None
        for segment in SCORING_SEGMENTS:
            value = measured[segment]
            assert value is not None  # guarded above
            delta = _miss(value, _widen(category.bounds(segment), category.tolerance_pct))
            if worst is None or abs(delta) > abs(worst.delta_pct):
                worst = Miss(segment=segment, measured_m=value, delta_pct=delta)

        if worst is not None and worst.delta_pct == 0.0:
            matches.append(category)
        elif worst is not None:
            score = abs(worst.delta_pct)
            if best_miss is None or score < best_miss[0]:
                best_miss = (score, category.key, worst)

    if matches:
        winner = min(matches, key=_width)
        return Classification(category_key=winner.key)

    if best_miss is not None:
        _, nearest_key, worst = best_miss
        return Classification(category_key=None, nearest_key=nearest_key, worst_miss=worst)

    return Classification(category_key=None)
