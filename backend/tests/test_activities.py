"""`GET/PATCH /activities/{id}` (§4 endpoint table): is_primary promotion,
manual gear assignment, the last-used-shoes suggestion, and tenancy."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.password import hash_password
from app.models import (
    ActivityGear,
    Gear,
    GearAssignedBy,
    GearKind,
    Race,
    RecordedActivity,
    Segment,
    UploadSource,
    User,
)

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


def _make_race(db: Session, *, owner: User) -> Race:
    race = Race(
        user_id=owner.id, name="Test Race", date=date(2026, 6, 21)
    )
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
) -> RecordedActivity:
    activity = RecordedActivity(
        race_id=race.id,
        segment=segment,
        source=UploadSource.FIT_UPLOAD,
        start_time=start_time or datetime(2026, 6, 21, 7, 0, tzinfo=UTC),
        elapsed_time=3600,
        distance_m=distance_m,
        is_primary=is_primary,
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


# --- GET ---------------------------------------------------------------------


def test_get_activity_returns_detail(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)
    activity = _make_activity(db, race=race, segment=Segment.BIKE)

    response = client.get(f"/api/activities/{activity.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == activity.id
    assert body["segment"] == "bike"
    assert body["gear"] == []
    assert body["laps"] == []
    # Only run segments ever get a shoe suggestion.
    assert body["suggested_gear_id"] is None


# --- is_primary promotion ----------------------------------------------------


def test_promoting_is_primary_demotes_the_incumbent(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)

    incumbent = _make_activity(db, race=race, segment=Segment.BIKE, is_primary=True)
    challenger = _make_activity(db, race=race, segment=Segment.BIKE, is_primary=False)

    response = client.patch(f"/api/activities/{challenger.id}", json={"is_primary": True})
    assert response.status_code == 200
    assert response.json()["is_primary"] is True

    db.refresh(incumbent)
    db.refresh(challenger)
    assert incumbent.is_primary is False
    assert challenger.is_primary is True


def test_promoting_the_current_primary_is_a_no_op(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)
    primary = _make_activity(db, race=race, segment=Segment.BIKE, is_primary=True)

    response = client.patch(f"/api/activities/{primary.id}", json={"is_primary": True})
    assert response.status_code == 200
    assert response.json()["is_primary"] is True


def test_explicitly_demoting_is_primary(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)
    activity = _make_activity(db, race=race, segment=Segment.BIKE, is_primary=True)

    response = client.patch(f"/api/activities/{activity.id}", json={"is_primary": False})
    assert response.status_code == 200
    assert response.json()["is_primary"] is False


# --- manual gear assignment ---------------------------------------------------


def test_assigning_gear_via_patch_is_manual(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)
    shoes = Gear(user_id=user.id, kind=GearKind.SHOES, name="Vaporfly")
    db.add(shoes)
    db.commit()
    db.refresh(shoes)

    run = _make_activity(db, race=race, segment=Segment.RUN)

    response = client.patch(f"/api/activities/{run.id}", json={"gear_id": shoes.id})
    assert response.status_code == 200
    body = response.json()
    assert [g["gear_id"] for g in body["gear"]] == [shoes.id]
    assert body["gear"][0]["assigned_by"] == "manual"
    # No longer suggested — it's already assigned.
    assert body["suggested_gear_id"] is None


def test_reassigning_gear_of_the_same_kind_replaces_the_previous_pick(
    client: TestClient, db: Session
) -> None:
    user = _make_user(db)
    _login(client)
    race = _make_race(db, owner=user)
    old_shoes = Gear(user_id=user.id, kind=GearKind.SHOES, name="Old Vaporfly")
    new_shoes = Gear(user_id=user.id, kind=GearKind.SHOES, name="New Vaporfly")
    db.add_all([old_shoes, new_shoes])
    db.commit()
    db.refresh(old_shoes)
    db.refresh(new_shoes)

    run = _make_activity(db, race=race, segment=Segment.RUN)
    db.add(
        ActivityGear(
            recorded_activity_id=run.id, gear_id=old_shoes.id, assigned_by=GearAssignedBy.MANUAL
        )
    )
    db.commit()

    response = client.patch(f"/api/activities/{run.id}", json={"gear_id": new_shoes.id})
    assert response.status_code == 200
    gear_ids = [g["gear_id"] for g in response.json()["gear"]]
    assert gear_ids == [new_shoes.id]


def test_gear_id_must_belong_to_the_caller(client: TestClient, db: Session) -> None:
    """Assigning gear on your own activity, but with a gear id you don't own,
    must 404 too — the gear-ownership check is separate from the race one."""
    owner = _make_user(db, email="owner@example.com")
    other_user = _make_user(db, email="other@example.com")
    race = _make_race(db, owner=owner)
    activity = _make_activity(db, race=race, segment=Segment.RUN)
    someone_elses_gear = Gear(user_id=other_user.id, kind=GearKind.SHOES, name="Not yours")
    db.add(someone_elses_gear)
    db.commit()
    db.refresh(someone_elses_gear)

    _login(client, email="owner@example.com")
    response = client.patch(
        f"/api/activities/{activity.id}", json={"gear_id": someone_elses_gear.id}
    )
    assert response.status_code == 404


# --- last-used shoes suggestion ----------------------------------------------


def test_suggested_gear_id_offers_the_last_used_shoes(client: TestClient, db: Session) -> None:
    # Three separate races, each contributing one `run` — a single race can
    # only have one `is_primary` recording per segment (the partial unique
    # index), so distinct past uses of shoes need distinct races here.
    user = _make_user(db)
    _login(client)
    race_1 = _make_race(db, owner=user)
    race_2 = _make_race(db, owner=user)
    race_3 = _make_race(db, owner=user)

    old_shoes = Gear(user_id=user.id, kind=GearKind.SHOES, name="Old Vaporfly")
    new_shoes = Gear(user_id=user.id, kind=GearKind.SHOES, name="New Vaporfly")
    db.add_all([old_shoes, new_shoes])
    db.commit()
    db.refresh(old_shoes)
    db.refresh(new_shoes)

    earlier_run = _make_activity(
        db, race=race_1, segment=Segment.RUN, start_time=datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    )
    later_run = _make_activity(
        db, race=race_2, segment=Segment.RUN, start_time=datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    )
    db.add(
        ActivityGear(
            recorded_activity_id=earlier_run.id,
            gear_id=old_shoes.id,
            assigned_by=GearAssignedBy.MANUAL,
        )
    )
    db.add(
        ActivityGear(
            recorded_activity_id=later_run.id,
            gear_id=new_shoes.id,
            assigned_by=GearAssignedBy.MANUAL,
        )
    )
    db.commit()

    unassigned_run = _make_activity(
        db, race=race_3, segment=Segment.RUN, start_time=datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    )

    response = client.get(f"/api/activities/{unassigned_run.id}")
    assert response.status_code == 200
    assert response.json()["suggested_gear_id"] == new_shoes.id

    # Retired shoes are never suggested (§2: hidden from pickers).
    db.refresh(new_shoes)
    new_shoes.retired_at = date(2026, 6, 15)
    db.commit()
    response = client.get(f"/api/activities/{unassigned_run.id}")
    assert response.json()["suggested_gear_id"] == old_shoes.id


# --- tenancy -----------------------------------------------------------------


def test_second_user_gets_404_on_get(client: TestClient, db: Session) -> None:
    owner = _make_user(db, email="owner@example.com")
    _make_user(db, email="intruder@example.com")
    race = _make_race(db, owner=owner)
    activity = _make_activity(db, race=race, segment=Segment.BIKE)

    _login(client, email="intruder@example.com")
    response = client.get(f"/api/activities/{activity.id}")
    assert response.status_code == 404


def test_second_user_gets_404_on_patch(client: TestClient, db: Session) -> None:
    owner = _make_user(db, email="owner@example.com")
    _make_user(db, email="intruder@example.com")
    race = _make_race(db, owner=owner)
    activity = _make_activity(db, race=race, segment=Segment.BIKE)

    _login(client, email="intruder@example.com")
    response = client.patch(f"/api/activities/{activity.id}", json={"is_primary": True})
    assert response.status_code == 404


def test_nonexistent_activity_404s(client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(client)

    assert client.get("/api/activities/999999").status_code == 404
    assert client.patch("/api/activities/999999", json={"is_primary": True}).status_code == 404
