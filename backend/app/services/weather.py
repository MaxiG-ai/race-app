"""Open-Meteo historical weather (docs/features/weather.md#rules).

Enrichment only: every failure is logged and leaves the fields null.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import utcnow
from app.models import Race
from app.services.tz import timezone_at

log = logging.getLogger(__name__)

# Used when the race has no recorded start time: age-group races start mid-morning.
DEFAULT_START_HOUR = 9

# WMO weather codes, grouped.
_CONDITIONS = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow",
    80: "showers",
    81: "showers",
    82: "heavy showers",
    85: "snow showers",
    86: "snow showers",
    95: "thunderstorm",
    96: "thunderstorm",
    99: "thunderstorm",
}


def fetch_json(url: str) -> Any:
    """The only network call; tests replace it."""
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def local_start_hour(race: Race) -> int:
    """The race's local start hour: from the first recording if there is one."""
    starts = [a.start_time for a in race.activities]
    zone = timezone_at(race.location_lat, race.location_lng)  # type: ignore[arg-type]
    if not starts or zone is None:
        return DEFAULT_START_HOUR
    return min(starts).astimezone(ZoneInfo(zone)).hour


def refresh(race: Race) -> bool:
    """Fetch race-morning conditions. Returns whether anything was written."""
    if race.location_lat is None or race.location_lng is None:
        return False
    hour = local_start_hour(race)
    params = {
        "latitude": race.location_lat,
        "longitude": race.location_lng,
        "start_date": race.date.isoformat(),  # the local date the owner entered is the anchor
        "end_date": race.date.isoformat(),
        "hourly": "temperature_2m,relative_humidity_2m,weather_code",
        "timezone": "auto",  # hourly times come back in the location's local time
    }
    url = f"{get_settings().open_meteo_base_url}?{urllib.parse.urlencode(params)}"
    try:
        hourly = fetch_json(url)["hourly"]
        wanted = datetime.combine(race.date, time(hour)).strftime("%Y-%m-%dT%H:%M")
        i = hourly["time"].index(wanted)
        temp, humidity, code = (
            hourly["temperature_2m"][i],
            hourly["relative_humidity_2m"][i],
            hourly["weather_code"][i],
        )
    except Exception as exc:  # network, HTTP, malformed body, missing hour: all the same here
        log.warning("Weather lookup failed for race %s: %s", race.id, exc)
        return False
    race.weather_temp_c = temp
    race.weather_humidity_pct = humidity
    race.weather_conditions = _CONDITIONS.get(code) if code is not None else None
    race.weather_fetched_at = utcnow()
    return True
