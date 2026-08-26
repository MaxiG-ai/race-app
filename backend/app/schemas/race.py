from __future__ import annotations

from datetime import date as date_
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import CategoryKind, ClassificationSource, Segment
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
    """`PUT /races/{id}/official-result` body.

    `total_time` is optional *here* but NOT NULL in the database: omit it and the server
    sums the five splits. If any split is also missing there is nothing to sum, and the
    request is refused with a message naming what to fill in — no zero, no guess.
    """

    total_time: int | None = None
    swim_time: int | None = None
    t1_time: int | None = None
    bike_time: int | None = None
    t2_time: int | None = None
    run_time: int | None = None
    overall_place: int | None = None
    age_group_place: int | None = None


class RaceCategoryRead(BaseModel):
    """A row of the distance-category table (§9)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    label: str
    kind: CategoryKind
    sort_order: int
    swim_min_m: float | None
    swim_max_m: float | None
    bike_min_m: float | None
    bike_max_m: float | None
    run_min_m: float | None
    run_max_m: float | None
    tolerance_pct: float


class RaceBase(BaseModel):
    name: str
    date: date_
    location_name: str | None = None
    location_lat: float | None = None
    location_lng: float | None = None
    notes: str | None = None


class RaceCreate(RaceBase):
    """`POST /races` — manual race creation.

    No category: it is inferred from the measured distances once activities land (§9).
    A race created by hand with no recordings stays unclassified until either an import
    arrives or the user picks a category themselves.
    """


class RaceUpdate(BaseModel):
    """`PATCH /races/{id}` — metadata only (name, type, location, notes); every
    field optional."""

    name: str | None = None
    date: date_ | None = None
    location_name: str | None = None
    location_lat: float | None = None
    location_lng: float | None = None
    notes: str | None = None
    #: Setting this is a manual override: it pins the category and stops the classifier
    #: from moving it. Passing null clears the override and hands control back (§9).
    race_category_id: int | None = None


class RaceRead(RaceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    category: RaceCategoryRead | None = None
    #: What the classifier chose, kept even when overridden — so the UI can show
    #: "you set Mitteldistanz; the measurements say Kurzdistanz".
    auto_category: RaceCategoryRead | None = None
    classification_source: ClassificationSource
    classification_detail: dict | None = None
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
    category: RaceCategoryRead | None = None
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


class RaceListResponse(BaseModel):
    """`GET /races` envelope. Offset pagination over cursor: a race timeline is a
    bounded, slow-growing per-user collection (dozens, not millions), so
    offset's O(offset) cost is irrelevant, and `total` lets the UI render page
    numbers without a second round trip."""

    items: list[RaceListItem]
    total: int
    limit: int
    offset: int
