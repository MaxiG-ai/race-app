from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import ClassificationSource

if TYPE_CHECKING:
    from app.models.race_category import RaceCategory
    from app.models.race_fueling import RaceFueling
    from app.models.recorded_activity import RecordedActivity
    from app.models.upload import Upload
    from app.models.user import User


class Race(Base):
    """Event shell — one row per race. Ownership root for everything hanging off
    `race_id` (§4 Tenancy): `OfficialResult`, `RaceFueling`, `RecordedActivity` and,
    transitively, `LapSplit`/`ActivityGear` carry no `user_id` of their own."""

    __tablename__ = "races"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    location_name: Mapped[str | None] = mapped_column(String(255))
    location_lat: Mapped[float | None] = mapped_column(Float)
    location_lng: Mapped[float | None] = mapped_column(Float)
    # Category is inferred from the measured distances (§9), not chosen on a form.
    race_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("race_categories.id", ondelete="SET NULL")
    )
    # What the classifier would pick, kept *alongside* a manual override rather than
    # being replaced by it: the UI can then say "you set Mitteldistanz; the measurements
    # say Kurzdistanz", which is usually how a wrong distance gets noticed. It also lets
    # a corrected range table be replayed without destroying manual choices.
    auto_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("race_categories.id", ondelete="SET NULL")
    )
    classification_source: Mapped[ClassificationSource] = mapped_column(
        Enum(ClassificationSource, name="classification_source"),
        nullable=False,
        # SQLAlchemy's Enum persists member *names*, so the default must be one too.
        server_default=ClassificationSource.UNCLASSIFIED.name,
    )
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Why the classifier declined, e.g. {"nearest": "kurz", "worst_segment": "bike",
    #: "worst_delta_pct": -15.6} — so the UI can explain rather than just shrug.
    classification_detail: Mapped[dict | None] = mapped_column(JSONB)
    # weather_* is populated by Open-Meteo enrichment (M7), not from the FIT humidity
    # field, which is declared but never populated by either vendor (§3 Traps 10).
    weather_temp_c: Mapped[float | None] = mapped_column(Float)
    weather_humidity_pct: Mapped[float | None] = mapped_column(Float)
    weather_conditions: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="races")
    category: Mapped[RaceCategory | None] = relationship(foreign_keys=[race_category_id])
    auto_category: Mapped[RaceCategory | None] = relationship(foreign_keys=[auto_category_id])
    official_result: Mapped[OfficialResult | None] = relationship(
        back_populates="race", cascade="all, delete-orphan", uselist=False
    )
    fuelings: Mapped[list[RaceFueling]] = relationship(
        back_populates="race", cascade="all, delete-orphan"
    )
    recorded_activities: Mapped[list[RecordedActivity]] = relationship(
        back_populates="race", cascade="all, delete-orphan"
    )
    # Uploads outlive races (§8 original-file retention) — no delete cascade here;
    # Upload.race_id is set NULL by the DB when the race disappears.
    uploads: Mapped[list[Upload]] = relationship(back_populates="race")


class OfficialResult(Base):
    """1:1 with `Race`, manually entered by the user from event timing."""

    __tablename__ = "official_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(
        ForeignKey("races.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # All durations are integer seconds (§2 Conventions); formatting is a frontend concern.
    total_time: Mapped[int] = mapped_column(Integer, nullable=False)
    swim_time: Mapped[int | None] = mapped_column(Integer)
    t1_time: Mapped[int | None] = mapped_column(Integer)
    bike_time: Mapped[int | None] = mapped_column(Integer)
    t2_time: Mapped[int | None] = mapped_column(Integer)
    run_time: Mapped[int | None] = mapped_column(Integer)
    overall_place: Mapped[int | None] = mapped_column(Integer)
    age_group_place: Mapped[int | None] = mapped_column(Integer)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    race: Mapped[Race] = relationship(back_populates="official_result")
