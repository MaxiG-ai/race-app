"""docs/features/strava.md#acceptance, against a fake Strava. Every activity here is made
up: no real recordings in the repository."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import hash_password
from app.config import get_settings
from app.main import app
from app.models import Owner, Race, RecordedActivity, SiteSettings, StravaAccount, Upload
from app.models.enums import Sport, UploadSource
from app.services import imports, strava, strava_api, strava_map
from app.services.strava_api import Response
from tests.conftest import upload

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
        if path == "/oauth/token":
            if data["grant_type"] == "authorization_code":
                return Response(200, self._token(data["code"]), {})
            if not self.refresh_ok:
                return Response(400, {"message": "Bad Request"}, {})
            return Response(
                200,
                {
                    "access_token": "access-2",
                    "refresh_token": "refresh-2",
                    "expires_at": int((datetime.now(UTC) + timedelta(hours=6)).timestamp()),
                },
                {},
            )
        if path in ("/oauth/revoke", "/oauth/deauthorize"):
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
    assert connected.post("/api/strava/disconnect").status_code == 204
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
    fake.valid_tokens.clear()  # now Strava really refuses the token
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
