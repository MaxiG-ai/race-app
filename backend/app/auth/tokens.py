"""JWT issue/verify (PyJWT, HS256). Access and refresh tokens are the same shape
— `sub` (user id), `iat`, `exp` — but carry a `typ` claim (`access` / `refresh`)
that `decode_token` checks explicitly, so a refresh token can never be presented
as an access token, or vice versa."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum

import jwt

from app.config import settings


class TokenType(Enum):
    """Deliberately a plain `Enum`, not `str, Enum` — never serialised through
    Pydantic/JSON, only ever compared via `.value` against the `typ` claim."""

    ACCESS = "access"
    REFRESH = "refresh"


class TokenError(Exception):
    """Raised for any invalid, expired, malformed token, or one presented with
    the wrong `typ`. Callers translate this to a 401; it never bubbles up raw."""


def _encode(user_id: int, typ: TokenType, ttl_s: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=ttl_s),
        "typ": typ.value,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    return _encode(user_id, TokenType.ACCESS, settings.access_token_ttl_s)


def create_refresh_token(user_id: int) -> str:
    return _encode(user_id, TokenType.REFRESH, settings.refresh_token_ttl_s)


def decode_token(token: str, expected_type: TokenType) -> int:
    """Verifies signature and expiry, then checks `typ` matches `expected_type`.
    Returns the user id from `sub`. Raises `TokenError` for anything else wrong."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError("invalid or expired token") from exc

    if payload.get("typ") != expected_type.value:
        raise TokenError(f"expected a {expected_type.value!r} token")

    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("token missing a valid subject") from exc
