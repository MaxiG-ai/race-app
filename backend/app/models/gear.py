from __future__ import annotations

from datetime import date as date_
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import GearAssignedBy, GearKind

if TYPE_CHECKING:
    from app.models.recorded_activity import RecordedActivity
    from app.models.user import User


class Gear(Base):
    """Equipment tracking — which bike/shoes were used, and how much distance each
    has accumulated. Own ownership root, like `Upload` — gear outlives any one race
    (§4 Tenancy)."""

    __tablename__ = "gear"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    kind: Mapped[GearKind] = mapped_column(Enum(GearKind, name="gear_kind"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(255))
    purchased_at: Mapped[date_ | None] = mapped_column(Date)
    # Retired gear is hidden from pickers, kept in history.
    retired_at: Mapped[date_ | None] = mapped_column(Date)
    # Float to match RecordedActivity.distance_m — accumulated distance (offset +
    # summed activity_gear distance) never needs an int/float reconciliation.
    distance_offset_m: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="gear")
    devices: Mapped[list[GearDevice]] = relationship(
        back_populates="gear", cascade="all, delete-orphan"
    )
    activity_gear: Mapped[list[ActivityGear]] = relationship(back_populates="gear")


class GearDevice(Base):
    """Serial -> gear mapping, populated as devices are recognised (§3 Gear detection)."""

    __tablename__ = "gear_devices"
    __table_args__ = (
        UniqueConstraint(
            "manufacturer", "serial_number", name="uq_gear_device_manufacturer_serial"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    gear_id: Mapped[int] = mapped_column(ForeignKey("gear.id", ondelete="CASCADE"), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(255), nullable=False)
    serial_number: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))

    gear: Mapped[Gear] = relationship(back_populates="devices")


class ActivityGear(Base):
    """Association: which gear was used for which recorded segment (§2)."""

    __tablename__ = "activity_gear"

    recorded_activity_id: Mapped[int] = mapped_column(
        ForeignKey("recorded_activities.id", ondelete="CASCADE"), primary_key=True
    )
    gear_id: Mapped[int] = mapped_column(
        ForeignKey("gear.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[GearAssignedBy] = mapped_column(
        Enum(GearAssignedBy, name="gear_assigned_by"), nullable=False
    )

    recorded_activity: Mapped[RecordedActivity] = relationship(back_populates="activity_gear")
    gear: Mapped[Gear] = relationship(back_populates="activity_gear")
