"""The import path must actually run gear detection (§3 Gear detection steps 1-3).

`persistence.persist_upload_sessions` and `services.gear.resolve_gear_for_activity`
were built independently, so these tests cover the join between them: uploading a
file with paired sensors has to surface those serials, and a serial already named
has to resolve to its gear automatically — scoped to the uploading user.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.password import hash_password
from app.models import ActivityGear, Gear, GearDevice, RecordedActivity, User
from app.models.enums import GearAssignedBy, GearKind
from tests.conftest import fixture_path

PASSWORD = "correct horse battery staple"

# The Edge 540 fixture pairs four real sensors; the power meter is the one whose
# serial identifies a bike most unambiguously (§3 Gear detection).
EDGE_FIXTURE = "single_bike_2026-06-21_edge540.fit"
# The same race, recorded by the watch — the multisport file that creates the race
# the Edge's bike leg attaches to (fixtures README).
WATCH_FIXTURE = "strava_export_2026-06-21_multisport.fit"
FAVERO_SERIAL = "1753485582"
FAVERO_MANUFACTURER = "favero_electronics"


@pytest.fixture(autouse=True)
def _isolate_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")


def _make_user(db: Session, email: str) -> User:
    user = User(email=email, password_hash=hash_password(PASSWORD), is_active=True)
    db.add(user)
    db.commit()
    return user


def _login(client: TestClient, email: str) -> None:
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


def _upload(client: TestClient, name: str, *, race_id: int | None = None) -> dict:
    kwargs: dict = {
        "files": [("files", (name, fixture_path(name).read_bytes(), "application/octet-stream"))]
    }
    if race_id is not None:
        kwargs["data"] = {"race_id": str(race_id)}
    response = client.post("/api/uploads", **kwargs)
    assert response.status_code == 201, response.text
    return response.json()[0]


def _upload_edge(client: TestClient) -> None:
    """The Edge file is single-sport, so on its own it only reaches the tray and
    produces no activities. Gear hangs off `RecordedActivity`, so the realistic path
    — and the one §3 describes — is the watch recording the race and the Edge
    attached to its bike leg."""
    race_id = _upload(client, WATCH_FIXTURE)["race"]["id"]
    _upload(client, EDGE_FIXTURE, race_id=race_id)


def test_import_surfaces_paired_sensor_serials_as_unknown_devices(
    client: TestClient, db: Session
) -> None:
    """An unnamed serial is not an error — it becomes a prompt (§3 step 3)."""
    _make_user(db, "athlete@example.com")
    _login(client, "athlete@example.com")
    _upload_edge(client)

    unknown = client.get("/api/gear/unknown-devices").json()
    seen = {(d["manufacturer"], d["serial_number"]) for d in unknown}
    assert (FAVERO_MANUFACTURER, FAVERO_SERIAL) in seen, (
        f"the Edge fixture's power meter serial should be offered for naming; got {seen}"
    )


def test_import_auto_attaches_gear_for_an_already_named_serial(
    client: TestClient, db: Session
) -> None:
    """Step 2: a known serial resolves to its gear with assigned_by=auto_device,
    which is what makes naming a device once enough."""
    user = _make_user(db, "athlete@example.com")
    bike = Gear(user_id=user.id, kind=GearKind.BIKE, name="Canyon Speedmax (race)")
    db.add(bike)
    db.flush()
    db.add(
        GearDevice(
            gear_id=bike.id,
            manufacturer=FAVERO_MANUFACTURER,
            serial_number=FAVERO_SERIAL,
            label="Assioma pedals",
        )
    )
    db.commit()

    _login(client, "athlete@example.com")
    _upload_edge(client)

    assignments = (
        db.query(ActivityGear).filter(ActivityGear.gear_id == bike.id).all()  # noqa: E501
    )
    assert assignments, "a named serial should have attached its gear at import time"
    assert all(a.assigned_by == GearAssignedBy.AUTO_DEVICE for a in assignments)

    # And the serial no longer needs naming.
    unknown = client.get("/api/gear/unknown-devices").json()
    seen = {(d["manufacturer"], d["serial_number"]) for d in unknown}
    assert (FAVERO_MANUFACTURER, FAVERO_SERIAL) not in seen


def test_another_users_mapping_never_claims_this_users_activity(
    client: TestClient, db: Session
) -> None:
    """§4 Tenancy. Serials are only practically unique, so a stranger's mapping of
    the same serial must neither attach their gear to this import nor suppress this
    user's prompt to name it — a serial that is never prompted is never named, and
    detection would silently never fire."""
    stranger = _make_user(db, "stranger@example.com")
    their_bike = Gear(user_id=stranger.id, kind=GearKind.BIKE, name="Someone else's bike")
    db.add(their_bike)
    db.flush()
    db.add(
        GearDevice(
            gear_id=their_bike.id,
            manufacturer=FAVERO_MANUFACTURER,
            serial_number=FAVERO_SERIAL,
        )
    )
    db.commit()

    _make_user(db, "athlete@example.com")
    _login(client, "athlete@example.com")
    _upload_edge(client)

    leaked = (
        db.query(ActivityGear)
        .join(RecordedActivity, RecordedActivity.id == ActivityGear.recorded_activity_id)
        .filter(ActivityGear.gear_id == their_bike.id)
        .count()
    )
    assert leaked == 0, "another user's gear was attached to this user's activity"

    unknown = client.get("/api/gear/unknown-devices").json()
    seen = {(d["manufacturer"], d["serial_number"]) for d in unknown}
    assert (FAVERO_MANUFACTURER, FAVERO_SERIAL) in seen, (
        "a stranger's mapping suppressed this user's prompt to name their own sensor"
    )


def test_two_users_can_each_map_the_same_serial(client: TestClient, db: Session) -> None:
    """§4 Tenancy. Serials are only practically unique, so uniqueness is scoped to
    the gear item rather than global — otherwise whoever mapped a serial first would
    permanently block every other account from naming their own identical sensor,
    and gear detection could never work for them."""
    stranger = _make_user(db, "stranger@example.com")
    their_bike = Gear(user_id=stranger.id, kind=GearKind.BIKE, name="Their bike")
    db.add(their_bike)
    db.flush()
    db.add(
        GearDevice(
            gear_id=their_bike.id,
            manufacturer=FAVERO_MANUFACTURER,
            serial_number=FAVERO_SERIAL,
        )
    )
    db.commit()

    user = _make_user(db, "athlete@example.com")
    my_bike = Gear(user_id=user.id, kind=GearKind.BIKE, name="My bike")
    db.add(my_bike)
    db.commit()

    _login(client, "athlete@example.com")
    response = client.post(
        f"/api/gear/{my_bike.id}/devices",
        json={"manufacturer": FAVERO_MANUFACTURER, "serial_number": FAVERO_SERIAL},
    )
    assert response.status_code == 201, response.text
    assert "backfilled_activity_count" in response.json()


def test_mapping_the_same_serial_twice_for_one_user_is_refused(
    client: TestClient, db: Session
) -> None:
    """Within one account the same serial must not name two different items —
    that is a genuine conflict, unlike the cross-account case above."""
    user = _make_user(db, "athlete@example.com")
    first = Gear(user_id=user.id, kind=GearKind.BIKE, name="Race bike")
    second = Gear(user_id=user.id, kind=GearKind.BIKE, name="Training bike")
    db.add_all([first, second])
    db.commit()

    _login(client, "athlete@example.com")
    payload = {"manufacturer": FAVERO_MANUFACTURER, "serial_number": FAVERO_SERIAL}
    assert client.post(f"/api/gear/{first.id}/devices", json=payload).status_code == 201
    assert client.post(f"/api/gear/{second.id}/devices", json=payload).status_code == 409
