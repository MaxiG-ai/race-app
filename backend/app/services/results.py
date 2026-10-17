"""Official total derivation, prefilled results and the completeness checklist
(docs/features/races-and-results.md#rules)."""

from __future__ import annotations

from typing import Any

from app.models import OfficialResult, Race
from app.models.enums import SEGMENTS, RaceType, Segment

SPLIT_LABELS = {"swim": "swim", "t1": "T1", "bike": "bike", "t2": "T2", "run": "run"}


class ResultError(ValueError):
    pass


def apply_result(race: Race, result: OfficialResult, values: dict[str, Any]) -> None:
    """Merge a partial write (only the fields the request sent) and settle the total.

    A typed total is never overwritten. A derived one is re-summed on every write. Any
    write by the owner ends the prefill: the result stops following the recordings.
    """
    result.is_prefilled = False
    segments = race.segments()
    if len(segments) == 1:
        _apply_single(result, values, segments[0])
        return
    for key, value in values.items():
        setattr(result, key, value)
    if values.get("total_time") is not None:
        result.total_is_derived = False
    elif "total_time" in values:
        result.total_is_derived = True  # the owner cleared it: derive if possible
    if result.total_time is not None and not result.total_is_derived:
        return
    splits = [result.split(s) for s in segments]
    missing = [SPLIT_LABELS[s] for s, v in zip(segments, splits, strict=True) if v is None]
    if missing:
        raise ResultError(
            "Enter the total time, or fill in every split so it can be calculated. "
            f"Still missing: {', '.join(missing)}."
        )
    result.total_time = sum(splits)  # type: ignore[arg-type]
    result.total_is_derived = True


def _apply_single(result: OfficialResult, values: dict[str, Any], segment: Segment) -> None:
    """A single-sport race has one split, and it is the total: they are kept equal."""
    split = values.pop(f"{segment}_time", None)
    for key, value in values.items():
        if not key.endswith("_time") or key == "total_time":
            setattr(result, key, value)
    if "total_time" not in values and split is not None:
        result.total_time = split
    if result.total_time is None:
        raise ResultError("Enter the total time.")
    for s in SEGMENTS:
        setattr(result, f"{s}_time", result.total_time if s == segment else None)
    result.total_is_derived = False


def normalise_for_type(race: Race) -> None:
    """After a race-type change: refill a prefilled result, otherwise keep the typed total
    and drop splits that no longer belong (a run race's split is its total)."""
    result = race.official_result
    if result is None or result.is_prefilled:
        prefill(race)
        return
    segments = race.segments()
    # A single-sport race's one split is its total, which says nothing about any legs.
    single = segments[0] if len(segments) == 1 else None
    for s in SEGMENTS:
        setattr(result, f"{s}_time", result.total_time if s == single else None)
    result.total_is_derived = False


def prefill(race: Race) -> None:
    """The official result from the primary recordings, refreshed whenever they change,
    until the owner edits or confirms it. Missing legs stay null; without every leg, the
    total is the clock time from the first recorded start to the last recorded finish."""
    result = race.official_result
    if result is not None and not result.is_prefilled:
        return
    primaries = race.primary_activities()
    segments = race.segments()
    legs = {s: primaries[s] for s in segments if s in primaries}
    if not legs:
        race.official_result = None
        return
    if result is None:
        result = OfficialResult()
        race.official_result = result
    for s in SEGMENTS:
        setattr(result, f"{s}_time", legs[s].elapsed_time if s in legs else None)
    complete = len(legs) == len(segments)
    if complete:
        result.total_time = sum(a.elapsed_time for a in legs.values())
    else:
        first = min(a.start_time for a in legs.values())
        last = max(a.end_time for a in legs.values())
        result.total_time = round((last - first).total_seconds())
    result.total_is_derived = complete and len(segments) > 1
    result.is_prefilled = True
    result.is_confirmed = False


def remainder_split(race: Race, result: OfficialResult) -> tuple[str, int] | None:
    """Total and all splits but one entered: the last is the remainder, derived on read."""
    segments = race.segments()
    missing = [s for s in segments if result.split(s) is None]
    if result.total_is_derived or len(missing) != 1 or len(segments) == 1:
        return None
    rest = result.total_time - sum(result.split(s) or 0 for s in segments)
    return (missing[0].value, rest) if rest >= 0 else None


def completeness(race: Race) -> dict[str, Any]:
    """What is missing, grouped by what each missing piece unlocks. Owner only."""
    result = race.official_result
    primaries = race.primary_activities()
    segments = race.segments()
    sports = [s for s in segments if s not in (Segment.T1, Segment.T2)]
    is_triathlon = race.race_type is RaceType.TRIATHLON

    official_missing = ["total_time"] if result is None else []
    if result is not None:
        if len(segments) > 1:
            official_missing = [f"{s}_time" for s in segments if result.split(s) is None]
        official_missing += [
            p for p in ("overall_place", "age_group_place") if not getattr(result, p)
        ]
        if not result.is_confirmed:
            official_missing.append("confirmation")

    # Fuel is taken on the bike and the run; a swim race has nothing to log.
    fuel_segments = [s for s in sports if s is not Segment.SWIM]
    fuelled = {f.segment for f in race.fueling}
    fueling_missing = [s.value for s in fuel_segments if s not in fuelled]

    distances_known = all(
        primaries.get(s) is not None and primaries[s].distance_m is not None for s in sports
    )
    has_times = result is not None or all(primaries.get(s) for s in sports)

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
    groups: dict[str, Any] = {
        "recordings": {
            "missing": [s.value for s in race.missing_segments()],
            "usable": not race.is_incomplete,
        },
        "official_result": {"missing": official_missing, "usable": result is not None},
    }
    if fuel_segments:
        groups["fueling"] = {"missing": fueling_missing, "usable": bool(fuelled)}
    if is_triathlon:
        groups["classification"] = {
            "missing": [] if distances_known else ["recorded_distances"],
            "usable": race.race_category_id is not None,
        }
        groups["comparison"] = {"usable": has_times and race.race_category_id is not None}
    else:
        groups["comparison"] = {"usable": has_times and distances_known}
    groups["public"] = {"missing": public_missing, "usable": True}
    return groups
