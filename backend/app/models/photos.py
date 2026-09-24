"""Race photos (docs/features/photos.md#data).

Only the re-encoded copies are kept, at paths derived from the id: the uploaded
original, with its EXIF (camera serial, GPS), is never written to disk.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class RacePhoto(Base):
    __tablename__ = "race_photo"
    __table_args__ = (
        UniqueConstraint("race_id", "content_hash"),
        Index("ix_race_photo_race_id", "race_id"),
        Index("uq_race_photo_cover", "race_id", unique=True, postgresql_where=text("is_cover")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("race.id", ondelete="CASCADE"))
    content_hash: Mapped[str] = mapped_column(String(64))  # of the uploaded bytes
    width: Mapped[int]  # of the stored full-size copy
    height: Mapped[int]
    caption: Mapped[str | None]
    position: Mapped[int] = mapped_column(default=0)
    is_cover: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
