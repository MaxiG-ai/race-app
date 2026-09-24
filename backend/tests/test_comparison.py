"""docs/features/comparison.md#acceptance."""

from itertools import accumulate

from tests.conftest import upload
from tests.factories import make_race

KURZ = {"swim": 1500, "bike": 40000, "run": 10000}
SPRINT = {"swim": 750, "bike": 20000, "run": 5000}


def _classified(owner, db, distances, times, **kw):
    race = make_race(db, distances=distances, times=times, **kw)
    owner.post(f"/api/races/{race.id}/classify")
    return race


def test_different_categories_are_not_comparable(owner, db):
    _classified(owner, db, KURZ, {"swim": 1, "bike": 1, "run": 1})
    _classified(owner, db, SPRINT, {"swim": 1, "bike": 1, "run": 1})
    _classified(owner, db, KURZ, {"swim": 2, "bike": 2, "run": 2})
    assert [r["id"] for r in owner.get("/api/races/1/comparable").json()] == [3]
    assert owner.get("/api/races/1/compare/2").status_code == 422


def test_boundary_gaps_equal_cumulative_split_deltas(owner):
    a = upload(owner, "apple_multisport.fit")[0]["race_id"]
    b = upload(owner, "garmin_multisport.fit")[0]["race_id"]
    for race in (a, b):  # the fixtures differ in distance; compare them as one category
        owner.patch(f"/api/races/{race}", json={"race_category_id": 6})
    body = owner.get(f"/api/races/{a}/compare/{b}").json()
    boundaries = [p for p in body["checkpoints"] if p["boundary"]]
    assert [p["label"] for p in boundaries] == ["Start", "Swim", "T1", "Bike", "T2", "Finish"]
    deltas = [body["deltas"][s] for s in ("swim", "t1", "bike", "t2", "run")]
    assert [p["gap_s"] for p in boundaries[1:]] == list(accumulate(deltas))
    intra = [p for p in body["checkpoints"] if not p["boundary"]]
    assert intra and body["normalised"]  # bike 33.7 km vs 20 km: fractions, not absolute
    xs = [p["x"] for p in body["checkpoints"]]
    assert xs == sorted(xs) and xs[-1] == 1


def test_official_only_side_still_has_five_checkpoints(owner, db):
    official = {
        "total_time": 4000,
        "swim_time": 600,
        "t1_time": 60,
        "bike_time": 2000,
        "t2_time": 40,
        "run_time": 1300,
    }
    _classified(owner, db, KURZ, {"swim": 700, "t1": 50, "bike": 2100, "t2": 30, "run": 1200})
    make_race(db, official=official, race_category_id=6, classification_source="manual")
    owner.patch("/api/races/1", json={"race_category_id": 6})
    body = owner.get("/api/races/1/compare/2").json()
    boundaries = [p for p in body["checkpoints"] if p["boundary"]]
    assert len(boundaries) == 6 and body["coarse"]
    assert boundaries[-1]["gap_s"] == 4000 - 4080


def test_conditions_and_fueling_diff(owner, db):
    a = _classified(owner, db, KURZ, {"swim": 600, "bike": 2000, "run": 1500}, weather_temp_c=18)
    b = _classified(
        owner,
        db,
        {**KURZ, "bike": 42000},
        {"swim": 600, "bike": 2000, "run": 1500},
        weather_temp_c=26,
    )
    owner.put(
        f"/api/races/{a.id}/fueling",
        json=[{"segment": "bike", "carbs_g": 60, "products": [{"name": "Gel"}]}],
    )
    owner.put(
        f"/api/races/{b.id}/fueling",
        json=[{"segment": "bike", "carbs_g": 90, "products": [{"name": "Drink"}]}],
    )
    body = owner.get(f"/api/races/{a.id}/compare/{b.id}").json()
    fields = {c["field"]: c for c in body["conditions"]}
    assert fields["bike_distance_m"]["delta_pct"] == 5.0 and fields["bike_distance_m"]["material"]
    assert fields["temp_c"]["material"] and "swim_distance_m" not in fields
    bike = body["fueling"][0]
    assert bike["carbs_delta_g"] == 30 and bike["a"]["g_per_h"] == 108
    assert bike["only_a"] == ["Gel"] and bike["only_b"] == ["Drink"]


def test_visitor_with_one_hidden_race_gets_404(anon, owner, db, public_mode):
    _classified(owner, db, KURZ, {"swim": 1, "bike": 1, "run": 1}, public=True)
    _classified(owner, db, KURZ, {"swim": 1, "bike": 1, "run": 1})
    assert anon.get("/api/races/1/compare/2").status_code == 404
    assert anon.get("/api/races/1/comparable").json() == []
