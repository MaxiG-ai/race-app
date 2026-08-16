from datetime import date

from pydantic import BaseModel, ConfigDict

from app.models.enums import GearAssignedBy, GearKind


class GearDeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    gear_id: int
    manufacturer: str
    serial_number: str
    label: str | None


class GearDeviceMapped(GearDeviceRead):
    """The `POST /gear/{id}/devices` response. Carries `backfilled_activity_count`
    because the backfill *is* the payoff of naming a device (§3 Gear detection): the
    user names a serial once and every past import carrying it resolves. Silently
    updating history would leave them unable to tell whether it worked."""

    backfilled_activity_count: int


class GearDeviceCreate(BaseModel):
    """`POST /gear/{id}/devices` — maps a serial to a gear item; backfills past
    activities carrying that serial (§3 Gear detection)."""

    manufacturer: str
    serial_number: str
    label: str | None = None


class GearRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: GearKind
    name: str
    brand: str | None
    model: str | None
    purchased_at: date | None
    retired_at: date | None
    distance_offset_m: float
    notes: str | None
    devices: list[GearDeviceRead] = []


class GearListItem(GearRead):
    """`GET /gear` — inventory with accumulated distance per item (§2), computed
    over `activity_gear` joined to primary `RecordedActivity` rows, plus
    `distance_offset_m`. Not a stored column."""

    total_distance_m: float


class GearCreate(BaseModel):
    kind: GearKind
    name: str
    brand: str | None = None
    model: str | None = None
    purchased_at: date | None = None
    distance_offset_m: float = 0.0
    notes: str | None = None


class GearUpdate(BaseModel):
    """`PATCH /gear/{id}` — edit or retire a gear item; all fields optional."""

    kind: GearKind | None = None
    name: str | None = None
    brand: str | None = None
    model: str | None = None
    purchased_at: date | None = None
    retired_at: date | None = None
    distance_offset_m: float | None = None
    notes: str | None = None


class UnknownDeviceRead(BaseModel):
    """`GET /gear/unknown-devices` — a sensor serial seen in imports with no gear
    mapping yet (§3 Gear detection step 3)."""

    manufacturer: str
    serial_number: str
    first_seen_at: str | None = None
    activity_count: int


class ActivityGearRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recorded_activity_id: int
    gear_id: int
    assigned_by: GearAssignedBy
    gear: GearRead | None = None


class ActivityGearAssign(BaseModel):
    """Gear-assignment half of `PATCH /activities/{id}`."""

    gear_id: int
    assigned_by: GearAssignedBy = GearAssignedBy.MANUAL
