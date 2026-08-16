from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Sport, UploadKind, UploadSource, UploadStatus

if TYPE_CHECKING:
    from app.models.race import Race
    from app.models.recorded_activity import RecordedActivity
    from app.models.user import User


class Upload(Base):
    """Staging row for every file or Strava activity, created before it belongs to
    a race — what makes manual grouping possible (§3)."""

    __tablename__ = "uploads"
    __table_args__ = (
        # Dedupe is per-owner (§2): the same file re-uploaded by a different user is
        # not a duplicate.
        UniqueConstraint("user_id", "content_hash", name="uq_upload_user_content_hash"),
        # Strava-sourced uploads dedupe on external_id, scoped per user; most rows
        # have external_id NULL (fit_upload), so the uniqueness is partial.
        Index(
            "uq_upload_user_external_id",
            "user_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Uploads exist before any race, so they carry their own owner (§4 Tenancy).
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    source: Mapped[UploadSource] = mapped_column(
        Enum(UploadSource, name="upload_source"), nullable=False
    )
    filename: Mapped[str | None] = mapped_column(String(255))
    stored_path: Mapped[str | None] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, name="upload_status"), nullable=False
    )
    kind: Mapped[UploadKind] = mapped_column(Enum(UploadKind, name="upload_kind"), nullable=False)
    detected_sport: Mapped[Sport | None] = mapped_column(Enum(Sport, name="sport"))
    detected_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_distance_m: Mapped[float | None] = mapped_column(Float)
    # Parse preview for the grouping UI, so the tray renders without re-reading files.
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    # Nullable, set once grouped. SET NULL on race delete — the upload (and its
    # retained file) outlives the race (§8).
    race_id: Mapped[int | None] = mapped_column(ForeignKey("races.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="uploads")
    race: Mapped[Race | None] = relationship(back_populates="uploads")
    recorded_activities: Mapped[list[RecordedActivity]] = relationship(back_populates="upload")
