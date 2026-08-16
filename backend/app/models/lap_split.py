from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Segment

if TYPE_CHECKING:
    from app.models.recorded_activity import RecordedActivity


class LapSplit(Base):
    """Many:1 with `RecordedActivity` — auto-laps (1 km run, 5 km bike; swim varies
    by vendor, §3). Pacing shape only — never summed for segment totals (§3 Traps 1)."""

    __tablename__ = "lap_splits"
    __table_args__ = (
        # Fast per-sport filtering (e.g. "all run splits under 5:00/km"). Leading
        # column also covers plain recorded_activity_id lookups, so no separate
        # single-column index on the FK.
        Index("ix_lap_split_sport_activity", "sport", "recorded_activity_id"),
        # Reconstruct lap order within one activity.
        Index("ix_lap_split_activity_lap_index", "recorded_activity_id", "lap_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recorded_activity_id: Mapped[int] = mapped_column(
        ForeignKey("recorded_activities.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized from the parent RecordedActivity.segment, for direct per-sport
    # filtering without a join. Shares the same enum domain (swim/t1/bike/t2/run).
    sport: Mapped[Segment] = mapped_column(Enum(Segment, name="segment"), nullable=False)
    lap_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_time: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[int | None] = mapped_column(Integer)
    max_hr: Mapped[int | None] = mapped_column(Integer)
    avg_power: Mapped[int | None] = mapped_column(Integer)  # device-dependent, not sport-dependent
    normalized_power: Mapped[int | None] = mapped_column(Integer)  # Garmin bike only
    avg_pace: Mapped[float | None] = mapped_column(Float)
    avg_speed: Mapped[float | None] = mapped_column(Float)
    elevation_gain_m: Mapped[int | None] = mapped_column(Integer)
    cadence_avg: Mapped[int | None] = mapped_column(Integer)
    # True when the lap is a trailing remainder (§3.2) — e.g. Apple's dropped
    # partial bike lap would be flagged here if it were ever recorded as a lap.
    is_partial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    recorded_activity: Mapped[RecordedActivity] = relationship(back_populates="lap_splits")
