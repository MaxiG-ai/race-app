"""Upload, recorded activity and lap split (docs/features/import.md#data)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow
from app.models.enums import Segment, Sport, UploadKind, UploadSource, UploadStatus

if TYPE_CHECKING:
    from app.models.gear import ActivityGear
    from app.models.race import Race


class Upload(Base):
    __tablename__ = "upload"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[UploadSource] = mapped_column(default=UploadSource.FIT_UPLOAD)
    filename: Mapped[str | None]
    stored_path: Mapped[str | None]
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    external_id: Mapped[str | None] = mapped_column(unique=True)
    status: Mapped[UploadStatus]
    kind: Mapped[UploadKind | None]
    detected_sport: Mapped[Sport | None]
    detected_start_time: Mapped[datetime | None]
    detected_end_time: Mapped[datetime | None]
    detected_distance_m: Mapped[float | None]
    summary: Mapped[dict[str, Any] | None]
    error: Mapped[str | None]
    race_id: Mapped[int | None] = mapped_column(ForeignKey("race.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow)


class RecordedActivity(Base):
    __tablename__ = "recorded_activity"
    __table_args__ = (
        UniqueConstraint("race_id", "segment", "upload_id"),
        Index("ix_recorded_activity_race_id", "race_id"),
        Index(
            "uq_recorded_activity_primary",
            "race_id",
            "segment",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("race.id", ondelete="CASCADE"))
    upload_id: Mapped[int | None] = mapped_column(ForeignKey("upload.id", ondelete="SET NULL"))
    segment: Mapped[Segment]
    is_primary: Mapped[bool] = mapped_column(default=False)
    device_key: Mapped[str | None]
    is_derived: Mapped[bool] = mapped_column(default=False)
    source: Mapped[UploadSource] = mapped_column(default=UploadSource.FIT_UPLOAD)
    start_time: Mapped[datetime]
    elapsed_time: Mapped[int]
    moving_time: Mapped[int | None]
    distance_m: Mapped[float | None]
    elevation_gain_m: Mapped[float | None]
    avg_hr: Mapped[int | None]
    max_hr: Mapped[int | None]
    avg_power: Mapped[int | None]
    normalized_power: Mapped[int | None]
    avg_cadence: Mapped[int | None]
    avg_temp_c: Mapped[float | None]
    rpe_estimated: Mapped[float | None]
    bounds_n: Mapped[float | None]
    bounds_s: Mapped[float | None]
    bounds_e: Mapped[float | None]
    bounds_w: Mapped[float | None]
    gps_track: Mapped[list[Any] | None]
    progress_series: Mapped[list[Any] | None]
    raw_data: Mapped[dict[str, Any] | None]
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)

    race: Mapped[Race] = relationship(back_populates="activities")
    laps: Mapped[list[LapSplit]] = relationship(
        cascade="all, delete-orphan", order_by="LapSplit.lap_index"
    )
    gear_links: Mapped[list[ActivityGear]] = relationship(cascade="all, delete-orphan")

    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(seconds=self.elapsed_time)


class LapSplit(Base):
    __tablename__ = "lap_split"
    __table_args__ = (
        Index("ix_lap_split_activity_lap", "recorded_activity_id", "lap_index"),
        Index("ix_lap_split_sport_activity", "sport", "recorded_activity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recorded_activity_id: Mapped[int] = mapped_column(
        ForeignKey("recorded_activity.id", ondelete="CASCADE")
    )
    sport: Mapped[Segment]
    lap_index: Mapped[int]
    start_time: Mapped[datetime]
    total_time: Mapped[int]
    distance_m: Mapped[float | None]
    elevation_gain_m: Mapped[float | None]
    avg_hr: Mapped[int | None]
    max_hr: Mapped[int | None]
    avg_power: Mapped[int | None]
    normalized_power: Mapped[int | None]
    cadence_avg: Mapped[int | None]
    is_partial: Mapped[bool] = mapped_column(default=False)
    raw_data: Mapped[dict[str, Any] | None]
