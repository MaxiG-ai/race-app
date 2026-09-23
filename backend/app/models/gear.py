"""Gear, sensor mappings and per-segment assignment (docs/features/gear.md#data)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import GearAssignedBy, GearKind


class Gear(Base):
    __tablename__ = "gear"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[GearKind]
    name: Mapped[str]
    brand: Mapped[str | None]
    model: Mapped[str | None]
    purchased_at: Mapped[date | None]
    retired_at: Mapped[date | None]
    distance_offset_m: Mapped[float] = mapped_column(default=0)
    notes: Mapped[str | None]

    devices: Mapped[list[GearDevice]] = relationship(cascade="all, delete-orphan")


class GearDevice(Base):
    __tablename__ = "gear_device"
    __table_args__ = (UniqueConstraint("manufacturer", "serial_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    gear_id: Mapped[int] = mapped_column(ForeignKey("gear.id", ondelete="CASCADE"))
    manufacturer: Mapped[str]
    serial_number: Mapped[str]
    label: Mapped[str | None]


class ActivityGear(Base):
    __tablename__ = "activity_gear"

    recorded_activity_id: Mapped[int] = mapped_column(
        ForeignKey("recorded_activity.id", ondelete="CASCADE"), primary_key=True
    )
    gear_id: Mapped[int] = mapped_column(
        ForeignKey("gear.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[GearAssignedBy]

    gear: Mapped[Gear] = relationship(lazy="joined")
