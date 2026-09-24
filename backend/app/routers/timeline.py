"""The home page's list and stat strip (docs/features/timeline.md)."""

from datetime import date

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.access import DB, CurrentViewer, reader_router, visible_races
from app.models import Race, RecordedActivity
from app.models.enums import SEGMENTS, SPORT_SEGMENTS
from app.routers.races import category_ref
from app.schemas.races import RaceList, RaceListItem, Stats

read = reader_router()


def list_item(race: Race) -> RaceListItem:
    """Official splits when all are known, recorded primary totals otherwise."""
    result = race.official_result
    primaries = race.primary_activities()
    official = {s: result.split(s) for s in SEGMENTS} if result else {}
    if result and all(v is not None for v in official.values()):
        source, times, derived = "official", official, []
    elif primaries:
        source = "recorded"
        times = {s: (a.elapsed_time if (a := primaries.get(s)) else None) for s in SEGMENTS}
        derived = [s for s, a in primaries.items() if a.is_derived]
    else:
        source, times, derived = None, dict.fromkeys(SEGMENTS), []
    recorded_total = sum(a.elapsed_time for a in primaries.values()) if primaries else None
    return RaceListItem(
        id=race.id,
        name=race.name,
        date=race.date,
        location_name=race.location_name,
        is_public=race.is_public,
        category=category_ref(race.category),
        total_time=result.total_time if result else recorded_total,
        times_source=source,
        segment_times=times,
        derived_segments=derived,
    )


@read.get("/races", response_model=RaceList)
def list_races(
    db: DB,
    viewer: CurrentViewer,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    query = visible_races(viewer)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    races = db.scalars(
        query.options(
            selectinload(Race.official_result),
            selectinload(Race.category),
            # Only the columns the card needs: no tracks, no laps, no raw_data.
            selectinload(Race.activities).load_only(
                RecordedActivity.segment,
                RecordedActivity.is_primary,
                RecordedActivity.is_derived,
                RecordedActivity.elapsed_time,
                RecordedActivity.start_time,
            ),
        )
        .order_by(Race.date.desc(), Race.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return RaceList(items=[list_item(r) for r in races], total=total)


@read.get("/stats/summary", response_model=Stats)
def stats(
    db: DB,
    viewer: CurrentViewer,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
):
    """Aggregates skip nulls and report their denominator."""
    races = visible_races(viewer)
    if date_from:
        races = races.where(Race.date >= date_from)
    if date_to:
        races = races.where(Race.date <= date_to)
    ids = races.with_only_columns(Race.id).subquery()
    per_race = (
        select(
            RecordedActivity.race_id,
            func.sum(RecordedActivity.distance_m).label("distance"),
            func.sum(RecordedActivity.elevation_gain_m).label("elevation"),
        )
        .where(
            RecordedActivity.race_id.in_(select(ids.c.id)),
            RecordedActivity.is_primary,
            RecordedActivity.segment.in_(SPORT_SEGMENTS),
        )
        .group_by(RecordedActivity.race_id)
        .subquery()
    )
    row = db.execute(
        select(
            func.coalesce(func.sum(per_race.c.distance), 0),
            func.count(per_race.c.distance),
            func.coalesce(func.sum(per_race.c.elevation), 0),
            func.count(per_race.c.elevation),
        )
    ).one()
    return Stats(
        race_count=db.scalar(select(func.count()).select_from(ids)) or 0,
        distance_m=row[0],
        distance_race_count=row[1],
        elevation_gain_m=row[2],
        elevation_race_count=row[3],
    )
