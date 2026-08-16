"""Open-Meteo historical weather enrichment (PLAN.md §3 "Weather enrichment", M7).

Neither vendor populates the FIT humidity field in practice (§3 Traps 10), so
`Race.weather_*` is filled entirely from Open-Meteo's free, keyless historical archive
API, keyed on the race's `location_lat`/`location_lng` and start time.

Sync, not async
----------------
`httpx` supports both. This module is sync-only: the caller is the FIT import path,
which runs synchronous SQLAlchemy against a synchronous `Session` (see `app/db.py`) — an
async HTTP call in the middle of that would buy nothing (there's nothing else to overlap
it with) and would force either a thread hop back into sync code or a parallel
async-session import path. `POST /races/{id}/weather/refresh` is a single request/response
FastAPI endpoint, not a hot path, so the small blocking call inside it is unremarkable.
A sync `httpx.Client` (optionally injected by the caller/tests) keeps one code path
instead of two.

Reasoning in UTC
-----------------
Race start times are stored/derived in local time in various places (`Race.date` is a
local date, per §3's Apple-filename timezone trap), but this module takes a **timezone-
aware UTC** `datetime` and requests the archive with `timezone=UTC`. Mixing local-time
reasoning into the archive lookup is exactly how an off-by-one-hour bug creeps in (the
archive's hourly buckets would otherwise need a second timezone conversion just to be
compared against); reasoning in UTC end-to-end sidesteps that entirely. It is the
caller's job to convert a local race start time to UTC before calling in.

Failure handling
-----------------
Weather is enrichment, not a precondition for import (§3: "Weather is API-only in
practice"). Anything short of a clean, fully-populated response degrades to `None`
rather than raising: network failure, timeout, non-200, malformed JSON, or a missing
hour. Each failure logs at WARNING with enough context to debug later. The one exception
is the archive's ~5 day ingestion lag: a race date inside that window returns HTTP 200
with nulls at the matched hour, which is an expected outcome (logged at INFO), not a
failure — the caller still gets back a `WeatherResult`, just an empty one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.config import settings

if TYPE_CHECKING:
    from app.models.race import Race

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0

# WMO weather_code -> short human string, grouped per PLAN §3: 0 clear, 1-3 partly
# cloudy/overcast, 45/48 fog, 51-57 drizzle, 61-67 rain, 71-77 snow, 80-82 showers,
# 85-86 snow showers, 95-99 thunderstorm. Any code outside this table (including
# negative/out-of-range values) degrades to `None` rather than raising — see
# `_map_weather_code`.
_WMO_CONDITIONS: dict[int, str] = {
    0: "clear",
    1: "partly cloudy",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
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
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm",
    99: "thunderstorm",
}


@dataclass(frozen=True, slots=True)
class WeatherResult:
    """One hour's conditions at a race's start location."""

    temp_c: float | None
    humidity_pct: float | None
    conditions: str | None


def _map_weather_code(code: Any) -> str | None:
    """WMO code -> short string. Unknown/missing/malformed input -> `None`, never raises."""
    if code is None:
        return None
    try:
        return _WMO_CONDITIONS.get(int(code))
    except (TypeError, ValueError):
        return None


def fetch_weather(
    lat: float,
    lng: float,
    start_at_utc: datetime,
    *,
    client: httpx.Client | None = None,
    base_url: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> WeatherResult | None:
    """Fetch the historical hour matching a race's start time from Open-Meteo.

    `start_at_utc` must be a timezone-aware datetime; it is converted to UTC (a no-op
    if it's already UTC) and truncated to the hour, both for the archive request's
    date range and for picking the matching hourly bucket out of the response —
    entirely in UTC, per the module docstring.

    Returns `None` on any network failure, non-200 response, or malformed/missing-hour
    body — never raises for those. A response that parses fine but carries nulls at the
    matched hour (the archive's ~5 day lag) is *not* a failure: it comes back as a
    `WeatherResult` with `None` fields.

    `client` lets a caller (or a test, via `httpx.MockTransport`) inject an
    `httpx.Client`; when omitted, one is created and closed for this call only.
    """
    if start_at_utc.tzinfo is None:
        raise ValueError("start_at_utc must be timezone-aware (UTC) — see module docstring")
    start_at_utc = start_at_utc.astimezone(UTC)
    date_str = start_at_utc.date().isoformat()
    target_hour = start_at_utc.replace(minute=0, second=0, microsecond=0)
    target_key = target_hour.strftime("%Y-%m-%dT%H:%M")

    url = base_url or settings.open_meteo_base_url
    params = {
        "latitude": lat,
        "longitude": lng,
        "start_date": date_str,
        "end_date": date_str,
        "hourly": "temperature_2m,relative_humidity_2m,weather_code,precipitation",
        "timezone": "UTC",
    }

    owns_client = client is None
    http_client = client if client is not None else httpx.Client(timeout=timeout_s)
    try:
        try:
            response = http_client.get(url, params=params, timeout=timeout_s)
        except httpx.TimeoutException:
            logger.warning(
                "Open-Meteo request timed out (lat=%s lng=%s date=%s)", lat, lng, date_str
            )
            return None
        except httpx.HTTPError as exc:
            logger.warning(
                "Open-Meteo request failed (lat=%s lng=%s date=%s): %s", lat, lng, date_str, exc
            )
            return None

        if response.status_code != 200:
            logger.warning(
                "Open-Meteo returned HTTP %s (lat=%s lng=%s date=%s)",
                response.status_code,
                lat,
                lng,
                date_str,
            )
            return None

        try:
            payload = response.json()
            hourly = payload["hourly"]
            times = hourly["time"]
            temps = hourly["temperature_2m"]
            humidities = hourly["relative_humidity_2m"]
            codes = hourly["weather_code"]
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "Open-Meteo returned a malformed body (lat=%s lng=%s date=%s): %s",
                lat,
                lng,
                date_str,
                exc,
            )
            return None

        try:
            idx = times.index(target_key)
        except ValueError:
            logger.warning(
                "Open-Meteo response has no hourly bucket for %s (lat=%s lng=%s, %d hours "
                "in response)",
                target_key,
                lat,
                lng,
                len(times),
            )
            return None

        try:
            temp = temps[idx]
            humidity = humidities[idx]
            code = codes[idx]
        except IndexError:
            logger.warning(
                "Open-Meteo hourly arrays are shorter than the time array (date=%s)", date_str
            )
            return None

        if temp is None and humidity is None and code is None:
            # Expected outcome, not a failure: the archive lags real time by ~5 days,
            # so a recent race date returns a well-formed response with nulls at every
            # field for the matched hour.
            logger.info(
                "Open-Meteo has no data yet for %s (lat=%s lng=%s) — likely within the "
                "archive's ~5 day ingestion lag",
                date_str,
                lat,
                lng,
            )

        return WeatherResult(
            temp_c=float(temp) if temp is not None else None,
            humidity_pct=float(humidity) if humidity is not None else None,
            conditions=_map_weather_code(code),
        )
    finally:
        if owns_client:
            http_client.close()


def apply_weather_to_race(
    race: Race, result: WeatherResult | None, *, overwrite: bool = False
) -> None:
    """Apply a `WeatherResult` onto a `Race` ORM instance. Does **not** commit — the
    caller (the import pipeline or the `/weather/refresh` endpoint) owns the
    transaction.

    Per PLAN §3, a real FIT humidity value would outrank the API were one ever
    populated (it never is in practice, §3 Traps 10, but the path is meant to exist
    untested against real data). So `weather_humidity_pct` is only overwritten when it
    is not already set, unless the caller explicitly passes `overwrite=True` (e.g. the
    `/weather/refresh` endpoint, which is a deliberate re-run). `temp_c` and
    `conditions` have no competing source in the schema, so a non-`None` API value
    always wins for those.

    A `None` result (total fetch failure) or `None` fields within a result (archive
    lag) leave the corresponding column(s) untouched rather than clobbering them with
    `None`.
    """
    if result is None:
        return
    if result.temp_c is not None:
        race.weather_temp_c = result.temp_c
    if result.conditions is not None:
        race.weather_conditions = result.conditions
    if result.humidity_pct is not None and (overwrite or race.weather_humidity_pct is None):
        race.weather_humidity_pct = result.humidity_pct
