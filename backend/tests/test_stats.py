"""`GET /stats/summary` (§4). Every assertion here doubles as a tenancy check:
`compute_stats_summary` must never leak another user's races into the count or
the totals."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests import factories


def _login(client: TestClient, email: str, password: str = factories.DEFAULT_PASSWORD) -> None:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200


def _expected_totals() -> tuple[float, float]:
    distance = sum(t["distance"] for t in factories.SEGMENT_TOTALS.values())
    elevation = sum(
        t["elevation"] for t in factories.SEGMENT_TOTALS.values() if t["elevation"] is not None
    )
    return distance, elevation


def test_summary_happy_path(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    race1 = factories.make_race(db, owner=user, name="Race 1", race_date=date(2026, 5, 1))
    factories.make_primary_activities(db, race=race1)
    race2 = factories.make_race(db, owner=user, name="Race 2", race_date=date(2026, 6, 1))
    factories.make_primary_activities(db, race=race2)

    response = client.get("/api/stats/summary")

    assert response.status_code == 200
    body = response.json()
    distance, elevation = _expected_totals()
    assert body["race_count"] == 2
    assert body["total_distance_m"] == distance * 2
    assert body["total_elevation_gain_m"] == elevation * 2


def test_summary_counts_races_with_no_recorded_activity(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    factories.make_race(db, owner=user)  # no activities at all

    response = client.get("/api/stats/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["race_count"] == 1
    assert body["total_distance_m"] == 0.0
    assert body["total_elevation_gain_m"] == 0.0


def test_summary_date_range_filter(client: TestClient, db: Session) -> None:
    user = factories.make_user(db)
    _login(client, user.email)
    early = factories.make_race(db, owner=user, name="Early", race_date=date(2026, 1, 1))
    factories.make_primary_activities(db, race=early)
    late = factories.make_race(db, owner=user, name="Late", race_date=date(2026, 12, 1))
    factories.make_primary_activities(db, race=late)

    response = client.get(
        "/api/stats/summary", params={"date_from": "2026-06-01", "date_to": "2026-12-31"}
    )

    assert response.status_code == 200
    assert response.json()["race_count"] == 1


def test_summary_is_zeroed_for_second_user(client: TestClient, db: Session) -> None:
    owner = factories.make_user(db, email="owner@example.com")
    race = factories.make_race(db, owner=owner)
    factories.make_primary_activities(db, race=race)

    intruder = factories.make_user(db, email="intruder@example.com")
    _login(client, intruder.email)

    response = client.get("/api/stats/summary")

    assert response.status_code == 200
    assert response.json() == {
        "race_count": 0,
        "total_distance_m": 0.0,
        "total_elevation_gain_m": 0.0,
    }


def test_summary_requires_auth(client: TestClient) -> None:
    assert client.get("/api/stats/summary").status_code == 401
