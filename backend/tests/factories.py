"""Build races directly in the database, for tests that don't need a FIT file."""

from datetime import UTC, date, datetime, timedelta

from app.models import OfficialResult, Race, RecordedActivity
from app.models.enums import SEGMENTS

START = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)


def make_race(
    db,
    *,
    name="Test race",
    public=False,
    distances=None,
    times=None,
    official=None,
    day=date(2026, 6, 21),
    **fields,
) -> Race:
    """`distances`/`times`: {segment: value} for primary recordings. `official`: kwargs."""
    race = Race(name=name, date=day, is_public=public, **fields)
    start = START
    for segment in SEGMENTS:
        if times is None or segment not in times:
            continue
        race.activities.append(
            RecordedActivity(
                segment=segment,
                is_primary=True,
                start_time=start,
                elapsed_time=times[segment],
                distance_m=(distances or {}).get(segment),
                gps_track=[{"lat": 49.0, "lng": 8.4, "elevation": 110, "t": 0, "distance_m": 0}],
                raw_data={"session": {"secret": 1}, "devices": []},
            )
        )
        start += timedelta(seconds=times[segment])
    if official:
        race.official_result = OfficialResult(**official)
    db.add(race)
    db.commit()
    return race
