"""`POST /races/group`, `POST /races/{id}/attach` (M2.5 — PLAN.md §3 "Single-sport
uploads and manual grouping").

These tests operate on `Upload` rows built directly (not via `POST /uploads`),
with `app.services.grouping.parse_fit` monkeypatched to return controlled,
synthetic `ParsedSession`s. That's deliberate: exercising the grouping/transition
math needs three single-sport legs with distinct, precisely-controlled start
times several hours apart on the same day (swim -> bike -> run), and the real
fixtures only provide one genuine single-sport file (`single_bike_...edge540`).
The FIT-parsing side of this (real bytes, real vendor quirks) is covered by
`test_fit_import.py` and the multisport/two-device paths in `test_uploads.py`;
this file is about what grouping *does* with a `ParsedFile`, not about parsing
one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.password import hash_password
from app.models import (
    Race,
    RecordedActivity,
    Segment,
    Sport,
    Upload,
    UploadKind,
    UploadSource,
    UploadStatus,
    User,
)
from app.services.fit_import import ParsedFile, ParsedSession

EMAIL = "athlete@example.com"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _stub_weather(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _make_single_sport_upload(
    db: Session,
    *,
    user: User,
    sport: Sport | None,
    content_hash: str,
    start_time: datetime,
    stored_path: Path | None = None,
) -> Upload:
    upload = Upload(
        user_id=user.id,
        source=UploadSource.FIT_UPLOAD,
        filename=f"{content_hash}.fit",
        stored_path=str(stored_path) if stored_path else None,
        content_hash=content_hash,
        status=UploadStatus.PARSED,
        kind=UploadKind.SINGLE_SPORT,
        detected_sport=sport,
        detected_start_time=start_time,
        detected_end_time=start_time + timedelta(seconds=1800),
        detected_distance_m=1000.0,
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload


def _fake_session(
    segment: str, sport: str, start_time: datetime, elapsed_s: int, distance_m: float = 1000.0
) -> ParsedSession:
    end_time = start_time + timedelta(seconds=elapsed_s)
    return ParsedSession(
        segment=segment,
        sport=sport,
        start_time=start_time,
        end_time=end_time,
        elapsed_time=elapsed_s,
        moving_time=elapsed_s,
        distance_m=distance_m,
        elevation_gain_m=None,
        avg_hr=140,
        max_hr=160,
        avg_power=None,
        normalized_power=None,
        avg_speed=(distance_m / elapsed_s) if elapsed_s else None,
        avg_cadence=None,
        avg_temp_c=None,
        rpe_estimated=None,
        avg_mets=None,
        bounds_n=48.1,
        bounds_s=48.0,
        bounds_e=8.5,
        bounds_w=8.4,
        gps_track=[],
        laps=[],
        device_keys=[],
        raw_data={},
    )


def _fake_parsed_file(session: ParsedSession) -> ParsedFile:
    return ParsedFile(
        kind="single_sport",
        sessions=[session],
        devices=[],
        orphan_record_count=0,
        start_time=session.start_time,
        end_time=session.end_time,
        local_date=session.start_time.date(),
        utc_offset_s=0,
        detected_sport=session.sport,
        total_distance_m=session.distance_m,
        warnings=[],
    )


def _stub_parse_fit(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, ParsedFile]) -> None:
    def _fake(path, filename=None):  # noqa: ANN001, ARG001
        return mapping[str(path)]

    monkeypatch.setattr("app.services.grouping.parse_fit", _fake)


def _stage_upload_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"stub")
    return path


# --------------------------------------------------------------------------- #
# Happy path: three single-sport uploads -> race with derived T1/T2
# --------------------------------------------------------------------------- #


def test_grouping_three_uploads_creates_derived_transitions(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db)
    _login(client)

    swim_path = _stage_upload_file(tmp_path, "swim.fit")
    bike_path = _stage_upload_file(tmp_path, "bike.fit")
    run_path = _stage_upload_file(tmp_path, "run.fit")

    swim_start = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
    swim_session = _fake_session("swim", "swim", swim_start, 1800, distance_m=1500.0)
    bike_start = swim_session.end_time + timedelta(seconds=300)  # 5 min T1
    bike_session = _fake_session("bike", "bike", bike_start, 3600, distance_m=40000.0)
    run_start = bike_session.end_time + timedelta(seconds=180)  # 3 min T2
    run_session = _fake_session("run", "run", run_start, 1800, distance_m=10000.0)

    _stub_parse_fit(
        monkeypatch,
        {
            str(swim_path): _fake_parsed_file(swim_session),
            str(bike_path): _fake_parsed_file(bike_session),
            str(run_path): _fake_parsed_file(run_session),
        },
    )

    swim_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.SWIM,
        content_hash="h-swim",
        start_time=swim_start,
        stored_path=swim_path,
    )
    bike_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.BIKE,
        content_hash="h-bike",
        start_time=bike_start,
        stored_path=bike_path,
    )
    run_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.RUN,
        content_hash="h-run",
        start_time=run_start,
        stored_path=run_path,
    )

    response = client.post(
        "/api/races/group",
        json={"upload_ids": [swim_upload.id, bike_upload.id, run_upload.id]},
    )
    assert response.status_code == 201, response.text
    race_id = response.json()["id"]

    activities = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race_id)
        .order_by(RecordedActivity.start_time)
        .all()
    )
    by_segment = {a.segment.value: a for a in activities}
    assert set(by_segment) == {"swim", "t1", "bike", "t2", "run"}

    assert by_segment["swim"].is_derived is False
    assert by_segment["bike"].is_derived is False
    assert by_segment["run"].is_derived is False

    t1 = by_segment["t1"]
    assert t1.is_derived is True
    assert t1.elapsed_time == 300
    assert t1.distance_m is None
    assert t1.avg_hr is None
    assert t1.gps_track is None

    t2 = by_segment["t2"]
    assert t2.is_derived is True
    assert t2.elapsed_time == 180
    assert t2.distance_m is None

    # Every segment (including the synthesized ones) is primary -- it's the only
    # recording for that segment.
    assert all(a.is_primary for a in activities)

    for upload in (swim_upload, bike_upload, run_upload):
        db.refresh(upload)
        assert upload.status == UploadStatus.GROUPED
        assert upload.race_id == race_id


# --------------------------------------------------------------------------- #
# Implausible gaps are rejected, not written
# --------------------------------------------------------------------------- #


def test_grouping_rejects_negative_gap(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db)
    _login(client)

    swim_path = _stage_upload_file(tmp_path, "swim.fit")
    bike_path = _stage_upload_file(tmp_path, "bike.fit")

    swim_start = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
    swim_session = _fake_session("swim", "swim", swim_start, 1800)
    # Bike starts *before* the swim ends -> the legs overlap -> negative T1 gap.
    bike_start = swim_session.end_time - timedelta(seconds=60)
    bike_session = _fake_session("bike", "bike", bike_start, 3600)

    _stub_parse_fit(
        monkeypatch,
        {
            str(swim_path): _fake_parsed_file(swim_session),
            str(bike_path): _fake_parsed_file(bike_session),
        },
    )
    swim_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.SWIM,
        content_hash="h-swim",
        start_time=swim_start,
        stored_path=swim_path,
    )
    bike_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.BIKE,
        content_hash="h-bike",
        start_time=bike_start,
        stored_path=bike_path,
    )

    response = client.post(
        "/api/races/group", json={"upload_ids": [swim_upload.id, bike_upload.id]}
    )
    assert response.status_code == 400
    assert "negative" in response.json()["detail"].lower()
    assert db.query(Race).count() == 0
    db.refresh(swim_upload)
    assert swim_upload.status == UploadStatus.PARSED


def test_grouping_rejects_implausibly_large_gap(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db)
    _login(client)

    swim_path = _stage_upload_file(tmp_path, "swim.fit")
    bike_path = _stage_upload_file(tmp_path, "bike.fit")

    swim_start = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
    swim_session = _fake_session("swim", "swim", swim_start, 1800)
    # Three hours between swim end and bike start -- not a transition.
    bike_start = swim_session.end_time + timedelta(hours=3)
    bike_session = _fake_session("bike", "bike", bike_start, 3600)

    _stub_parse_fit(
        monkeypatch,
        {
            str(swim_path): _fake_parsed_file(swim_session),
            str(bike_path): _fake_parsed_file(bike_session),
        },
    )
    swim_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.SWIM,
        content_hash="h-swim",
        start_time=swim_start,
        stored_path=swim_path,
    )
    bike_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.BIKE,
        content_hash="h-bike",
        start_time=bike_start,
        stored_path=bike_path,
    )

    response = client.post(
        "/api/races/group", json={"upload_ids": [swim_upload.id, bike_upload.id]}
    )
    assert response.status_code == 400
    assert "implausib" in response.json()["detail"].lower()
    assert db.query(Race).count() == 0


# --------------------------------------------------------------------------- #
# Ambiguous sport detection
# --------------------------------------------------------------------------- #


def test_grouping_rejects_ambiguous_sport_detection(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    _login(client)
    base = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
    run1 = _make_single_sport_upload(
        db, user=user, sport=Sport.RUN, content_hash="r1", start_time=base
    )
    run2 = _make_single_sport_upload(
        db, user=user, sport=Sport.RUN, content_hash="r2", start_time=base + timedelta(hours=1)
    )

    response = client.post("/api/races/group", json={"upload_ids": [run1.id, run2.id]})
    assert response.status_code == 400
    assert "ambiguous" in response.json()["detail"].lower()
    assert db.query(Race).count() == 0


def test_explicit_segment_assignment_resolves_ambiguity(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `other`/unrecognised sport, or two uploads racing for the same
    auto-detected sport, is exactly what `segment_assignments` is for (§3: "the
    API asks rather than guessing")."""
    user = _make_user(db)
    _login(client)

    swim_path = _stage_upload_file(tmp_path, "swim.fit")
    other_path = _stage_upload_file(tmp_path, "other.fit")

    swim_start = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
    swim_session = _fake_session("swim", "swim", swim_start, 1800)
    other_start = swim_session.end_time + timedelta(seconds=120)
    other_session = _fake_session("other", "other", other_start, 3600)

    _stub_parse_fit(
        monkeypatch,
        {
            str(swim_path): _fake_parsed_file(swim_session),
            str(other_path): _fake_parsed_file(other_session),
        },
    )
    swim_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.SWIM,
        content_hash="h-swim",
        start_time=swim_start,
        stored_path=swim_path,
    )
    other_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.OTHER,
        content_hash="h-other",
        start_time=other_start,
        stored_path=other_path,
    )

    response = client.post(
        "/api/races/group",
        json={
            "upload_ids": [swim_upload.id, other_upload.id],
            "segment_assignments": {str(other_upload.id): "bike"},
        },
    )
    assert response.status_code == 201, response.text
    race_id = response.json()["id"]
    bike = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race_id, RecordedActivity.segment == Segment.BIKE)
        .one()
    )
    assert bike.upload_id == other_upload.id


