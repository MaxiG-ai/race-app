from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Segment, UploadSource

if TYPE_CHECKING:
    from app.models.gear import ActivityGear
    from app.models.lap_split import LapSplit
    from app.models.race import Race
    from app.models.upload import Upload


class RecordedActivity(Base):
    """Many:1 with `Race` — 5+ rows per race, one per (segment, recording device).
    Exactly one row per segment is `is_primary` (§2, §3 Assembly)."""

    __tablename__ = "recorded_activities"
    __table_args__ = (
        # One row per segment *per upload* — two devices recording the same leg
        # coexist. Idempotency comes from Upload.content_hash, not this constraint.
        UniqueConstraint(
            "race_id", "segment", "upload_id", name="uq_recorded_activity_race_segment_upload"
        ),
        # Partial unique index: exactly one primary recording per segment.
        Index(
            "uq_recorded_activity_primary_per_segment",
            "race_id",
            "segment",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(
        ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    upload_id: Mapped[int | None] = mapped_column(ForeignKey("uploads.id", ondelete="SET NULL"))
    segment: Mapped[Segment] = mapped_column(Enum(Segment, name="segment"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # manufacturer + serial of the recording device (device_info) — how a segment's
    # gear is inferred (§3 Gear detection).
    device_key: Mapped[str | None] = mapped_column(String(255))
    # True for transitions synthesized from gaps between grouped single-sport
    # uploads; false for anything actually recorded (§3 Grouping).
    is_derived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    source: Mapped[UploadSource] = mapped_column(
        Enum(UploadSource, name="upload_source"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Wall-clock, session total_elapsed_time.
    elapsed_time: Mapped[int] = mapped_column(Integer, nullable=False)
    # session total_timer_time — differs from elapsed on Garmin (pauses).
    moving_time: Mapped[int | None] = mapped_column(Integer)
    # Float: transitions are short but real (20 m-550 m observed), precision matters
    # when reconciling recorded vs. official distance.
    distance_m: Mapped[float | None] = mapped_column(Float)
    # Whole metres read naturally for elevation; absent on swim/transitions.
    elevation_gain_m: Mapped[int | None] = mapped_column(Integer)
    avg_hr: Mapped[int | None] = mapped_column(Integer)
    max_hr: Mapped[int | None] = mapped_column(Integer)
    # Device-dependent, NOT sport-dependent (§3 Traps 2) — kept on every segment.
    avg_power: Mapped[int | None] = mapped_column(Integer)
    normalized_power: Mapped[int | None] = mapped_column(Integer)  # Garmin bike only
    # Derived from distance/time on read, not the FIT field (avg_speed is unreliable
    # on laps per §3, and pace/speed are never stored redundantly per §2 Conventions)
    # — these columns hold the session-level derived value computed at import time.
    avg_pace: Mapped[float | None] = mapped_column(Float)
    avg_speed: Mapped[float | None] = mapped_column(Float)
    avg_cadence: Mapped[int | None] = mapped_column(Integer)
    # Garmin device temperature, NOT air temperature (§3 Traps 8) — Race.weather_temp_c
    # is the source of truth for conditions.
    avg_temp_c: Mapped[float | None] = mapped_column(Float)
    rpe_estimated: Mapped[float | None] = mapped_column(Float)  # Apple developer field only
    avg_mets: Mapped[float | None] = mapped_column(Float)  # Apple developer field only
    # Session bounding box, for map fitting without touching the point stream.
    bounds_n_lat: Mapped[float | None] = mapped_column(Float)
    bounds_s_lat: Mapped[float | None] = mapped_column(Float)
    bounds_e_lng: Mapped[float | None] = mapped_column(Float)
    bounds_w_lng: Mapped[float | None] = mapped_column(Float)
    # Array of {lat, lng, elevation, timestamp}. Never returned from list/detail
    # endpoints (§4 Payload note) — served only via /races/{id}/track.
    gps_track: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Full parsed FIT/Strava payload, safety net for unmapped fields.
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    race: Mapped[Race] = relationship(back_populates="recorded_activities")
    upload: Mapped[Upload | None] = relationship(back_populates="recorded_activities")
    lap_splits: Mapped[list[LapSplit]] = relationship(
        back_populates="recorded_activity", cascade="all, delete-orphan"
    )
    activity_gear: Mapped[list[ActivityGear]] = relationship(
        back_populates="recorded_activity", cascade="all, delete-orphan"
    )
