"""docs/features/trends.md#acceptance."""

from datetime import date

from app.schemas.trends import SportTotals, TrendRace
from tests.factories import make_race


def test_rows_are_oldest_first_with_primary_totals(owner, db):
    make_race(db, name="Later", day=date(2026, 8, 1), times={"run": 1500}, distances={"run": 5000})
    make_race(
        db,
        name="Earlier",
        day=date(2025, 6, 1),
        times={"swim": 600, "bike": 2000},
        distances={"swim": 750, "bike": 20000},
        weather_temp_c=21.5,
    )
    make_race(db, name="Manual only", day=date(2026, 1, 1))
    races = owner.get("/api/trends").json()["races"]
    assert [r["name"] for r in races] == ["Earlier", "Manual only", "Later"]
    earlier = races[0]
    assert earlier["temp_c"] == 21.5
    assert earlier["sports"]["swim"] == {
        "distance_m": 750,
        "elapsed_time": 600,
        "avg_hr": None,
        "avg_power": None,
    }
    assert earlier["sports"]["run"] is None  # unknown, never zero
    assert races[1]["sports"] == {"swim": None, "bike": None, "run": None}


def test_visitors_get_public_races_and_the_public_shape_only(anon, db, public_mode):
    make_race(db, name="hidden", times={"run": 1500})
    make_race(db, name="shown", public=True, times={"run": 1500})
    body = anon.get("/api/trends").json()
    assert [r["name"] for r in body["races"]] == ["shown"]
    row = body["races"][0]
    assert set(row) == set(TrendRace.model_fields)
    assert set(row["sports"]["run"]) == set(SportTotals.model_fields)
    assert "hidden" not in anon.get("/api/trends").text


def test_non_primary_recordings_are_left_out(owner, db):
    race = make_race(db, times={"run": 1500}, distances={"run": 5000})
    from app.models import RecordedActivity

    second = RecordedActivity(
        segment="run",
        is_primary=False,
        start_time=race.activities[0].start_time,
        elapsed_time=9999,
        upload_id=None,
    )
    race.activities.append(second)
    db.commit()
    run = owner.get("/api/trends").json()["races"][0]["sports"]["run"]
    assert run["elapsed_time"] == 1500
