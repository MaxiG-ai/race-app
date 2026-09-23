"""Official total derivation and the completeness checklist
(docs/features/races-and-results.md#rules)."""

from __future__ import annotations

from typing import Any

from app.models import OfficialResult, Race
from app.models.enums import SEGMENTS, SPORT_SEGMENTS

SPLIT_LABELS = {"swim": "swim", "t1": "T1", "bike": "bike", "t2": "T2", "run": "run"}


class ResultError(ValueError):
    pass


def apply_result(result: OfficialResult, values: dict[str, Any]) -> None:
    """Merge a partial write (only the fields the request sent) and settle the total.

    A typed total is never overwritten. A derived one is re-summed on every write.
    """
    for key, value in values.items():
        setattr(result, key, value)
    if values.get("total_time") is not None:
        result.total_is_derived = False
    elif "total_time" in values:
        result.total_is_derived = True  # the owner cleared it: derive if possible
    if result.total_time is not None and not result.total_is_derived:
        return
    splits = [result.split(s) for s in SEGMENTS]
    missing = [SPLIT_LABELS[s] for s, v in zip(SEGMENTS, splits, strict=True) if v is None]
    if missing:
        raise ResultError(
            "Enter the total time, or fill in every split so it can be calculated. "
            f"Still missing: {', '.join(missing)}."
        )
    result.total_time = sum(splits)  # type: ignore[arg-type]
    result.total_is_derived = True


def remainder_split(result: OfficialResult) -> tuple[str, int] | None:
    """Total and four splits entered: the fifth is the remainder, derived on read."""
    missing = [s for s in SEGMENTS if result.split(s) is None]
    if result.total_is_derived or len(missing) != 1:
        return None
    rest = result.total_time - sum(result.split(s) or 0 for s in SEGMENTS)
    return (missing[0].value, rest) if rest >= 0 else None


def completeness(race: Race) -> dict[str, Any]:
    """What is missing, grouped by what each missing piece unlocks. Owner only."""
    result = race.official_result
    primaries = race.primary_activities()

    official_missing = ["total_time"] if result is None else []
    if result is not None:
        official_missing = [f"{s}_time" for s in SEGMENTS if result.split(s) is None]
        official_missing += [
            p for p in ("overall_place", "age_group_place") if not getattr(result, p)
        ]

    fuelled = {f.segment for f in race.fueling}
    fueling_missing = [s.value for s in (SPORT_SEGMENTS[1], SPORT_SEGMENTS[2]) if s not in fuelled]

    distances_known = all(
        primaries.get(s) is not None and primaries[s].distance_m is not None for s in SPORT_SEGMENTS
    )
    has_times = result is not None or all(primaries.get(s) for s in SPORT_SEGMENTS)

    public_missing = [
        name
        for name, present in (
            ("name", race.name != "Untitled race"),
            ("location", bool(race.location_name)),
            ("official_result", result is not None),
            ("notes", bool(race.notes)),
        )
        if not present
    ]
    return {
        "official_result": {"missing": official_missing, "usable": result is not None},
        "fueling": {"missing": fueling_missing, "usable": bool(fuelled)},
        "classification": {
            "missing": [] if distances_known else ["recorded_distances"],
            "usable": race.race_category_id is not None,
        },
        "comparison": {"usable": has_times and race.race_category_id is not None},
        "public": {"missing": public_missing, "usable": True},
    }
