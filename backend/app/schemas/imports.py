from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.enums import Segment, Sport, UploadKind, UploadStatus
from app.schemas.common import Model, Patch


class UploadOut(Model):
    id: int
    filename: str | None
    status: UploadStatus
    kind: UploadKind | None
    detected_sport: Sport | None
    detected_start_time: datetime | None
    detected_end_time: datetime | None
    detected_distance_m: float | None
    summary: dict[str, Any] | None
    error: str | None
    race_id: int | None
    uploaded_at: datetime


class Proposal(BaseModel):
    upload_ids: list[int]
    segments: dict[int, Segment]
    ambiguous: bool


class Tray(BaseModel):
    uploads: list[UploadOut]
    proposals: list[Proposal]


class Outcome(BaseModel):
    filename: str | None
    result: str
    message: str
    upload_id: int | None
    race_id: int | None


class GroupIn(BaseModel):
    upload_ids: list[int]
    segments: dict[int, Segment] = {}


class ActivityPatch(Patch):
    is_primary: bool | None = None
    gear_ids: list[int] | None = None