# --------------------------------------------------------------------------- #
# Suggested groupings (GET /uploads)
# --------------------------------------------------------------------------- #


def test_tray_proposes_a_same_day_grouping(client: TestClient, db: Session) -> None:
    user = _make_user(db)
    _login(client)
    base = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
    swim = _make_single_sport_upload(
        db, user=user, sport=Sport.SWIM, content_hash="p-swim", start_time=base
    )
    bike = _make_single_sport_upload(
        db, user=user, sport=Sport.BIKE, content_hash="p-bike", start_time=base + timedelta(hours=1)
    )
    run = _make_single_sport_upload(
        db, user=user, sport=Sport.RUN, content_hash="p-run", start_time=base + timedelta(hours=2)
    )

    response = client.get("/api/uploads")
    assert response.status_code == 200
    proposals = response.json()["proposals"]
    assert len(proposals) == 1
    proposal = proposals[0]
    assert set(proposal["upload_ids"]) == {swim.id, bike.id, run.id}
    assert proposal["segments"] == {
        str(swim.id): "swim",
        str(bike.id): "bike",
        str(run.id): "run",
    }


def test_tray_never_auto_commits_a_proposal(client: TestClient, db: Session) -> None:
    """A brick session and a race look identical from the data -- proposals are
    suggestions the tray shows, never something the server acts on by itself."""
    user = _make_user(db)
    _login(client)
    base = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
    _make_single_sport_upload(
        db, user=user, sport=Sport.SWIM, content_hash="p2-swim", start_time=base
    )
    _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.BIKE,
        content_hash="p2-bike",
        start_time=base + timedelta(hours=1),
    )

    response = client.get("/api/uploads")
    assert response.status_code == 200
    assert response.json()["proposals"]  # a proposal exists ...
    assert db.query(Race).count() == 0  # ... but nothing was created from it


