from typing import Literal

from pydantic import BaseModel

from app.schemas.common import Model, Patch


class LoginIn(BaseModel):
    username: str
    password: str


class Me(BaseModel):
    owner: bool
    site_mode: Literal["private", "public"]
    # How the owner logs in: the password form, or "Log in with Strava" once the owner's
    # Strava athlete is linked (docs/features/strava.md#login).
    login: Literal["password", "strava"]


class SiteOut(Model):
    site_title: str
    athlete_name: str | None
    about: str | None


class SitePatch(Patch):
    site_title: str | None = None
    athlete_name: str | None = None
    about: str | None = None


class VisibilityIn(BaseModel):
    is_public: bool
    # The owner's one-time consent to show data that came from Strava to visitors
    # (docs/features/strava.md#publishing). Only read when it is needed.
    strava_consent: bool = False
