from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import Segment, Sport, UploadKind, UploadSource, UploadStatus


class UploadRead(BaseModel):
    """Staging tray row. `stored_path` is a server-internal filesystem detail and
    is deliberately not exposed, same spirit as omitting `password_hash` from
    `UserRead`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: UploadSource
    filename: str | None
    external_id: str | None
    status: UploadStatus
    kind: UploadKind
    detected_sport: Sport | None
    detected_start_time: datetime | None
    detected_end_time: datetime | None
    detected_distance_m: float | None
    summary: dict[str, Any] | None
    error: str | None
    race_id: int | None
    uploaded_at: datetime


class UploadGroupProposal(BaseModel):
    """One suggested grouping surfaced by `GET /uploads?status=parsed` — a
    proposal the user confirms, never auto-committed (§3 Single-sport uploads)."""

    upload_ids: list[int]
    segments: dict[int, Segment]


class RaceGroupRequest(BaseModel):
    """`POST /races/group` body: the upload ids the user selected plus, where
    detection was ambiguous, an explicit segment assignment per upload id."""

    upload_ids: list[int]
    segment_assignments: dict[int, Segment] | None = None


class RaceAttachRequest(BaseModel):
    """`POST /races/{id}/attach` body — attach further uploads to an existing
    race, e.g. backfilling a leg or replacing one recorded from the wrong device."""

    upload_ids: list[int]
    segment_assignments: dict[int, Segment] | None = None
