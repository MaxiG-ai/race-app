from typing import Literal

from pydantic import BaseModel

from app.schemas.common import Model, Patch


class LoginIn(BaseModel):
    username: str
    password: str


class Me(BaseModel):
    owner: bool
    site_mode: Literal["private", "public"]


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
