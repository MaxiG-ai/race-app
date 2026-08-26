"""Gear, the device serials that identify it, and its use per recorded segment (§3)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, Enum, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    GearAssignedBy,
    GearKind,
    created_at_col,
    pk,
    updated_at_col,
)


class Gear(Base):
    """A bike, a pair of shoes, a wetsuit.

    Its own ownership root, like Upload — gear outlives any one race.
    """

    __tablename__ = "gear"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[GearKind] = mapped_column(Enum(GearKind, native_enum=False), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(80))
    purchased_at: Mapped[date | None] = mapped_column(Date)
    #: Retired gear is hidden from pickers but kept in history.
    retired_at: Mapped[date | None] = mapped_column(Date)
    #: Mileage accrued before tracking started. Distance is otherwise a query over
    #: activity_gear, never a stored counter that can drift.
    distance_offset_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    devices: Mapped[list[GearDevice]] = relationship(
        back_populates="gear", cascade="all, delete-orphan"
    )


class GearDevice(Base):
    """A sensor serial mapped to a gear item.

    Serials are stable across rides, so a bike resolves itself after being named once.
    """

    __tablename__ = "gear_device"
    __table_args__ = (
        UniqueConstraint("user_id", "device_key", name="uq_gear_device_user_key"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gear_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gear.id", ondelete="CASCADE"), nullable=False
    )
    #: "manufacturer:serial", e.g. "favero_electronics:1753485582"
    device_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(64))
    serial_number: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(120))

    created_at: Mapped[datetime] = created_at_col()

    gear: Mapped[Gear] = relationship(back_populates="devices")


class ActivityGear(Base):
    """Which gear was used for which recorded segment."""

    __tablename__ = "activity_gear"
    __table_args__ = (
        UniqueConstraint("recorded_activity_id", "gear_id", name="uq_activity_gear"),
    )

    id: Mapped[uuid.UUID] = pk()
    recorded_activity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recorded_activity.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gear_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gear.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_by: Mapped[GearAssignedBy] = mapped_column(
        Enum(GearAssignedBy, native_enum=False), nullable=False
    )
    created_at: Mapped[datetime] = created_at_col()
