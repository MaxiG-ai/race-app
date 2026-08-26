"""Upload staging, recorded activities and lap splits."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    ActivitySource,
    Base,
    DetectedSport,
    JSONBType,
    Segment,
    UploadKind,
    UploadStatus,
    created_at_col,
    pk,
)


class Upload(Base):
    """Every file or Strava activity, staged before it belongs to a race (§3).

    Carries its own `user_id`: uploads exist before any race, so they cannot inherit
    ownership through `race_id`. That makes a second ownership root — grouping must check
    it explicitly (§4 Tenancy).
    """

    __tablename__ = "upload"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", name="uq_upload_user_hash"),
        Index("ix_upload_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[ActivitySource] = mapped_column(
        Enum(ActivitySource, native_enum=False), nullable=False
    )

    filename: Mapped[str | None] = mapped_column(String(255))
    stored_path: Mapped[str | None] = mapped_column(String(1024))
    #: sha256 of the file. Strava's "export original" hands back the same bytes for every
    #: activity of a multisport upload, so this is what stops three downloads becoming
    #: three copies (§3).
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, native_enum=False), nullable=False, default=UploadStatus.PARSED
    )
    kind: Mapped[UploadKind | None] = mapped_column(Enum(UploadKind, native_enum=False))
    detected_sport: Mapped[DetectedSport | None] = mapped_column(
        Enum(DetectedSport, native_enum=False)
    )
    detected_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_distance_m: Mapped[float | None] = mapped_column(Float)

    #: Parse preview so the grouping tray renders without re-reading files.
    summary: Mapped[dict | None] = mapped_column(JSONBType)
    error: Mapped[str | None] = mapped_column(Text)

    race_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("race.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = created_at_col()

    activities: Mapped[list[RecordedActivity]] = relationship(back_populates="upload")


class RecordedActivity(Base):
    """One device's recording of one segment.

    A leg recorded by two devices yields two rows; exactly one is primary and every
    displayed number for that leg comes from it (§3). Assembly is per segment, never per
    metric.
    """

    __tablename__ = "recorded_activity"
    __table_args__ = (
        UniqueConstraint("race_id", "segment", "upload_id", name="uq_activity_race_seg_upload"),
        # Exactly one primary recording per segment. Partial index rather than a plain
        # unique constraint, because the non-primary duplicates are kept, not deleted.
        Index(
            "uq_activity_primary",
            "race_id",
            "segment",
            unique=True,
            postgresql_where=text("is_primary"),
            sqlite_where=text("is_primary"),
        ),
        Index("ix_activity_race_segment", "race_id", "segment"),
    )

    id: Mapped[uuid.UUID] = pk()
    race_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("race.id", ondelete="CASCADE"), nullable=False, index=True
    )
    upload_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("upload.id", ondelete="SET NULL")
    )

    segment: Mapped[Segment] = mapped_column(Enum(Segment, native_enum=False), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: True for transitions synthesized from gaps between grouped single-sport uploads.
    #: Carries a duration only — no distance, no track, no HR.
    is_derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: manufacturer:serial of the recording device — how gear is inferred (§3).
    device_key: Mapped[str | None] = mapped_column(String(128), index=True)
    source: Mapped[ActivitySource] = mapped_column(
        Enum(ActivitySource, native_enum=False), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(64))

    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    elapsed_time: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Garmin's differs from elapsed (pauses exist); Apple's does not.
    moving_time: Mapped[int | None] = mapped_column(Integer)

    distance_m: Mapped[float | None] = mapped_column(Float)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[int | None] = mapped_column(Integer)
    max_hr: Mapped[int | None] = mapped_column(Integer)
    #: Device-dependent, not sport-dependent: Apple reports running power and no bike
    #: power; Garmin the reverse (§3 Traps 2).
    avg_power: Mapped[int | None] = mapped_column(Integer)
    normalized_power: Mapped[int | None] = mapped_column(Integer)
    avg_speed: Mapped[float | None] = mapped_column(Float)
    avg_cadence: Mapped[float | None] = mapped_column(Float)
    #: Garmin device temperature — NOT air temperature (§3 Traps 8).
    avg_temp_c: Mapped[float | None] = mapped_column(Float)
    rpe_estimated: Mapped[float | None] = mapped_column(Float)
    avg_mets: Mapped[float | None] = mapped_column(Float)

    bounds_north: Mapped[float | None] = mapped_column(Float)
    bounds_south: Mapped[float | None] = mapped_column(Float)
    bounds_east: Mapped[float | None] = mapped_column(Float)
    bounds_west: Mapped[float | None] = mapped_column(Float)

    #: [{lat, lng, elevation, timestamp, distance_m}] — distance from the device's own
    #: odometer, never recomputed from coordinates (§11).
    gps_track: Mapped[list | None] = mapped_column(JSONBType)
    #: ~200 points of {progress, elapsed_s, distance_m}, resampled at import. Backs the
    #: gap chart without shipping the full stream (§11).
    progress_series: Mapped[list | None] = mapped_column(JSONBType)
    raw_data: Mapped[dict | None] = mapped_column(JSONBType)

    imported_at: Mapped[datetime] = created_at_col()

    race: Mapped[Race] = relationship(back_populates="activities")  # noqa: F821
    upload: Mapped[Upload | None] = relationship(back_populates="activities")
    laps: Mapped[list[LapSplit]] = relationship(
        back_populates="activity", cascade="all, delete-orphan", order_by="LapSplit.lap_index"
    )


class LapSplit(Base):
    """Auto-lap within one recording: 1 km run, 5 km bike, swim varies by vendor."""

    __tablename__ = "lap_split"
    __table_args__ = (
        Index("ix_lap_sport_activity", "sport", "recorded_activity_id"),
        Index("ix_lap_activity_index", "recorded_activity_id", "lap_index"),
    )

    id: Mapped[uuid.UUID] = pk()
    recorded_activity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recorded_activity.id", ondelete="CASCADE"), nullable=False
    )
    #: Denormalized from the parent for per-sport filtering without a join.
    sport: Mapped[Segment] = mapped_column(Enum(Segment, native_enum=False), nullable=False)

    lap_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_time: Mapped[int] = mapped_column(Integer, nullable=False)

    distance_m: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[int | None] = mapped_column(Integer)
    max_hr: Mapped[int | None] = mapped_column(Integer)
    avg_power: Mapped[int | None] = mapped_column(Integer)
    normalized_power: Mapped[int | None] = mapped_column(Integer)
    avg_speed: Mapped[float | None] = mapped_column(Float)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float)
    cadence_avg: Mapped[float | None] = mapped_column(Float)
    #: True when this is a trailing remainder rather than a full-length lap.
    is_partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    raw_data: Mapped[dict | None] = mapped_column(JSONBType)

    activity: Mapped[RecordedActivity] = relationship(back_populates="laps")
