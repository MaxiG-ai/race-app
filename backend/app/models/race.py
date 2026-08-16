from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import RaceType

if TYPE_CHECKING:
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
    race_type: Mapped[RaceType] = mapped_column(Enum(RaceType, name="race_type"), nullable=False)
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
