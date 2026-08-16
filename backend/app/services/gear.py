"""Serial -> gear resolution and distance aggregation (§3 Gear detection, §2).

This module has three jobs:

1. `resolve_gear_for_activity` — called once per persisted `RecordedActivity`
   (import code's entry point; see its docstring for the exact contract).
2. `backfill_gear_for_device` — the payoff of `POST /gear/{id}/devices`: naming a
   serial once retroactively attaches its gear to every past activity that
   recorded it.
3. `total_distance_m` / `suggest_gear_for_segment` — the read-side queries behind
   `GET /gear` (accumulated distance) and the `GearPicker`'s last-used-shoes
   default, since shoes can't be auto-detected (§3).

No denormalised counters anywhere: distance is always a live query over
`activity_gear` joined to `is_primary` `RecordedActivity` rows, per §2.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    ActivityGear,
    Gear,
    GearAssignedBy,
    GearDevice,
    GearKind,
    Race,
    RecordedActivity,
    Segment,
)
from app.services.fit_import import ParsedDevice


def _device_infos_payload(parsed_devices: Sequence[ParsedDevice]) -> list[dict[str, Any]]:
    return [
        {
            "manufacturer": d.manufacturer,
            "serial_number": str(d.serial_number),
            "product": d.product,
            "label": d.label,
        }
        for d in parsed_devices
        if d.manufacturer is not None and d.serial_number is not None
    ]


def _record_device_infos(activity: RecordedActivity, infos: list[dict[str, Any]]) -> None:
    """Merge `infos` into `activity.raw_data["device_infos"]` without clobbering
    whatever else import code has stored there (it's the JSONB safety net, per
    `RecordedActivity.raw_data`'s docstring).

    This is the only place the *full* per-session device list is persisted:
    `RecordedActivity.device_key` holds a single "recording device" serial, but a
    session can pair several sensors (Edge head unit + power pedals + Di2 + HR
    strap, all in the one Edge fixture) — `GET /gear/unknown-devices` and the
    serial backfill below both need the whole list, not just one.
    """
    if not infos:
        return
    existing = dict(activity.raw_data or {})
    merged: list[dict[str, Any]] = list(existing.get("device_infos", []))
    seen = {(i.get("manufacturer"), i.get("serial_number")) for i in merged}
    for info in infos:
        key = (info["manufacturer"], info["serial_number"])
        if key not in seen:
            merged.append(info)
            seen.add(key)
    existing["device_infos"] = merged
    activity.raw_data = existing


def _attach_gear(
    db: Session, activity_id: int, gear_id: int, assigned_by: GearAssignedBy
) -> ActivityGear:
    """Upsert one `activity_gear` row. Never downgrades an existing `manual`
    assignment (§3 Gear detection step 4 — the hard rule): a manual row is
    returned untouched rather than overwritten."""
    existing = (
        db.query(ActivityGear)
        .filter(ActivityGear.recorded_activity_id == activity_id, ActivityGear.gear_id == gear_id)
        .one_or_none()
    )
    if existing is not None:
        if existing.assigned_by == GearAssignedBy.MANUAL:
            return existing
        existing.assigned_by = assigned_by
        db.flush()
        return existing
    row = ActivityGear(recorded_activity_id=activity_id, gear_id=gear_id, assigned_by=assigned_by)
    db.add(row)
    db.flush()
    return row


def resolve_gear_for_activity(
    db: Session,
    activity: RecordedActivity,
    parsed_devices: Sequence[ParsedDevice],
) -> list[ActivityGear]:
    """Import-code entry point (§3 Gear detection steps 1-2). Call once per
    `RecordedActivity` right after it has been added and flushed (it must have an
    `id`), passing every `ParsedDevice` paired in that activity's session —
    `ParsedFile.devices` as a whole for a single-sport upload (one session), or
    the session-scoped subset of devices for a multisport file.

    For each device:
      - it is recorded on `activity.raw_data["device_infos"]` regardless of
        whether it resolves, so unmapped serials remain discoverable later;
      - a known serial (`GearDevice.manufacturer` + `.serial_number` match) gets
        its `Gear` attached via an `assigned_by=auto_device` `ActivityGear` row,
        unless that (activity, gear) pair is already `manual` (never overwritten).

    Returns the `ActivityGear` rows that now exist for a known serial (created or
    already `auto_device`); does not flush unrelated changes beyond what it
    itself makes. Caller is still responsible for committing.
    """
    infos = _device_infos_payload(parsed_devices)
    _record_device_infos(activity, infos)

    touched: list[ActivityGear] = []
    for device in parsed_devices:
        if device.manufacturer is None or device.serial_number is None:
            continue
        gear_device = (
            db.query(GearDevice)
            .filter(
                GearDevice.manufacturer == device.manufacturer,
                GearDevice.serial_number == str(device.serial_number),
            )
            .one_or_none()
        )
        if gear_device is None:
            continue
        touched.append(
            _attach_gear(db, activity.id, gear_device.gear_id, GearAssignedBy.AUTO_DEVICE)
        )
    return touched


def backfill_gear_for_device(db: Session, user_id: int, gear_device: GearDevice) -> int:
    """`POST /gear/{id}/devices` payoff: naming a serial once backfills every past
    activity (owned by `user_id`, via its race) that recorded it — matching
    either the single `device_key` column or the fuller `raw_data["device_infos"]`
    list left by `resolve_gear_for_activity` at import time.

    Respects the manual-override rule (via `_attach_gear`). Returns the number of
    activities newly/still attached (a `manual` slot that's skipped doesn't
    count).
    """
    key = f"{gear_device.manufacturer}:{gear_device.serial_number}"
    activities = (
        db.query(RecordedActivity)
        .join(Race, Race.id == RecordedActivity.race_id)
        .filter(Race.user_id == user_id)
        .all()
    )
    touched = 0
    for activity in activities:
        matches_device_key = activity.device_key == key
        device_infos = (activity.raw_data or {}).get("device_infos", [])
        matches_raw = any(
            info.get("manufacturer") == gear_device.manufacturer
            and info.get("serial_number") == gear_device.serial_number
            for info in device_infos
        )
        if not (matches_device_key or matches_raw):
            continue
        attached = _attach_gear(db, activity.id, gear_device.gear_id, GearAssignedBy.AUTO_DEVICE)
        if attached.assigned_by == GearAssignedBy.AUTO_DEVICE:
            touched += 1
    return touched


def unknown_devices(db: Session, user_id: int) -> list[dict[str, Any]]:
    """`GET /gear/unknown-devices` (§3 step 3): sensor serials seen in this user's
    imports with no `GearDevice` mapping yet, aggregated from
    `raw_data["device_infos"]` across all their activities — e.g. surfaces
    `favero_electronics 1753485582`, with how many activities carry it and when
    it was first seen, enough context for the user to recognise it.
    """
    known = {(gd.manufacturer, gd.serial_number) for gd in db.query(GearDevice).all()}
    activities = (
        db.query(RecordedActivity)
        .join(Race, Race.id == RecordedActivity.race_id)
        .filter(Race.user_id == user_id)
        .order_by(RecordedActivity.start_time)
        .all()
    )
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for activity in activities:
        for info in (activity.raw_data or {}).get("device_infos", []):
            manufacturer, serial = info.get("manufacturer"), info.get("serial_number")
            if manufacturer is None or serial is None or (manufacturer, serial) in known:
                continue
            entry = agg.setdefault(
                (manufacturer, serial),
                {
                    "manufacturer": manufacturer,
                    "serial_number": serial,
                    "first_seen_at": activity.start_time,
                    "activity_count": 0,
                },
            )
            entry["activity_count"] += 1
            if activity.start_time < entry["first_seen_at"]:
                entry["first_seen_at"] = activity.start_time
    return sorted(agg.values(), key=lambda e: e["first_seen_at"])


def total_distance_m(db: Session, gear: Gear) -> float:
    """§2: distance per gear is a query, never a stored counter — sum
    `distance_m` over `activity_gear` joined to `is_primary` `RecordedActivity`
    rows, plus `distance_offset_m` for mileage accrued before tracking started. A
    gear item with no primary-recording assignments reports exactly its offset.
    """
    summed = (
        db.query(func.coalesce(func.sum(RecordedActivity.distance_m), 0.0))
        .join(ActivityGear, ActivityGear.recorded_activity_id == RecordedActivity.id)
        .filter(ActivityGear.gear_id == gear.id, RecordedActivity.is_primary.is_(True))
        .scalar()
    )
    return gear.distance_offset_m + float(summed or 0.0)


def suggest_gear_for_segment(
    db: Session,
    user_id: int,
    *,
    kind: GearKind,
    segment: Segment,
    exclude_activity_id: int | None = None,
) -> Gear | None:
    """Shoes can't be auto-detected (§3: no sensor, and Apple lists only the
    watch) — the `run` segment's gear is manual, with the last-used pair of
    `kind` offered as the default. Picks the most recently *used* (by the
    recording activity's `start_time`, not when it was assigned) non-retired
    gear of `kind` that has ever been attached to a `segment` activity.
    """
    query = (
        db.query(Gear)
        .join(ActivityGear, ActivityGear.gear_id == Gear.id)
        .join(RecordedActivity, RecordedActivity.id == ActivityGear.recorded_activity_id)
        .filter(
            Gear.user_id == user_id,
            Gear.kind == kind,
            Gear.retired_at.is_(None),
            RecordedActivity.segment == segment,
        )
    )
    if exclude_activity_id is not None:
        query = query.filter(RecordedActivity.id != exclude_activity_id)
    return query.order_by(RecordedActivity.start_time.desc()).first()


def isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
