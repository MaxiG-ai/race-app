"""Trends shapes (docs/features/trends.md). Everything here is public: the same fields
the race detail already shows a visitor, one row per race."""

import datetime as dt

from pydantic import BaseModel

from app.models.enums import Segment
from app.schemas.races import CategoryRef


class SportTotals(BaseModel):
    """The primary recording's totals. Pace is derived from these on read."""

    distance_m: float | None
    elapsed_time: int
    avg_hr: int | None
    avg_power: int | None


class TrendRace(BaseModel):
    id: int
    name: str
    date: dt.date
    category: CategoryRef | None
    temp_c: float | None
    humidity_pct: float | None
    sports: dict[Segment, SportTotals | None]  # swim, bike, run


class Trends(BaseModel):
    races: list[TrendRace]  # oldest first
