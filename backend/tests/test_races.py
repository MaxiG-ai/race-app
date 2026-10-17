"""docs/features/races-and-results.md#acceptance and timeline.md#acceptance."""

from datetime import date

from tests.factories import make_race

FIVE = {"swim_time": 600, "t1_time": 60, "bike_time": 2000, "t2_time": 40, "run_time": 1300}


def test_race_with_only_a_date(owner):
    body = owner.post("/api/races", json={"date": "2025-05-01"}).json()
    assert body["name"] == "Untitled race"
    items = owner.get("/api/races").json()["items"]
    assert [(i["name"], i["date"], i["total_time"]) for i in items] == [
        ("Untitled race", "2025-05-01", None)
    ]


def test_date_defaults_to_today(owner):
    assert owner.post("/api/races", json={}).json()["date"] == date.today().isoformat()


def test_five_splits_derive_the_total(owner, db):
    make_race(db)
    result = owner.put("/api/races/1/official-result", json=FIVE).json()["official_result"]
    assert result["total_time"] == 4000 and result["total_is_derived"] is True
    # a derived total follows later split edits
    result = owner.put("/api/races/1/official-result", json={"run_time": 1400}).json()
    assert result["official_result"]["total_time"] == 4100


def test_typed_total_survives_split_edits(owner, db):
    make_race(db)
    owner.put("/api/races/1/official-result", json={**FIVE, "total_time": 4003})
    result = owner.put("/api/races/1/official-result", json={"run_time": 1400}).json()
    assert result["official_result"]["total_time"] == 4003
    assert result["official_result"]["total_is_derived"] is False


def test_total_and_four_splits_derive_the_fifth(owner, db):
    make_race(db)
    body = {**FIVE, "t1_time": None, "total_time": 4010}
    result = owner.put("/api/races/1/official-result", json=body).json()["official_result"]
    assert result["t1_time"] == 70 and result["derived_split"] == "t1"


def test_three_splits_and_no_total_are_refused(owner, db):
    make_race(db)
    response = owner.put(
        "/api/races/1/official-result", json={"swim_time": 600, "bike_time": 2000, "t2_time": 40}
    )
    assert response.status_code == 422
    assert "Still missing: T1, run." in response.json()["detail"]
    assert owner.get("/api/races/1").json()["official_result"] is None


def test_negative_time_is_invalid(owner, db):
    make_race(db)
    assert owner.put("/api/races/1/official-result", json={"total_time": -1}).status_code == 422


def test_patch_leaves_out_vs_null(owner, db):
    make_race(db, notes="keep", location_name="Lake")
    owner.patch("/api/races/1", json={"location_name": None})
    body = owner.get("/api/races/1").json()
    assert body["notes"] == "keep" and body["location_name"] is None
    assert owner.patch("/api/races/1", json={"name": None}).status_code == 422


def test_delete_takes_the_uploads_and_their_files(owner, db):
    from pathlib import Path

    from sqlalchemy import func, select

    from app.models import Upload
    from tests.conftest import upload

    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    stored = Path(db.scalar(select(Upload)).stored_path)
    assert owner.delete(f"/api/races/{race_id}").status_code == 204
    assert db.scalar(select(func.count()).select_from(Upload)) == 0 and not stored.exists()
    # the same file makes a race again
    assert upload(owner, "apple_multisport.fit")[0]["result"] == "attached"


def test_timeline_order_and_light_payload(owner, db):
    make_race(db, name="old", day=date(2024, 1, 1))
    make_race(db, name="same day, first", day=date(2025, 1, 1))
    make_race(db, name="same day, second", day=date(2025, 1, 1))
    response = owner.get("/api/races")
    assert [i["name"] for i in response.json()["items"]] == [
        "same day, second",
        "same day, first",
        "old",
    ]
    for key in ("gps_track", "raw_data", "laps"):
        assert key not in response.text


def test_segment_bar_prefers_official_splits(owner, db):
    make_race(
        db,
        times={"swim": 1, "t1": 1, "bike": 1, "t2": 1, "run": 1},
        official={**FIVE, "total_time": 4000},
    )
    make_race(db, times={"swim": 600, "bike": 2000})
    items = {i["id"]: i for i in owner.get("/api/races").json()["items"]}
    assert items[1]["times_source"] == "official" and items[1]["segment_times"]["bike"] == 2000
    assert items[2]["times_source"] == "recorded" and items[2]["segment_times"]["run"] is None


def test_stats_report_their_denominator(owner, db):
    make_race(
        db,
        distances={"swim": 1000, "bike": 20000, "run": 5000},
        times={"swim": 1, "bike": 1, "run": 1},
    )
    make_race(db)  # manual race: no distance
    stats = owner.get("/api/stats/summary").json()
    assert stats == {
        "race_count": 2,
        "distance_m": 26000,
        "distance_race_count": 1,
        "elevation_gain_m": 0,
        "elevation_race_count": 0,
    }


def test_completeness_lists_the_gaps(owner, db):
    make_race(db, name="Untitled race")
    completeness = owner.get("/api/races/1").json()["completeness"]
    assert completeness["official_result"] == {"missing": ["total_time"], "usable": False}
    assert completeness["classification"]["missing"] == ["recorded_distances"]
    assert "name" in completeness["public"]["missing"]


# --- race types, the incomplete tag, prefilled and confirmed results -----------------------