# --------------------------------------------------------------------------- #
# Attach
# --------------------------------------------------------------------------- #


def test_attach_backfills_a_missing_leg(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db)
    _login(client)
    race = Race(user_id=user.id, name="Sprint", date=date(2026, 6, 21))
    db.add(race)
    db.commit()
    db.refresh(race)

    run_path = _stage_upload_file(tmp_path, "run.fit")
    run_start = datetime(2026, 6, 21, 9, 0, tzinfo=UTC)
    run_session = _fake_session("run", "run", run_start, 1800, distance_m=10000.0)
    _stub_parse_fit(monkeypatch, {str(run_path): _fake_parsed_file(run_session)})
    run_upload = _make_single_sport_upload(
        db,
        user=user,
        sport=Sport.RUN,
        content_hash="a-run",
        start_time=run_start,
        stored_path=run_path,
    )

    response = client.post(f"/api/races/{race.id}/attach", json={"upload_ids": [run_upload.id]})
    assert response.status_code == 200, response.text

    run_activity = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race.id, RecordedActivity.segment == Segment.RUN)
        .one()
    )
    assert run_activity.is_primary is True
    db.refresh(run_upload)
    assert run_upload.status == UploadStatus.GROUPED
    assert run_upload.race_id == race.id


# --------------------------------------------------------------------------- #
# Tenancy
# --------------------------------------------------------------------------- #


