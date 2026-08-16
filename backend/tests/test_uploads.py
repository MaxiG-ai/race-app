"""`POST /uploads`, `GET /uploads`, `DELETE /uploads/{id}` (M2 persistence + the
tray half of M2.5 — PLAN.md §3 "Import flow" and "What Strava exports actually
look like").

Uses the five real fixtures via `fixture_path()`. Weather enrichment is stubbed
out everywhere here — it's Open-Meteo's own concern (`test_weather.py`), and a
real network call has no place in this suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.password import hash_password
from app.config import settings
from app.models import (
    Race,
    RecordedActivity,
    Segment,
    Upload,
    UploadStatus,
    User,
)

from .conftest import fixture_path

APPLE_1 = "multisport_2026-07-11.fit"
GARMIN = "multisport_2024-08-03_garmin.fit"
EDGE_BIKE = "single_bike_2026-06-21_edge540.fit"
STRAVA = "strava_export_2026-06-21_multisport.fit"

EMAIL = "athlete@example.com"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _isolate_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uploaded files must not land in the real (dev) `var/uploads` directory
    during tests, and weather enrichment must never make a real network call."""
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr("app.routers.uploads.fetch_weather", lambda *a, **k: None)
    monkeypatch.setattr("app.services.grouping.fetch_weather", lambda *a, **k: None)


def _make_user(db: Session, *, email: str = EMAIL) -> User:
    user = User(email=email, password_hash=hash_password(PASSWORD))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, email: str = EMAIL) -> None:
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def _upload(client: TestClient, name: str, *, race_id: int | None = None) -> dict:
    data = fixture_path(name).read_bytes()
    kwargs: dict = {"files": [("files", (name, data, "application/octet-stream"))]}
    if race_id is not None:
        kwargs["data"] = {"race_id": str(race_id)}
    response = client.post("/api/uploads", **kwargs)
    assert response.status_code == 201, response.text
    return response.json()[0]


# --------------------------------------------------------------------------- #
# Multisport auto-grouping
# --------------------------------------------------------------------------- #


def test_multisport_upload_auto_creates_race_with_five_activities(
    client: TestClient, db: Session
) -> None:
    _make_user(db)
    _login(client)

    result = _upload(client, APPLE_1)
    assert result["outcome"] == "created"
    assert result["upload"]["status"] == "grouped"
    assert result["upload"]["kind"] == "multisport"
    assert result["upload"]["race_id"] is not None
    assert result["race"] is not None
    race_id = result["race"]["id"]

    assert db.query(Race).count() == 1

    activities = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race_id)
        .order_by(RecordedActivity.start_time)
        .all()
    )
    assert len(activities) == 5
    assert [a.segment.value for a in activities] == ["swim", "t1", "bike", "t2", "run"]
    # A genuine multisport file's transitions are recorded, not synthesized.
    assert all(not a.is_derived for a in activities)
    # Exactly one recording per segment -> every one of them is primary.
    assert all(a.is_primary for a in activities)

    by_segment = {a.segment.value: a for a in activities}
    # Ground truth from PLAN.md §3's table (Apple `2026-07-11`).
    assert by_segment["swim"].distance_m == pytest.approx(1572.69, abs=1.0)
    assert by_segment["swim"].elapsed_time == pytest.approx(1739, abs=1)
    assert by_segment["t1"].distance_m == pytest.approx(552.5, abs=1.0)
    assert by_segment["t1"].elapsed_time == pytest.approx(216, abs=1)
    assert by_segment["bike"].distance_m == pytest.approx(33746.45, abs=1.0)
    assert by_segment["bike"].elapsed_time == pytest.approx(4388, abs=1)
    assert by_segment["bike"].elevation_gain_m == pytest.approx(558, abs=1)
    assert by_segment["t2"].distance_m == pytest.approx(218.82, abs=1.0)
    assert by_segment["t2"].elapsed_time == pytest.approx(121, abs=1)
    assert by_segment["run"].distance_m == pytest.approx(9172.36, abs=1.0)
    assert by_segment["run"].elapsed_time == pytest.approx(3363, abs=1)
    assert by_segment["run"].elevation_gain_m == pytest.approx(166, abs=1)

    # Segment totals must come from the session message, never a lap sum (§3
    # Traps 1) -- spot-check the bike leg has laps recorded, but its stored
    # distance is the (larger) session total, not the sum of its laps.
    bike_laps_total = sum(lap.distance_m or 0 for lap in by_segment["bike"].lap_splits)
    assert bike_laps_total < by_segment["bike"].distance_m

    # Draft race: date/location filled in, name/type left as an obvious placeholder.
    race = db.get(Race, race_id)
    assert race.date == by_segment["swim"].start_time.date() or race.date is not None
    assert race.location_lat is not None
    assert race.location_lng is not None
    assert race.name == "Untitled race"


