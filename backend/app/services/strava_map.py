"""A Strava activity → the same `ParsedFile` a FIT parse produces
(docs/features/strava.md#mapping).

Pure functions, no I/O: the JSON Strava returned in, dataclasses out. Everything after
this point (which race, which leg, primaries, derived transitions) is the FIT path's own
code in `services/imports.py`, so a Strava race and a dropped-file race can't drift apart.

The import traps carry over (docs/reference/fit-files.md):
- Trap 1: totals are the activity's own (`elapsed_time`, `distance`), never lap sums.
- Trap 2: power only when a power meter measured it (`device_watts`). Strava *estimates*
  watts for rides without one; an estimate is not a measurement, so it stays null.
- Trap 11: the race date is `start_date_local`, the athlete's wall clock.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.models.enums import Sport, UploadKind
from app.services.fit_import import ParsedFile, ParsedLap, ParsedSession, progress_series

# sport_type → our sport. Anything not listed (walks, e-bikes, workouts, ...) is not a
# swim, bike or run leg and is never imported.
SPORT_TYPES: dict[str, Sport] = {
    "Swim": Sport.SWIM,
    "Ride": Sport.BIKE,
    "VirtualRide": Sport.BIKE,
    "GravelRide": Sport.BIKE,
    "MountainBikeRide": Sport.BIKE,
    "Handcycle": Sport.BIKE,
    "Run": Sport.RUN,
    "TrailRun": Sport.RUN,
    "VirtualRun": Sport.RUN,
    "Wheelchair": Sport.RUN,
}

# workout_type values that mean "race" (Strava API reference, DetailedActivity).
RACE_WORKOUT_TYPES = {1, 11}

# Summary fields kept as `raw_data`. Polylines, segment efforts, photos and the like are
# either recomputed (the track) or none of our business.
_RAW_KEYS = (
    "id",
    "name",
    "sport_type",
    "type",
    "workout_type",
    "start_date",
    "start_date_local",
    "timezone",
    "elapsed_time",
    "moving_time",
    "distance",
    "total_elevation_gain",
    "average_heartrate",
    "max_heartrate",
    "average_watts",
    "weighted_average_watts",
    "max_watts",
    "kilojoules",
    "device_watts",
    "average_cadence",
    "average_temp",
    "average_speed",
    "max_speed",
    "trainer",
    "manual",
    "private",
    "visibility",
    "device_name",
    "external_id",
    "upload_id",
    "gear_id",
)


def sport_of(activity: dict[str, Any]) -> Sport | None:
    return SPORT_TYPES.get(activity.get("sport_type") or activity.get("type") or "")


def is_race(activity: dict[str, Any]) -> bool:
    return activity.get("workout_type") in RACE_WORKOUT_TYPES


def parse_time(value: str) -> datetime:
    """Strava's ISO 8601 with a trailing Z, as an aware UTC datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def local_date(activity: dict[str, Any]) -> date:
    """`start_date_local` is the wall clock at the start, despite its trailing Z."""
    local = activity.get("start_date_local") or activity["start_date"]
    return date.fromisoformat(local[:10])


def start_time(activity: dict[str, Any]) -> datetime:
    return parse_time(activity["start_date"])


def end_time(activity: dict[str, Any]) -> datetime:
    return start_time(activity) + timedelta(seconds=int(activity.get("elapsed_time") or 0))


def _int(value: Any) -> int | None:
    return round(value) if isinstance(value, int | float) else None


def _positive(value: Any) -> float | None:
    """Strava reports an unknown distance or climb as 0; unknown is null here."""
    return float(value) if isinstance(value, int | float) and value > 0 else None


def _measured_power(d: dict[str, Any], key: str) -> int | None:
    return _int(d.get(key)) if d.get("device_watts") else None


def _stream(streams: dict[str, Any], key: str) -> list[Any]:
    entry = streams.get(key)
    if isinstance(entry, dict) and isinstance(entry.get("data"), list):
        return entry["data"]
    return []


def track(streams: dict[str, Any]) -> list[dict[str, Any]]:
    """[{lat, lng, elevation, t, distance_m}], exactly the shape the FIT parser writes."""
    times = _stream(streams, "time")
    latlng = _stream(streams, "latlng")
    altitude = _stream(streams, "altitude")
    distance = _stream(streams, "distance")
    points = []
    for i, t in enumerate(times):
        pos = latlng[i] if i < len(latlng) else None
        if not pos or len(pos) != 2 or pos[0] is None or pos[1] is None:
            continue
        elevation = altitude[i] if i < len(altitude) else None
        d = distance[i] if i < len(distance) else None
        points.append(
            {
                "lat": round(pos[0], 6),
                "lng": round(pos[1], 6),
                "elevation": round(elevation, 1) if elevation is not None else None,
                "t": round(t),
                "distance_m": round(d, 1) if d is not None else None,
            }
        )
    return points


