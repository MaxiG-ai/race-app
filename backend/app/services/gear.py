"""Gear detection from sensor serials and mileage (docs/features/gear.md#rules)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ActivityGear, Gear, GearDevice, RecordedActivity
from app.models.enums import GearAssignedBy, GearKind, Segment

# Which segments a kind of gear can be auto-attached to. A Garmin watch still lists the
# power pedals during the run; that mustn't put run kilometres on the bike.
_KIND_SEGMENTS: dict[GearKind, set[Segment]] = {
    GearKind.BIKE: {Segment.BIKE},
    GearKind.SHOES: {Segment.RUN},
    GearKind.WETSUIT: {Segment.SWIM},
    GearKind.OTHER: set(Segment),
}


def sensors(activity: RecordedActivity) -> list[dict[str, Any]]:
    """Paired sensors from import: [{manufacturer, serial_number, label}]."""
    return (activity.raw_data or {}).get("devices", [])


def detect(db: Session, activity: RecordedActivity) -> None:
    """Attach gear for every known serial. A manual assignment is never touched."""
    if any(link.assigned_by is GearAssignedBy.MANUAL for link in activity.gear_links):
        return
    linked = {link.gear_id for link in activity.gear_links}
    for sensor in sensors(activity):
        device = db.scalar(
            select(GearDevice).where(
                GearDevice.manufacturer == sensor["manufacturer"],
                GearDevice.serial_number == sensor["serial_number"],
            )
        )
        if device is None or device.gear_id in linked:
            continue
        gear = db.get(Gear, device.gear_id)
        if gear and activity.segment in _KIND_SEGMENTS[gear.kind]:
            activity.gear_links.append(
                ActivityGear(gear_id=gear.id, assigned_by=GearAssignedBy.AUTO_DEVICE)
            )
            linked.add(gear.id)


def backfill(db: Session, device: GearDevice) -> None:
    """Once a serial is named, every recording that carries it gets the gear."""
    needle = [{"manufacturer": device.manufacturer, "serial_number": device.serial_number}]
    carrying = db.scalars(
        select(RecordedActivity).where(RecordedActivity.raw_data["devices"].contains(needle))
    )
    for activity in carrying:
        detect(db, activity)


def unknown_devices(db: Session) -> list[dict[str, Any]]:
    known = {(d.manufacturer, d.serial_number) for d in db.scalars(select(GearDevice))}
    found: dict[tuple[str, str], dict[str, Any]] = {}
    rows = db.execute(
        select(RecordedActivity.raw_data["devices"], RecordedActivity.start_time).where(
            RecordedActivity.raw_data.has_key("devices")
        )
    )
    for devices, start in rows:
        for d in devices or []:
            key = (d["manufacturer"], d["serial_number"])
            if key in known:
                continue
            entry = found.setdefault(key, {**d, "activity_count": 0, "last_seen": start})
            entry["activity_count"] += 1
            entry["last_seen"] = max(entry["last_seen"], start)
    return sorted(found.values(), key=lambda d: d["last_seen"], reverse=True)


def assign_manually(activity: RecordedActivity, gear_ids: list[int]) -> None:
    activity.gear_links.clear()
    for gear_id in dict.fromkeys(gear_ids):
        activity.gear_links.append(ActivityGear(gear_id=gear_id, assigned_by=GearAssignedBy.MANUAL))


def distances(db: Session) -> dict[int, float]:
    """Race distance per gear item: primary recordings only, plus the offset."""
    rows = db.execute(
        select(ActivityGear.gear_id, func.coalesce(func.sum(RecordedActivity.distance_m), 0))
        .join(RecordedActivity, RecordedActivity.id == ActivityGear.recorded_activity_id)
        .where(RecordedActivity.is_primary)
        .group_by(ActivityGear.gear_id)
    )
    return {gear_id: float(total) for gear_id, total in rows}


def last_used_shoes(db: Session) -> Gear | None:
    return db.scalar(
        select(Gear)
        .join(ActivityGear, ActivityGear.gear_id == Gear.id)
        .join(RecordedActivity, RecordedActivity.id == ActivityGear.recorded_activity_id)
        .where(Gear.kind == GearKind.SHOES, Gear.retired_at.is_(None))
        .order_by(RecordedActivity.start_time.desc())
        .limit(1)
    )
