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


@anyone.post("/auth/login", status_code=status.HTTP_204_NO_CONTENT)
def login(body: LoginIn, request: Request, response: Response, db: DB, _=Depends(check_login_rate)):
    owner = db.scalar(select(Owner).where(Owner.username == body.username))
    if not verify_password(owner, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password.")
    start_session(db, request, response)


@anyone.get("/auth/me", response_model=Me)
def me(viewer: CurrentViewer):
    return Me(owner=viewer.is_owner, site_mode=get_settings().site_mode)


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
    race.is_public = body.is_public
    if body.is_public and race.published_at is None:
        race.published_at = utcnow()
    db.commit()
