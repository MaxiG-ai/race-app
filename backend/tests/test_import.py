"""docs/features/import.md#acceptance, against the synthetic fixtures."""

import hashlib
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import RecordedActivity, Upload
from app.services.fit_import import FitParseError, parse_fit
from tests.conftest import PRIVATE, fixture_bytes, upload


def parsed(name):
    return parse_fit(fixture_bytes(name))


def by_segment(name):
    return {s.segment: s for s in parsed(name).sessions}


def test_segment_totals_come_from_the_session_not_laps():  # Trap 1
    bike = by_segment("apple_multisport.fit")["bike"]
    assert bike.distance_m == pytest.approx(33746)
    assert sum(lap.distance_m for lap in bike.laps) == pytest.approx(30000)
    assert not any(lap.is_partial for lap in bike.laps)  # Apple dropped the remainder


def test_trailing_partial_lap_is_marked():
    run = by_segment("apple_multisport.fit")["run"]
    assert [lap.is_partial for lap in run.laps] == [False] * 9 + [True]


def test_power_is_a_device_property_not_a_sport_property():  # Trap 2
    apple, garmin = by_segment("apple_multisport.fit"), by_segment("garmin_multisport.fit")
    assert apple["bike"].avg_power is None and apple["run"].avg_power == 300
    assert garmin["bike"].avg_power == 225 and garmin["bike"].normalized_power == 250
    assert garmin["run"].avg_power is None


def test_overlapping_boundaries_count_each_record_once():  # Trap 3
    result = parsed("overlap_boundary.fit")
    counts = [len(s.gps_track) for s in result.sessions]
    # 22 records; the one at each boundary instant belongs to the *next* session only
    assert sum(counts) == 22 and result.orphan_record_count == 0
    assert counts == [4, 3, 7, 3, 5]
    for session in result.sessions:
        assert all(p["t"] < session.elapsed_time for p in session.gps_track)


def test_one_orphan_is_dropped_and_counted():  # Trap 4
    result = parsed("garmin_multisport.fit")
    assert result.orphan_record_count == 1
    assert "1 record(s) fell outside" in " ".join(result.warnings)


def test_elapsed_and_moving_time_are_both_kept():  # Trap 5
    bike = by_segment("garmin_multisport.fit")["bike"]
    assert (bike.elapsed_time, bike.moving_time) == (1206, 1200)


def test_lap_counts_vary_by_vendor():  # Trap 7
    assert len(by_segment("apple_multisport.fit")["swim"].laps) == 1
    assert len(by_segment("garmin_multisport.fit")["swim"].laps) == 4


def test_device_temperature_is_stored():  # Trap 8
    assert by_segment("garmin_multisport.fit")["bike"].avg_temp_c is not None


def test_time_in_zone_comes_from_the_device_only():
    garmin = by_segment("garmin_multisport.fit")
    high = [100, 120, 135, 150, 165, 185]
    assert garmin["swim"].time_in_zone == {
        "hr": {"high": high, "seconds": [0, 140, 840, 520, 0, 0]},
        "power": None,
    }
    bike = garmin["bike"].time_in_zone
    assert bike["hr"]["seconds"] == [0, 0, 246, 488, 472, 0]
    assert bike["power"] == {
        "high": [140, 200, 240, 280, 320, 380, 2000],
        "seconds": [0, 182, 688, 336, 0, 0, 0],
    }
    # the lap-level message (999 s everywhere) is not mistaken for a session's
    assert all(999 not in (s.time_in_zone or {})["hr"]["seconds"] for s in garmin.values())
    # Apple writes no time_in_zone; nothing is computed from records instead (Trap 2)
    assert all(s.time_in_zone is None for s in by_segment("apple_multisport.fit").values())


