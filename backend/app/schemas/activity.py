from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import Segment, UploadSource
from app.schemas.gear import ActivityGearRead


class LapSplitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recorded_activity_id: int
    sport: Segment
    lap_index: int
    start_time: datetime
    total_time: int
    distance_m: float | None
    avg_hr: int | None
    max_hr: int | None
    avg_power: int | None
    normalized_power: int | None
    avg_pace: float | None
    avg_speed: float | None
    elevation_gain_m: int | None
    cadence_avg: int | None
    is_partial: bool


class RecordedActivityRead(BaseModel):
    """Mirrors `RecordedActivity` minus `gps_track` — this is the shape used in
    `RaceDetail` and everywhere else except `/races/{id}/track` (§4 Payload note)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    race_id: int
    upload_id: int | None
    segment: Segment
    is_primary: bool
    device_key: str | None
    is_derived: bool
    source: UploadSource
    external_id: str | None
    start_time: datetime
    elapsed_time: int
    moving_time: int | None
    distance_m: float | None
    elevation_gain_m: int | None
    avg_hr: int | None
    max_hr: int | None
    avg_power: int | None
    normalized_power: int | None
    avg_pace: float | None
    avg_speed: float | None
    avg_cadence: int | None
    avg_temp_c: float | None
    rpe_estimated: float | None
    avg_mets: float | None
    bounds_n_lat: float | None
    bounds_s_lat: float | None
    bounds_e_lng: float | None
    bounds_w_lng: float | None
    imported_at: datetime
    laps: list[LapSplitRead] = []
    gear: list[ActivityGearRead] = []
    # raw_data intentionally omitted from every response schema: it's an internal
    # safety-net payload, not part of the API surface.


class RecordedActivityUpdate(BaseModel):
    """`PATCH /activities/{id}` — set `is_primary`, or assign gear to this segment.
    Gear assignment is a separate sub-object rather than a flat field because it
    also carries `assigned_by`."""

    is_primary: bool | None = None
    gear_id: int | None = None


class TrackPoint(BaseModel):
    lat: float
    lng: float
    elevation: float | None = None
    timestamp: datetime


class TrackBoundsRead(BaseModel):
    """A segment's bounding box, for map viewport fitting without the frontend
    having to scan `points` itself. Mirrors `RecordedActivity.bounds_*`."""

    n_lat: float
    s_lat: float
    e_lng: float
    w_lng: float


class SegmentTrack(BaseModel):
    """One segment's polyline, as returned by `GET /races/{id}/track`. This is the
    only place `gps_track` is ever serialised."""

    recorded_activity_id: int
    segment: Segment
    bounds: TrackBoundsRead | None = None
    points: list[TrackPoint]


class RaceTrackResponse(BaseModel):
    segments: list[SegmentTrack]
