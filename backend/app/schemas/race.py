from __future__ import annotations

from datetime import date as date_
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import RaceType, Segment
from app.schemas.activity import RecordedActivityRead
from app.schemas.fueling import RaceFuelingRead


class OfficialResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    race_id: int
    total_time: int
    swim_time: int | None
    t1_time: int | None
    bike_time: int | None
    t2_time: int | None
    run_time: int | None
    overall_place: int | None
    age_group_place: int | None
    entered_at: datetime


class OfficialResultUpsert(BaseModel):
    """`PUT /races/{id}/official-result` body."""

    total_time: int
    swim_time: int | None = None
    t1_time: int | None = None
    bike_time: int | None = None
    t2_time: int | None = None
    run_time: int | None = None
    overall_place: int | None = None
    age_group_place: int | None = None


class RaceBase(BaseModel):
    name: str
    date: date_
    location_name: str | None = None
    location_lat: float | None = None
    location_lng: float | None = None
    race_type: RaceType
    notes: str | None = None


class RaceCreate(RaceBase):
    """`POST /races` — manual race creation."""


class RaceUpdate(BaseModel):
    """`PATCH /races/{id}` — metadata only (name, type, location, notes); every
    field optional."""

    name: str | None = None
    date: date_ | None = None
    location_name: str | None = None
    location_lat: float | None = None
    location_lng: float | None = None
    race_type: RaceType | None = None
    notes: str | None = None


class RaceRead(RaceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    weather_temp_c: float | None
    weather_humidity_pct: float | None
    weather_conditions: str | None
    created_at: datetime
    updated_at: datetime


class SegmentSummary(BaseModel):
    """One segment's official-vs-recorded totals, as shown in the timeline
    `SegmentBar` and the detail `SplitsComparison` table."""

    segment: Segment
    official_time_s: int | None = None
    recorded_time_s: int | None = None
    recorded_distance_m: float | None = None
    is_derived: bool | None = None


class RaceListItem(BaseModel):
    """`GET /races` preview payload — deliberately excludes `gps_track`, lap
    splits and raw activity rows; only what the timeline card needs (§4)."""

    id: int
    name: str
    date: date_
    race_type: RaceType
    location_name: str | None
    badges: list[str] = []
    total_official_time_s: int | None = None
    total_recorded_time_s: int | None = None
    segments: list[SegmentSummary] = []


class RaceDetail(RaceRead):
    """`GET /races/{id}` — race + official result + recorded activities (each
    with their laps and gear, no `gps_track`) + fueling. Everything the detail
    page needs in one call except the GPS track (`/races/{id}/track`, its own
    endpoint per §4 Payload note)."""

    official_result: OfficialResultRead | None = None
    recorded_activities: list[RecordedActivityRead] = []
    fuelings: list[RaceFuelingRead] = []
