"""docs/features/strava.md#acceptance, against a fake Strava. Every activity here is made
up: no real recordings in the repository."""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from app.access import hash_password
from app.config import get_settings
from app.db import _session_factory
from app.main import app
from app.models import Owner, Race, RecordedActivity, SiteSettings, StravaAccount, Upload
from app.models.enums import Sport, UploadSource
from app.services import imports, strava, strava_api, strava_map
from app.services.fit_import import parse_fit
from app.services.imports import GroupingError
from app.services.strava_api import Response, StravaError
from tests.conftest import fixture_bytes, upload

ATHLETE = 4242
STRANGER = 777
RACE_DAY = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)  # 09:00 local, Europe/Berlin summer


# --- a fake Strava ------------------------------------------------------------------------


def _streams(start_lat: float, n: int, step_s: int, step_m: float) -> dict:
    return {
        "time": {"data": [i * step_s for i in range(n)]},
        "latlng": {"data": [[start_lat + i * 0.0005, 8.4] for i in range(n)]},
        "distance": {"data": [round(i * step_m, 1) for i in range(n)]},
        "altitude": {"data": [110.0 + (i % 5) for i in range(n)]},
        "heartrate": {"data": [140 + (i % 10) for i in range(n)]},
    }


def activity(
    activity_id: int,
    name: str,
    sport_type: str,
    start: datetime,
    elapsed: int,
    distance: float,
    workout_type: int | None = None,
    **extra,
) -> dict:
    local = start + timedelta(hours=2)
    return {
        "id": activity_id,
        "name": name,
        "sport_type": sport_type,
        "type": sport_type,
        "workout_type": workout_type,
        "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_date_local": local.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timezone": "(GMT+01:00) Europe/Berlin",
        "elapsed_time": elapsed,
        "moving_time": elapsed - 30,
        "distance": distance,
        "total_elevation_gain": 0 if sport_type == "Swim" else 120.0,
        "has_heartrate": True,
        "average_heartrate": 150.4,
        "max_heartrate": 171.0,
        "average_cadence": 85.2,
        "device_watts": False,
        "average_watts": 180.0,  # Strava's estimate unless device_watts
        "description": None,
        **extra,
    }


def triathlon(base_id: int = 100, day: datetime = RACE_DAY) -> list[dict]:
    swim = activity(base_id + 1, "Example Triathlon Swim", "Swim", day, 1500, 1500.0)
    ride_start = day + timedelta(seconds=1500 + 180)
    ride = activity(
        base_id + 2,
        "Example Triathlon Bike",
        "Ride",
        ride_start,
        4000,
        40000.0,
        workout_type=11,
        device_watts=True,
        average_watts=210.0,
        weighted_average_watts=222.0,
    )
    run_start = ride_start + timedelta(seconds=4000 + 120)
    run = activity(
        base_id + 3,
        "Example Triathlon Run",
        "Run",
        run_start,
        2700,
        10000.0,
        workout_type=1,
        description="Hot day.",
    )
    return [swim, ride, run]


