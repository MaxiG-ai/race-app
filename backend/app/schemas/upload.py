from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.models.enums import Segment, Sport, UploadKind, UploadSource, UploadStatus
from app.schemas.race import RaceRead


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


class UploadResult(BaseModel):
    """One file's outcome from `POST /uploads` — the tray needs an explicit,
    per-file status rather than a single batch-level response, because a
    duplicate must be *reported*, not silently dropped (§3 "What Strava exports
    actually look like": a user deliberately selecting three files can
    legitimately get one distinct upload back, and the tray has to say so).

    - ``created``   — parsed and stored as a new `Upload` (possibly auto-grouped
      into a race, if multisport, or attached to the `race_id` given in the
      request).
    - ``duplicate`` — byte-identical to an upload this user already has;
      `upload` is the *existing* row, not a new one.
    - ``failed``    — looked like a FIT file but failed to parse; still stored as
      an `Upload` row with `status=failed` so it's visible in the tray, not lost
      to a bare 400.
    - ``rejected``  — rejected before parsing was even attempted (oversized, or
      not a `.fit` file per its name) — no `Upload` row is created for this case,
      since nothing was actually staged.
    """

    outcome: Literal["created", "duplicate", "failed", "rejected"]
    message: str | None = None
    filename: str
    upload: UploadRead | None = None
    race: RaceRead | None = None


class UploadBatchResponse(BaseModel):
    """`POST /uploads` response — one `UploadResult` per file submitted, in the
    same order."""

    results: list[UploadResult]


class UploadTrayResponse(BaseModel):
    """`GET /uploads` response — the ungrouped tray plus suggested groupings.
    Proposals are always computed over every *parsed* (ungrouped) upload
    regardless of the `?status=` filter applied to `uploads`, since a proposal
    only makes sense over what's still sitting in the tray."""

    uploads: list[UploadRead]
    proposals: list[UploadGroupProposal]
