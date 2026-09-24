"""docs/features/weather.md#acceptance. The Open-Meteo call is always stubbed."""

from urllib.parse import parse_qs, urlparse

from app.services import weather
from tests.factories import make_race

HOURLY = {
    "time": [f"2026-06-21T{h:02d}:00" for h in range(24)],
    "temperature_2m": [10 + h for h in range(24)],
    "relative_humidity_2m": [80 - h for h in range(24)],
    "weather_code": [0] * 9 + [3] * 15,
}


def test_request_uses_local_date_and_start_hour(owner, db, monkeypatch):
    calls = []
    monkeypatch.setattr(weather, "fetch_json", lambda url: calls.append(url) or {"hourly": HOURLY})
    # 07:00 UTC in Karlsruhe (CEST) is 09:00 local
    make_race(db, location_lat=49.0, location_lng=8.4, times={"swim": 600})
    body = owner.post("/api/races/1/weather/refresh").json()
    query = parse_qs(urlparse(calls[0]).query)
    assert query["start_date"] == ["2026-06-21"] and query["timezone"] == ["auto"]
    assert body["weather"] == {"temp_c": 19, "humidity_pct": 71, "conditions": "overcast"}


def test_without_recordings_the_default_hour_is_used(owner, db, monkeypatch):
    monkeypatch.setattr(weather, "fetch_json", lambda url: {"hourly": HOURLY})
    make_race(db, location_lat=49.0, location_lng=8.4)
    assert owner.post("/api/races/1/weather/refresh").json()["weather"]["temp_c"] == 19


def test_failure_leaves_fields_null(owner, db):
    make_race(db, location_lat=49.0, location_lng=8.4)  # conftest: network raises
    assert owner.post("/api/races/1/weather/refresh").status_code == 502
    assert owner.get("/api/races/1").json()["weather"]["temp_c"] is None


def test_no_location_nothing_to_fetch(owner, db):
    make_race(db)
    response = owner.post("/api/races/1/weather/refresh")
    assert response.status_code == 422 and "location" in response.json()["detail"]