def test_garmin_multisport_ground_truth(client: TestClient, db: Session) -> None:
    """Second vendor, second fixture -- Garmin's bike power/NP and the orphan
    record near the T2 boundary shouldn't disturb persistence."""
    _make_user(db)
    _login(client)

    result = _upload(client, GARMIN)
    race_id = result["race"]["id"]

    activities = db.query(RecordedActivity).filter(RecordedActivity.race_id == race_id).all()
    by_segment = {a.segment.value: a for a in activities}

    assert by_segment["bike"].distance_m == pytest.approx(40868.11, abs=1.0)
    assert by_segment["bike"].elapsed_time == pytest.approx(4355, abs=1)
    assert by_segment["bike"].avg_power is not None
    assert by_segment["bike"].normalized_power is not None
    # Device-dependent, not sport-dependent (§3 Traps 2): Garmin has no run power.
    assert by_segment["run"].avg_power is None


# --------------------------------------------------------------------------- #
# Strava dedupe
# --------------------------------------------------------------------------- #


def test_duplicate_upload_reports_duplicate_not_a_second_row(
    client: TestClient, db: Session
) -> None:
    user = _make_user(db)
    _login(client)

    first = _upload(client, STRAVA)
    assert first["outcome"] == "created"
    first_upload_id = first["upload"]["id"]

    second = _upload(client, STRAVA)
    assert second["outcome"] == "duplicate"
    assert second["upload"]["id"] == first_upload_id
    assert "identical" in second["message"].lower()
    assert "already have" in second["message"].lower()

    assert db.query(Upload).filter(Upload.user_id == user.id).count() == 1


def test_duplicate_reported_per_file_within_one_batch(client: TestClient, db: Session) -> None:
    """The exact Strava scenario from PLAN §3: two filenames, same bytes,
    submitted together -- one created, one reported duplicate, in one request."""
    _make_user(db)
    _login(client)
    data = fixture_path(STRAVA).read_bytes()

    response = client.post(
        "/api/uploads",
        files=[
            ("files", ("Stutensee_Schwimmen_.fit", data, "application/octet-stream")),
            ("files", ("Stutensee_Lauf.fit", data, "application/octet-stream")),
        ],
    )
    assert response.status_code == 201
    results = response.json()
    outcomes = sorted(r["outcome"] for r in results)
    assert outcomes == ["created", "duplicate"]


# --------------------------------------------------------------------------- #
# Two-device assembly (§3 Assembly)
# --------------------------------------------------------------------------- #


def test_two_device_bike_segment_edge_wins_primacy(client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(client)

    watch_result = _upload(client, STRAVA)
    race_id = watch_result["race"]["id"]

    edge_result = _upload(client, EDGE_BIKE, race_id=race_id)
    assert edge_result["outcome"] == "created"
    assert edge_result["upload"]["status"] == "grouped"

    bike_activities = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race_id, RecordedActivity.segment == Segment.BIKE)
        .all()
    )
    assert len(bike_activities) == 2

    primaries = [a for a in bike_activities if a.is_primary]
    assert len(primaries) == 1
    winner = primaries[0]
    # The Edge recording has power; the watch's bike leg doesn't (§3 Assembly).
    assert winner.avg_power == 247
    assert winner.device_key is not None

    # The watch is still the only record of swim/run.
    swim = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race_id, RecordedActivity.segment == Segment.SWIM)
        .one()
    )
    assert swim.is_primary is True


# --------------------------------------------------------------------------- #
# Single-sport uploads stay in the tray
# --------------------------------------------------------------------------- #