def test_time_in_zone_shape_checks():
    from app.services.fit_import import time_in_zone

    assert time_in_zone(None) is None
    both = {"hr_zone_high_boundary": (100, 150), "time_in_hr_zone": (10.4, 20.6, 5.0)}
    assert time_in_zone(both) == {"hr": {"high": [100, 150], "seconds": [10, 21, 5]}, "power": None}
    # misaligned arrays or no time at all are unknown, not a guess
    assert time_in_zone({"hr_zone_high_boundary": (1,), "time_in_hr_zone": (1, 2, 3)}) is None
    assert time_in_zone({"hr_zone_high_boundary": (1, 2), "time_in_hr_zone": (0, 0)}) is None


def test_time_in_zone_reaches_the_race_detail(anon, owner, public_mode):
    race_id = upload(owner, "garmin_multisport.fit")[0]["race_id"]
    owner.patch(f"/api/races/{race_id}/visibility", json={"is_public": True})
    segments = {s["segment"]: s for s in anon.get(f"/api/races/{race_id}").json()["segments"]}
    assert segments["run"]["time_in_zone"]["hr"]["seconds"] == [0, 0, 259, 1109, 1104, 331]
    assert segments["run"]["time_in_zone"]["power"] is None


def test_swim_has_no_altitude():  # Trap 9
    apple = by_segment("apple_multisport.fit")
    assert all(p["elevation"] is None for p in apple["swim"].gps_track)
    assert all(p["elevation"] is not None for p in apple["bike"].gps_track)


def test_developer_fields():  # Trap 10
    assert by_segment("apple_multisport.fit")["bike"].rpe_estimated == 7


def test_local_date_from_gps_position():  # Trap 11
    result = parsed("early_start_local_date.fit")
    assert result.start_time.date() == date(2026, 6, 20)
    assert result.local_date == date(2026, 6, 21)


def test_local_date_from_timestamp_correlation():
    result = parsed("edge_single_bike.fit")
    assert result.kind == "single_sport" and result.local_date == date(2026, 4, 10)


def test_track_and_progress_series_shapes():
    run = by_segment("apple_multisport.fit")["run"]
    assert set(run.gps_track[0]) == {"lat", "lng", "elevation", "t", "distance_m"}
    series = run.progress_series
    assert len(series) == 201 and series[0]["progress"] == 0 and series[-1]["progress"] == 1
    assert series[-1]["elapsed_s"] == pytest.approx(3363, abs=1)
    elapsed = [p["elapsed_s"] for p in series]
    assert elapsed == sorted(elapsed)


def test_wrong_session_sequence_writes_nothing(owner, db):
    data = bytearray(fixture_bytes("apple_multisport.fit"))
    with pytest.raises(FitParseError):
        parse_fit(bytes(data[:14]) + b"\x00" * 40)
    before = db.scalar(select(func.count()).select_from(RecordedActivity))
    out = upload(owner, "overlap_boundary.fit")[0]
    assert out["result"] == "attached"
    assert db.scalar(select(func.count()).select_from(RecordedActivity)) == before + 5


# --- through the API --------------------------------------------------------------------


def test_multisport_upload_creates_a_private_race(owner):
    out = upload(owner, "apple_multisport.fit")[0]
    assert out["result"] == "attached"
    race = owner.get(f"/api/races/{out['race_id']}").json()
    assert race["is_public"] is False and race["name"] == "Untitled race"
    assert race["date"] == "2026-07-11"
    assert [s["segment"] for s in race["segments"]] == ["swim", "t1", "bike", "t2", "run"]
    assert race["segments"][2]["distance_m"] == pytest.approx(33746)
    assert race["location_lat"] == pytest.approx(49.0, abs=0.1)
    laps = owner.get(f"/api/races/{out['race_id']}/laps?sport=bike").json()
    assert len(laps) == 6


