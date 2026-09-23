"""docs/features/classification.md#acceptance, against dtu-categories.md's measured races."""

import pytest
from sqlalchemy import select

from app.models import RaceCategory
from app.models.enums import Segment
from app.services.classify import classify
from tests.factories import make_race

MEASURED = [  # km, from docs/reference/dtu-categories.md
    ((1.55, 40.57, 9.93), "kurz", None),
    ((1.65, 40.87, 9.24), "kurz", None),
    ((0.54, 19.99, 5.11), "sprint", None),
    ((1.57, 33.75, 9.17), None, ("bike", -15.6)),
]


@pytest.mark.parametrize(("km", "expected", "miss"), MEASURED)
def test_measured_races(db, km, expected, miss):
    categories = list(db.scalars(select(RaceCategory)))
    distances = dict(
        zip((Segment.SWIM, Segment.BIKE, Segment.RUN), (k * 1000 for k in km), strict=True)
    )
    result = classify(distances, categories)
    assert (result.category.key if result.category else None) == expected
    if miss:
        assert result.detail["nearest"] == "kurz"
        assert (result.detail["segment"], result.detail["delta_pct"]) == miss


def test_narrowest_band_wins(db):
    categories = list(db.scalars(select(RaceCategory)))
    result = classify({Segment.SWIM: 400, Segment.BIKE: 10000, Segment.RUN: 2500}, categories)
    assert result.category.key == "supersprint"  # also inside Sprint


def test_no_distances_stay_unclassified(owner, db):
    make_race(db)
    body = owner.post("/api/races/1/classify").json()
    assert body["category"] is None and body["classification"]["source"] == "unclassified"


def _kurz_race(db):
    return make_race(
        db,
        distances={"swim": 1500, "bike": 40000, "run": 10000},
        times={"swim": 1, "bike": 1, "run": 1},
    )


def test_manual_override_survives_and_clearing_restores_auto(owner, db):
    _kurz_race(db)
    owner.post("/api/races/1/classify")
    sprint = db.scalar(select(RaceCategory).where(RaceCategory.key == "sprint"))
    body = owner.patch("/api/races/1", json={"race_category_id": sprint.id}).json()
    assert body["category"]["key"] == "sprint" and body["classification"]["source"] == "manual"
    body = owner.post("/api/races/1/classify").json()
    assert body["category"]["key"] == "sprint"
    assert body["classification"]["auto_category"]["key"] == "kurz"
    body = owner.patch("/api/races/1", json={"race_category_id": None}).json()
    assert body["category"]["key"] == "kurz" and body["classification"]["source"] == "auto"


def test_categories_endpoint(owner):
    keys = [c["key"] for c in owner.get("/api/categories").json()]
    assert keys[:3] == ["schueler", "jugend", "junioren"] and len(keys) == 9