def test_import_prefills_an_unconfirmed_result(owner):
    from tests.conftest import upload

    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    result = owner.get(f"/api/races/{race_id}").json()["official_result"]
    assert (result["swim_time"], result["t1_time"], result["run_time"]) == (1800, 180, 3363)
    assert result["total_time"] == 1800 + 180 + 4388 + 120 + 3363
    assert result["total_is_derived"] and result["is_prefilled"]
    assert not result["is_confirmed"]


def test_prefill_follows_the_recordings_until_edited(owner, edge_alongside_apple):
    from tests.conftest import upload

    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    upload(owner, "edge_single_bike.fit", race_id=race_id)
    bikes = [
        r for r in owner.get(f"/api/races/{race_id}").json()["recordings"] if r["segment"] == "bike"
    ]
    edge = next(r for r in bikes if not r["is_primary"])
    apple = next(r for r in bikes if r["is_primary"])
    body = owner.patch(f"/api/activities/{edge['id']}", json={"is_primary": True}).json()
    assert body["official_result"]["bike_time"] == edge["elapsed_time"]

    body = owner.put(f"/api/races/{race_id}/official-result", json={"run_time": 3300}).json()
    assert not body["official_result"]["is_prefilled"]
    body = owner.patch(f"/api/activities/{apple['id']}", json={"is_primary": True}).json()
    assert body["official_result"]["bike_time"] == edge["elapsed_time"]  # no longer follows


def test_prefill_without_every_leg_uses_the_clock_time(db):
    from app.services import results

    race = make_race(db, times={"swim": 600, "t1": 60, "bike": 2000})
    results.prefill(race)
    result = race.official_result
    assert (result.bike_time, result.run_time, result.total_time) == (2000, None, 2660)
    assert result.is_prefilled and not result.total_is_derived


def test_confirming_ends_the_prefill(owner):
    from tests.conftest import upload

    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    body = owner.put(f"/api/races/{race_id}/official-result", json={"is_confirmed": True}).json()
    result = body["official_result"]
    assert result["is_confirmed"] and not result["is_prefilled"]
    assert result["total_time"] == 9851 and result["total_is_derived"]
    assert "confirmation" not in body["completeness"]["official_result"]["missing"]


def test_a_removed_file_takes_its_prefilled_result(owner):
    from tests.conftest import upload

    out = upload(owner, "edge_single_bike.fit")[0]
    owner.delete(f"/api/uploads/{out['upload_id']}")
    race = owner.get(f"/api/races/{out['race_id']}").json()
    assert race["official_result"] is None and race["missing_segments"] == ["bike"]


def test_single_sport_result_is_its_total(owner):
    race_id = owner.post("/api/races", json={"race_type": "run"}).json()["id"]
    path = f"/api/races/{race_id}/official-result"
    result = owner.put(path, json={"total_time": 2400, "bike_time": 5}).json()["official_result"]
    assert result["run_time"] == 2400 and result["bike_time"] is None
    assert not result["total_is_derived"] and result["derived_split"] is None
    assert owner.put(path, json={"total_time": None}).status_code == 422
    items = owner.get("/api/races").json()["items"]
    assert items[0]["times_source"] == "official" and items[0]["segment_times"]["run"] == 2400


def test_race_type_change_needs_fitting_recordings(owner, db):
    make_race(db, times={"swim": 600, "bike": 2000}, official={**FIVE, "total_time": 4000})
    response = owner.patch("/api/races/1", json={"race_type": "run"})
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "This race has swim, bike recordings, which a run race doesn't have. "
        "Remove those files first."
    )
    assert owner.patch("/api/races/1", json={"race_type": None}).status_code == 422


def test_race_type_change_reshapes_a_typed_result(owner, db):
    make_race(db, official={**FIVE, "total_time": 4000})
    body = owner.patch("/api/races/1", json={"race_type": "run"}).json()
    assert body["race_type"] == "run" and body["missing_segments"] == ["run"]
    result = body["official_result"]
    assert result["total_time"] == 4000 and result["run_time"] == 4000
    assert result["swim_time"] is None
    assert "classification" not in body["completeness"]
    result = owner.patch("/api/races/1", json={"race_type": "triathlon"}).json()["official_result"]
    assert result["total_time"] == 4000 and result["run_time"] is None  # not a leg's time


def test_incomplete_and_the_manual_override(owner, db):
    make_race(db, times={"swim": 600, "t1": 60, "bike": 2000})
    body = owner.get("/api/races/1").json()
    assert body["missing_segments"] == ["t2", "run"] and body["is_incomplete"]
    assert body["completeness"]["recordings"] == {"missing": ["t2", "run"], "usable": False}
    body = owner.patch("/api/races/1", json={"marked_complete": True}).json()
    assert body["missing_segments"] == ["t2", "run"] and not body["is_incomplete"]
    assert owner.get("/api/races").json()["items"][0]["is_incomplete"] is False


def test_single_sport_races_compare_with_their_own_kind(owner, db):
    make_race(db, race_type="run", distances={"run": 10000}, times={"run": 2400})
    make_race(db, race_type="run", distances={"run": 10050}, times={"run": 2350})
    make_race(db, distances={"run": 10000}, times={"run": 2400})  # a triathlon's run
    assert [r["id"] for r in owner.get("/api/races/1/comparable").json()] == [2]
    body = owner.get("/api/races/1/compare/2").json()
    assert [p["label"] for p in body["checkpoints"] if p["boundary"]][-1] == "Finish"
