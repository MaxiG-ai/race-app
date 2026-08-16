from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Segment

if TYPE_CHECKING:
    from app.models.race import Race


class RaceFueling(Base):
    """Many:1 with `Race` — one row per fuelled segment. Hangs off `Race` rather
    than `RecordedActivity` because it's hand-entered and must exist before any
    import (§2)."""

    __tablename__ = "race_fuelings"
    __table_args__ = (UniqueConstraint("race_id", "segment", name="uq_race_fueling_race_segment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(
        ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment: Mapped[Segment] = mapped_column(Enum(Segment, name="segment"), nullable=False)
    carbs_g: Mapped[int | None] = mapped_column(Integer)
    # [{name, quantity}, ...] — free-form, not a normalised product catalogue (§9).
    products: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    race: Mapped[Race] = relationship(back_populates="fuelings")
