"""docs/features/fueling.md#acceptance."""

from tests.factories import make_race


def test_put_replaces_the_whole_set(owner, db):
    make_race(db)
    body = [
        {"segment": "bike", "carbs_g": 90, "products": [{"name": "Maurten 160", "quantity": 2}]},
        {"segment": "run", "carbs_g": 25},
    ]
    fueling = owner.put("/api/races/1/fueling", json=body).json()["fueling"]
    assert [f["segment"] for f in fueling] == ["bike", "run"]
    fueling = owner.put("/api/races/1/fueling", json=body[:1]).json()["fueling"]
    assert [(f["segment"], f["carbs_g"]) for f in fueling] == [("bike", 90)]


def test_one_row_per_segment(owner, db):
    make_race(db)
    twice = [{"segment": "bike"}, {"segment": "bike"}]
    assert owner.put("/api/races/1/fueling", json=twice).status_code == 422


def test_no_row_is_unknown_not_zero(owner, db):
    make_race(db)
    owner.put("/api/races/1/fueling", json=[{"segment": "bike"}])
    assert owner.get("/api/races/1").json()["fueling"][0]["carbs_g"] is None
