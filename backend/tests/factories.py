"""Test factories for the M3 read-API tests (test_races.py, test_stats.py):
build a user, a race, and a realistic set of primary recorded activities +
laps in a couple of calls. Self-contained — other agents own their own
fixtures per the M3 task split."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.auth.password import hash_password
from app.models import (
    LapSplit,
    OfficialResult,
    Race,
    RaceType,
    RecordedActivity,
    Segment,
    UploadSource,
    User,
)

DEFAULT_EMAIL = "athlete@example.com"
DEFAULT_PASSWORD = "correct-horse-battery-staple"


def make_user(db: Session, *, email: str = DEFAULT_EMAIL, password: str = DEFAULT_PASSWORD) -> User:
    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_race(
    db: Session,
    *,
    owner: User,
    name: str = "Stutensee Olympic",
    race_date: date = date(2026, 6, 21),
    race_type: RaceType = RaceType.OLYMPIC,
    location_name: str | None = "Stutensee",
    location_lat: float | None = 49.05,
    location_lng: float | None = 8.40,
    notes: str | None = None,
) -> Race:
    race = Race(
        user_id=owner.id,
        name=name,
        date=race_date,
        race_type=race_type,
        location_name=location_name,
        location_lat=location_lat,
        location_lng=location_lng,
        notes=notes,
    )
    db.add(race)
    db.commit()
    db.refresh(race)
    return race


# Realistic-ish Olympic-distance totals per segment (session totals, never
# summed from laps — §3 Traps 1). Sums to a 2:42:43 total, matching
# make_official_result's default total_time of 9763s.
SEGMENT_TOTALS: dict[Segment, dict[str, Any]] = {
    Segment.SWIM: {"elapsed": 1740, "distance": 1500.0, "elevation": None},
    Segment.T1: {"elapsed": 180, "distance": 200.0, "elevation": None},
    Segment.BIKE: {"elapsed": 4390, "distance": 40000.0, "elevation": 350},
    Segment.T2: {"elapsed": 90, "distance": 100.0, "elevation": None},
    Segment.RUN: {"elapsed": 3363, "distance": 10000.0, "elevation": 80},
}


def _make_track(start: datetime, elapsed_s: int, n: int = 40) -> list[dict[str, Any]]:
    """A small synthetic GPS track — enough points for Douglas-Peucker
    simplification to have something to do, without approaching the ~9k-point
    real-world size (that's what the FIT fixtures are for)."""
    points = []
    for i in range(n):
        frac = i / max(n - 1, 1)
        t = start + timedelta(seconds=elapsed_s * frac)
        points.append(
            {
                "lat": 49.05 + 0.01 * frac + (0.0007 if i % 3 == 0 else 0.0),
                "lng": 8.40 + 0.01 * frac - (0.0007 if i % 5 == 0 else 0.0),
                "elevation": 100.0 + 5 * (i % 7),
                "timestamp": t.isoformat(),
            }
        )
    return points


def make_primary_activities(
    db: Session,
    *,
    race: Race,
    start: datetime | None = None,
    with_track: bool = False,
) -> dict[Segment, RecordedActivity]:
    """One `is_primary` `RecordedActivity` per segment, back-to-back in race
    order, each with 3 evenly-split lap splits (pacing shape only)."""
    cursor = start or datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
    activities: dict[Segment, RecordedActivity] = {}

    for segment in Segment:
        totals = SEGMENT_TOTALS[segment]
        activity = RecordedActivity(
            race_id=race.id,
            segment=segment,
            is_primary=True,
            is_derived=False,
            source=UploadSource.FIT_UPLOAD,
            start_time=cursor,
            elapsed_time=totals["elapsed"],
            moving_time=totals["elapsed"],
            distance_m=totals["distance"],
            elevation_gain_m=totals["elevation"],
            avg_hr=145,
            avg_power=180 if segment in (Segment.BIKE, Segment.RUN) else None,
        )
        if with_track:
            activity.gps_track = _make_track(cursor, totals["elapsed"])
            bounds_lats = [p["lat"] for p in activity.gps_track]
            bounds_lngs = [p["lng"] for p in activity.gps_track]
            activity.bounds_n_lat = max(bounds_lats)
            activity.bounds_s_lat = min(bounds_lats)
            activity.bounds_e_lng = max(bounds_lngs)
            activity.bounds_w_lng = min(bounds_lngs)
        db.add(activity)
        db.flush()

        lap_time = totals["elapsed"] // 3
        lap_distance = (totals["distance"] / 3) if totals["distance"] else None
        for i in range(3):
            db.add(
                LapSplit(
                    recorded_activity_id=activity.id,
                    sport=segment,
                    lap_index=i + 1,
                    start_time=cursor + timedelta(seconds=lap_time * i),
                    total_time=lap_time,
                    distance_m=lap_distance,
                    avg_hr=140 + i,
                )
            )

        activities[segment] = activity
        cursor = cursor + timedelta(seconds=totals["elapsed"])

    db.commit()
    for activity in activities.values():
        db.refresh(activity)
    return activities


def make_official_result(db: Session, *, race: Race, **overrides: Any) -> OfficialResult:
    defaults: dict[str, Any] = {
        "total_time": 9763,
        "swim_time": 1740,
        "t1_time": 180,
        "bike_time": 4390,
        "t2_time": 90,
        "run_time": 3363,
        "overall_place": 42,
        "age_group_place": 5,
    }
    defaults.update(overrides)
    result = OfficialResult(race_id=race.id, **defaults)
    db.add(result)
    db.commit()
    db.refresh(result)
    return result
