"""`/api/auth/...` — login, logout, refresh, and the frontend's boot-time
`/me` check. The deliberate exception to the router-level `current_user`
convention (see `app/routers/__init__.py`): login/logout/refresh must be
reachable without a session, so only `/me` carries the dependency, per-endpoint.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.auth.deps import (
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    current_user,
    set_access_cookie,
    set_auth_cookies,
)
from app.auth.password import hash_password, needs_rehash, verify_dummy, verify_password
from app.auth.tokens import TokenError, TokenType, decode_token
from app.db import get_db
from app.models import User
from app.schemas.user import LoginRequest, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]

_INVALID_CREDENTIALS = "Incorrect email or password."


class LoginRateLimiter:
    """In-process fixed-window limiter for `/auth/login` (§4), keyed by
    `(client_ip, email)` so a burst against one account, or from one source, is
    throttled without touching unrelated logins. A single small in-process
    structure is proportionate for a single-VPS personal app — no dependency, no
    shared store. Not safe across multiple worker processes; fine for this
    deployment's single gunicorn worker (see §8).

    A test resets state between cases via `reset_all()`.
    """

    max_attempts = 5
    window_s = 15 * 60

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}

    @staticmethod
    def _key(client_ip: str, email: str) -> str:
        return f"{client_ip}:{email.lower()}"

    def retry_after(self, client_ip: str, email: str, *, now: float | None = None) -> int | None:
        """`None` if another attempt is allowed now, else seconds until it is."""
        now = time.monotonic() if now is None else now
        key = self._key(client_ip, email)
        window_start = now - self.window_s
        recent = [t for t in self._failures.get(key, []) if t > window_start]
        self._failures[key] = recent
        if len(recent) < self.max_attempts:
            return None
        return max(1, int(self.window_s - (now - recent[0])) + 1)

    def record_failure(self, client_ip: str, email: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        key = self._key(client_ip, email)
        self._failures.setdefault(key, []).append(now)

    def reset(self, client_ip: str, email: str) -> None:
        self._failures.pop(self._key(client_ip, email), None)

    def reset_all(self) -> None:
        """Test hook — clears every tracked attempt."""
        self._failures.clear()


login_rate_limiter = LoginRateLimiter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=UserRead)
def login(payload: LoginRequest, request: Request, response: Response, db: DbSession) -> User:
    client_ip = _client_ip(request)
    retry_after = login_rate_limiter.retry_after(client_ip, payload.email)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.query(User).filter(User.email == payload.email).one_or_none()

    # Same error, comparable timing, whether the email doesn't exist or the
    # password is wrong (§4: login must not leak which emails exist).
    if user is None:
        verify_dummy(payload.password)
        login_rate_limiter.record_failure(client_ip, payload.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS)

    if not verify_password(payload.password, user.password_hash):
        login_rate_limiter.record_failure(client_ip, payload.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS)

    if not user.is_active:
        login_rate_limiter.record_failure(client_ip, payload.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS)

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()

    login_rate_limiter.reset(client_ip, payload.email)
    set_auth_cookies(response, user.id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    clear_auth_cookies(response)


@router.post("/refresh", response_model=UserRead)
def refresh(request: Request, response: Response, db: DbSession) -> User:
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token"
        )
    try:
        user_id = decode_token(token, TokenType.REFRESH)
    except TokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        ) from None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        )

    set_access_cookie(response, user.id)
    return user


@router.get("/me", response_model=UserRead)
def me(user: Annotated[User, Depends(current_user)]) -> User:
    return user
