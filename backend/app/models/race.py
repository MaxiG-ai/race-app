"""Race and official result (docs/features/races-and-results.md#data).

Classification and weather columns live here too; their features write them.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow
from app.models.enums import ClassificationSource

if TYPE_CHECKING:
    from app.models.classification import RaceCategory
    from app.models.fueling import RaceFueling
    from app.models.imports import RecordedActivity
    from app.models.photos import RacePhoto


class Race(Base):
    __tablename__ = "race"
    __table_args__ = (Index("ix_race_date", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(default="Untitled race")
    date: Mapped[date]
    location_name: Mapped[str | None]
    location_lat: Mapped[float | None]
    location_lng: Mapped[float | None]
    notes: Mapped[str | None]

    is_public: Mapped[bool] = mapped_column(default=False)
    published_at: Mapped[datetime | None]

    race_category_id: Mapped[int | None] = mapped_column(ForeignKey("race_category.id"))
    auto_category_id: Mapped[int | None] = mapped_column(ForeignKey("race_category.id"))
    classification_source: Mapped[ClassificationSource] = mapped_column(
        default=ClassificationSource.UNCLASSIFIED
    )
    classified_at: Mapped[datetime | None]
    classification_detail: Mapped[dict[str, Any] | None]

    weather_temp_c: Mapped[float | None]
    weather_humidity_pct: Mapped[float | None]
    weather_conditions: Mapped[str | None]
    weather_fetched_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    category: Mapped[RaceCategory | None] = relationship(foreign_keys=[race_category_id])
    auto_category: Mapped[RaceCategory | None] = relationship(foreign_keys=[auto_category_id])
    official_result: Mapped[OfficialResult | None] = relationship(
        back_populates="race", cascade="all, delete-orphan"
    )
    activities: Mapped[list[RecordedActivity]] = relationship(
        back_populates="race", cascade="all, delete-orphan", order_by="RecordedActivity.start_time"
    )
    fueling: Mapped[list[RaceFueling]] = relationship(cascade="all, delete-orphan")
    photos: Mapped[list[RacePhoto]] = relationship(
        cascade="all, delete-orphan", order_by="(RacePhoto.position, RacePhoto.id)"
    )

    def cover_photo(self) -> RacePhoto | None:
        return next((p for p in self.photos if p.is_cover), None)

    def primary_activities(self) -> dict[str, RecordedActivity]:
        """segment -> the primary recording; the only rows a visitor ever sees."""
        return {a.segment: a for a in self.activities if a.is_primary}


class OfficialResult(Base):
    __tablename__ = "official_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("race.id", ondelete="CASCADE"), unique=True)
    total_time: Mapped[int]
    total_is_derived: Mapped[bool] = mapped_column(default=False)
    swim_time: Mapped[int | None]
    t1_time: Mapped[int | None]
    bike_time: Mapped[int | None]
    t2_time: Mapped[int | None]
    run_time: Mapped[int | None]
    overall_place: Mapped[int | None]
    age_group_place: Mapped[int | None]
    entered_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    race: Mapped[Race] = relationship(back_populates="official_result")

    def split(self, segment: str) -> int | None:
        return getattr(self, f"{segment}_time")
