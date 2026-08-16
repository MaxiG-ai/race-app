"""M3 Read API + manual-entry write endpoints: `app/routers/races.py`.

Tenancy (§4) gets checked directly and repeatedly here rather than once: a
second user must see an empty timeline, 404 on the first user's race (every
by-id endpoint, not just detail), and the query-count test proves the
timeline doesn't quietly N+1 as it grows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import RecordedActivity, Segment, UploadSource
from app.services.geo import PRECISION_TOLERANCES_M
from tests import factories

# --- helpers --------------------------------------------------------------- #


def _login(client: TestClient, email: str, password: str = factories.DEFAULT_PASSWORD) -> None:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200


@contextmanager
def _count_queries(db: Session) -> Iterator[dict[str, int]]:
    """Counts SQL statements executed on `db`'s connection for the duration of
    the `with` block — SQLAlchemy's `before_cursor_execute` event is the
    standard way to do this without instrumenting every query site by hand."""
    counter = {"n": 0}
    connection = db.connection()

    def _before_cursor_execute(*args: object, **kwargs: object) -> None:
        counter["n"] += 1

    event.listen(connection, "before_cursor_execute", _before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(connection, "before_cursor_execute", _before_cursor_execute)


def _full_race(db: Session, *, owner, **race_kwargs):
    race = factories.make_race(db, owner=owner, **race_kwargs)
    factories.make_primary_activities(db, race=race)
    factories.make_official_result(db, race=race)
    return race


# --- GET /races (list) ------------------------------------------------------ #


def test_list_races_happy_path(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    _full_race(db, owner=user, name="Stutensee", race_date=date(2026, 6, 21))

    response = client.get("/api/races")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 20
    assert body["offset"] == 0
    item = body["items"][0]
    assert item["name"] == "Stutensee"
    assert item["badges"] == ["Olympic"]
    assert item["total_official_time_s"] == 9763
    assert item["total_recorded_time_s"] == sum(
        t["elapsed"] for t in factories.SEGMENT_TOTALS.values()
    )
    segments = {s["segment"]: s for s in item["segments"]}
    assert segments["swim"]["official_time_s"] == 1740
    assert segments["swim"]["recorded_time_s"] == 1740
    assert segments["bike"]["recorded_distance_m"] == 40000.0
    assert segments["t1"]["is_derived"] is False


def test_list_races_never_includes_gps_track(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)
    factories.make_primary_activities(db, race=race, with_track=True)

    response = client.get("/api/races")

    assert "gps_track" not in response.text


def test_list_races_pagination(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    for i in range(5):
        factories.make_race(db, owner=user, name=f"Race {i}", race_date=date(2026, 1, i + 1))

    page1 = client.get("/api/races", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/races", params={"limit": 2, "offset": 2}).json()

    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    # Newest first: Race 4 (Jan 5) before Race 0 (Jan 1).
    assert page1["items"][0]["name"] == "Race 4"
    assert page2["items"][0]["name"] == "Race 2"
    assert {i["id"] for i in page1["items"]}.isdisjoint({i["id"] for i in page2["items"]})


def test_list_races_query_count_is_bounded_regardless_of_page_size(
    client: TestClient, db: Session
) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    for i in range(12):
        _full_race(db, owner=user, name=f"Race {i}", race_date=date(2026, 1, i + 1))

    with _count_queries(db) as counter:
        small = client.get("/api/races", params={"limit": 3, "offset": 0})
    assert small.status_code == 200
    small_count = counter["n"]

    with _count_queries(db) as counter:
        large = client.get("/api/races", params={"limit": 100, "offset": 0})
    assert large.status_code == 200
    large_count = counter["n"]

    assert small_count == large_count, "query count must not grow with page size"
    assert small_count <= 6


def test_list_races_empty_for_second_user(client: TestClient, db: Session) -> None:
    owner = factories.make_user(db, email="owner@example.com")
    _full_race(db, owner=owner)
    intruder = factories.make_user(db, email="intruder@example.com")
    _login(client, intruder.email)

    response = client.get("/api/races")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


# --- GET /races/{id} (detail) ----------------------------------------------- #


def test_get_race_detail_happy_path(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = _full_race(db, owner=user)

    response = client.get(f"/api/races/{race.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["official_result"]["total_time"] == 9763
    assert len(body["recorded_activities"]) == 5
    swim = next(a for a in body["recorded_activities"] if a["segment"] == "swim")
    assert len(swim["laps"]) == 3
    assert [lap["lap_index"] for lap in swim["laps"]] == [1, 2, 3]
    assert "raw_data" not in swim


def test_get_race_detail_includes_assigned_gear(client: TestClient, db: Session) -> None:
    from app.models import ActivityGear, Gear, GearAssignedBy, GearKind

    user = factories.make_user(db)
    _login(client, user.email)
    race = _full_race(db, owner=user)
    bike = Gear(user_id=user.id, kind=GearKind.BIKE, name="Canyon Speedmax")
    db.add(bike)
    db.commit()
    db.refresh(bike)
    bike_activity = next(a for a in race.recorded_activities if a.segment.value == "bike")
    db.add(
        ActivityGear(
            recorded_activity_id=bike_activity.id,
            gear_id=bike.id,
            assigned_by=GearAssignedBy.MANUAL,
        )
    )
    db.commit()

    response = client.get(f"/api/races/{race.id}")

    assert response.status_code == 200
    bike_body = next(a for a in response.json()["recorded_activities"] if a["segment"] == "bike")
    assert len(bike_body["gear"]) == 1
    assert bike_body["gear"][0]["gear"]["name"] == "Canyon Speedmax"


def test_get_race_detail_never_includes_gps_track(client: TestClient, db: Session) -> None:
    """Pinned directly against the response body string so a future schema
    change can't quietly reintroduce the field (§4 Payload note)."""
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)
    factories.make_primary_activities(db, race=race, with_track=True)

    response = client.get(f"/api/races/{race.id}")

    assert response.status_code == 200
    assert "gps_track" not in response.text


