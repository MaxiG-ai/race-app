from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import GearKind
from app.schemas.common import Metres, Model, Patch


class DeviceOut(Model):
    id: int
    manufacturer: str
    serial_number: str
    label: str | None


class GearOut(Model):
    id: int
    kind: GearKind
    name: str
    brand: str | None
    model: str | None
    purchased_at: date | None
    retired_at: date | None
    distance_offset_m: float
    notes: str | None
    distance_m: float = 0
    devices: list[DeviceOut]


class GearIn(BaseModel):
    kind: GearKind
    name: str = Field(min_length=1)
    brand: str | None = None
    model: str | None = None
    purchased_at: date | None = None
    distance_offset_m: Metres = 0
    notes: str | None = None


class GearPatch(Patch):
    kind: GearKind | None = None
    name: str | None = Field(default=None, min_length=1)
    brand: str | None = None
    model: str | None = None
    purchased_at: date | None = None
    retired_at: date | None = None
    distance_offset_m: Metres | None = None
    notes: str | None = None


class DeviceIn(BaseModel):
    manufacturer: str
    serial_number: str
    label: str | None = None


class UnknownDevice(BaseModel):
    manufacturer: str
    serial_number: str
    label: str | None
    activity_count: int
    last_seen: datetime
