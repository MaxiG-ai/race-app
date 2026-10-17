"""docs/features/grouping.md#acceptance.

The single-sport legs are cut from the early-start multisport fixture's sessions, so they
line up like three watch files of one race would."""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import Race, RecordedActivity, Upload
from app.models.enums import Segment, Sport, UploadKind
from app.services import imports
from app.services.fit_import import parse_fit
from tests.conftest import fixture_bytes, upload

FIT_HEADER = b"\x0e\x10\x00\x00\x00\x00\x00\x00.FIT"


@pytest.fixture
def legs(monkeypatch):
    """`drop(*segments, race_id=None)` drops one single-sport file per leg. Each is a
    one-session parse of the early-start fixture; `shift` moves a leg in time."""
    shifts = {}

    def fake_parse(data):
        segment = data[len(FIT_HEADER) :].decode()
        one = parse_fit(fixture_bytes("early_start_local_date.fit"))
        session = next(s for s in one.sessions if s.segment == segment.removesuffix("?"))
        session.segment = None
        if segment.endswith("?"):  # a file that doesn't say its sport
            session.sport = Sport.OTHER
        session.start_time += shifts.get(segment, timedelta(0))
        one.sessions, one.kind = [session], UploadKind.SINGLE_SPORT
        return one

    monkeypatch.setattr(imports, "parse_fit", fake_parse)

    def drop(owner, *segments, race_id=None, shift=None):
        shifts.update(shift or {})
        files = [("files", (f"{s}.fit", FIT_HEADER + s.encode())) for s in segments]
        path = f"/api/races/{race_id}/uploads" if race_id else "/api/races/import"
        response = owner.post(path, files=files)
        assert response.status_code == 200, response.text
        return response.json()

    return drop


def test_three_legs_become_five_segments(owner, legs):
    body = legs(owner, "swim", "bike", "run")
    assert [o["message"] for o in body["outcomes"]] == [
        "Added as the swim leg.",
        "Added as the bike leg.",
        "Added as the run leg.",
    ]
    race = body["race"]
    segments = {s["segment"]: s for s in race["segments"]}
    assert list(segments) == ["swim", "t1", "bike", "t2", "run"]
    assert segments["t1"]["is_derived"] and segments["t2"]["is_derived"]
    assert segments["t1"]["elapsed_time"] == 60 and segments["t1"]["distance_m"] is None
    assert race["date"] == "2026-06-21" and race["race_type"] == "triathlon"
    assert race["missing_segments"] == [] and not race["is_incomplete"]


def test_legs_can_arrive_one_at_a_time(owner, legs):
    race_id = legs(owner, "swim", "bike")["race"]["id"]
    race = legs(owner, "run", race_id=race_id)["race"]
    assert [s["segment"] for s in race["segments"]] == ["swim", "t1", "bike", "t2", "run"]


def test_hours_apart_leg_is_refused_and_the_rest_is_kept(owner, legs, db):
    body = legs(owner, "swim", "bike", "run", shift={"run": timedelta(hours=3)})
    run = body["outcomes"][2]
    assert run["result"] == "failed" and "apart" in run["message"]
    race = body["race"]
    assert race["missing_segments"] == ["t2", "run"] and race["is_incomplete"]
    assert db.scalar(select(func.count()).select_from(Upload)) == 2  # the run isn't kept
    assert not list(db.scalars(select(RecordedActivity).where(RecordedActivity.segment == "run")))


def test_an_unknown_sport_waits_for_its_leg(owner, legs):
    body = legs(owner, "swim?", "bike")
    pending = body["outcomes"][0]
    assert pending["result"] == "needs_segment"
    race = body["race"]
    assert [(u["filename"], u["status"]) for u in race["uploads"]] == [
        ("swim?.fit", "parsed"),
        ("bike.fit", "grouped"),
    ]
    wrong = owner.post(
        f"/api/races/{race['id']}/attach", json={"segments": {str(pending["upload_id"]): "t9"}}
    )
    assert wrong.status_code == 422
    race = owner.post(
        f"/api/races/{race['id']}/attach",
        json={"segments": {str(pending["upload_id"]): "swim"}},
    ).json()
    assert [s["segment"] for s in race["segments"]] == ["swim", "t1", "bike"]


def test_removing_a_file_takes_its_recordings_and_derived_transition(owner, legs, db):
    race = legs(owner, "swim", "bike", "run")["race"]
    swim = next(u for u in race["uploads"] if u["filename"] == "swim.fit")
    stored = db.get(Upload, swim["id"]).stored_path
    assert owner.delete(f"/api/uploads/{swim['id']}").status_code == 204
    after = owner.get(f"/api/races/{race['id']}").json()
    assert [s["segment"] for s in after["segments"]] == ["bike", "t2", "run"]
    assert after["missing_segments"] == ["swim", "t1"] and after["is_incomplete"]
    assert not Path(stored).exists()


def test_second_device_keeps_primary_and_switch_is_atomic(owner, db, edge_alongside_apple):
    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    out = upload(owner, "edge_single_bike.fit", race_id=race_id)[0]
    assert out["message"] == "Added as a second bike recording. The first one stays primary."
    body = owner.get(f"/api/races/{race_id}").json()
    bikes = [r for r in body["recordings"] if r["segment"] == "bike"]
    assert len(bikes) == 2
    old = next(r for r in bikes if r["is_primary"])
    assert old["upload_id"] != out["upload_id"]  # attaching keeps the old primary
    new = next(r for r in bikes if not r["is_primary"])
    body = owner.patch(f"/api/activities/{new['id']}", json={"is_primary": True}).json()
    primaries = [r["id"] for r in body["recordings"] if r["segment"] == "bike" and r["is_primary"]]
    assert primaries == [new["id"]]
    visitor_view = [s for s in body["segments"] if s["segment"] == "bike"]
    assert [s["id"] for s in visitor_view] == [new["id"]]


def test_switching_primary_reclassifies(owner, db, edge_alongside_apple):
    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    upload(owner, "edge_single_bike.fit", race_id=race_id)
    body = owner.get(f"/api/races/{race_id}").json()
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
