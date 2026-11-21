"""Strava: log in, connect, pull races, settings, webhook (docs/features/strava.md#api).

Three routers, each from an access factory like every other:
- `oauth` holds the two browser redirects of the authorization-code flow. They are page
  navigations, not JSON, so they live outside /api and out of the OpenAPI schema; the
  `state` cookie is what protects them.
- `write`: everything the owner does with the connection.
- `anyone`: the webhook Strava calls. It trusts no event: see `strava.handle_event`.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from app.access import (
    DB,
    CurrentViewer,
    anyone_router,
    owner_router,
    start_session,
)
from app.config import get_settings
from app.db import _session_factory, utcnow
from app.models import Owner
from app.routers.access import site_settings
from app.schemas.strava import DisconnectOut, PullIn, PullOut, StravaPatch, StravaStatus
from app.services import strava, strava_api
from app.services.strava_api import StravaAuthError, StravaError

log = logging.getLogger(__name__)

oauth = APIRouter(include_in_schema=False)
write = owner_router()
anyone = anyone_router()

STATE_COOKIE = "strava_oauth"
STATE_TTL_S = 600
CALLBACK_PATH = "/auth/strava/callback"
# Never verifies as an argon2 hash: an owner made by logging in with Strava has no password.
NO_PASSWORD = "!strava-login-only"


def _enabled() -> None:
    if not get_settings().strava_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Strava isn't set up on this site.")


def _base_url(request: Request) -> str:
    return (get_settings().public_base_url or str(request.base_url)).rstrip("/")


def _safe_next(target: str | None) -> str:
    """A path on this site. Browsers read a backslash as a slash and drop tabs and
    newlines, so "/\\evil.example" would be "//evil.example": refused too."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    if "\\" in target or any(ord(c) < 0x20 or ord(c) == 0x7F for c in target):
        return "/"
    return target


def _with(path: str, key: str, value: str) -> str:
    return f"{path}{'&' if '?' in path else '?'}{key}={quote(value)}"


# --- the authorization-code flow ----------------------------------------------------------


@oauth.get("/auth/strava")
def start(request: Request, next: str | None = None):
    """Send the browser to Strava's consent screen. Used both to log in and, by a
    logged-in owner, to connect."""
    _enabled()
    state = secrets.token_urlsafe(24)
    url = strava_api.authorize_url(
        get_settings().strava_client_id, _base_url(request) + CALLBACK_PATH, state
    )
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        STATE_COOKIE,
        f"{state}|{_safe_next(next)}",
        max_age=STATE_TTL_S,
        httponly=True,
        secure=request.url.hostname not in {"localhost", "127.0.0.1", "::1"},
        samesite="lax",  # sent on the top-level redirect back from strava.com
        path="/auth/strava",
    )
    return response


@oauth.get(CALLBACK_PATH)
def callback(
    request: Request,
    db: DB,
    viewer: CurrentViewer,
    state: str = "",
    code: str | None = None,
    scope: str = "",
    error: str | None = None,
):
    """Strava sends the browser back here. The athlete it names either is the owner (log
    in / connect) or is turned away, and a stranger's token is revoked at once."""
    _enabled()
    saved = request.cookies.get(STATE_COOKIE, "")
    saved_state, _, target = saved.partition("|")
    target = _safe_next(target)
    fail_to = target if viewer.is_owner else "/login"

    def done(path: str) -> RedirectResponse:
        response = RedirectResponse(path, status_code=303)
        response.delete_cookie(STATE_COOKIE, path="/auth/strava")
        return response

    if not saved_state or not secrets.compare_digest(saved_state, state):
        return done(_with(fail_to, "strava", "expired"))
    if error or not code:
        return done(_with(fail_to, "strava", "denied"))
    settings = get_settings()
    try:
        token = strava_api.exchange_code(
            settings.strava_client_id, settings.strava_client_secret, code
        )
    except StravaError:
        return done(_with(fail_to, "strava", "failed"))
    athlete_id = int(token["athlete"]["id"])
    granted = scope or ",".join(token.get("scope", "").split())

    owner = db.scalar(select(Owner))
    if viewer.is_owner:
        if owner is None or owner.strava_athlete_id not in (None, athlete_id):
            _turn_away(token)
            return done(_with(target, "strava", "other_athlete"))
        owner.strava_athlete_id = athlete_id
        strava.connect(db, token, granted)
        db.commit()
        return done(_with(target, "strava", "connected"))

    if owner is not None and owner.strava_athlete_id == athlete_id:
        strava.connect(db, token, granted)
        db.commit()
    elif owner is None and settings.strava_owner_athlete_id == athlete_id:
        # A fresh instance claimed by the athlete named in the deployment config.
        db.add(
            Owner(
                username=f"strava-{athlete_id}",
                password_hash=NO_PASSWORD,
                strava_athlete_id=athlete_id,
            )
        )
        strava.connect(db, token, granted)
        db.commit()
    else:
        _turn_away(token)
        return done(_with("/login", "strava", "not_owner"))
    response = done(target)
    start_session(db, request, response)
    return response


