"""Race shapes. `*Public` classes list everything a visitor may see; owner classes
extend them. Never-public fields (uploads, raw_data, serials, completeness, non-primary
recordings) exist only on owner classes."""

import datetime as dt
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import ClassificationSource, Segment
from app.schemas.common import Model, Patch, Seconds
from app.schemas.photos import PhotoOut


class CategoryRef(Model):
    id: int
    key: str
    label: str


class Weather(BaseModel):
    temp_c: float | None
    humidity_pct: float | None
    conditions: str | None


class OfficialResultOut(Model):
    total_time: int
    total_is_derived: bool
    swim_time: int | None
    t1_time: int | None
    bike_time: int | None
    t2_time: int | None
    run_time: int | None
    derived_split: Segment | None = None  # the one split computed as the remainder
    overall_place: int | None
    age_group_place: int | None


class OfficialResultIn(Patch):
    total_time: Seconds | None = None
    swim_time: Seconds | None = None
    t1_time: Seconds | None = None
    bike_time: Seconds | None = None
    t2_time: Seconds | None = None
    run_time: Seconds | None = None
    overall_place: int | None = Field(default=None, ge=1)
    age_group_place: int | None = Field(default=None, ge=1)


class GearName(Model):
    id: int
    name: str
    kind: str


class ZoneSet(BaseModel):
    high: list[int]  # upper bound of each zone (bpm or W); zone 0 is below zone 1
    seconds: list[int]  # may have one more entry than `high`: the open-ended top zone


class TimeInZone(BaseModel):
    hr: ZoneSet | None
    power: ZoneSet | None


class SegmentPublic(Model):
    """The primary recording of one segment."""

    id: int
    segment: Segment
    is_derived: bool
    start_time: datetime
    elapsed_time: int
    moving_time: int | None
    distance_m: float | None
    elevation_gain_m: float | None
    avg_hr: int | None
    max_hr: int | None
    avg_power: int | None
    normalized_power: int | None
    avg_cadence: int | None
    lap_count: int
    bounds: list[float] | None  # [n, s, e, w]
    time_in_zone: TimeInZone | None  # only from devices that record it
    gear: list[GearName]


class Recording(SegmentPublic):
    """Any recording, primary or not. Owner only."""

    is_primary: bool
    upload_id: int | None
    device_key: str | None
    avg_temp_c: float | None
    rpe_estimated: float | None
    sensors: list[dict[str, Any]]


class FuelingProduct(BaseModel):
    name: str = Field(min_length=1)
    quantity: float | None = Field(default=None, ge=0)


class FuelingOut(Model):
    segment: Segment
    carbs_g: float | None
    products: list[FuelingProduct]
    notes: str | None


class RaceDetailPublic(BaseModel):
    id: int
    name: str
    date: dt.date
    location_name: str | None
    location_lat: float | None
    location_lng: float | None
    notes: str | None
    notes_html: str | None  # the race report, rendered from the Markdown subset
    is_public: bool
    category: CategoryRef | None
    weather: Weather
    official_result: OfficialResultOut | None
    segments: list[SegmentPublic]
    fueling: list[FuelingOut]
    photos: list[PhotoOut]


class Classification(BaseModel):
    source: ClassificationSource
    auto_category: CategoryRef | None
    detail: dict[str, Any] | None
    classified_at: datetime | None


class RaceDetailOwner(RaceDetailPublic):
    published_at: datetime | None
    weather_fetched_at: datetime | None
    classification: Classification
    recordings: list[Recording]
    completeness: dict[str, Any]


class RaceListItem(BaseModel):
    """Timeline card. No tracks, no laps."""

    id: int
    name: str
    date: dt.date
    location_name: str | None
    is_public: bool
    category: CategoryRef | None
    total_time: int | None
    times_source: str | None  # "official" | "recorded": what the segment bar uses
    segment_times: dict[Segment, int | None]
    derived_segments: list[Segment]
    cover_photo: PhotoOut | None


class RaceList(BaseModel):
    items: list[RaceListItem]
    total: int


class RaceCreate(BaseModel):
    date: dt.date | None = None  # defaults to today
    name: str | None = None


class RacePatch(Patch):
    name: str | None = Field(default=None, min_length=1)
    date: dt.date | None = None
    location_name: str | None = None
    location_lat: float | None = Field(default=None, ge=-90, le=90)
    location_lng: float | None = Field(default=None, ge=-180, le=180)
    notes: str | None = None
    race_category_id: int | None = None


class Stats(BaseModel):
    race_count: int
    distance_m: float
    distance_race_count: int
    elevation_gain_m: float
    elevation_race_count: int


class Lap(Model):
    segment: Segment = Field(validation_alias="sport")
    lap_index: int
    start_time: datetime
    total_time: int
    distance_m: float | None
    elevation_gain_m: float | None
    avg_hr: int | None
    max_hr: int | None
    avg_power: int | None
    normalized_power: int | None
    cadence_avg: int | None
    is_partial: bool


class TrackSegment(BaseModel):
    segment: Segment
    bounds: list[float] | None
    points: list[dict[str, Any]]  # {lat, lng, elevation, t, distance_m}
