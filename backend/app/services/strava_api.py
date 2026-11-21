"""Strava API v3 over HTTP (docs/features/strava.md#api-usage).

Standard library only, like the Open-Meteo client: `urllib.request` and `json`. Every
request goes through `http()`, the one network call, which the tests replace.

This module knows URLs, tokens and rate limits; it knows nothing about races. What a
Strava activity becomes is `strava_map`; when to call what is `strava`.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
# /oauth/revoke replaces /oauth/deauthorize, which Strava retires on 2027-06-01.
REVOKE_URL = "https://www.strava.com/oauth/revoke"
DEAUTHORIZE_URL = "https://www.strava.com/oauth/deauthorize"
ACTIVITY_URL = "https://www.strava.com/activities/{id}"

# One consent screen for everything the integration does: read races (including the
# "Only Me" ones; races start private here anyway) and write the tagline.
SCOPES = ("read", "activity:read_all", "activity:write")
READ_SCOPE = "activity:read_all"
WRITE_SCOPE = "activity:write"

# Streams the import asks for: everything a FIT session's records would give us.
STREAM_KEYS = ("time", "latlng", "distance", "altitude", "heartrate", "watts", "cadence", "temp")
PER_PAGE = 200  # the maximum /athlete/activities allows
TIMEOUT_S = 15


class StravaError(Exception):
    """User-facing: something Strava said, or couldn't be reached."""


class StravaAuthError(StravaError):
    """401: the token is invalid or the athlete revoked access. Reconnect."""


class StravaNotFound(StravaError):
    """404: the activity doesn't exist (any more), or isn't visible with our scopes."""


class StravaRateLimited(StravaError):
    """429: stop for now. Strava's windows reset every 15 minutes and at midnight UTC."""


@dataclass
class Response:
    status: int
    body: Any
    headers: dict[str, str]


@dataclass
class RateLimit:
    """Usage reported by the last response: (15-minute, daily) for each pair."""

    limit: tuple[int, int] | None = None
    usage: tuple[int, int] | None = None
    read_limit: tuple[int, int] | None = None
    read_usage: tuple[int, int] | None = None
    seen_at: datetime | None = None  # when a response last reported these

    def current(self, now: datetime | None = None) -> tuple[bool, bool]:
        """Whether the (15-minute, daily) numbers still describe Strava's windows: the
        first resets on the quarter hour, the second at 00:00 UTC."""
        if self.seen_at is None:
            return True, True
        now = now or datetime.now(UTC)
        seen = self.seen_at.astimezone(UTC)
        quarter = seen.replace(minute=seen.minute - seen.minute % 15, second=0, microsecond=0)
        return now < quarter + timedelta(minutes=15), now.date() == seen.date()

    def nearly_spent(self, margin: int = 5, now: datetime | None = None) -> bool:
        """True when the next few calls could hit a limit: stop before Strava says 429.
        A window that has reset since the last response no longer counts."""
        current = self.current(now)
        for limit, usage in ((self.limit, self.usage), (self.read_limit, self.read_usage)):
            if not (limit and usage):
                continue
            if any(
                live and u >= lim - margin
                for u, lim, live in zip(usage, limit, current, strict=True)
            ):
                return True
        return False


last_rate_limit = RateLimit()


