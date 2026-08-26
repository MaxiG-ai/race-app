"""Race, its category, official result and fueling.

Nullability here is deliberately permissive (§10): apart from ownership and the date the
timeline orders by, everything can be absent. Validation lives at the point of use.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    CategoryKind,
    ClassificationSource,
    JSONBType,
    Segment,
    created_at_col,
    pk,
    updated_at_col,
)


class RaceCategory(Base):
    """Distance bands used to classify races (§9).

    Seeded by migration and editable as data so a boundary can be corrected without a
    deploy. Bounds are stored exactly as published; `tolerance_pct` is applied outward at
    match time, keeping the fudge factor visible rather than baked into the numbers.
    """

    __tablename__ = "race_category"

    id: Mapped[uuid.UUID] = pk()
    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[CategoryKind] = mapped_column(
        Enum(CategoryKind, native_enum=False), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # A null bound is open-ended — Sprintdistanz's "bis 0,75" has no floor.
    swim_min_m: Mapped[float | None] = mapped_column(Float)
    swim_max_m: Mapped[float | None] = mapped_column(Float)
    bike_min_m: Mapped[float | None] = mapped_column(Float)
    bike_max_m: Mapped[float | None] = mapped_column(Float)
    run_min_m: Mapped[float | None] = mapped_column(Float)
    run_max_m: Mapped[float | None] = mapped_column(Float)

    tolerance_pct: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def bounds(self, segment: Segment) -> tuple[float | None, float | None]:
        prefix = {Segment.SWIM: "swim", Segment.BIKE: "bike", Segment.RUN: "run"}[segment]
        return getattr(self, f"{prefix}_min_m"), getattr(self, f"{prefix}_max_m")


class Race(Base):
    __tablename__ = "race"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Required: the timeline orders by date, and a dateless race has nowhere to appear.
    # Forced to something correctable (import value, or today) rather than blocking a save.
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="Untitled race")

    location_name: Mapped[str | None] = mapped_column(String(200))
    location_lat: Mapped[float | None] = mapped_column(Float)
    location_lng: Mapped[float | None] = mapped_column(Float)

    race_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("race_category.id", ondelete="SET NULL")
    )
    # Kept alongside a manual override, never overwritten by it (§9).
    auto_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("race_category.id", ondelete="SET NULL")
    )
    classification_source: Mapped[ClassificationSource] = mapped_column(
        Enum(ClassificationSource, native_enum=False),
        nullable=False,
        default=ClassificationSource.UNCLASSIFIED,
    )
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Why classification failed, e.g. {"nearest": "kurz", "worst": "bike", "delta_pct": -15.6}
    classification_detail: Mapped[dict | None] = mapped_column(JSONBType)

    weather_temp_c: Mapped[float | None] = mapped_column(Float)
    weather_humidity_pct: Mapped[float | None] = mapped_column(Float)
    weather_conditions: Mapped[str | None] = mapped_column(String(64))

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    category: Mapped[RaceCategory | None] = relationship(foreign_keys=[race_category_id])
    auto_category: Mapped[RaceCategory | None] = relationship(foreign_keys=[auto_category_id])
    official_result: Mapped[OfficialResult | None] = relationship(
        back_populates="race", cascade="all, delete-orphan", uselist=False
    )
    fueling: Mapped[list[RaceFueling]] = relationship(
        back_populates="race", cascade="all, delete-orphan"
    )
    activities: Mapped[list[RecordedActivity]] = relationship(  # noqa: F821
        back_populates="race", cascade="all, delete-orphan"
    )


class OfficialResult(Base):
    """Manually entered organizer timing. 1:1 with Race.

    Every field is nullable so a half-entered result still saves, but `total_time` is
    filled by derivation whenever the five splits are present (§10) — the user never has
    to type a sum.
    """

    __tablename__ = "official_result"

    id: Mapped[uuid.UUID] = pk()
    race_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("race.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    total_time: Mapped[int | None] = mapped_column(Integer)
    total_is_derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    swim_time: Mapped[int | None] = mapped_column(Integer)
    t1_time: Mapped[int | None] = mapped_column(Integer)
    bike_time: Mapped[int | None] = mapped_column(Integer)
    t2_time: Mapped[int | None] = mapped_column(Integer)
    run_time: Mapped[int | None] = mapped_column(Integer)

    overall_place: Mapped[int | None] = mapped_column(Integer)
    age_group_place: Mapped[int | None] = mapped_column(Integer)

    entered_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    race: Mapped[Race] = relationship(back_populates="official_result")

    #: Segment columns in course order, for derivation and comparison.
    SPLIT_FIELDS = ("swim_time", "t1_time", "bike_time", "t2_time", "run_time")

    def splits(self) -> dict[str, int | None]:
        return {f: getattr(self, f) for f in self.SPLIT_FIELDS}


class RaceFueling(Base):
    """What was consumed during one segment of a race (§2).

    Hangs off Race rather than RecordedActivity because fueling is hand-entered and must
    be recordable for a race with no import yet.
    """

    __tablename__ = "race_fueling"
    __table_args__ = (UniqueConstraint("race_id", "segment", name="uq_fueling_race_segment"),)

    id: Mapped[uuid.UUID] = pk()
    race_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("race.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment: Mapped[Segment] = mapped_column(Enum(Segment, native_enum=False), nullable=False)

    carbs_g: Mapped[float | None] = mapped_column(Float)
    #: [{"name": "Maurten 160", "quantity": 2}]
    products: Mapped[list | None] = mapped_column(JSONBType)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    race: Mapped[Race] = relationship(back_populates="fueling")
