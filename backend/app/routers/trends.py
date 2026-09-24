"""Cross-race trends (docs/features/trends.md): one light row per visible race."""

from sqlalchemy.orm import selectinload

from app.access import DB, CurrentViewer, reader_router, visible_races
from app.models import Race, RecordedActivity
from app.models.enums import SPORT_SEGMENTS
from app.routers.races import category_ref
from app.schemas.trends import SportTotals, TrendRace, Trends

read = reader_router()


def trend_row(race: Race) -> TrendRace:
    primaries = race.primary_activities()
    return TrendRace(
        id=race.id,
        name=race.name,
        date=race.date,
        category=category_ref(race.category),
        temp_c=race.weather_temp_c,
        humidity_pct=race.weather_humidity_pct,
        sports={
            s: SportTotals(
                distance_m=a.distance_m,
                elapsed_time=a.elapsed_time,
                avg_hr=a.avg_hr,
                avg_power=a.avg_power,
            )
            if (a := primaries.get(s))
            else None
            for s in SPORT_SEGMENTS
        },
    )


@read.get("/trends", response_model=Trends)
def trends(db: DB, viewer: CurrentViewer):
    races = db.scalars(
        visible_races(viewer)
        .options(
            selectinload(Race.category),
            # Totals only: no tracks, no laps, no raw_data.
            selectinload(Race.activities).load_only(
                RecordedActivity.segment,
                RecordedActivity.is_primary,
                RecordedActivity.start_time,
                RecordedActivity.elapsed_time,
                RecordedActivity.distance_m,
                RecordedActivity.avg_hr,
                RecordedActivity.avg_power,
            ),
        )
        .order_by(Race.date, Race.id)
    )
    return Trends(races=[trend_row(r) for r in races])
