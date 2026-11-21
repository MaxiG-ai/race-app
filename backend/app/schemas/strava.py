"""Strava shapes (docs/features/strava.md#api). Owner only: no token is ever in one."""

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import Patch
from app.schemas.imports import Outcome


class StravaStatus(BaseModel):
    enabled: bool  # STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET are set
    connected: bool
    athlete_id: str | None
    athlete_name: str | None
    scope: list[str]  # what the athlete granted
    can_pull: bool
    can_write: bool  # activity:write granted: the tagline can be written
    last_pull_at: datetime | None
    tagline: bool  # the site setting
    public_consent_at: datetime | None
    rate_usage: list[int] | None  # [15-minute, daily] from Strava's last response
    rate_limit: list[int] | None
    webhook: bool  # STRAVA_WEBHOOK_VERIFY_TOKEN is set


class StravaPatch(Patch):
    tagline: bool | None = None
    public_consent: bool | None = None  # true: agree; false: withdraw (unpublishes)


class PullIn(BaseModel):
    everything: bool = False  # search all of Strava, not just since the last pull


class PulledRace(BaseModel):
    race_id: int
    name: str
    created: bool
    outcomes: list[Outcome]


class Skipped(BaseModel):
    activity_id: str
    name: str | None
    message: str


class PullOut(BaseModel):
    races: list[PulledRace]
    skipped: list[Skipped]
    taglines: int
    tagline_errors: list[str]
    scanned: int
    complete: bool
    message: str

    @classmethod
    def from_result(cls, result) -> "PullOut":
        return cls(
            races=[
                PulledRace(
                    race_id=r.race_id,
                    name=r.name,
                    created=r.created,
                    outcomes=[Outcome(**vars(o)) for o in r.outcomes],
                )
                for r in result.races
            ],
            skipped=[Skipped(**s) for s in result.skipped],
            taglines=result.taglines,
            tagline_errors=result.tagline_errors,
            scanned=result.scanned,
            complete=result.complete,
            message=result.message,
        )
