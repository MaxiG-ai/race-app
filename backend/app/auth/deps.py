"""`current_user` — the dependency every router other than `auth` carries at
*router* level (see `app/routers/__init__.py`), so a new endpoint is authenticated
the moment it's added rather than by remembering a per-endpoint dependency — plus
`get_owned_race`, the one place race ownership is checked (§4 Tenancy).

Cookie shape: both the access and refresh token live in `HttpOnly`, `Secure`
(from `settings.cookie_secure`), `SameSite=Lax` cookies rather than
`localStorage`, so a script-injection can't exfiltrate a session (§4). The access
cookie is scoped to `path=/` (needed on every request); the refresh cookie is
scoped to `REFRESH_COOKIE_PATH` only, so it is never sent on ordinary API calls —
only when the frontend deliberately hits `/api/auth/refresh`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth.tokens import (
    TokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.config import settings
from app.db import get_db
from app.models import Race, User

ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth/refresh"


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def _set_cookie(response: Response, *, name: str, value: str, max_age: int, path: str) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=path,
        domain=settings.cookie_domain,
    )


def set_access_cookie(response: Response, user_id: int) -> None:
    _set_cookie(
        response,
        name=ACCESS_COOKIE_NAME,
        value=create_access_token(user_id),
        max_age=settings.access_token_ttl_s,
        path="/",
    )


def set_auth_cookies(response: Response, user_id: int) -> None:
    """Issues both cookies — called on login only. `refresh` (below) reissues just
    the access cookie."""
    set_access_cookie(response, user_id)
    _set_cookie(
        response,
        name=REFRESH_COOKIE_NAME,
        value=create_refresh_token(user_id),
        max_age=settings.refresh_token_ttl_s,
        path=REFRESH_COOKIE_PATH,
    )


def clear_auth_cookies(response: Response) -> None:
    """Deletion must repeat the same `path`/`domain` used at set-time — a cookie is
    keyed by those attributes too, so mismatched values silently no-op."""
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/", domain=settings.cookie_domain)
    response.delete_cookie(
        REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH, domain=settings.cookie_domain
    )


def current_user(
    db: Annotated[Session, Depends(get_db)],
    access_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE_NAME)] = None,
) -> User:
    """401 for a missing cookie, an expired/invalid/mis-signed token, a refresh
    token presented here (rejected by its `typ` claim), or an inactive user."""
    if access_token is None:
        raise _unauthorized()
    try:
        user_id = decode_token(access_token, TokenType.ACCESS)
    except TokenError:
        raise _unauthorized() from None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    return user


def get_owned_race(db: Session, user: User, race_id: int) -> Race:
    """The one place a `Race` is looked up by id (§4 Tenancy) — every router that
    touches a race must call this rather than querying `Race` directly. Filters by
    `user_id` and raises **404**, never 403, for a race owned by someone else, so
    the API never confirms to a caller that a given id exists."""
    race = db.query(Race).filter(Race.id == race_id, Race.user_id == user.id).one_or_none()
    if race is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Race not found")
    return race
