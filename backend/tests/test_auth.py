"""Auth (M1.5): login/logout/refresh/me, the login rate limiter, and the
`get_owned_race` tenancy helper (§4). Runs against the real Postgres-backed `db`/
`client` fixtures in `conftest.py` — never the dev database's committed state."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.deps import get_owned_race
from app.auth.password import hash_password
from app.auth.tokens import create_access_token, create_refresh_token
from app.models import Race, RaceType, User
from app.routers.auth import login_rate_limiter

EMAIL = "athlete@example.com"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    login_rate_limiter.reset_all()
    yield
    login_rate_limiter.reset_all()


def _make_user(
    db: Session, *, email: str = EMAIL, password: str = PASSWORD, is_active: bool = True
) -> User:
    user = User(email=email, password_hash=hash_password(password), is_active=is_active)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_race(db: Session, *, owner: User) -> Race:
    race = Race(
        user_id=owner.id, name="Test Race", date=date(2026, 6, 21), race_type=RaceType.OLYMPIC
    )
    db.add(race)
    db.commit()
    db.refresh(race)
    return race


# --- login ---------------------------------------------------------------------


def test_login_success_sets_both_cookies(client: TestClient, db: Session) -> None:
    _make_user(db)

    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL
    assert "password_hash" not in response.json()
    assert response.cookies.get("access_token") is not None
    assert response.cookies.get("refresh_token") is not None


def test_login_wrong_password_and_unknown_email_are_indistinguishable(
    client: TestClient, db: Session
) -> None:
    _make_user(db)

    wrong_password = client.post("/api/auth/login", json={"email": EMAIL, "password": "nope"})
    unknown_email = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.cookies.get("access_token") is None


def test_inactive_user_cannot_log_in(client: TestClient, db: Session) -> None:
    _make_user(db, is_active=False)

    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 401
    assert response.cookies.get("access_token") is None


# --- /me -------------------------------------------------------------------


def test_me_requires_a_cookie(client: TestClient) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_me_succeeds_after_login(client: TestClient, db: Session) -> None:
    _make_user(db)
    client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


# --- refresh -----------------------------------------------------------------


def test_refresh_issues_a_new_access_cookie(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    refresh_token = create_refresh_token(user.id)

    response = client.post("/api/auth/refresh", cookies={"refresh_token": refresh_token})

    assert response.status_code == 200
    assert response.cookies.get("access_token") is not None


def test_access_token_rejected_at_refresh(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    access_token = create_access_token(user.id)

    response = client.post("/api/auth/refresh", cookies={"refresh_token": access_token})

    assert response.status_code == 401


def test_refresh_token_rejected_at_me(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    refresh_token = create_refresh_token(user.id)

    response = client.get("/api/auth/me", cookies={"access_token": refresh_token})

    assert response.status_code == 401


# --- logout ------------------------------------------------------------------


def test_logout_clears_cookies_and_me_then_401s(client: TestClient, db: Session) -> None:
    _make_user(db)
    client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert client.get("/api/auth/me").status_code == 200

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 204

    assert client.get("/api/auth/me").status_code == 401


# --- rate limiting -------------------------------------------------------------


def test_rate_limit_kicks_in_after_repeated_failures(client: TestClient, db: Session) -> None:
    _make_user(db)

    last_response = None
    for _ in range(login_rate_limiter.max_attempts):
        last_response = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": "wrong-password"}
        )
        assert last_response.status_code == 401

    throttled = client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong-password"})

    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers
    assert int(throttled.headers["Retry-After"]) > 0


# --- tenancy -------------------------------------------------------------------


def test_get_owned_race_404s_for_another_users_race(db: Session) -> None:
    owner = _make_user(db, email="owner@example.com")
    intruder = _make_user(db, email="intruder@example.com")
    race = _make_race(db, owner=owner)

    # The owner can fetch it.
    assert get_owned_race(db, owner, race.id).id == race.id

    # A different user gets 404, not 403 — the API never confirms the id exists.
    with pytest.raises(HTTPException) as exc_info:
        get_owned_race(db, intruder, race.id)
    assert exc_info.value.status_code == 404
