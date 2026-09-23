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


def test_delete_sends_uploads_back_to_the_tray(owner, db):
    from app.models import Upload
    from app.models.enums import UploadStatus

    race = make_race(db)
    db.add(Upload(filename="a.fit", status=UploadStatus.GROUPED, race_id=race.id))
    db.commit()
    assert owner.delete("/api/races/1").status_code == 204
    tray = owner.get("/api/uploads").json()["uploads"]
    assert [(u["filename"], u["status"], u["race_id"]) for u in tray] == [("a.fit", "parsed", None)]


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
