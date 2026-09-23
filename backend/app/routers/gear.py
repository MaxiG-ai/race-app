"""Gear inventory and sensor mapping (docs/features/gear.md). Owner only."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.access import DB, owner_router
from app.models import Gear, GearDevice
from app.schemas.gear import DeviceIn, GearIn, GearOut, GearPatch, UnknownDevice
from app.services import gear

write = owner_router()


def _out(item: Gear, distances: dict[int, float]) -> GearOut:
    out = GearOut.model_validate(item)
    out.distance_m = item.distance_offset_m + distances.get(item.id, 0)
    return out


def _get(db: DB, gear_id: int) -> Gear:
    item = db.get(Gear, gear_id)
    if item is None:
        raise HTTPException(404, "Gear not found.")
    return item


@write.get("/gear", response_model=list[GearOut])
def inventory(db: DB):
    distances = gear.distances(db)
    items = db.scalars(select(Gear).order_by(Gear.retired_at.nulls_first(), Gear.kind, Gear.name))
    return [_out(item, distances) for item in items]


@write.get("/gear/unknown-devices", response_model=list[UnknownDevice])
def unknown_devices(db: DB):
    return gear.unknown_devices(db)


@write.get("/gear/last-used-shoes", response_model=GearOut | None)
def last_used_shoes(db: DB):
    item = gear.last_used_shoes(db)
    return _out(item, gear.distances(db)) if item else None


@write.post("/gear", response_model=GearOut, status_code=status.HTTP_201_CREATED)
def create(body: GearIn, db: DB):
    item = Gear(**body.model_dump())
    db.add(item)
    db.commit()
    return _out(item, {})


@write.patch("/gear/{gear_id}", response_model=GearOut)
def patch(gear_id: int, body: GearPatch, db: DB):
    item = _get(db, gear_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        if key in {"kind", "name", "distance_offset_m"} and value is None:
            raise HTTPException(422, f"{key} can't be empty.")
        setattr(item, key, value)
    db.commit()
    return _out(item, gear.distances(db))


@write.post("/gear/{gear_id}/devices", response_model=GearOut)
def map_device(gear_id: int, body: DeviceIn, db: DB):
    """Name a serial; every past recording that carries it gets this gear."""
    item = _get(db, gear_id)
    device = GearDevice(gear_id=item.id, **body.model_dump())
    db.add(device)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "That sensor is already mapped to gear.") from exc
    gear.backfill(db, device)
    db.commit()
    db.refresh(item)
    return _out(item, gear.distances(db))