def test_same_file_twice_is_one_upload(owner, db):
    first = upload(owner, "apple_multisport.fit")[0]
    again = upload(owner, "apple_multisport.fit", race_id=first["race_id"])[0]
    assert again["result"] == "duplicate" and again["message"] == "Already part of this race."
    elsewhere = upload(owner, "apple_multisport.fit")[0]  # dropped on a new race
    assert elsewhere["result"] == "duplicate" and elsewhere["race_id"] == first["race_id"]
    assert (
        "Identical to apple_multisport.fit in “Untitled race” (11 Jul 2026)"
        in (elsewhere["message"])
    )
    assert db.scalar(select(func.count()).select_from(Upload)) == 1
    assert len(owner.get("/api/races").json()["items"]) == 1  # no empty race left behind


def test_a_single_sport_file_makes_that_single_sport_race(owner):
    out = upload(owner, "edge_single_bike.fit")[0]
    assert out["result"] == "attached" and out["message"] == "Added as the bike leg."
    race = owner.get(f"/api/races/{out['race_id']}").json()
    assert race["race_type"] == "bike" and race["date"] == "2026-04-10"
    assert race["missing_segments"] == [] and race["is_incomplete"] is False
    assert race["classification"]["source"] == "unclassified" and race["category"] is None
    assert [(u["filename"], u["status"]) for u in race["uploads"]] == [
        ("edge_single_bike.fit", "grouped")
    ]


def test_chosen_type_beats_detection_and_leaves_legs_missing(owner):
    out = upload(owner, "edge_single_bike.fit", race_type="triathlon")[0]
    race = owner.get(f"/api/races/{out['race_id']}").json()
    assert race["race_type"] == "triathlon"
    assert race["missing_segments"] == ["swim", "t1", "t2", "run"] and race["is_incomplete"]


def test_a_multisport_file_on_a_single_sport_race_is_refused(owner, db):
    bike = upload(owner, "edge_single_bike.fit")[0]["race_id"]
    out = upload(owner, "apple_multisport.fit", race_id=bike)[0]
    assert out["result"] == "failed" and "Make it a triathlon" in out["message"]
    assert db.scalar(select(func.count()).select_from(Upload)) == 1  # nothing stored


def test_a_bike_file_on_a_run_race_is_refused(owner):
    run = owner.post("/api/races", json={"race_type": "run"}).json()["id"]
    out = upload(owner, "edge_single_bike.fit", race_id=run)[0]
    assert out["result"] == "failed"
    assert out["message"] == (
        "This is a bike file, but this is a run race. Change the race type to add it."
    )
    assert owner.get(f"/api/races/{run}").json()["uploads"] == []


def test_a_file_from_another_day_is_refused(owner):
    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    out = upload(owner, "edge_single_bike.fit", race_id=race_id)[0]
    assert out["result"] == "failed" and "looks like a different race" in out["message"]
    assert "10 Apr 2026" in out["message"]


def test_nothing_that_fits_makes_no_race(owner):
    out = upload(owner, "apple_multisport.fit", race_type="run")[0]
    assert out["result"] == "failed" and out["race_id"] is None
    assert owner.get("/api/races").json()["items"] == []


def test_non_fit_and_oversized_files_are_rejected(owner, monkeypatch):
    from app.config import get_settings

    response = owner.post(
        "/api/races/import", files=[("files", ("x.fit", b"not a fit file at all"))]
    )
    assert response.json()["outcomes"][0]["result"] == "failed"
    assert response.json()["race"] is None
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 100)
    assert upload(owner, "apple_multisport.fit")[0]["message"].startswith("The file is larger")


def test_original_is_stored_verbatim(owner, db):
    upload(owner, "garmin_multisport.fit")
    stored = db.scalar(select(Upload)).stored_path
    assert open(stored, "rb").read() == fixture_bytes("garmin_multisport.fit")


def test_enrichment_failure_never_fails_the_import(owner, monkeypatch):
    from app.services import classify

    def broken(db, race):
        raise RuntimeError("boom")

    monkeypatch.setattr(classify, "classify_race", broken)
    assert upload(owner, "apple_multisport.fit")[0]["result"] == "attached"