class FakeStrava:
    """Answers `strava_api.http` like Strava would, and records every call."""

    def __init__(self) -> None:
        self.activities: dict[int, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.puts: list[tuple[int, str]] = []
        self.revoked: list[str] = []
        self.token_athlete = ATHLETE
        self.valid_tokens = {"access-1", "access-2"}
        self.refresh_ok = True
        self.rate_usage = (1, 1)
        self.deleted: set[int] = set()
        # path -> an HTTP status Strava answers instead, or "network" for no answer at all
        self.errors: dict[str, int | str] = {}
        # Strava rotates the refresh token: with `rotate`, an old one is refused (400).
        self.rotate = False
        self.current_refresh = "refresh-1"
        self.refreshes = 0
        self.token_delay = 0.0
        self._lock = threading.Lock()

    def add(self, *items: dict) -> None:
        for a in items:
            self.activities[a["id"]] = a

    def _headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": "200,2000",
            "X-RateLimit-Usage": f"{self.rate_usage[0]},{self.rate_usage[1]}",
            "X-ReadRateLimit-Limit": "100,1000",
            "X-ReadRateLimit-Usage": f"{self.rate_usage[0]},{self.rate_usage[1]}",
        }

    def __call__(self, method, url, *, data=None, token=None, basic=None) -> Response:
        parsed = urlparse(url)
        path = parsed.path
        self.calls.append((method, path))
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        error = self.errors.get(path)
        if error == "network":
            raise StravaError("Strava couldn't be reached: timed out")
        if error is not None:
            return Response(int(error), {"message": "Server Error"}, self._headers())
        if path == "/oauth/token":
            if data["grant_type"] == "authorization_code":
                return Response(200, self._token(data["code"]), {})
            return self._refresh(data["refresh_token"])
        if path in ("/oauth/revoke", "/oauth/deauthorize"):
            if path == "/oauth/deauthorize" and token not in self.valid_tokens:
                return Response(401, {"message": "Authorization Error"}, {})
            self.revoked.append(path)
            return Response(200, None, {})
        if token not in self.valid_tokens:
            return Response(401, {"message": "Authorization Error"}, {})
        headers = self._headers()
        if path == "/api/v3/athlete":
            return Response(200, {"id": ATHLETE}, headers)
        if path == "/api/v3/athlete/activities":
            after = int(query.get("after", 0))
            page, per_page = int(query["page"]), int(query["per_page"])
            items = sorted(
                (
                    a
                    for a in self.activities.values()
                    if strava_map.start_time(a).timestamp() > after
                ),
                key=lambda a: a["start_date"],
                reverse=True,
            )
            return Response(200, items[(page - 1) * per_page : page * per_page], headers)
        m = re.fullmatch(r"/api/v3/activities/(\d+)(/laps|/streams)?", path)
        if m:
            a = self.activities.get(int(m.group(1)))
            if a is None or a["id"] in self.deleted:
                return Response(404, {"message": "Record Not Found"}, headers)
            if m.group(2) == "/laps":
                return Response(200, self._laps(a), headers)
            if m.group(2) == "/streams":
                if a.get("manual"):
                    return Response(404, {"message": "Record Not Found"}, headers)
                sport = strava_map.sport_of(a)
                n = 60
                step_s = a["elapsed_time"] // (n - 1)
                return Response(
                    200,
                    _streams(
                        49.0 if sport is not Sport.RUN else 49.1, n, step_s, a["distance"] / (n - 1)
                    ),
                    headers,
                )
            if method == "PUT":
                a["description"] = data["description"]
                self.puts.append((a["id"], data["description"]))
                return Response(200, a, headers)
            return Response(200, a, headers)
        return Response(404, {"message": "Record Not Found"}, headers)

    def _refresh(self, sent: str) -> Response:
        with self._lock:
            if not self.refresh_ok or (self.rotate and sent != self.current_refresh):
                return Response(400, {"message": "Bad Request"}, {})
            self.refreshes += 1
            new = f"refresh-{self.refreshes + 1}"
            if self.rotate:
                self.current_refresh = new  # the old one stops working at once
        time.sleep(self.token_delay)
        return Response(
            200,
            {
                "access_token": "access-2",
                "refresh_token": new,
                "expires_at": int((datetime.now(UTC) + timedelta(hours=6)).timestamp()),
            },
            {},
        )

    def _token(self, code: str) -> dict:
        athlete = STRANGER if code == "stranger" else self.token_athlete
        return {
            "token_type": "Bearer",
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_at": int((datetime.now(UTC) + timedelta(hours=6)).timestamp()),
            "athlete": {"id": athlete, "firstname": "Alex", "lastname": "Example"},
        }

    @staticmethod
    def _laps(a: dict) -> list[dict]:
        """Two laps whose sum is *not* the activity's total (Trap 1)."""
        start = strava_map.start_time(a)
        half = a["elapsed_time"] // 2
        return [
            {
                "id": a["id"] * 10 + i,
                "lap_index": i + 1,
                "start_date": (start + timedelta(seconds=half * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "elapsed_time": half - 5,
                "distance": a["distance"] / 2,
                "average_heartrate": 150,
                "average_watts": 200,
                "device_watts": a.get("device_watts", False),
            }
            for i in range(2)
        ]


@pytest.fixture
def fake(monkeypatch):
    fake = FakeStrava()
    monkeypatch.setattr(strava_api, "http", fake)
    monkeypatch.setattr(strava_api, "last_rate_limit", strava_api.RateLimit())
    settings = get_settings()
    monkeypatch.setattr(settings, "strava_client_id", "123")
    monkeypatch.setattr(settings, "strava_client_secret", "secret")
    monkeypatch.setattr(settings, "strava_owner_athlete_id", None)
    monkeypatch.setattr(settings, "strava_webhook_verify_token", "")
    monkeypatch.setattr(settings, "public_base_url", "")
    return fake


def _authorize(client: TestClient, code: str = "ok", next_path: str = "/") -> str:
    """Run the redirect dance; returns where the callback sends the browser."""
    start = client.get("/auth/strava", params={"next": next_path}, follow_redirects=False)
    assert start.status_code == 303
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    back = client.get(
        "/auth/strava/callback",
        params={"state": state, "code": code, "scope": "read,activity:read_all,activity:write"},
        follow_redirects=False,
    )
    assert back.status_code == 303
    return back.headers["location"]


@pytest.fixture
def connected(owner, fake):
    """The owner, logged in with the password, connects Strava."""
    assert _authorize(owner, next_path="/admin/site") == "/admin/site?strava=connected"
    return owner


# --- mapping ------------------------------------------------------------------------------


def test_mapping_keeps_activity_totals_and_drops_estimated_power():
    swim, ride, run = triathlon()
    laps = FakeStrava._laps(run)
    parsed = strava_map.to_parsed(run, laps, _streams(49.0, 30, 90, 333.0))
    session = parsed.sessions[0]
    assert session.elapsed_time == 2700  # the activity's total, not the lap sum (Trap 1)
    assert sum(lap.total_time for lap in session.laps) != 2700
    assert session.avg_power is None  # estimated, not measured (Trap 2)
    assert all(lap.avg_power is None for lap in session.laps)
    assert parsed.local_date.isoformat() == "2026-06-21"
    assert session.gps_track and session.progress_series
    bike = strava_map.to_parsed(ride, FakeStrava._laps(ride), {}).sessions[0]
    assert (bike.avg_power, bike.normalized_power) == (210, 222)
    assert bike.gps_track == [] and bike.bounds is None
    swim_session = strava_map.to_parsed(swim, [], {}).sessions[0]
    assert swim_session.elevation_gain_m is None


def test_unknown_distance_is_null_not_zero():
    a = activity(1, "Indoor", "Run", RACE_DAY, 600, 0.0, workout_type=1)
    assert strava_map.to_parsed(a, [], {}).sessions[0].distance_m is None


def test_local_date_comes_from_start_date_local():
    late = activity(1, "Night run", "Run", datetime(2026, 6, 20, 23, 30, tzinfo=UTC), 600, 5.0)
    assert strava_map.to_parsed(late, [], {}).local_date.isoformat() == "2026-06-21"


def test_other_sports_are_not_mapped():
    with pytest.raises(ValueError):
        strava_map.to_parsed(activity(1, "Walk", "Walk", RACE_DAY, 600, 1000.0), [], {})


# --- finding races --------------------------------------------------------------------------


def test_a_triathlon_is_one_chain_with_its_untagged_swim():
    legs = triathlon()
    warmup = activity(9, "Warm-up", "Run", RACE_DAY - timedelta(minutes=40), 600, 2000.0)
    training = activity(10, "Easy ride", "Ride", RACE_DAY + timedelta(days=1), 3600, 30000.0)
    found = strava.chains([warmup, training, *legs])
    assert [[a["id"] for a in c] for c in found] == [[101, 102, 103]]
    assert strava.race_type_of(found[0]).value == "triathlon"
    assert strava.race_name(found[0]) == "Example Triathlon"


def test_untagged_rides_and_runs_stay_out():
    ride = activity(1, "Ride to the start", "Ride", RACE_DAY, 1200, 8000.0)
    run = activity(
        2, "City 10k", "Run", RACE_DAY + timedelta(seconds=1500), 2400, 10000.0, workout_type=1
    )
    assert [[a["id"] for a in c] for c in strava.chains([ride, run])] == [[2]]


def test_legs_far_apart_are_separate_races():
    swim, ride, run = triathlon()
    run = {**run, "start_date": "2026-06-21T12:00:00Z"}
    found = strava.chains([swim, ride, run])
    assert [[a["id"] for a in c] for c in found] == [[101, 102], [103]]


def test_race_name_uses_shared_words():
    a = {"name": "🏊 Lake Sprint"}
    b = {"name": "🚴 Lake Sprint"}
    assert strava.race_name([a, b]) == "Lake Sprint"
    assert strava.race_name([{"name": "Swim"}, {"name": "Bike"}]) == "Swim"


def test_merge_description_keeps_the_athletes_text():
    first = strava.merge_description("Hot day.", "🏁 Race #1 of 2026 · 2:20:00")
    assert first == "Hot day.\n\n🏁 Race #1 of 2026 · 2:20:00"
    again = strava.merge_description(first, "🏁 Race #2 of 2026 · 2:20:00")
    assert again == "Hot day.\n\n🏁 Race #2 of 2026 · 2:20:00"
    assert strava.merge_description(None, "🏁 Race #1 of 2026") == "🏁 Race #1 of 2026"


# --- login and connect ------------------------------------------------------------------------


def test_connect_stores_tokens_server_side_only(connected, db):
    row = db.scalar(select(StravaAccount))
    assert (row.athlete_id, row.refresh_token) == (ATHLETE, "refresh-1")
    status = connected.get("/api/strava").json()
    assert status["connected"] and status["can_write"]
    assert "access" not in json.dumps(status) and "refresh" not in json.dumps(status)
    assert db.scalar(select(Owner)).strava_athlete_id == ATHLETE


def test_strava_replaces_the_password_once_linked(connected, anon):
    assert anon.get("/api/auth/me").json()["login"] == "strava"
    response = anon.post(
        "/api/auth/login", json={"username": "owner", "password": "correct horse battery"}
    )
    assert response.status_code == 403


def test_the_linked_athlete_logs_in_with_strava(connected, fake):
    browser = TestClient(app, base_url="http://localhost")
    assert _authorize(browser, next_path="/admin/site") == "/admin/site"
    assert browser.get("/api/auth/me").json()["owner"] is True


def test_a_stranger_is_turned_away_and_revoked(connected, fake):
    browser = TestClient(app, base_url="http://localhost")
    assert _authorize(browser, code="stranger") == "/login?strava=not_owner"
    assert browser.get("/api/auth/me").json()["owner"] is False
    assert fake.revoked == ["/oauth/revoke"]


def test_without_a_linked_owner_nobody_logs_in_with_strava(fake, db):
    db.add(Owner(username="owner", password_hash=hash_password("x" * 10)))
    db.commit()
    browser = TestClient(app, base_url="http://localhost")
    assert _authorize(browser) == "/login?strava=not_owner"


def test_the_configured_athlete_claims_a_fresh_instance(fake, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "strava_owner_athlete_id", ATHLETE)
    browser = TestClient(app, base_url="http://localhost")
    assert browser.get("/api/auth/me").json()["login"] == "strava"
    assert _authorize(browser) == "/"
    assert browser.get("/api/auth/me").json()["owner"] is True
    owner = db.scalar(select(Owner))
    assert owner.strava_athlete_id == ATHLETE and not owner.password_hash.startswith("$argon2")


def test_a_forged_state_is_refused(fake):
    browser = TestClient(app, base_url="http://localhost")
    browser.get("/auth/strava", follow_redirects=False)
    back = browser.get(
        "/auth/strava/callback", params={"state": "forged", "code": "ok"}, follow_redirects=False
    )
    assert back.headers["location"] == "/login?strava=expired"


def test_the_callback_only_redirects_to_this_site(fake, owner):
    assert _authorize(owner, next_path="//evil.example") == "/?strava=connected"
    # Browsers read "\\" as "/" and drop tabs: both would make "//evil.example".
    assert _authorize(owner, next_path="/\\evil.example") == "/?strava=connected"
    assert _authorize(owner, next_path="/\t/evil.example") == "/?strava=connected"


def test_strava_routes_are_off_without_credentials(anon):
    assert anon.get("/auth/strava", follow_redirects=False).status_code == 404


# --- pulling ------------------------------------------------------------------------------


def test_pull_makes_a_private_triathlon_from_three_activities(connected, fake, db):
    fake.add(*triathlon())
    fake.add(activity(50, "Easy run", "Run", RACE_DAY - timedelta(days=3), 1800, 5000.0))
    body = connected.post("/api/strava/pull", json={}).json()
    assert body["complete"] and len(body["races"]) == 1
    race = db.get(Race, body["races"][0]["race_id"])
    assert race.name == "Example Triathlon" and race.race_type.value == "triathlon"
    assert race.is_public is False
    assert race.date.isoformat() == "2026-06-21"
    primaries = race.primary_activities()
    assert set(primaries) == {"swim", "t1", "bike", "t2", "run"}
    assert primaries["t1"].is_derived and primaries["t1"].elapsed_time == 180
    assert primaries["bike"].avg_power == 210 and primaries["run"].avg_power is None
    assert all(a.source is UploadSource.STRAVA for a in race.activities)
    uploads = db.scalars(select(Upload).where(Upload.race_id == race.id)).all()
    assert sorted(u.external_id for u in uploads) == ["101", "102", "103"]


def test_a_second_pull_imports_nothing_again(connected, fake):
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    again = connected.post("/api/strava/pull", json={"everything": True}).json()
    assert again["races"] == [] and again["message"] == "No new races on Strava."


def test_a_deleted_race_does_not_come_back(connected, fake):
    fake.add(*triathlon())
    race_id = connected.post("/api/strava/pull", json={}).json()["races"][0]["race_id"]
    assert connected.delete(f"/api/races/{race_id}").status_code == 204
    again = connected.post("/api/strava/pull", json={"everything": True}).json()
    assert again["races"] == []


def test_a_strava_activity_joins_the_race_its_fit_file_made(connected, fake, db):
    upload(connected, "edge_single_bike.fit")
    race = db.scalars(select(Race)).one()
    fit_bike = race.primary_activities()["bike"]
    ride = activity(
        300,
        "Bike race",
        "Ride",
        fit_bike.start_time,
        fit_bike.elapsed_time,
        40000.0,
        workout_type=11,
    )
    fake.add(ride)
    body = connected.post("/api/strava/pull", json={}).json()
    assert body["races"][0]["race_id"] == race.id and body["races"][0]["created"] is False
    db.expire_all()
    bikes = [a for a in db.get(Race, race.id).activities if a.segment == "bike"]
    assert len(bikes) == 2
    assert [a.source for a in bikes if a.is_primary] == [UploadSource.FIT_UPLOAD]


def test_the_tagline_is_appended_once_and_kept_up_to_date(connected, fake, db):
    fake.add(*triathlon())
    body = connected.post("/api/strava/pull", json={}).json()
    assert body["taglines"] == 3
    run = fake.activities[103]["description"]
    assert run.startswith("Hot day.\n\n🏁 Race #1 of 2026")
    puts = len(fake.puts)
    connected.post("/api/strava/pull", json={})
    assert len(fake.puts) == puts  # nothing changed: nothing written
    race_id = body["races"][0]["race_id"]
    connected.put(
        f"/api/races/{race_id}/official-result", json={"total_time": 8600, "is_confirmed": True}
    )
    connected.post("/api/strava/pull", json={})
    assert fake.activities[103]["description"].count("🏁") == 1
    assert fake.activities[103]["description"].endswith("2:23:20")


def test_the_tagline_can_be_switched_off(connected, fake):
    connected.patch("/api/strava", json={"tagline": False})
    fake.add(*triathlon())
    assert connected.post("/api/strava/pull", json={}).json()["taglines"] == 0
    assert fake.puts == []


def test_a_public_race_links_from_the_tagline(connected, fake, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "site_mode", "public")
    monkeypatch.setattr(get_settings(), "public_base_url", "https://race.example.com/")
    fake.add(*triathlon())
    race_id = connected.post("/api/strava/pull", json={}).json()["races"][0]["race_id"]
    connected.patch(
        f"/api/races/{race_id}/visibility", json={"is_public": True, "strava_consent": True}
    )
    connected.post("/api/strava/pull", json={})
    assert fake.activities[101]["description"].endswith(f"https://race.example.com/races/{race_id}")


def test_the_pull_stops_before_the_rate_limit(connected, fake):
    fake.add(*triathlon())
    fake.rate_usage = (95, 400)
    body = connected.post("/api/strava/pull", json={}).json()
    assert body["complete"] is False and "rate limit" in body["message"]


def test_an_expiring_token_is_refreshed_and_the_newest_refresh_token_kept(connected, fake, db):
    row = db.scalar(select(StravaAccount))
    row.expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db.commit()
    connected.post("/api/strava/pull", json={})
    db.expire_all()
    assert db.scalar(select(StravaAccount)).refresh_token == "refresh-2"


def test_a_revoked_connection_deletes_strava_data(connected, fake, db):
    fake.add(*triathlon())
    race_id = connected.post("/api/strava/pull", json={}).json()["races"][0]["race_id"]
    row = db.scalar(select(StravaAccount))
    row.expires_at = datetime.now(UTC)
    db.commit()
    fake.refresh_ok = False
    assert connected.post("/api/strava/pull", json={}).status_code == 409
    db.expire_all()
    assert db.scalar(select(StravaAccount)) is None
    assert db.get(Race, race_id) is not None  # the race stays, its Strava recordings go
    assert db.scalars(select(RecordedActivity)).all() == []


# --- publishing and deleting ------------------------------------------------------------------


def test_publishing_strava_data_needs_consent_once(connected, fake, db):
    fake.add(*triathlon(), *triathlon(200, RACE_DAY + timedelta(days=7)))
    races = [r["race_id"] for r in connected.post("/api/strava/pull", json={}).json()["races"]]
    first = connected.patch(f"/api/races/{races[0]}/visibility", json={"is_public": True})
    assert first.status_code == 409
    agreed = connected.patch(
        f"/api/races/{races[0]}/visibility", json={"is_public": True, "strava_consent": True}
    )
    assert agreed.status_code == 204
    assert db.get(SiteSettings, 1).strava_public_consent_at is not None
    later = connected.patch(f"/api/races/{races[1]}/visibility", json={"is_public": True})
    assert later.status_code == 204


def test_public_segments_link_to_strava(connected, fake, anon, monkeypatch):
    fake.add(*triathlon())
    race_id = connected.post("/api/strava/pull", json={}).json()["races"][0]["race_id"]
    connected.patch(
        f"/api/races/{race_id}/visibility", json={"is_public": True, "strava_consent": True}
    )
    monkeypatch.setattr(get_settings(), "site_mode", "public")
    segments = anon.get(f"/api/races/{race_id}").json()["segments"]
    urls = {s["segment"]: s["strava_url"] for s in segments}
    assert urls["run"] == "https://www.strava.com/activities/103"
    assert urls["t1"] is None


def test_disconnect_revokes_and_deletes_only_strava_data(connected, fake, db):
    upload(connected, "edge_single_bike.fit")
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    response = connected.post("/api/strava/disconnect")
    assert response.status_code == 200 and response.json()["revoked"] is True
    assert fake.revoked == ["/oauth/revoke"]
    db.expire_all()
    sources = {a.source for a in db.scalars(select(RecordedActivity))}
    assert sources == {UploadSource.FIT_UPLOAD}
    assert db.scalar(select(StravaAccount)) is None
    assert db.scalars(select(Upload).where(Upload.source == UploadSource.STRAVA)).all() == []
    assert len(db.scalars(select(Race)).all()) == 2


def test_a_pulled_activity_reimports_from_its_stored_original(connected, fake, db):
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    calls = len(fake.calls)
    for u in db.scalars(select(Upload)).all():
        assert imports.reimport(db, u) == "updated"
    assert len(fake.calls) == calls  # no request to Strava


# --- webhook ------------------------------------------------------------------------------


def test_webhook_handshake(fake, anon, monkeypatch):
    monkeypatch.setattr(get_settings(), "strava_webhook_verify_token", "s3cret")
    ok = anon.get(
        "/api/strava/webhook",
        params={"hub.mode": "subscribe", "hub.challenge": "abc", "hub.verify_token": "s3cret"},
    )
    assert ok.json() == {"hub.challenge": "abc"}
    bad = anon.get(
        "/api/strava/webhook",
        params={"hub.mode": "subscribe", "hub.challenge": "abc", "hub.verify_token": "nope"},
    )
    assert bad.status_code == 403


def test_a_forged_deauthorization_event_deletes_nothing(connected, fake, anon, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "strava_webhook_verify_token", "s3cret")
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    event = {
        "object_type": "athlete",
        "object_id": ATHLETE,
        "aspect_type": "update",
        "owner_id": ATHLETE,
        "updates": {"authorized": "false"},
    }
    assert anon.post("/api/strava/webhook", json=event).status_code == 200
    db.expire_all()
    assert db.scalar(select(StravaAccount)) is not None
    fake.valid_tokens.clear()  # now Strava really refuses the token, and its renewal
    fake.refresh_ok = False
    anon.post("/api/strava/webhook", json=event)
    db.expire_all()
    assert db.scalar(select(StravaAccount)) is None


def test_an_activity_deleted_on_strava_leaves_the_race(connected, fake, anon, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "strava_webhook_verify_token", "s3cret")
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    event = {
        "object_type": "activity",
        "object_id": 103,
        "aspect_type": "delete",
        "owner_id": ATHLETE,
    }
    anon.post("/api/strava/webhook", json=event)
    db.expire_all()
    assert (
        db.scalar(select(Upload).where(Upload.external_id == "103")) is not None
    )  # still on Strava
    fake.deleted.add(103)
    anon.post("/api/strava/webhook", json=event)
    db.expire_all()
    assert db.scalar(select(Upload).where(Upload.external_id == "103")) is None


def test_withdrawing_consent_unpublishes_strava_races(connected, fake, db):
    fake.add(*triathlon())
    race_id = connected.post("/api/strava/pull", json={}).json()["races"][0]["race_id"]
    connected.patch(
        f"/api/races/{race_id}/visibility", json={"is_public": True, "strava_consent": True}
    )
    status = connected.patch("/api/strava", json={"public_consent": False}).json()
    assert status["public_consent_at"] is None
    db.expire_all()
    assert db.get(Race, race_id).is_public is False


def test_strava_data_waits_for_consent_on_a_public_race(connected, fake, db):
    upload(connected, "edge_single_bike.fit")
    race = db.scalars(select(Race)).one()
    connected.patch(f"/api/races/{race.id}/visibility", json={"is_public": True})
    bike = race.primary_activities()["bike"]
    fake.add(activity(300, "Bike race", "Ride", bike.start_time, bike.elapsed_time, 4e4, 11))
    body = connected.post("/api/strava/pull", json={}).json()
    assert body["races"] == [] and "agree" in body["skipped"][0]["message"], body
    connected.patch("/api/strava", json={"public_consent": True})
    body = connected.post("/api/strava/pull", json={}).json()
    assert body["races"][0]["race_id"] == race.id


# --- failures that must not read as revocation ------------------------------------------------


def _expire(db, at: datetime | None = None) -> None:
    row = db.scalar(select(StravaAccount))
    row.expires_at = at or datetime.now(UTC)
    db.commit()


def _strava_uploads(db) -> list[Upload]:
    db.expire_all()
    return db.scalars(select(Upload).where(Upload.source == UploadSource.STRAVA)).all()


@pytest.mark.parametrize(("failure", "status"), [(502, 502), (429, 429), ("network", 502)])
def test_a_refresh_strava_does_not_answer_deletes_nothing(connected, fake, db, failure, status):
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    _expire(db)
    fake.errors["/oauth/token"] = failure
    assert connected.post("/api/strava/pull", json={}).status_code == status
    assert len(_strava_uploads(db)) == 3
    assert db.scalar(select(StravaAccount)) is not None


def test_two_refreshes_at_once_keep_the_connection(connected, fake, db):
    """A pull and a (possibly forged) webhook check refresh together; Strava rotates the
    refresh token, so the second refresh with the old one would be refused."""
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    _expire(db, datetime.now(UTC) + timedelta(minutes=1))
    fake.rotate, fake.token_delay = True, 0.5
    errors: list[Exception] = []

    def refresh() -> None:
        with _session_factory()() as session:
            try:
                strava.access_token(session, strava.account(session))
            except Exception as exc:  # noqa: BLE001 - collected for the assertion
                errors.append(exc)

    threads = [threading.Thread(target=refresh) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert fake.refreshes == 1
    assert len(_strava_uploads(db)) == 3
    assert db.scalar(select(StravaAccount)).refresh_token == fake.current_refresh


def test_a_401_on_data_renews_and_deletes_once_strava_refuses(connected, fake, db):
    """Revoked on strava.com while the access token still had hours left."""
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    fake.valid_tokens.clear()
    fake.refresh_ok = False
    assert connected.post("/api/strava/pull", json={}).status_code == 409
    assert _strava_uploads(db) == []
    assert db.scalar(select(StravaAccount)) is None


def test_the_owner_api_is_off_without_credentials(connected, fake, db, monkeypatch):
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    _expire(db)
    monkeypatch.setattr(get_settings(), "strava_client_secret", "")
    assert connected.get("/api/strava").json()["enabled"] is False
    assert connected.post("/api/strava/pull", json={}).status_code == 404
    assert connected.patch("/api/strava", json={"tagline": False}).status_code == 404
    assert connected.post("/api/strava/disconnect").status_code == 404
    assert len(_strava_uploads(db)) == 3


# --- the rate limit -------------------------------------------------------------------------


def test_the_next_pull_continues_once_the_window_resets(connected, fake):
    fake.add(*triathlon())
    fake.rate_usage = (95, 400)
    assert connected.post("/api/strava/pull", json={}).json()["complete"] is False
    fake.rate_usage = (1, 400)
    strava_api.last_rate_limit.seen_at -= timedelta(minutes=15)  # the quarter hour passed
    body = connected.post("/api/strava/pull", json={}).json()
    assert body["complete"] and len(body["races"]) == 1
    daily = strava_api.RateLimit(
        limit=(200, 2000), usage=(1, 1995), seen_at=datetime(2026, 6, 21, 23, 58, tzinfo=UTC)
    )
    assert daily.nearly_spent(10, now=datetime(2026, 6, 21, 23, 59, tzinfo=UTC))
    assert not daily.nearly_spent(10, now=datetime(2026, 6, 22, 0, 1, tzinfo=UTC))


def test_an_unfinished_search_of_everything_says_to_keep_it_ticked(connected, fake):
    fake.add(*triathlon())
    fake.rate_usage = (95, 400)
    body = connected.post("/api/strava/pull", json={"everything": True}).json()
    assert body["complete"] is False and "Search all of Strava" in body["message"]


def test_the_tagline_reuses_the_description_the_pull_just_read(connected, fake):
    fake.add(*triathlon())
    assert connected.post("/api/strava/pull", json={}).json()["taglines"] == 3
    assert fake.calls.count(("GET", "/api/v3/activities/103")) == 1


def test_taglines_count_the_season_once_per_pull(connected, fake, db):
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    engine = db.get_bind()
    seasons: list[str] = []

    def count(conn, cursor, statement, *args) -> None:
        if "FROM race" in statement and "ORDER BY race.date" in statement:
            seasons.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        connected.post("/api/strava/pull", json={})
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(seasons) == 1


# --- pulling, continued -----------------------------------------------------------------------


def test_a_second_pull_at_once_is_refused(connected, fake, db):
    fake.add(*triathlon())
    with db.get_bind().connect() as other:  # a pull in progress elsewhere
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": strava.PULL_LOCK})
        try:
            response = connected.post("/api/strava/pull", json={})
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": strava.PULL_LOCK})
    assert response.status_code == 409 and "already running" in response.json()["detail"]
    assert len(connected.post("/api/strava/pull", json={}).json()["races"]) == 1


def test_an_error_mid_pull_keeps_the_races_already_filed(connected, fake):
    fake.add(*triathlon(), *triathlon(200, RACE_DAY + timedelta(days=7)))
    fake.errors["/api/v3/activities/201/streams"] = 500
    response = connected.post("/api/strava/pull", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["complete"] is False and "500" in body["message"]
    assert len(body["races"]) == 1


def test_an_activity_its_race_refused_is_tried_again(connected, fake, db):
    ride = activity(1, "Lake Ride", "Ride", RACE_DAY, 3600, 30000.0)  # not tagged yet
    run = activity(
        2, "Lake Run", "Run", RACE_DAY + timedelta(seconds=3720), 2400, 10000.0, workout_type=1
    )
    fake.add(ride, run)
    race_id = connected.post("/api/strava/pull", json={}).json()["races"][0]["race_id"]
    ride["workout_type"] = 11
    body = connected.post("/api/strava/pull", json={"everything": True}).json()
    assert "Change the race type" in body["skipped"][0]["message"]
    patched = connected.patch(f"/api/races/{race_id}", json={"race_type": "triathlon"})
    assert patched.status_code == 200
    body = connected.post("/api/strava/pull", json={"everything": True}).json()
    assert [r["race_id"] for r in body["races"]] == [race_id]
    db.expire_all()
    assert {"bike", "run"} <= set(db.get(Race, race_id).primary_activities())


def test_reconnecting_refills_the_races_a_disconnect_emptied(connected, fake, db):
    fake.add(*triathlon())
    race_id = connected.post("/api/strava/pull", json={}).json()["races"][0]["race_id"]
    connected.patch(f"/api/races/{race_id}", json={"name": "My named race"})
    connected.post("/api/strava/disconnect")
    assert _authorize(connected, next_path="/admin/site") == "/admin/site?strava=connected"
    body = connected.post("/api/strava/pull", json={}).json()
    assert [(r["race_id"], r["created"]) for r in body["races"]] == [(race_id, False)]
    db.expire_all()
    races = db.scalars(select(Race)).all()
    assert [r.name for r in races] == ["My named race"]
    assert len(races[0].activities) == 5


def _fit_bike():
    return parse_fit(fixture_bytes("edge_single_bike.fit")).sessions[0]


def test_a_fit_file_dropped_after_the_strava_pull_becomes_primary(connected, fake, db):
    bike = _fit_bike()
    fake.add(activity(300, "Bike race", "Ride", bike.start_time, bike.elapsed_time, 4e4, 11))
    race_id = connected.post("/api/strava/pull", json={}).json()["races"][0]["race_id"]
    outcome = upload(connected, "edge_single_bike.fit", race_id=race_id)[0]
    assert outcome["result"] == "attached"
    assert "stays primary" not in outcome["message"]
    db.expire_all()
    bikes = [a for a in db.get(Race, race_id).activities if a.segment == "bike"]
    assert [a.source for a in bikes if a.is_primary] == [UploadSource.FIT_UPLOAD]


def test_purging_without_transitions_keeps_a_primary_and_drops_stale_ones(
    connected, fake, db, monkeypatch
):
    """The fallback when the legs left behind can't make transitions."""
    bike = _fit_bike()
    swim_start = bike.start_time - timedelta(seconds=1500 + 180)
    bike_end = bike.start_time + timedelta(seconds=bike.elapsed_time)
    fake.add(
        activity(401, "Lake Swim", "Swim", swim_start, 1500, 1500.0),
        activity(402, "Lake Bike", "Ride", bike.start_time, bike.elapsed_time, 4e4, 11),
        activity(403, "Lake Run", "Run", bike_end + timedelta(seconds=300), 2400, 1e4, 1),
    )
    race_id = connected.post("/api/strava/pull", json={}).json()["races"][0]["race_id"]
    upload(connected, "edge_single_bike.fit", race_id=race_id)
    db.expire_all()
    race = db.get(Race, race_id)
    strava_bike = next(
        a for a in race.activities if a.segment == "bike" and a.source is UploadSource.STRAVA
    )
    imports.set_primary(db, strava_bike)  # as a race filed before FIT took precedence
    db.commit()

    def overlap(db, race):
        raise GroupingError("The bike and run legs overlap; they can't be one race.")

    monkeypatch.setattr(imports, "synthesize_transitions", overlap)
    connected.post("/api/strava/disconnect")
    db.expire_all()
    race = db.get(Race, race_id)
    assert [(a.segment, a.source, a.is_primary) for a in race.activities] == [
        ("bike", UploadSource.FIT_UPLOAD, True)
    ]
    assert race.official_result is None or race.official_result.swim_time is None


def test_disconnect_renews_the_token_for_the_legacy_fallback(connected, fake, db):
    _expire(db, datetime.now(UTC) - timedelta(hours=1))
    fake.valid_tokens = {"access-2"}  # the stored access token has expired
    fake.errors["/oauth/revoke"] = 500
    body = connected.post("/api/strava/disconnect").json()
    assert body["revoked"] is True and fake.revoked == ["/oauth/deauthorize"]


def test_disconnect_says_when_strava_did_not_confirm_it(connected, fake, db):
    fake.errors["/oauth/revoke"] = 500
    fake.errors["/oauth/deauthorize"] = 500
    body = connected.post("/api/strava/disconnect").json()
    assert body["revoked"] is False and "Strava settings" in body["message"]
    assert db.scalar(select(StravaAccount)) is None


def test_the_comparison_links_its_strava_legs(connected, fake):
    fake.add(*triathlon(), *triathlon(200, RACE_DAY + timedelta(days=7)))
    a, b = (r["race_id"] for r in connected.post("/api/strava/pull", json={}).json()["races"])
    urls = connected.get(f"/api/races/{a}/compare/{b}").json()["strava_urls"]
    assert urls["a"]["run"] == "https://www.strava.com/activities/103"
    assert urls["b"]["bike"] == "https://www.strava.com/activities/202"
    assert "t1" not in urls["a"]


def test_a_forged_delete_event_for_an_unknown_activity_costs_nothing(
    connected, fake, anon, monkeypatch
):
    monkeypatch.setattr(get_settings(), "strava_webhook_verify_token", "s3cret")
    calls = len(fake.calls)
    event_ = {"object_type": "activity", "object_id": 999, "aspect_type": "delete"}
    for _ in range(5):
        anon.post("/api/strava/webhook", json={**event_, "owner_id": ATHLETE})
    assert len(fake.calls) == calls


def test_a_wrong_client_secret_is_not_a_revocation(connected, fake, db, monkeypatch):
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    row = db.scalar(select(StravaAccount))
    row.expires_at = datetime.now(UTC)
    db.commit()
    real = fake.__call__

    def bad_client(method, url, **kw):
        if url.endswith("/oauth/token"):
            body = {
                "message": "Bad Request",
                "errors": [{"resource": "Application", "field": "client_id", "code": "invalid"}],
            }
            return Response(400, body, {})
        return real(method, url, **kw)

    monkeypatch.setattr(strava_api, "http", bad_client)
    response = connected.post("/api/strava/pull", json={})
    assert response.status_code != 200 or response.json()["complete"] is False
    db.expire_all()
    assert db.scalar(select(StravaAccount)) is not None
    assert db.scalars(select(Upload)).all()


def test_a_broken_webhook_event_is_ignored(connected, fake, anon, monkeypatch):
    monkeypatch.setattr(get_settings(), "strava_webhook_verify_token", "s3cret")
    event = {"object_type": "activity", "aspect_type": "delete", "owner_id": ATHLETE}
    assert anon.post("/api/strava/webhook", json=event).status_code == 200
    event["object_id"] = "not a number"
    assert anon.post("/api/strava/webhook", json=event).status_code == 200


def test_a_removed_strava_file_keeps_no_tagline(connected, fake, db):
    fake.add(*triathlon())
    connected.post("/api/strava/pull", json={})
    run = db.scalar(select(Upload).where(Upload.external_id == "103"))
    assert connected.delete(f"/api/uploads/{run.id}").status_code == 204
    puts = len(fake.puts)
    connected.put(
        f"/api/races/{run.race_id}/official-result", json={"total_time": 8600, "is_confirmed": True}
    )
    connected.post("/api/strava/pull", json={})
    assert 103 not in [activity_id for activity_id, _ in fake.puts[puts:]]


def test_the_owners_pick_of_a_strava_recording_survives_a_reimport(connected, fake, db):
    upload(connected, "edge_single_bike.fit")
    race = db.scalars(select(Race)).one()
    bike = race.primary_activities()["bike"]
    fake.add(activity(300, "Bike race", "Ride", bike.start_time, bike.elapsed_time, 4e4, 11))
    connected.post("/api/strava/pull", json={})
    db.expire_all()
    pulled = next(a for a in db.get(Race, race.id).activities if a.source is UploadSource.STRAVA)
    assert connected.patch(f"/api/activities/{pulled.id}", json={"is_primary": True}).is_success
    for u in db.scalars(select(Upload).where(Upload.source == UploadSource.FIT_UPLOAD)):
        imports.reimport(db, u)
    db.expire_all()
    assert db.get(RecordedActivity, pulled.id).is_primary
