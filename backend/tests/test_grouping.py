"""docs/features/grouping.md#acceptance.

The single-sport legs are cut from the early-start multisport fixture's sessions, so they
line up like three watch files of one race would."""

import pytest
from sqlalchemy import func, select

from app.models import Race, RecordedActivity, Upload
from app.models.enums import Segment, Sport, UploadKind, UploadStatus
from app.services import imports
from app.services.fit_import import parse_fit
from tests.conftest import fixture_bytes, upload


@pytest.fixture
def legs(db, monkeypatch, tmp_path):
    """Three tray uploads (swim, bike, run) backed by one-session parses of a real file."""
    full = parse_fit(fixture_bytes("early_start_local_date.fit"))
    sessions = {s.segment: s for s in full.sessions}
    ids = {}
    for segment in ("swim", "bike", "run"):
        path = tmp_path / f"{segment}.fit"
        path.write_bytes(segment.encode())
        u = Upload(
            filename=f"{segment}.fit",
            content_hash=segment * 8,
            stored_path=str(path),
            status=UploadStatus.PARSED,
            kind=UploadKind.SINGLE_SPORT,
            detected_sport=Sport(segment),
            detected_start_time=sessions[segment].start_time,
            summary={"local_date": "2026-06-21"},
        )
        db.add(u)
        db.flush()
        ids[segment] = u.id
    db.commit()

    def fake_parse(data):
        segment = data.decode()
        one = parse_fit(fixture_bytes("early_start_local_date.fit"))
        session = next(s for s in one.sessions if s.segment == segment)
        session.segment = None
        one.sessions, one.kind = [session], UploadKind.SINGLE_SPORT
        return one

    monkeypatch.setattr(imports, "parse_fit", fake_parse)
    return ids


def test_three_legs_become_five_segments(owner, legs):
    proposals = owner.get("/api/uploads?status=parsed").json()["proposals"]
    assert proposals == [
        {
            "upload_ids": [legs["swim"], legs["bike"], legs["run"]],
            "segments": {str(v): k for k, v in legs.items()},
            "ambiguous": False,
        }
    ]
    race = owner.post("/api/races/group", json={"upload_ids": list(legs.values())})
    assert race.status_code == 201, race.text
    segments = {s["segment"]: s for s in race.json()["segments"]}
    assert list(segments) == ["swim", "t1", "bike", "t2", "run"]
    assert segments["t1"]["is_derived"] and segments["t2"]["is_derived"]
    assert segments["t1"]["elapsed_time"] == 60 and segments["t1"]["distance_m"] is None
    assert race.json()["date"] == "2026-06-21"


def test_proposals_write_nothing(owner, legs, db):
    owner.get("/api/uploads")
    assert db.scalar(select(func.count()).select_from(Race)) == 0


def test_hours_apart_legs_are_rejected_and_nothing_written(owner, legs, db, monkeypatch):
    from datetime import timedelta

    one_leg = imports.parse_fit  # the `legs` fixture's stand-in

    def late_run(data):
        result = one_leg(data)
        if data == b"run":
            result.sessions[0].start_time += timedelta(hours=3)
        return result

    monkeypatch.setattr(imports, "parse_fit", late_run)
    response = owner.post("/api/races/group", json={"upload_ids": list(legs.values())})
    assert response.status_code == 422 and "apart" in response.json()["detail"]
    assert db.scalar(select(func.count()).select_from(Race)) == 0
    assert db.scalar(select(func.count()).select_from(RecordedActivity)) == 0


def test_ambiguous_sports_need_segments(owner, legs, db):
    db.get(Upload, legs["swim"]).detected_sport = Sport.RUN
    db.commit()
    response = owner.post("/api/races/group", json={"upload_ids": list(legs.values())})
    assert response.status_code == 422 and "same sport" in response.json()["detail"]
    ok = owner.post(
        "/api/races/group",
        json={"upload_ids": list(legs.values()), "segments": {str(legs["swim"]): "swim"}},
    )
    assert ok.status_code == 201


def test_second_device_keeps_primary_and_switch_is_atomic(owner, db):
    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    edge = upload(owner, "edge_single_bike.fit")[0]["upload_id"]
    body = owner.post(f"/api/races/{race_id}/attach", json={"upload_ids": [edge]}).json()
    bikes = [r for r in body["recordings"] if r["segment"] == "bike"]
    assert len(bikes) == 2
    old = next(r for r in bikes if r["is_primary"])
    assert old["upload_id"] != edge  # attaching keeps the old primary
    new = next(r for r in bikes if not r["is_primary"])
    body = owner.patch(f"/api/activities/{new['id']}", json={"is_primary": True}).json()
    primaries = [r["id"] for r in body["recordings"] if r["segment"] == "bike" and r["is_primary"]]
    assert primaries == [new["id"]]
    visitor_view = [s for s in body["segments"] if s["segment"] == "bike"]
    assert [s["id"] for s in visitor_view] == [new["id"]]


def test_switching_primary_reclassifies(owner, db):
    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    edge = upload(owner, "edge_single_bike.fit")[0]["upload_id"]
    body = owner.post(f"/api/races/{race_id}/attach", json={"upload_ids": [edge]}).json()
    before = body["classification"]["detail"]
    new = next(r for r in body["recordings"] if r["segment"] == "bike" and not r["is_primary"])
    after = owner.patch(f"/api/activities/{new['id']}", json={"is_primary": True}).json()
    assert after["classification"]["detail"] != before


def test_reconcile_default_prefers_power(db):
    from datetime import UTC, date, datetime

    race = Race(date=date(2026, 1, 1))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    watch = RecordedActivity(segment=Segment.BIKE, start_time=start, elapsed_time=4000)
    edge = RecordedActivity(
        segment=Segment.BIKE, start_time=start, elapsed_time=3000, avg_power=240
    )
    race.activities += [watch, edge]
    db.add(race)
    db.flush()
    imports.reconcile_primary(db, race, Segment.BIKE)
    assert edge.is_primary and not watch.is_primary