def test_reimport_keeps_primary_and_manual_gear(owner, db):
    from app.services import imports

    race_id = upload(owner, "garmin_multisport.fit")[0]["race_id"]
    gear = owner.post("/api/gear", json={"kind": "bike", "name": "TT bike"}).json()
    bike = next(
        s for s in owner.get(f"/api/races/{race_id}").json()["segments"] if s["segment"] == "bike"
    )
    owner.patch(f"/api/activities/{bike['id']}", json={"gear_ids": [gear["id"]]})
    imports.reimport(db, db.scalar(select(Upload)))
    after = next(
        s for s in owner.get(f"/api/races/{race_id}").json()["segments"] if s["segment"] == "bike"
    )
    assert after["id"] == bike["id"] and [g["name"] for g in after["gear"]] == ["TT bike"]


@pytest.mark.skipif(not list(PRIVATE.glob("*.fit")), reason="no private real files")
def test_private_real_files_parse():
    for path in PRIVATE.glob("*.fit"):
        result = parse_fit(path.read_bytes())
        assert result.sessions, path.name


def test_identical_copy_of_a_failed_upload_is_retried_not_called_duplicate(owner, db):
    """Issue #5: Strava's swim and run downloads are the same file. When that file fails
    to import, the second copy must report the failure, not "you already have this"."""
    broken = fixture_bytes("apple_multisport.fit")[:14] + b"\x00" * 40
    first, second = (
        owner.post("/api/races/import", files=[("files", (name, broken))]).json()["outcomes"][0]
        for name in ("swim.fit", "run.fit")
    )
    assert first["result"] == second["result"] == "failed"
    assert second["message"] == first["message"] and "Could not read" in second["message"]
    assert db.scalar(select(func.count()).select_from(Upload)) == 0  # failures aren't kept


def test_the_same_file_twice_in_one_drop(owner, db):
    data = fixture_bytes("apple_multisport.fit")
    files = [("files", (name, data)) for name in ("swim.fit", "run.fit")]
    outcomes = owner.post("/api/races/import", files=files).json()["outcomes"]
    assert [o["result"] for o in outcomes] == ["attached", "duplicate"]
    assert outcomes[1]["message"] == "The same file as swim.fit, sent twice."
    assert db.scalar(select(func.count()).select_from(Upload)) == 1


def test_an_upload_left_without_a_race_is_adopted(owner, db):
    """Uploads the old tray held (or a deleted race sent back) join the race they're
    dropped on, instead of being called duplicates of nothing."""
    from app.models.enums import UploadStatus

    data = fixture_bytes("apple_multisport.fit")
    db.add(
        Upload(
            filename="old.fit",
            content_hash=hashlib.sha256(data).hexdigest(),
            status=UploadStatus.PARSED,
        )
    )
    db.commit()
    out = upload(owner, "apple_multisport.fit")[0]
    assert out["result"] == "attached" and out["upload_id"] == 1
    assert db.scalar(select(func.count()).select_from(Upload)) == 1


def test_multisport_without_a_recorded_t2_derives_it(owner):
    """Issue #5: a watch that went straight from bike to run recorded four sessions."""
    out = upload(owner, "missing_t2.fit")[0]
    assert out["result"] == "attached", out["message"]
    segments = {
        s["segment"]: s for s in owner.get(f"/api/races/{out['race_id']}").json()["segments"]
    }
    assert list(segments) == ["swim", "t1", "bike", "t2", "run"]
    assert not segments["t1"]["is_derived"]
    assert segments["t2"]["is_derived"] and segments["t2"]["elapsed_time"] == 146
    assert segments["t2"]["distance_m"] is None


def test_multisport_out_of_order_is_still_rejected():
    from app.services.fit_import import _check_shape

    with pytest.raises(FitParseError):
        _check_shape([{"sport": s} for s in ("cycling", "swimming", "running")])
    with pytest.raises(FitParseError):
        _check_shape(
            [{"sport": s} for s in ("swimming", "transition", "transition", "cycling", "running")]
        )
