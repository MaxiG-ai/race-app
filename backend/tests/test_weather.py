"""Unit tests for `app.services.weather` — no live network calls.

Every test builds an `httpx.Client` over an `httpx.MockTransport`, so the request never
leaves the process. A local `_Race` stand-in (not the real ORM model) is used for the
`apply_weather_to_race` tests to keep this file independent of DB setup / conftest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest

from app.services.weather import WeatherResult, apply_weather_to_race, fetch_weather

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

# 24 hourly buckets for a single UTC day, matching what `timezone=UTC` produces.
_HOURS = [f"2026-07-11T{h:02d}:00" for h in range(24)]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _good_payload(*, temps=None, humidities=None, codes=None) -> dict:
    n = len(_HOURS)
    return {
        "hourly": {
            "time": list(_HOURS),
            "temperature_2m": temps if temps is not None else [15.0 + i * 0.1 for i in range(n)],
            "relative_humidity_2m": humidities if humidities is not None else [60.0] * n,
            "weather_code": codes if codes is not None else [0] * n,
        }
    }


@dataclass
class _Race:
    """Minimal stand-in for `app.models.race.Race` — only the three weather columns
    `apply_weather_to_race` touches."""

    weather_temp_c: float | None = None
    weather_humidity_pct: float | None = None
    weather_conditions: str | None = None


# --------------------------------------------------------------------------- #
# fetch_weather
# --------------------------------------------------------------------------- #


def test_good_response_parsed_correctly():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["timezone"] == "UTC"
        assert request.url.params["start_date"] == "2026-07-11"
        assert request.url.params["end_date"] == "2026-07-11"
        payload = _good_payload(temps=[20.5] * 24, humidities=[55.0] * 24, codes=[1] * 24)
        return httpx.Response(200, json=payload)

    start = datetime(2026, 7, 11, 9, 30, tzinfo=UTC)
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result == WeatherResult(temp_c=20.5, humidity_pct=55.0, conditions="partly cloudy")


def test_hour_selection_lands_on_the_matching_hour():
    temps = [float(h) for h in range(24)]  # temp == hour, so the picked index is obvious

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_good_payload(temps=temps))

    start = datetime(2026, 7, 11, 14, 45, tzinfo=UTC)  # should select hour 14
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result.temp_c == 14.0


def test_hour_selection_reasons_in_utc_regardless_of_input_offset():
    """A non-UTC but tz-aware input is converted to UTC before hour matching — an
    explicit regression guard against the off-by-one-hour bug PLAN §3 warns about."""
    from zoneinfo import ZoneInfo

    temps = [float(h) for h in range(24)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_good_payload(temps=temps))

    # 16:45 CEST (UTC+2) == 14:45 UTC -> still hour 14.
    start = datetime(2026, 7, 11, 16, 45, tzinfo=ZoneInfo("Europe/Berlin"))
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result.temp_c == 14.0


def test_naive_datetime_rejected():
    start = datetime(2026, 7, 11, 9, 0)  # no tzinfo
    with pytest.raises(ValueError):
        fetch_weather(48.0, 8.0, start, base_url=BASE_URL)


def test_unknown_weather_code_maps_to_none_conditions():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_good_payload(codes=[42] * 24))

    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result.conditions is None
    # Unknown-code handling doesn't cascade into dropping the rest of the result.
    assert result.temp_c is not None


def test_http_500_returns_none_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result is None


def test_timeout_returns_none_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result is None


def test_connection_error_returns_none_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result is None


def test_malformed_json_returns_none_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all {")

    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result is None


def test_missing_hourly_key_returns_none_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result is None


def test_missing_hour_in_response_returns_none_gracefully():
    """The response is well-formed but doesn't cover the requested hour at all — as
    opposed to covering it with nulls (the archive-lag case, tested below)."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _good_payload()
        payload["hourly"]["time"] = _HOURS[:5]  # only hours 0-4 present
        payload["hourly"]["temperature_2m"] = payload["hourly"]["temperature_2m"][:5]
        payload["hourly"]["relative_humidity_2m"] = payload["hourly"]["relative_humidity_2m"][:5]
        payload["hourly"]["weather_code"] = payload["hourly"]["weather_code"][:5]
        return httpx.Response(200, json=payload)

    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)  # hour 9, not in [0..4]
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result is None


def test_archive_lag_nulls_are_a_normal_outcome_not_a_failure():
    """A race date within Open-Meteo's ~5 day ingestion lag returns HTTP 200 with the
    hour present but every value null — expected, and must come back as an (empty)
    result rather than None."""

    def handler(request: httpx.Request) -> httpx.Response:
        n = len(_HOURS)
        return httpx.Response(
            200,
            json={
                "hourly": {
                    "time": list(_HOURS),
                    "temperature_2m": [None] * n,
                    "relative_humidity_2m": [None] * n,
                    "weather_code": [None] * n,
                }
            },
        )

    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    result = fetch_weather(48.0, 8.0, start, client=_client(handler), base_url=BASE_URL)

    assert result == WeatherResult(temp_c=None, humidity_pct=None, conditions=None)