def http(
    method: str,
    url: str,
    *,
    data: dict | None = None,
    token: str | None = None,
    basic: tuple[str, str] | None = None,
) -> Response:
    """The only network call; tests replace it. Form-encoded body, JSON response. The
    access token always travels in the Authorization header, never as a parameter."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif basic:
        pair = base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {pair}"
    body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as r:
            raw = r.read()
            return Response(r.status, json.loads(raw) if raw else None, dict(r.headers))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return Response(exc.code, parsed, dict(exc.headers or {}))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StravaError(f"Strava couldn't be reached: {exc}") from exc


def _pair(value: str | None) -> tuple[int, int] | None:
    try:
        a, b = (int(v) for v in (value or "").split(","))
    except ValueError:
        return None
    return a, b


def _track_rate_limit(headers: dict[str, str]) -> None:
    lower = {k.lower(): v for k, v in headers.items()}
    if "x-ratelimit-usage" not in lower:
        return
    last_rate_limit.limit = _pair(lower.get("x-ratelimit-limit"))
    last_rate_limit.usage = _pair(lower.get("x-ratelimit-usage"))
    last_rate_limit.read_limit = _pair(lower.get("x-readratelimit-limit"))
    last_rate_limit.read_usage = _pair(lower.get("x-readratelimit-usage"))
    last_rate_limit.seen_at = datetime.now(UTC)


def _message(body: Any) -> str:
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                field, code = first.get("field", ""), first.get("code", "")
                return f"{body.get('message', 'Error')}: {field} {code}".strip()
        if body.get("message"):
            return str(body["message"])
    return "unexpected response"


def _check(response: Response) -> Any:
    _track_rate_limit(response.headers)
    if response.status == 401:
        raise StravaAuthError("Strava no longer accepts this connection. Reconnect Strava.")
    if response.status == 429:
        raise StravaRateLimited(
            "Strava's rate limit is reached. Try again in 15 minutes (or tomorrow, for the "
            "daily limit)."
        )
    if response.status == 404:
        raise StravaNotFound(f"Strava answered 404: {_message(response.body)}")
    if response.status >= 400:
        raise StravaError(f"Strava answered {response.status}: {_message(response.body)}")
    return response.body


# --- OAuth ------------------------------------------------------------------------------


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        # "auto" skips the consent screen when the athlete already granted these scopes,
        # so logging in again is one click.
        "approval_prompt": "auto",
        "scope": ",".join(SCOPES),
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(client_id: str, client_secret: str, code: str) -> dict[str, Any]:
    """Code → {access_token, refresh_token, expires_at, athlete: {id, ...}}."""
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }
    body = _check(http("POST", TOKEN_URL, data=data))
    if not isinstance(body, dict) or "access_token" not in body or "athlete" not in body:
        raise StravaError("Strava's token response is missing the athlete or the token.")
    return body


def _names_application(body: Any) -> bool:
    """Strava's error body names what it refused: `Application` / `client_id` /
    `client_secret` is the app's credentials, `RefreshToken` / `refresh_token` the athlete's
    grant."""
    errors = body.get("errors") if isinstance(body, dict) else None
    for e in errors if isinstance(errors, list) else []:
        if not isinstance(e, dict):
            continue
        if e.get("resource") == "Application" or e.get("field") in ("client_id", "client_secret"):
            return True
    return False


def refresh_token(client_id: str, client_secret: str, refresh: str) -> dict[str, Any]:
    """A new access token. The refresh token may change too: always keep the newest."""
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    }
    response = http("POST", TOKEN_URL, data=data)
    if response.status in (400, 401) and _names_application(response.body):
        # Our client id or secret is wrong (a deployment mistake), not the athlete's
        # refusal: never a reason to delete their data.
        raise StravaError(
            "Strava refused this site's client id or secret. Check STRAVA_CLIENT_ID and "
            f"STRAVA_CLIENT_SECRET ({_message(response.body)})."
        )
    if response.status in (400, 401):
        # A refused refresh token means the athlete revoked access. Only a refusal does:
        # a timeout, a 429 or a 5xx is Strava being busy, and raises a plain StravaError.
        raise StravaAuthError(
            f"Strava refused to renew the connection ({_message(response.body)})."
        )
    body = _check(response)
    if not isinstance(body, dict) or "access_token" not in body:
        raise StravaError("Strava's refresh response has no access token.")
    return body


def revoke(client_id: str, client_secret: str, refresh: str, access: str) -> bool:
    """Revoke our access, so the app disappears from the athlete's Strava settings; the
    legacy endpoint is the fallback. Returns whether Strava confirmed it."""
    try:
        response = http(
            "POST",
            REVOKE_URL,
            data={"token": refresh, "token_type_hint": "refresh_token"},
            basic=(client_id, client_secret),
        )
        if response.status < 400:
            return True
        return http("POST", DEAUTHORIZE_URL, token=access).status < 400
    except StravaError:
        log.warning("Strava revocation failed", exc_info=True)
        return False


# --- data -------------------------------------------------------------------------------


def _base() -> str:
    from app.config import get_settings

    return get_settings().strava_api_base.rstrip("/")


def get(token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    return _check(http("GET", f"{_base()}{path}{query}", token=token))


def put(token: str, path: str, data: dict[str, Any]) -> Any:
    return _check(http("PUT", f"{_base()}{path}", data=data, token=token))


def list_activities(token: str, *, after: int | None = None, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"per_page": PER_PAGE, "page": page}
    if after is not None:
        params["after"] = after
    body = get(token, "/athlete/activities", params)
    return body if isinstance(body, list) else []


def activity(token: str, activity_id: int) -> dict[str, Any]:
    return get(token, f"/activities/{activity_id}", {"include_all_efforts": "false"})


def laps(token: str, activity_id: int) -> list[dict[str, Any]]:
    body = get(token, f"/activities/{activity_id}/laps")
    return body if isinstance(body, list) else []


def streams(token: str, activity_id: int) -> dict[str, Any]:
    """{key: {data: [...], ...}}. A manual activity has no streams: 404 is an empty dict."""
    path = f"/activities/{activity_id}/streams"
    params = {"keys": ",".join(STREAM_KEYS), "key_by_type": "true"}
    response = http("GET", f"{_base()}{path}?{urllib.parse.urlencode(params)}", token=token)
    if response.status == 404:
        _track_rate_limit(response.headers)
        return {}
    body = _check(response)
    return body if isinstance(body, dict) else {}


def update_description(token: str, activity_id: int, description: str) -> dict[str, Any]:
    return put(token, f"/activities/{activity_id}", {"description": description})