def _bounds(points: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    lats = [p["lat"] for p in points]
    lngs = [p["lng"] for p in points]
    return max(lats), min(lats), max(lngs), min(lngs)


def _partial_flags(laps: list[dict[str, Any]]) -> list[bool]:
    """Only the last of several laps can be a remainder: one well short of the others."""
    flags = [False] * len(laps)
    earlier = [d for lap in laps[:-1] if (d := _positive(lap.get("distance"))) is not None]
    if len(laps) >= 2 and earlier:
        flags[-1] = (_positive(laps[-1].get("distance")) or 0) < 0.9 * statistics.median(earlier)
    return flags


def _laps(laps: list[dict[str, Any]], sport: Sport) -> list[ParsedLap]:
    """Pacing shape only; the leg's totals come from the activity (Trap 1)."""
    ordered = sorted(laps, key=lambda lap: (lap.get("lap_index") or 0, lap.get("start_date", "")))
    partial = _partial_flags(ordered)
    return [
        ParsedLap(
            lap_index=i,
            start_time=parse_time(lap["start_date"]),
            total_time=int(lap.get("elapsed_time") or 0),
            distance_m=_positive(lap.get("distance")),
            elevation_gain_m=_positive(lap.get("total_elevation_gain"))
            if sport is not Sport.SWIM
            else None,
            avg_hr=_int(lap.get("average_heartrate")),
            max_hr=_int(lap.get("max_heartrate")),
            avg_power=_measured_power(lap, "average_watts"),
            normalized_power=None,
            cadence_avg=_int(lap.get("average_cadence")),
            is_partial=partial[i],
            raw_data={
                k: lap.get(k)
                for k in ("id", "name", "lap_index", "split", "start_date", "moving_time")
            },
        )
        for i, lap in enumerate(ordered)
        if lap.get("start_date")
    ]


def to_parsed(
    activity: dict[str, Any], laps: list[dict[str, Any]], streams: dict[str, Any]
) -> ParsedFile:
    """One Strava activity is one single-sport 'file' with one session.

    Raises ValueError for an activity that isn't a swim, bike or run."""
    sport = sport_of(activity)
    if sport is None:
        raise ValueError(f"A {activity.get('sport_type')} activity isn't a swim, bike or run.")
    elapsed = int(activity.get("elapsed_time") or 0)
    distance = _positive(activity.get("distance"))
    points = track(streams)
    has_hr = activity.get("has_heartrate", activity.get("average_heartrate") is not None)
    session = ParsedSession(
        sport=sport,
        segment=None,
        start_time=start_time(activity),
        elapsed_time=elapsed,
        moving_time=_int(activity.get("moving_time")),
        distance_m=distance,
        elevation_gain_m=_positive(activity.get("total_elevation_gain"))
        if sport is not Sport.SWIM
        else None,
        avg_hr=_int(activity.get("average_heartrate")) if has_hr else None,
        max_hr=_int(activity.get("max_heartrate")) if has_hr else None,
        avg_power=_measured_power(activity, "average_watts"),
        normalized_power=_measured_power(activity, "weighted_average_watts"),
        avg_cadence=_int(activity.get("average_cadence")),
        avg_temp_c=activity.get("average_temp"),
        rpe_estimated=None,
        bounds=_bounds(points),
        gps_track=points,
        progress_series=progress_series(points, elapsed, distance),
        laps=_laps(laps, sport),
        devices=[],  # Strava exposes no sensor serials: gear stays manual (gear.md)
        raw_data={"strava": {k: activity.get(k) for k in _RAW_KEYS if k in activity}},
    )
    warnings = []
    if activity.get("manual"):
        warnings.append("Entered by hand on Strava: no recording behind it.")
    return ParsedFile(
        kind=UploadKind.SINGLE_SPORT,
        sessions=[session],
        local_date=local_date(activity),
        orphan_record_count=0,
        device_key=None,  # device_name is a product name, not a serial (Trap 2, gear.md)
        warnings=warnings,
    )


# --- the stored original ----------------------------------------------------------------
# A pulled activity is kept as the JSON Strava returned, like a dropped FIT file is kept
# as its bytes, so `cli reimport` can re-map it without asking Strava again.


def original(
    activity: dict[str, Any], laps: list[dict[str, Any]], streams: dict[str, Any]
) -> bytes:
    return json.dumps({"activity": activity, "laps": laps, "streams": streams}).encode()


def from_original(data: bytes) -> ParsedFile:
    stored = json.loads(data)
    return to_parsed(stored["activity"], stored.get("laps") or [], stored.get("streams") or {})