def _turn_away(token: dict) -> None:
    """Someone who isn't the owner authorized the app: give their access straight back."""
    settings = get_settings()
    strava_api.revoke(
        settings.strava_client_id,
        settings.strava_client_secret,
        token.get("refresh_token", ""),
        token.get("access_token", ""),
    )


# --- the owner's connection ---------------------------------------------------------------


def _status(db: DB) -> StravaStatus:
    settings = get_settings()
    row = strava.account(db)
    site = site_settings(db)
    rate = strava_api.last_rate_limit
    return StravaStatus(
        enabled=settings.strava_enabled,
        connected=row is not None,
        athlete_id=str(row.athlete_id) if row else None,
        athlete_name=row.athlete_name if row else None,
        scope=row.scope.split(",") if row else [],
        can_pull=bool(row and (row.granted("activity:read_all") or row.granted("activity:read"))),
        can_write=bool(row and row.granted(strava_api.WRITE_SCOPE)),
        last_pull_at=row.last_pull_at if row else None,
        tagline=site.strava_tagline,
        public_consent_at=site.strava_public_consent_at,
        rate_usage=list(rate.usage) if rate.usage else None,
        rate_limit=list(rate.limit) if rate.limit else None,
        webhook=bool(settings.strava_webhook_verify_token),
    )


def _strava_errors(exc: StravaError) -> HTTPException:
    if isinstance(exc, StravaAuthError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, strava_api.StravaRateLimited):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
    if isinstance(exc, strava.NotConnected | strava.PullRunning):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@write.get("/strava", response_model=StravaStatus)
def get_status(db: DB):
    return _status(db)


# Without STRAVA_CLIENT_ID/SECRET everything but the status is off: a refresh sent without
# them would be refused, and a refusal reads as revocation.
@write.patch("/strava", response_model=StravaStatus, dependencies=[Depends(_enabled)])
def patch_settings(body: StravaPatch, db: DB):
    values = body.model_dump(exclude_unset=True)
    site = site_settings(db)
    if values.get("tagline") is not None:
        site.strava_tagline = values["tagline"]
    if values.get("public_consent") is True and site.strava_public_consent_at is None:
        site.strava_public_consent_at = utcnow()
    elif values.get("public_consent") is False:
        site.strava_public_consent_at = None
        strava.unpublish_strava_races(db)  # withdrawn: visitors stop seeing Strava data
    db.commit()
    return _status(db)


@write.post("/strava/pull", response_model=PullOut, dependencies=[Depends(_enabled)])
def pull(body: PullIn, db: DB):
    """The "Pull from Strava" button."""
    try:
        result = strava.pull(db, everything=body.everything)
    except StravaError as exc:
        db.rollback()
        raise _strava_errors(exc) from exc
    return PullOut.from_result(result)


@write.post("/strava/disconnect", response_model=DisconnectOut, dependencies=[Depends(_enabled)])
def disconnect(db: DB):
    """Revoke the app's access at Strava and delete every recording pulled from it."""
    if strava.disconnect(db):
        return DisconnectOut(revoked=True, message="Strava is disconnected.")
    return DisconnectOut(
        revoked=False,
        message="The Strava data is deleted, but Strava didn't confirm the revocation. "
        "Remove this site under My Apps in your Strava settings.",
    )


# --- the webhook ------------------------------------------------------------------------


@anyone.get("/strava/webhook")
def webhook_challenge(
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
    verify_token: Annotated[str, Query(alias="hub.verify_token")] = "",
):
    """Strava's subscription handshake: echo the challenge when the token matches."""
    expected = get_settings().strava_webhook_verify_token
    if not expected or mode != "subscribe" or not secrets.compare_digest(verify_token, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unknown subscription.")
    return JSONResponse({"hub.challenge": challenge})


def _handle_event(event: dict) -> None:
    with _session_factory()() as db:
        try:
            strava.handle_event(db, event)
        except Exception:  # a background task has nobody to answer: log, never crash
            db.rollback()
            log.exception("Strava webhook event failed: %s", event.get("aspect_type"))


@anyone.post("/strava/webhook", status_code=status.HTTP_200_OK)
async def webhook_event(request: Request, background: BackgroundTasks):
    """Strava wants a 200 within two seconds, so the event is handled after answering."""
    if not get_settings().strava_webhook_verify_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No webhook on this site.")
    try:
        event = await request.json()
    except ValueError:
        return Response(status_code=status.HTTP_200_OK)
    if isinstance(event, dict):
        background.add_task(_handle_event, event)
    return Response(status_code=status.HTTP_200_OK)
