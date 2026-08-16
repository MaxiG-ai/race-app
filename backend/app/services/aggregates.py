"""Owner-scoped aggregate queries (§4 Tenancy).

Every function here takes a `user_id` explicitly and joins through `Race` to
enforce it — there is no unscoped ``SELECT ... FROM races`` anywhere, including
what backs `/stats/summary`. Callers (routers) must not bypass this by
querying `RecordedActivity`/`OfficialResult` directly for aggregate purposes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models import OfficialResult, Race, RecordedActivity, Segment
from app.schemas.race import SegmentSummary
from app.schemas.stats import StatsSummary

# Race order, shared with routers/races.py so segment lists and track/lap
# ordering agree with the timeline's own left-to-right convention.
RACE_SEGMENT_ORDER: list[Segment] = list(Segment)

# Segment -> OfficialResult column name.
_OFFICIAL_TIME_FIELDS: dict[Segment, str] = {
    Segment.SWIM: "swim_time",
    Segment.T1: "t1_time",
    Segment.BIKE: "bike_time",
    Segment.T2: "t2_time",
    Segment.RUN: "run_time",
}


@dataclass(frozen=True, slots=True)
class RaceAggregates:
    """Per-race rollup for the `GET /races` timeline preview: official + recorded
    per-segment totals for the `SegmentBar`, plus the two race-level totals."""

    segments: list[SegmentSummary] = field(default_factory=list)
    total_official_time_s: int | None = None
    total_recorded_time_s: int | None = None


_EMPTY_AGGREGATES = RaceAggregates()


def compute_stats_summary(
    db: Session,
    user_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> StatsSummary:
    """Race count, total distance and total elevation gain across `is_primary`
    recorded activities, scoped to one user's races (optionally further
    restricted to a date range on `Race.date`).

    A single query: `RecordedActivity` is LEFT JOINed with the `is_primary`
    filter folded into the join condition (not a WHERE clause), so a race with
    no primary recording yet still counts toward `race_count` instead of
    vanishing from it.
    """
    filters = [Race.user_id == user_id]
    if date_from is not None:
        filters.append(Race.date >= date_from)
    if date_to is not None:
        filters.append(Race.date <= date_to)

    race_count, total_distance_m, total_elevation_gain_m = (
        db.query(
            func.count(func.distinct(Race.id)),
            func.coalesce(func.sum(RecordedActivity.distance_m), 0.0),
            func.coalesce(func.sum(RecordedActivity.elevation_gain_m), 0.0),
        )
        .select_from(Race)
        .outerjoin(
            RecordedActivity,
            and_(
                RecordedActivity.race_id == Race.id,
                RecordedActivity.is_primary.is_(True),
            ),
        )
        .filter(*filters)
        .one()
    )
    return StatsSummary(
        race_count=race_count,
        total_distance_m=float(total_distance_m),
        total_elevation_gain_m=float(total_elevation_gain_m),
    )


def race_aggregates_by_id(
    db: Session, user_id: int, race_ids: Sequence[int]
) -> dict[int, RaceAggregates]:
    """Official + recorded per-segment totals for a batch of races, for the
    `GET /races` timeline preview.

    Exactly two queries regardless of how many ids are passed — never one
    query per race — so a paginated timeline doesn't N+1 as the page grows.
    Both queries join through `Race` and filter by `user_id` themselves, as a
    second line of defense beyond the caller already having scoped `race_ids`
    from a `Race.user_id`-filtered query.

    Recorded totals come from the `is_primary` recording per segment, never
    from summing `LapSplit` rows (§3 Traps 1: Apple drops the bike's trailing
    partial lap, understating the segment by kilometres).
    """
    if not race_ids:
        return {}

    official_rows = (
        db.query(OfficialResult)
        .join(Race, OfficialResult.race_id == Race.id)
        .filter(Race.user_id == user_id, OfficialResult.race_id.in_(race_ids))
        .all()
    )
    recorded_rows = (
        db.query(
            RecordedActivity.race_id,
            RecordedActivity.segment,
            RecordedActivity.elapsed_time,
            RecordedActivity.distance_m,
            RecordedActivity.is_derived,
        )
        .join(Race, RecordedActivity.race_id == Race.id)
        .filter(
            Race.user_id == user_id,
            RecordedActivity.race_id.in_(race_ids),
            RecordedActivity.is_primary.is_(True),
        )
        .all()
    )

    per_race_segments: dict[int, dict[Segment, SegmentSummary]] = {rid: {} for rid in race_ids}
    total_official_by_race: dict[int, int] = {}

    for official in official_rows:
        total_official_by_race[official.race_id] = official.total_time
        segments = per_race_segments[official.race_id]
        for segment, field_name in _OFFICIAL_TIME_FIELDS.items():
            official_time = getattr(official, field_name)
            if official_time is None:
                continue
            segments.setdefault(
                segment, SegmentSummary(segment=segment)
            ).official_time_s = official_time

    for race_id, segment, elapsed_time, distance_m, is_derived in recorded_rows:
        segments = per_race_segments[race_id]
        summary = segments.setdefault(segment, SegmentSummary(segment=segment))
        summary.recorded_time_s = elapsed_time
        summary.recorded_distance_m = distance_m
        summary.is_derived = is_derived

    result: dict[int, RaceAggregates] = {}
    for race_id, segments_by_segment in per_race_segments.items():
        segments = [segments_by_segment[s] for s in RACE_SEGMENT_ORDER if s in segments_by_segment]
        recorded_times = [s.recorded_time_s for s in segments if s.recorded_time_s is not None]
        result[race_id] = RaceAggregates(
            segments=segments,
            total_official_time_s=total_official_by_race.get(race_id),
            total_recorded_time_s=sum(recorded_times) if recorded_times else None,
        )
    return result


def empty_aggregates() -> RaceAggregates:
    """Fallback for a race id absent from `race_aggregates_by_id`'s result
    (can't happen when `race_ids` is derived from the same user-scoped query,
    but keeps callers total)."""
    return _EMPTY_AGGREGATES
