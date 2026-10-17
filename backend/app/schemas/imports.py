from pydantic import BaseModel

from app.models.enums import Segment
from app.schemas.common import Patch
from app.schemas.races import RaceDetailOwner


class Outcome(BaseModel):
    filename: str | None
    result: str  # attached | needs_segment | duplicate | failed
    message: str
    upload_id: int | None
    race_id: int | None


class ImportResult(BaseModel):
    outcomes: list[Outcome]
    race: RaceDetailOwner | None  # null when no race was made: nothing in the files fitted


class AssignIn(BaseModel):
    segments: dict[int, Segment]  # upload id -> the leg it fills


class ActivityPatch(Patch):
    is_primary: bool | None = None
    gear_ids: list[int] | None = None