def test_single_sport_upload_lands_in_tray_without_creating_a_race(
    client: TestClient, db: Session
) -> None:
    _make_user(db)
    _login(client)

    result = _upload(client, EDGE_BIKE)
    assert result["outcome"] == "created"
    assert result["upload"]["status"] == "parsed"
    assert result["upload"]["kind"] == "single_sport"
    assert result["upload"]["detected_sport"] == "bike"
    assert result["upload"]["race_id"] is None
    assert result["race"] is None
    assert db.query(Race).count() == 0
    assert db.query(RecordedActivity).count() == 0

    tray = client.get("/api/uploads", params={"status": "parsed"})
    assert tray.status_code == 200
    body = tray.json()
    assert len(body["uploads"]) == 1
    assert body["uploads"][0]["id"] == result["upload"]["id"]


# --------------------------------------------------------------------------- #
# Corrupt file
# --------------------------------------------------------------------------- #


def test_corrupt_file_produces_failed_status_not_500(client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(client)

    response = client.post(
        "/api/uploads",
        files=[("files", ("corrupt.fit", b"this is not a fit file at all", "text/plain"))],
    )
    assert response.status_code == 201
    result = response.json()[0]
    assert result["outcome"] == "failed"
    assert result["upload"]["status"] == "failed"
    assert result["upload"]["error"]

    stored = db.query(Upload).filter(Upload.id == result["upload"]["id"]).one()
    assert stored.status == UploadStatus.FAILED
    assert stored.error


def test_oversized_file_is_rejected_without_creating_a_row(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_upload_bytes", 10)
    _make_user(db)
    _login(client)

    response = client.post(
        "/api/uploads",
        files=[("files", ("big.fit", b"x" * 100, "application/octet-stream"))],
    )
    assert response.status_code == 201
    result = response.json()[0]
    assert result["outcome"] == "rejected"
    assert result["upload"] is None
    assert db.query(Upload).count() == 0


def test_non_fit_filename_is_rejected_before_parsing(client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(client)

    response = client.post(
        "/api/uploads",
        files=[("files", ("photo.jpg", b"\xff\xd8\xff", "image/jpeg"))],
    )
    assert response.status_code == 201
    result = response.json()[0]
    assert result["outcome"] == "rejected"
    assert db.query(Upload).count() == 0


# --------------------------------------------------------------------------- #
# DELETE /uploads/{id}
# --------------------------------------------------------------------------- #


def test_delete_ungrouped_upload_removes_row_and_file(client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(client)
    result = _upload(client, EDGE_BIKE)
    upload_id = result["upload"]["id"]
    stored_path = Path(db.query(Upload).filter(Upload.id == upload_id).one().stored_path)
    assert stored_path.exists()

    response = client.delete(f"/api/uploads/{upload_id}")
    assert response.status_code == 204
    assert db.query(Upload).filter(Upload.id == upload_id).one_or_none() is None
    assert not stored_path.exists()


def test_delete_grouped_upload_is_refused(client: TestClient, db: Session) -> None:
    """Decision: a grouped upload's file is retained for future re-import (§8),
    and detaching it would leave real RecordedActivity rows pointing at a
    vanished upload_id, so deletion is refused rather than silently detaching."""
    _make_user(db)
    _login(client)
    result = _upload(client, APPLE_1)
    upload_id = result["upload"]["id"]

    response = client.delete(f"/api/uploads/{upload_id}")
    assert response.status_code == 409
    assert db.query(Upload).filter(Upload.id == upload_id).one_or_none() is not None


def test_delete_nonexistent_upload_404s(client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(client)
    assert client.delete("/api/uploads/999999").status_code == 404


# --------------------------------------------------------------------------- #
# Tenancy
# --------------------------------------------------------------------------- #


def test_second_user_cannot_see_first_users_tray(client: TestClient, db: Session) -> None:
    _make_user(db, email="owner@example.com")
    _make_user(db, email="intruder@example.com")

    _login(client, email="owner@example.com")
    _upload(client, EDGE_BIKE)

    _login(client, email="intruder@example.com")
    tray = client.get("/api/uploads")
    assert tray.status_code == 200
    assert tray.json()["uploads"] == []


def test_uploading_with_someone_elses_race_id_404s(client: TestClient, db: Session) -> None:
    _make_user(db, email="owner@example.com")
    _make_user(db, email="intruder@example.com")

    _login(client, email="owner@example.com")
    owner_race = _upload(client, APPLE_1)["race"]["id"]

    _login(client, email="intruder@example.com")
    data = fixture_path(EDGE_BIKE).read_bytes()
    response = client.post(
        "/api/uploads",
        files=[("files", (EDGE_BIKE, data, "application/octet-stream"))],
        data={"race_id": str(owner_race)},
    )
    assert response.status_code == 404
