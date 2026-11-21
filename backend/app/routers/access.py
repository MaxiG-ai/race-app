"""Login, site settings and per-race visibility (docs/features/access-and-visibility.md)."""

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from app.access import (
    DB,
    CurrentViewer,
    Viewer,
    anyone_router,
    check_login_rate,
    end_session,
    owner_router,
    reader_router,
    start_session,
    verify_password,
    visible_race,
)
from app.config import get_settings
from app.db import utcnow
from app.models import Owner, SiteSettings
from app.schemas.access import LoginIn, Me, SiteOut, SitePatch, VisibilityIn
from app.services import strava

anyone = anyone_router()
read = reader_router()
write = owner_router()


def site_settings(db: DB) -> SiteSettings:
    row = db.get(SiteSettings, 1)
    if row is None:
        row = SiteSettings(id=1)
        db.add(row)
        db.commit()
    return row


def login_method(db: DB) -> str:
    """Strava replaces the password once the owner's athlete is linked; the CLI
    (`strava-unlink`, `set-password`) is the way back."""
    if not get_settings().strava_enabled:
        return "password"
    owner = db.scalar(select(Owner))
    if owner is None:
        return "strava" if get_settings().strava_owner_athlete_id else "password"
    return "strava" if owner.strava_athlete_id is not None else "password"


@anyone.post("/auth/login", status_code=status.HTTP_204_NO_CONTENT)
def login(body: LoginIn, request: Request, response: Response, db: DB, _=Depends(check_login_rate)):
    if login_method(db) == "strava":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This site logs in with Strava.")
    owner = db.scalar(select(Owner).where(Owner.username == body.username))
    if not verify_password(owner, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password.")
    start_session(db, request, response)


@anyone.get("/auth/me", response_model=Me)
def me(viewer: CurrentViewer, db: DB):
    return Me(owner=viewer.is_owner, site_mode=get_settings().site_mode, login=login_method(db))


@write.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DB):
    end_session(db, request, response)


@read.get("/site", response_model=SiteOut)
def get_site(db: DB):
    return site_settings(db)


@write.patch("/site", response_model=SiteOut)
def patch_site(body: SitePatch, db: DB):
    row = site_settings(db)
    for key, value in body.model_dump(exclude_unset=True).items():
        if key == "site_title" and not value:
            raise HTTPException(422, "The site title can't be empty.")
        setattr(row, key, value)
    db.commit()
    return row


@write.patch("/races/{race_id}/visibility", status_code=status.HTTP_204_NO_CONTENT)
def set_visibility(race_id: int, body: VisibilityIn, db: DB):
    race = visible_race(db, Viewer(is_owner=True), race_id)
    if body.is_public and not race.is_public and strava.has_strava_data(race):
        site = site_settings(db)
        if site.strava_public_consent_at is None:
            if not body.strava_consent:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "This race has data from Strava. Strava's terms let it be shown only to "
                    "you unless you agree to show it to visitors.",
                )
            site.strava_public_consent_at = utcnow()
    race.is_public = body.is_public
    if body.is_public and race.published_at is None:
        race.published_at = utcnow()
    db.commit()