def test_default_client_is_created_and_closed_when_none_injected(monkeypatch):
    """When no client is injected, fetch_weather must still not touch the network —
    verified by monkeypatching httpx.Client itself to return a mock-transport client."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_good_payload())

    real_client_cls = httpx.Client

    def fake_client(*args, **kwargs):
        return real_client_cls(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", fake_client)

    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    result = fetch_weather(48.0, 8.0, start, base_url=BASE_URL)

    assert result is not None
    assert result.temp_c is not None


# --------------------------------------------------------------------------- #
# apply_weather_to_race
# --------------------------------------------------------------------------- #


def test_apply_weather_sets_all_fields_when_empty():
    race = _Race()
    result = WeatherResult(temp_c=18.2, humidity_pct=70.0, conditions="overcast")

    apply_weather_to_race(race, result)

    assert race.weather_temp_c == 18.2
    assert race.weather_humidity_pct == 70.0
    assert race.weather_conditions == "overcast"


def test_apply_weather_none_result_leaves_race_untouched():
    race = _Race(weather_temp_c=10.0, weather_humidity_pct=40.0, weather_conditions="clear")

    apply_weather_to_race(race, None)

    assert race.weather_temp_c == 10.0
    assert race.weather_humidity_pct == 40.0
    assert race.weather_conditions == "clear"


def test_apply_weather_overwrite_false_preserves_existing_humidity():
    """A real FIT humidity value (were one ever populated) outranks the API — the
    default must not clobber it."""
    race = _Race(weather_humidity_pct=33.0)
    result = WeatherResult(temp_c=21.0, humidity_pct=70.0, conditions="clear")

    apply_weather_to_race(race, result, overwrite=False)

    assert race.weather_humidity_pct == 33.0  # untouched
    assert race.weather_temp_c == 21.0  # temp has no competing source, always applied
    assert race.weather_conditions == "clear"


def test_apply_weather_overwrite_true_replaces_existing_humidity():
    race = _Race(weather_humidity_pct=33.0)
    result = WeatherResult(temp_c=21.0, humidity_pct=70.0, conditions="clear")

    apply_weather_to_race(race, result, overwrite=True)

    assert race.weather_humidity_pct == 70.0


def test_apply_weather_partial_result_only_updates_present_fields():
    race = _Race(weather_temp_c=5.0, weather_humidity_pct=50.0, weather_conditions="fog")
    empty_result = WeatherResult(temp_c=None, humidity_pct=None, conditions=None)

    apply_weather_to_race(race, empty_result, overwrite=True)

    assert race.weather_temp_c == 5.0
    assert race.weather_humidity_pct == 50.0
    assert race.weather_conditions == "fog"


# --- utc_anchor_for_local_date ---------------------------------------------- #


def test_anchor_resolves_the_local_hour_through_the_race_location_timezone() -> None:
    """`Race.date` is a plain local date (§2). Resolving it through the *race's*
    timezone rather than the server's is what stops an early start landing on the
    wrong day — the same trap §3 flags for Apple's local-time filenames."""
    from datetime import date

    from app.services.weather import DEFAULT_LOCAL_RACE_HOUR, utc_anchor_for_local_date

    summer = utc_anchor_for_local_date(49.0, 8.4, date(2026, 7, 11))
    winter = utc_anchor_for_local_date(49.0, 8.4, date(2026, 1, 11))

    # Karlsruhe: CEST (UTC+2) in July, CET (UTC+1) in January. A fixed offset would
    # get one of these wrong, so this pins that the zone is applied, not an offset.
    assert summer.hour == DEFAULT_LOCAL_RACE_HOUR - 2
    assert winter.hour == DEFAULT_LOCAL_RACE_HOUR - 1
    assert summer.date().isoformat() == "2026-07-11"
    assert winter.date().isoformat() == "2026-01-11"
    assert summer.tzinfo is not None and winter.tzinfo is not None


def test_anchor_falls_back_to_utc_when_no_timezone_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mid-ocean coordinates resolve to no zone. A few hours off is a far better
    answer than refusing to report weather at all."""
    from datetime import date

    from app.services import weather as weather_module

    monkeypatch.setattr(weather_module, "timezone_at", lambda lat, lng: None)
    anchor = weather_module.utc_anchor_for_local_date(0.0, -140.0, date(2026, 7, 11))

    assert anchor.date().isoformat() == "2026-07-11"
    assert anchor.hour == weather_module.DEFAULT_LOCAL_RACE_HOUR
    assert anchor.utcoffset().total_seconds() == 0
