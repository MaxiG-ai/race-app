"""Fueling per segment (docs/features/fueling.md#data)."""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow
from app.models.enums import Segment


class RaceFueling(Base):
    __tablename__ = "race_fueling"
    __table_args__ = (UniqueConstraint("race_id", "segment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("race.id", ondelete="CASCADE"))
    segment: Mapped[Segment]
    carbs_g: Mapped[float | None]
    products: Mapped[list[Any]] = mapped_column(default=list)
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