def test_group_asserts_every_upload_belongs_to_caller_before_creating_anything(
    client: TestClient, db: Session
) -> None:
    owner = _make_user(db, email="owner@example.com")
    intruder = _make_user(db, email="intruder@example.com")
    base = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
    own_swim = _make_single_sport_upload(
        db, user=owner, sport=Sport.SWIM, content_hash="t-swim", start_time=base
    )
    others_bike = _make_single_sport_upload(
        db,
        user=intruder,
        sport=Sport.BIKE,
        content_hash="t-bike",
        start_time=base + timedelta(hours=1),
    )

    _login(client, email="owner@example.com")
    response = client.post("/api/races/group", json={"upload_ids": [own_swim.id, others_bike.id]})
    assert response.status_code == 404
    assert db.query(Race).count() == 0
    db.refresh(own_swim)
    assert own_swim.status == UploadStatus.PARSED
    assert own_swim.race_id is None


def test_second_user_cannot_see_first_users_uploads_in_tray(
    client: TestClient, db: Session
) -> None:
    owner = _make_user(db, email="owner@example.com")
    _make_user(db, email="intruder@example.com")
    _make_single_sport_upload(
        db,
        user=owner,
        sport=Sport.SWIM,
        content_hash="v-swim",
        start_time=datetime(2026, 6, 21, 7, 0, tzinfo=UTC),
    )

    _login(client, email="intruder@example.com")
    response = client.get("/api/uploads")
    assert response.json()["uploads"] == []
    assert response.json()["proposals"] == []


def test_attach_to_someone_elses_race_404s(client: TestClient, db: Session) -> None:
    owner = _make_user(db, email="owner@example.com")
    _make_user(db, email="intruder@example.com")
    race = Race(user_id=owner.id, name="Sprint", date=date(2026, 6, 21))
    db.add(race)
    db.commit()
    db.refresh(race)

    _login(client, email="intruder@example.com")
    response = client.post(f"/api/races/{race.id}/attach", json={"upload_ids": [999999]})
    assert response.status_code == 404
