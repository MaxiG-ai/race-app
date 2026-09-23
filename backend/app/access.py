"""Who may read and who may write (docs/features/access-and-visibility.md).

Enforcement lives in two places, both defined here:

1. Router factories. Every router is built with `reader_router()`, `owner_router()` or
   `anyone_router()`, so the access check is applied once per router, never per endpoint.
2. `visible_races` / `visible_race`. Every race read goes through them; a race the viewer
   can't see is a 404, never a 403.
"""

import hashlib
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import Select, false, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, utcnow
from app.models import Owner, OwnerSession, Race

SESSION_COOKIE = "race_session"
SESSION_TTL = timedelta(days=30)
# Sliding expiry is refreshed at most this often, so reads don't write on every request.
_REFRESH_EVERY = timedelta(hours=1)
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_hasher = PasswordHasher()

DB = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class Viewer:
    is_owner: bool


# --- passwords and sessions ------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(owner: Owner | None, password: str) -> bool:
    if owner is None:
        # Same cost as a real check, so timing doesn't reveal whether the username exists.
        _hasher.hash(password)
        return False
    try:
        return _hasher.verify(owner.password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _is_localhost(request: Request) -> bool:
    return request.url.hostname in {"localhost", "127.0.0.1", "::1"}


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=not _is_localhost(request),
        samesite="lax",
        path="/",
    )


def start_session(db: Session, request: Request, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(OwnerSession(token_hash=_hash_token(token), expires_at=now + SESSION_TTL))
    db.commit()
    set_session_cookie(request, response, token)


def end_session(db: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        row = db.get(OwnerSession, _hash_token(token))
        if row is not None:
            db.delete(row)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


def get_viewer(request: Request, response: Response, db: DB) -> Viewer:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return Viewer(is_owner=False)
    row = db.get(OwnerSession, _hash_token(token))
    now = utcnow()
    if row is None or row.expires_at <= now:
        return Viewer(is_owner=False)
    if now - row.last_seen_at > _REFRESH_EVERY:
        row.last_seen_at = now
        row.expires_at = now + SESSION_TTL
        db.commit()
        set_session_cookie(request, response, token)
    return Viewer(is_owner=True)


CurrentViewer = Annotated[Viewer, Depends(get_viewer)]


# --- login rate limit -------------------------------------------------------------------

_LOGIN_LIMIT = 5
_LOGIN_WINDOW_S = 60.0
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


def check_login_rate(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = _login_attempts[ip]
    while attempts and now - attempts[0] > _LOGIN_WINDOW_S:
        attempts.popleft()
    if len(attempts) >= _LOGIN_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts; wait a minute.")
    attempts.append(now)


# --- router-level guards ----------------------------------------------------------------


def is_reader(viewer: Viewer) -> bool:
    return viewer.is_owner or get_settings().site_mode == "public"


def require_reader(viewer: CurrentViewer) -> Viewer:
    if not is_reader(viewer):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Log in to view this site.")
    return viewer


def require_owner(request: Request, viewer: CurrentViewer) -> Viewer:
    if not viewer.is_owner:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Owner login required.")
    if request.method not in _SAFE_METHODS:
        # CSRF: a cross-site form can set neither a JSON content type nor a custom header.
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("application/json") and not request.headers.get(
            "x-requested-with"
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing X-Requested-With header.")
    return viewer


def anyone_router() -> APIRouter:
    return APIRouter(prefix="/api")


def reader_router() -> APIRouter:
    return APIRouter(prefix="/api", dependencies=[Depends(require_reader)])


def owner_router() -> APIRouter:
    return APIRouter(prefix="/api", dependencies=[Depends(require_owner)])


# --- the only way to read races ---------------------------------------------------------


def visible_races(viewer: Viewer) -> Select[tuple[Race]]:
    query = select(Race)
    if viewer.is_owner:
        return query
    if get_settings().site_mode == "public":
        return query.where(Race.is_public)
    return query.where(false())


def visible_race(db: Session, viewer: Viewer, race_id: int) -> Race:
    race = db.scalar(visible_races(viewer).where(Race.id == race_id))
    if race is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Race not found.")
    return race
