"""Race category from measured distances (docs/features/classification.md#rules)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Race, RaceCategory
from app.models.enums import (
    SPORT_SEGMENTS,
    CategoryKind,
    ClassificationSource,
    RaceType,
    Segment,
)


def _km(lo: float | None, hi: float | None) -> tuple[float | None, float | None]:
    return (lo * 1000 if lo is not None else None, hi * 1000 if hi is not None else None)


# docs/reference/dtu-categories.md, exactly as published. A None bound is open-ended.
# (key, label, kind, swim km, bike km, run km)
DTU_CATEGORIES: tuple[tuple[str, str, CategoryKind, tuple, tuple, tuple], ...] = (
    ("schueler", "Schüler (A/B/C/D)", CategoryKind.AGE_CLASS, (0.05, 0.4), (1, 10), (0.2, 2.5)),
    ("jugend", "Jugend (A und B)", CategoryKind.AGE_CLASS, (0.4, 0.75), (10, 20), (2.5, 5)),
    ("junioren", "Junioren", CategoryKind.AGE_CLASS, (0.75, 1.5), (20, 40), (5, 10)),
    (
        "supersprint",
        "Supersprintdistanz",
        CategoryKind.DISTANCE,
        (0.25, 0.5),
        (6.5, 13),
        (1.7, 3.5),
    ),
    ("sprint", "Sprintdistanz", CategoryKind.DISTANCE, (None, 0.75), (None, 20), (None, 5)),
    ("kurz", "Kurzdistanz", CategoryKind.DISTANCE, (1.5, 1.5), (40, 40), (10, 10)),
    ("mittel", "Mitteldistanz", CategoryKind.DISTANCE, (1.9, 3.0), (80, 90), (20, 21)),
    ("lang", "Langdistanz", CategoryKind.DISTANCE, (1.0, 4.0), (100, 200), (10.0, 42.2)),
    ("mixed_relay", "Mixed Relay", CategoryKind.RELAY, (0.25, 0.3), (5, 8), (1.5, 2)),
)


def seed_rows() -> list[dict[str, Any]]:
    rows = []
    for order, (key, label, kind, swim, bike, run) in enumerate(DTU_CATEGORIES, start=1):
        row: dict[str, Any] = {"key": key, "label": label, "kind": kind, "sort_order": order * 10}
        for segment, (lo, hi) in zip(("swim", "bike", "run"), (swim, bike, run), strict=True):
            row[f"{segment}_min_m"], row[f"{segment}_max_m"] = _km(lo, hi)
        rows.append(row)
    return rows


@dataclass(frozen=True)
class Result:
    category: RaceCategory | None
    detail: dict[str, Any] | None  # nearest category and furthest-out discipline


def _miss_pct(value: float, lo: float | None, hi: float | None, tolerance_pct: float) -> float:
    """Signed % outside the bounds widened by the tolerance; 0 when inside."""
    f = tolerance_pct / 100
    if lo is not None and value < lo * (1 - f):
        return (value - lo * (1 - f)) / (lo * (1 - f)) * 100
    if hi is not None and value > hi * (1 + f):
        return (value - hi * (1 + f)) / (hi * (1 + f)) * 100
    return 0.0


def _width(category: RaceCategory) -> float:
    """Summed range width; an open bound counts as very wide, so it never wins a tie."""
    total = 0.0
    for segment in SPORT_SEGMENTS:
        lo, hi = category.bounds(segment)
        total += (hi if hi is not None else 1e9) - (lo or 0)
    return total


def classify(distances: dict[Segment, float | None], categories: list[RaceCategory]) -> Result:
    if any(distances.get(s) is None for s in SPORT_SEGMENTS):
        return Result(None, None)
    matches: list[RaceCategory] = []
    nearest: tuple[float, RaceCategory, Segment, float] | None = None
    for category in categories:
        if category.kind is not CategoryKind.DISTANCE or not category.is_active:
            continue
        misses = [
            (s, _miss_pct(distances[s], *category.bounds(s), category.tolerance_pct))  # type: ignore[arg-type]
            for s in SPORT_SEGMENTS
        ]
        segment, worst = max(misses, key=lambda m: abs(m[1]))
        if worst == 0:
            matches.append(category)
        elif nearest is None or abs(worst) < nearest[0]:
            nearest = (abs(worst), category, segment, worst)
    if matches:
        return Result(min(matches, key=_width), None)
    if nearest is None:
        return Result(None, None)
    _, category, segment, _ = nearest
    # Reported against the published bound, not the widened one: "the bike is 15.6 %
    # short of Kurzdistanz" is what the owner can check against the table.
    published_miss = _miss_pct(distances[segment], *category.bounds(segment), 0)  # type: ignore[arg-type]
    return Result(
        None,
        {
            "nearest": category.key,
            "nearest_label": category.label,
            "segment": segment.value,
            "delta_pct": round(published_miss, 1),
        },
    )


def classify_race(db: Session, race: Race) -> None:
    """Rules 1-7: updates auto_category_id always, race_category_id only when auto."""
    if race.race_type is not RaceType.TRIATHLON:
        # The categories are triathlon distances; a single-sport race has none.
        race.race_category_id = race.auto_category_id = None
        race.classification_detail = None
        race.classification_source = ClassificationSource.UNCLASSIFIED
        race.classified_at = utcnow()
        return
    primaries = race.primary_activities()
    distances = {s: (a.distance_m if (a := primaries.get(s)) else None) for s in SPORT_SEGMENTS}
    categories = list(db.scalars(select(RaceCategory)))
    result = classify(distances, categories)
    race.auto_category_id = result.category.id if result.category else None
    race.classification_detail = result.detail
    race.classified_at = utcnow()
    if race.classification_source is not ClassificationSource.MANUAL:
        race.race_category_id = race.auto_category_id
        race.classification_source = (
            ClassificationSource.AUTO if result.category else ClassificationSource.UNCLASSIFIED
        )
