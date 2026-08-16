"""`GET/POST /gear`, `PATCH /gear/{id}`, `GET /gear/unknown-devices`,
`POST /gear/{id}/devices` (§3 Gear detection, §2).

Tenancy: `Gear` is its own ownership root (`Gear.user_id`, §4) — `/gear` and
`/gear/unknown-devices` are scoped by `user_id` directly, never through a race.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import Gear, GearDevice, User
from app.schemas.gear import (
    GearCreate,
    GearDeviceCreate,
    GearDeviceRead,
    GearListItem,
    GearRead,
    GearUpdate,
    UnknownDeviceRead,
)
from app.services.gear import backfill_gear_for_device, isoformat, total_distance_m, unknown_devices

router = APIRouter(prefix="/gear", tags=["gear"], dependencies=[Depends(current_user)])


def _get_owned_gear(db: Session, user: User, gear_id: int) -> Gear:
    """The `Gear`-side counterpart to `get_owned_race` (§4 Tenancy) — filters by
    owner and raises 404, never 403, for someone else's gear."""
    gear = db.query(Gear).filter(Gear.id == gear_id, Gear.user_id == user.id).one_or_none()
    if gear is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gear not found")
    return gear


@router.get("", response_model=list[GearListItem])
def list_gear(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
    include_retired: bool = False,
) -> list[GearListItem]:
    """§2: retired gear is hidden from the default list (kept in history via
    `?include_retired=true`); each item's distance is a live query, never a
    stored counter."""
    query = db.query(Gear).filter(Gear.user_id == user.id)
    if not include_retired:
        query = query.filter(Gear.retired_at.is_(None))
    items = query.order_by(Gear.name).all()
    return [
        GearListItem(
            **GearRead.model_validate(item, from_attributes=True).model_dump(),
            total_distance_m=total_distance_m(db, item),
        )
        for item in items
    ]


@router.post("", response_model=GearRead, status_code=status.HTTP_201_CREATED)
def create_gear(
    payload: GearCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> Gear:
    gear = Gear(user_id=user.id, **payload.model_dump())
    db.add(gear)
    db.commit()
    db.refresh(gear)
    return gear


@router.patch("/{gear_id}", response_model=GearRead)
def update_gear(
    gear_id: int,
    payload: GearUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> Gear:
    """Edit or retire (`retired_at`) a gear item — retiring never touches past
    `activity_gear` rows or the distance they contribute (§2)."""
    gear = _get_owned_gear(db, user, gear_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(gear, field, value)
    db.commit()
    db.refresh(gear)
    return gear


@router.get("/unknown-devices", response_model=list[UnknownDeviceRead])
def list_unknown_devices(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> list[UnknownDeviceRead]:
    """§3 step 3: sensor serials seen in this user's imports with no `GearDevice`
    mapping yet."""
    rows = unknown_devices(db, user.id)
    return [
        UnknownDeviceRead(
            manufacturer=row["manufacturer"],
            serial_number=row["serial_number"],
            first_seen_at=isoformat(row["first_seen_at"]),
            activity_count=row["activity_count"],
        )
        for row in rows
    ]


@router.post(
    "/{gear_id}/devices", response_model=GearDeviceRead, status_code=status.HTTP_201_CREATED
)
def create_gear_device(
    gear_id: int,
    payload: GearDeviceCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> GearDevice:
    """Maps a serial to `gear_id` and backfills every past activity (owned by
    this user) that already recorded it (§3 step 3's payoff) — this is what lets
    the user name a device once and have both past and future imports resolve
    it."""
    gear = _get_owned_gear(db, user, gear_id)

    conflict = (
        db.query(GearDevice)
        .filter(
            GearDevice.manufacturer == payload.manufacturer,
            GearDevice.serial_number == payload.serial_number,
        )
        .one_or_none()
    )
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This device is already mapped to a gear item.",
        )

    device = GearDevice(
        gear_id=gear.id,
        manufacturer=payload.manufacturer,
        serial_number=payload.serial_number,
        label=payload.label,
    )
    db.add(device)
    db.flush()

    backfill_gear_for_device(db, user.id, device)

    db.commit()
    db.refresh(device)
    return device