def test_get_race_404_for_missing_id(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)

    assert client.get("/api/races/999999").status_code == 404


def test_get_race_404_for_another_users_race(client: TestClient, db: Session) -> None:
    owner = factories.make_user(db, email="owner@example.com")
    race = factories.make_race(db, owner=owner)
    intruder = factories.make_user(db, email="intruder@example.com")
    _login(client, intruder.email)

    response = client.get(f"/api/races/{race.id}")

    assert response.status_code == 404


# --- POST / PATCH / DELETE /races ------------------------------------------- #


def test_create_race(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)

    response = client.post(
        "/api/races",
        json={
            "name": "Roth",
            "date": "2026-07-12",
            "race_type": "full_iron",
            "location_name": "Roth, Germany",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Roth"
    assert body["race_type"] == "full_iron"


def test_patch_race_updates_metadata_and_leaves_weather_untouched(
    client: TestClient, db: Session
) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user, location_lat=49.0, location_lng=8.0)
    race.weather_temp_c = 21.5
    race.weather_conditions = "clear"
    db.add(race)
    db.commit()

    response = client.patch(
        f"/api/races/{race.id}",
        json={"location_lat": 50.0, "location_lng": 9.0, "name": "Corrected Name"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Corrected Name"
    assert body["location_lat"] == 50.0
    # Editing location does not auto-refetch weather — it's left as-is (now
    # stale) until /weather/refresh is called explicitly.
    assert body["weather_temp_c"] == 21.5
    assert body["weather_conditions"] == "clear"


def test_patch_race_404_for_another_users_race(client: TestClient, db: Session) -> None:
    owner = factories.make_user(db, email="owner@example.com")
    race = factories.make_race(db, owner=owner)
    intruder = factories.make_user(db, email="intruder@example.com")
    _login(client, intruder.email)

    response = client.patch(f"/api/races/{race.id}", json={"name": "Hijacked"})

    assert response.status_code == 404


def test_delete_race_cascades(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = _full_race(db, owner=user)
    race_id = race.id

    response = client.delete(f"/api/races/{race_id}")
    assert response.status_code == 204

    assert client.get(f"/api/races/{race_id}").status_code == 404
    remaining = db.query(RecordedActivity).filter(RecordedActivity.race_id == race_id).count()
    assert remaining == 0


# --- PUT /races/{id}/official-result ----------------------------------------- #


def test_upsert_official_result_creates_then_updates(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)

    create = client.put(
        f"/api/races/{race.id}/official-result",
        json={"total_time": 9800, "swim_time": 1700, "overall_place": 10},
    )
    assert create.status_code == 200
    assert create.json()["total_time"] == 9800
    assert create.json()["overall_place"] == 10

    update = client.put(
        f"/api/races/{race.id}/official-result",
        json={"total_time": 9700, "swim_time": 1650},
    )
    assert update.status_code == 200
    body = update.json()
    assert body["total_time"] == 9700
    assert body["swim_time"] == 1650
    # Fields omitted from the second call reset to their (unspecified) default
    # since this is a full replace of the single official-result row, not a
    # per-field patch.
    assert body["overall_place"] is None


# --- PUT /races/{id}/fueling -------------------------------------------------- #


def test_upsert_fueling_creates_updates_and_deletes_omitted_segment(
    client: TestClient, db: Session
) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)

    first = client.put(
        f"/api/races/{race.id}/fueling",
        json={
            "fuelings": [
                {
                    "segment": "bike",
                    "carbs_g": 90,
                    "products": [{"name": "Maurten 160", "quantity": 2}],
                },
                {"segment": "run", "carbs_g": 30, "notes": "ran out of gels"},
            ]
        },
    )
    assert first.status_code == 200
    segments = {row["segment"] for row in first.json()}
    assert segments == {"bike", "run"}

    # Second call omits "run" and updates "bike" — the omitted segment's row
    # must be deleted (full-replace semantics), not left behind.
    second = client.put(
        f"/api/races/{race.id}/fueling",
        json={"fuelings": [{"segment": "bike", "carbs_g": 100}]},
    )
    assert second.status_code == 200
    rows = second.json()
    assert [row["segment"] for row in rows] == ["bike"]
    assert rows[0]["carbs_g"] == 100
    assert rows[0]["products"] is None

    detail = client.get(f"/api/races/{race.id}").json()
    assert [f["segment"] for f in detail["fuelings"]] == ["bike"]


# --- GET /races/{id}/track --------------------------------------------------- #


def test_track_returns_one_polyline_per_segment_with_bounds(
    client: TestClient, db: Session
) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)
    factories.make_primary_activities(db, race=race, with_track=True)

    response = client.get(f"/api/races/{race.id}/track")

    assert response.status_code == 200
    body = response.json()
    segment_order = [s["segment"] for s in body["segments"]]
    assert segment_order == ["swim", "t1", "bike", "t2", "run"]
    for seg in body["segments"]:
        assert seg["bounds"] is not None
        assert seg["points"]


def test_track_skips_derived_transitions(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)
    activities = factories.make_primary_activities(db, race=race, with_track=True)
    t1 = activities[Segment.T1]
    t1.is_derived = True
    t1.gps_track = None
    db.add(t1)
    db.commit()

    response = client.get(f"/api/races/{race.id}/track")

    segment_order = [s["segment"] for s in response.json()["segments"]]
    assert "t1" not in segment_order
    assert segment_order == ["swim", "bike", "t2", "run"]


def test_track_only_uses_primary_recording(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)
    factories.make_primary_activities(db, race=race, with_track=True)

    # A second, non-primary recording of the bike leg from another device.
    shadow = RecordedActivity(
        race_id=race.id,
        segment=Segment.BIKE,
        is_primary=False,
        is_derived=False,
        source=UploadSource.FIT_UPLOAD,
        start_time=datetime(2026, 6, 21, 8, 0, tzinfo=UTC),
        elapsed_time=4400,
        distance_m=39000.0,
        gps_track=[
            {"lat": 1.0, "lng": 1.0, "elevation": None, "timestamp": "2026-06-21T08:00:00Z"},
            {"lat": 1.1, "lng": 1.1, "elevation": None, "timestamp": "2026-06-21T08:01:00Z"},
        ],
    )
    db.add(shadow)
    db.commit()

    response = client.get(f"/api/races/{race.id}/track")

    bike_segments = [s for s in response.json()["segments"] if s["segment"] == "bike"]
    assert len(bike_segments) == 1
    assert bike_segments[0]["recorded_activity_id"] != shadow.id


def test_track_precision_levels_produce_progressively_fewer_points(
    client: TestClient, db: Session
) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)
    factories.make_primary_activities(db, race=race, with_track=True)

    counts = {}
    for precision in ["low", "medium", "high", "full"]:
        response = client.get(f"/api/races/{race.id}/track", params={"precision": precision})
        assert response.status_code == 200
        bike = next(s for s in response.json()["segments"] if s["segment"] == "bike")
        counts[precision] = len(bike["points"])

    assert (
        PRECISION_TOLERANCES_M["low"]
        > PRECISION_TOLERANCES_M["medium"]
        > (PRECISION_TOLERANCES_M["high"])
    )
    assert counts["low"] <= counts["medium"] <= counts["high"] <= counts["full"]


def test_track_404_for_another_users_race(client: TestClient, db: Session) -> None:
    owner = factories.make_user(db, email="owner@example.com")
    race = factories.make_race(db, owner=owner)
    intruder = factories.make_user(db, email="intruder@example.com")
    _login(client, intruder.email)

    assert client.get(f"/api/races/{race.id}/track").status_code == 404


# --- GET /races/{id}/laps ----------------------------------------------------- #


def test_laps_sport_filter(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)
    factories.make_primary_activities(db, race=race)

    all_laps = client.get(f"/api/races/{race.id}/laps")
    assert all_laps.status_code == 200
    assert len(all_laps.json()) == 15  # 5 segments x 3 laps

    run_laps = client.get(f"/api/races/{race.id}/laps", params={"sport": "run"})
    assert run_laps.status_code == 200
    body = run_laps.json()
    assert len(body) == 3
    assert all(lap["sport"] == "run" for lap in body)
    assert [lap["lap_index"] for lap in body] == [1, 2, 3]


def test_laps_only_from_primary_activity(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user)
    factories.make_primary_activities(db, race=race)

    shadow = RecordedActivity(
        race_id=race.id,
        segment=Segment.BIKE,
        is_primary=False,
        is_derived=False,
        source=UploadSource.FIT_UPLOAD,
        start_time=datetime(2026, 6, 21, 8, 0, tzinfo=UTC),
        elapsed_time=4400,
    )
    db.add(shadow)
    db.commit()
    db.refresh(shadow)
    from app.models import LapSplit

    db.add(
        LapSplit(
            recorded_activity_id=shadow.id,
            sport=Segment.BIKE,
            lap_index=1,
            start_time=shadow.start_time,
            total_time=4400,
        )
    )
    db.commit()

    response = client.get(f"/api/races/{race.id}/laps", params={"sport": "bike"})

    assert response.status_code == 200
    assert len(response.json()) == 3  # the shadow recording's lap is excluded


def test_laps_404_for_another_users_race(client: TestClient, db: Session) -> None:
    owner = factories.make_user(db, email="owner@example.com")
    race = factories.make_race(db, owner=owner)
    intruder = factories.make_user(db, email="intruder@example.com")
    _login(client, intruder.email)

    assert client.get(f"/api/races/{race.id}/laps").status_code == 404


# --- POST /races/{id}/weather/refresh ---------------------------------------- #


def test_weather_refresh_updates_race(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.weather import WeatherResult

    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user, location_lat=49.05, location_lng=8.40)
    factories.make_primary_activities(db, race=race)

    def _fake_fetch_weather(lat, lng, start_at_utc, **kwargs):
        assert lat == 49.05
        assert lng == 8.40
        return WeatherResult(temp_c=18.0, humidity_pct=70.0, conditions="clear")

    monkeypatch.setattr("app.routers.races.fetch_weather", _fake_fetch_weather)

    response = client.post(f"/api/races/{race.id}/weather/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["weather_temp_c"] == 18.0
    assert body["weather_humidity_pct"] == 70.0
    assert body["weather_conditions"] == "clear"


def test_weather_refresh_400_without_location(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user, location_lat=None, location_lng=None)

    response = client.post(f"/api/races/{race.id}/weather/refresh")

    assert response.status_code == 400


def test_weather_refresh_400_without_recorded_activity(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user, location_lat=49.05, location_lng=8.40)

    response = client.post(f"/api/races/{race.id}/weather/refresh")

    assert response.status_code == 400


def test_weather_refresh_404_for_another_users_race(client: TestClient, db: Session) -> None:
    owner = factories.make_user(db, email="owner@example.com")
    race = factories.make_race(db, owner=owner)
    intruder = factories.make_user(db, email="intruder@example.com")
    _login(client, intruder.email)

    assert client.post(f"/api/races/{race.id}/weather/refresh").status_code == 404
