"""M6.5 Gear: CRUD, retirement, distance aggregation, serial detection/backfill,
the manual-override rule, and tenancy (§2, §3 Gear detection, §4)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.password import hash_password
from app.models import (
    ActivityGear,
    Gear,
    GearAssignedBy,
    GearDevice,
    GearKind,
    Race,
    RecordedActivity,
    Segment,
    UploadSource,
    User,
)
from app.services.fit_import import parse_fit
from app.services.gear import backfill_gear_for_device, resolve_gear_for_activity, total_distance_m
from tests.conftest import fixture_path

EMAIL = "athlete@example.com"
PASSWORD = "correct-horse-battery-staple"


def _make_user(db: Session, *, email: str = EMAIL) -> User:
    user = User(email=email, password_hash=hash_password(PASSWORD))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, email: str = EMAIL) -> None:
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def _make_race(db: Session, *, owner: User, name: str = "Test Race") -> Race:
    race = Race(user_id=owner.id, name=name, date=date(2026, 6, 21))
    db.add(race)
    db.commit()
    db.refresh(race)
    return race


def _make_activity(
    db: Session,
    *,
    race: Race,
    segment: Segment,
    is_primary: bool = True,
    distance_m: float = 20_000.0,
    start_time: datetime | None = None,
    device_key: str | None = None,
) -> RecordedActivity:
    activity = RecordedActivity(
        race_id=race.id,
        segment=segment,
        source=UploadSource.FIT_UPLOAD,
        start_time=start_time or datetime(2026, 6, 21, 7, 0, tzinfo=UTC),
        elapsed_time=3600,
        distance_m=distance_m,
        is_primary=is_primary,
        device_key=device_key,
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


@pytest.fixture
def _edge_devices():
    """Real ParsedDevice values from the Edge fixture (§3 Gear detection table):
    Garmin Edge 540 head unit, Favero Assioma pedals, Shimano Di2, Polar strap."""
    parsed = parse_fit(fixture_path("single_bike_2026-06-21_edge540.fit"))
    return parsed.devices


FAVERO = ("favero_electronics", "1753485582")
SHIMANO = ("shimano", "2783122544")
GARMIN = ("garmin", "3449642804")
POLAR = ("polar_electro", "584467901")


# --- CRUD ------------------------------------------------------------------


def test_create_list_and_patch_gear(client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(client)

    create = client.post(
        "/api/gear",
        json={"kind": "bike", "name": "Canyon Speedmax", "brand": "Canyon"},
    )
    assert create.status_code == 201
    gear_id = create.json()["id"]
    assert create.json()["retired_at"] is None

    listing = client.get("/api/gear")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["total_distance_m"] == 0.0

    patched = client.patch(f"/api/gear/{gear_id}", json={"name": "Canyon Speedmax (race)"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Canyon Speedmax (race)"


def test_retire_hides_from_default_list_but_keeps_distance(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)

    gear = Gear(user_id=user.id, kind=GearKind.BIKE, name="Old TT bike", distance_offset_m=1_000.0)
    db.add(gear)
    db.commit()
    db.refresh(gear)

    activity = _make_activity(db, race=race, segment=Segment.BIKE, distance_m=5_000.0)
    db.add(
        ActivityGear(
            recorded_activity_id=activity.id, gear_id=gear.id, assigned_by=GearAssignedBy.MANUAL
        )
    )
    db.commit()

    retire = client.patch(f"/api/gear/{gear.id}", json={"retired_at": "2026-06-22"})
    assert retire.status_code == 200
    assert retire.json()["retired_at"] == "2026-06-22"

    default_list = client.get("/api/gear")
    assert default_list.json() == []

    with_retired = client.get("/api/gear", params={"include_retired": "true"})
    assert len(with_retired.json()) == 1
    assert with_retired.json()[0]["total_distance_m"] == 6_000.0


def test_distance_sums_primary_only_plus_offset(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)

    gear = Gear(user_id=user.id, kind=GearKind.BIKE, name="Bike", distance_offset_m=500.0)
    db.add(gear)
    db.commit()
    db.refresh(gear)

    primary = _make_activity(
        db, race=race, segment=Segment.BIKE, is_primary=True, distance_m=1_000.0
    )
    # A second, non-primary recording of the *same* segment would collide with
    # the partial unique index if both were primary — use a different segment
    # to stand in for "a non-primary recording elsewhere that must not count".
    non_primary = _make_activity(
        db, race=race, segment=Segment.RUN, is_primary=False, distance_m=9_999.0
    )
    db.add(
        ActivityGear(
            recorded_activity_id=primary.id, gear_id=gear.id, assigned_by=GearAssignedBy.MANUAL
        )
    )
    db.add(
        ActivityGear(
            recorded_activity_id=non_primary.id, gear_id=gear.id, assigned_by=GearAssignedBy.MANUAL
        )
    )
    db.commit()

    assert total_distance_m(db, gear) == 500.0 + 1_000.0

    listing = client.get("/api/gear").json()
    assert listing[0]["total_distance_m"] == 1_500.0


# --- serial detection / unknown devices -------------------------------------


def test_unknown_devices_lists_serials_with_context(
    client: TestClient, db: Session, _edge_devices
) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)
    activity = _make_activity(db, race=race, segment=Segment.BIKE)

    resolve_gear_for_activity(db, activity, _edge_devices)
    db.commit()

    response = client.get("/api/gear/unknown-devices")
    assert response.status_code == 200
    seen = {(d["manufacturer"], d["serial_number"]) for d in response.json()}
    assert seen == {FAVERO, SHIMANO, GARMIN, POLAR}
    for d in response.json():
        assert d["activity_count"] == 1
        assert d["first_seen_at"] is not None


def test_known_serial_auto_attaches_gear_on_resolve(
    client: TestClient, db: Session, _edge_devices
) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)
    activity = _make_activity(db, race=race, segment=Segment.BIKE)

    bike = Gear(user_id=user.id, kind=GearKind.BIKE, name="Canyon")
    db.add(bike)
    db.commit()
    db.refresh(bike)
    db.add(GearDevice(gear_id=bike.id, manufacturer=FAVERO[0], serial_number=FAVERO[1]))
    db.commit()

    resolve_gear_for_activity(db, activity, _edge_devices)
    db.commit()

    detail = client.get(f"/api/activities/{activity.id}")
    assert detail.status_code == 200
    gear_ids = {g["gear_id"]: g["assigned_by"] for g in detail.json()["gear"]}
    assert gear_ids == {bike.id: "auto_device"}

    # The other three serials are still unmapped.
    unknown = {
        (d["manufacturer"], d["serial_number"])
        for d in client.get("/api/gear/unknown-devices").json()
    }
    assert unknown == {SHIMANO, GARMIN, POLAR}


def test_mapping_a_serial_backfills_past_activities(
    client: TestClient, db: Session, _edge_devices
) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)

    # Two past activities, both recorded the Favero pedals, before any gear
    # mapping existed.
    activity_1 = _make_activity(db, race=race, segment=Segment.BIKE)
    race_2 = _make_race(db, owner=user, name="Another race")
    activity_2 = _make_activity(db, race=race_2, segment=Segment.BIKE)
    resolve_gear_for_activity(db, activity_1, _edge_devices)
    resolve_gear_for_activity(db, activity_2, _edge_devices)
    db.commit()

    bike = Gear(user_id=user.id, kind=GearKind.BIKE, name="Canyon")
    db.add(bike)
    db.commit()
    db.refresh(bike)

    response = client.post(
        f"/api/gear/{bike.id}/devices",
        json={"manufacturer": FAVERO[0], "serial_number": FAVERO[1], "label": "Assioma pedals"},
    )
    assert response.status_code == 201

    for activity_id in (activity_1.id, activity_2.id):
        detail = client.get(f"/api/activities/{activity_id}").json()
        gear_ids = {g["gear_id"]: g["assigned_by"] for g in detail["gear"]}
        assert gear_ids[bike.id] == "auto_device"


def test_manual_assignment_survives_a_later_auto_detection(
    client: TestClient, db: Session, _edge_devices
) -> None:
    """§3 step 4, the hard rule: auto-detection must never overwrite a manual
    assignment for the same (activity, gear) pair."""
    user = _make_user(db)
    race = _make_race(db, owner=user)
    activity = _make_activity(db, race=race, segment=Segment.BIKE)

    bike = Gear(user_id=user.id, kind=GearKind.BIKE, name="Canyon")
    db.add(bike)
    db.commit()
    db.refresh(bike)

    # The user manually assigned this gear to this activity (e.g. correcting a
    # wrong guess) before the serial was ever mapped.
    manual = ActivityGear(
        recorded_activity_id=activity.id, gear_id=bike.id, assigned_by=GearAssignedBy.MANUAL
    )
    db.add(manual)
    db.add(GearDevice(gear_id=bike.id, manufacturer=FAVERO[0], serial_number=FAVERO[1]))
    db.commit()

    # Re-running detection against the same activity must not downgrade it.
    resolve_gear_for_activity(db, activity, _edge_devices)
    db.commit()

    row = (
        db.query(ActivityGear)
        .filter(ActivityGear.recorded_activity_id == activity.id, ActivityGear.gear_id == bike.id)
        .one()
    )
    assert row.assigned_by == GearAssignedBy.MANUAL


def test_manual_assignment_survives_backfill(client: TestClient, db: Session) -> None:
    """Same rule, exercised through the backfill path (`POST /gear/{id}/devices`)
    rather than a direct re-resolve."""
    user = _make_user(db)
    race = _make_race(db, owner=user)
    activity = _make_activity(
        db, race=race, segment=Segment.BIKE, device_key="favero_electronics:1753485582"
    )

    correct_bike = Gear(user_id=user.id, kind=GearKind.BIKE, name="Correct bike")
    wrong_bike = Gear(user_id=user.id, kind=GearKind.BIKE, name="Wrong bike guess")
    db.add_all([correct_bike, wrong_bike])
    db.commit()
    db.refresh(correct_bike)
    db.refresh(wrong_bike)

    db.add(
        ActivityGear(
            recorded_activity_id=activity.id,
            gear_id=wrong_bike.id,
            assigned_by=GearAssignedBy.MANUAL,
        )
    )
    db.commit()

    device = GearDevice(gear_id=correct_bike.id, manufacturer=FAVERO[0], serial_number=FAVERO[1])
    db.add(device)
    db.commit()
    db.refresh(device)

    backfill_gear_for_device(db, user.id, device)
    db.commit()

    wrong_row = (
        db.query(ActivityGear)
        .filter(
            ActivityGear.recorded_activity_id == activity.id, ActivityGear.gear_id == wrong_bike.id
        )
        .one()
    )
    assert wrong_row.assigned_by == GearAssignedBy.MANUAL


# --- tenancy -----------------------------------------------------------------


def test_gear_list_and_unknown_devices_are_scoped_by_user(client: TestClient, db: Session) -> None:
    owner = _make_user(db, email="owner@example.com")
    _make_user(db, email="intruder@example.com")
    race = _make_race(db, owner=owner)
    activity = _make_activity(db, race=race, segment=Segment.BIKE)
    resolve_gear_for_activity(
        db,
        activity,
        parse_fit(fixture_path("single_bike_2026-06-21_edge540.fit")).devices,
    )
    gear = Gear(user_id=owner.id, kind=GearKind.BIKE, name="Owner's bike")
    db.add(gear)
    db.commit()

    _login(client, email="intruder@example.com")

    assert client.get("/api/gear").json() == []
    assert client.get("/api/gear/unknown-devices").json() == []


def test_second_user_gets_404_mapping_a_device_to_first_users_gear(
    client: TestClient, db: Session
) -> None:
    owner = _make_user(db, email="owner@example.com")
    _make_user(db, email="intruder@example.com")
    gear = Gear(user_id=owner.id, kind=GearKind.BIKE, name="Owner's bike")
    db.add(gear)
    db.commit()
    db.refresh(gear)

    _login(client, email="intruder@example.com")

    response = client.post(
        f"/api/gear/{gear.id}/devices",
        json={"manufacturer": "favero_electronics", "serial_number": "1753485582"},
    )
    assert response.status_code == 404
