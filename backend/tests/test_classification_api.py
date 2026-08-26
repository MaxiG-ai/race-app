"""Classification through the stack: import, override, re-run (§9)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ClassificationSource, RecordedActivity, Segment
from app.services.classification import apply_classification
from tests import factories


def _login(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/auth/login", json={"email": email, "password": factories.DEFAULT_PASSWORD}
    )
    assert response.status_code == 200


def _classified_race(db: Session, owner):
    race = factories.make_race(db, owner=owner, name="Stutensee", race_date=date(2026, 6, 21))
    factories.make_primary_activities(db, race=race)
    apply_classification(db, race)
    db.commit()
    return race


# --- the category table ------------------------------------------------------


def test_categories_endpoint_lists_the_seeded_table(client: TestClient, db: Session, categories):
    user = factories.make_user(db)
    _login(client, user.email)

    response = client.get("/api/categories")
    assert response.status_code == 200
    keys = [c["key"] for c in response.json()]
    assert keys == [
        "schueler",
        "jugend",
        "junioren",
        "supersprint",
        "sprint",
        "kurz",
        "mittel",
        "lang",
        "mixed_relay",
    ]


def test_categories_expose_their_bounds_for_the_ui(client: TestClient, db: Session, categories):
    user = factories.make_user(db)
    _login(client, user.email)

    by_key = {c["key"]: c for c in client.get("/api/categories").json()}
    # Sprintdistanz is published as "bis 0,75" — an open lower bound, not zero.
    assert by_key["sprint"]["swim_min_m"] is None
    assert by_key["sprint"]["swim_max_m"] == 750
    assert by_key["kurz"]["tolerance_pct"] == 10.0


def test_categories_require_a_login(client: TestClient, db: Session, categories):
    assert client.get("/api/categories").status_code == 401


# --- classification on a real race --------------------------------------------


def test_a_race_with_distances_is_classified(client: TestClient, db: Session, categories):
    user = factories.make_user(db)
    _login(client, user.email)
    race = _classified_race(db, user)

    body = client.get(f"/api/races/{race.id}").json()
    assert body["category"]["key"] == "kurz"
    assert body["classification_source"] == "auto"


def test_a_hand_created_race_stays_unclassified(client: TestClient, db: Session, categories):
    """No recordings, no distances, nothing to infer from — so it says so rather than
    guessing a bucket (§10)."""
    user = factories.make_user(db)
    _login(client, user.email)
    created = client.post("/api/races", json={"name": "Planned", "date": "2027-05-01"})

    assert created.status_code == 201
    assert created.json()["category"] is None
    assert created.json()["classification_source"] == "unclassified"


def test_an_unmatched_race_records_why(client: TestClient, db: Session, categories):
    """A 33.7 km bike is not a 40 km course. Saying which discipline missed, and by how
    much, is more use than an empty category."""
    user = factories.make_user(db)
    _login(client, user.email)
    race = factories.make_race(db, owner=user, name="Short bike")
    factories.make_primary_activities(db, race=race)
    bike = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race.id, RecordedActivity.segment == Segment.BIKE)
        .one()
    )
    bike.distance_m = 33746.45
    db.flush()
    apply_classification(db, race)
    db.commit()

    body = client.get(f"/api/races/{race.id}").json()
    assert body["category"] is None
    assert body["classification_source"] == "unclassified"
    assert body["classification_detail"]["nearest"] == "kurz"
    assert body["classification_detail"]["worst_segment"] == "bike"


# --- override ------------------------------------------------------------------


def test_setting_a_category_by_hand_pins_it(client: TestClient, db: Session, categories):
    user = factories.make_user(db)
    _login(client, user.email)
    race = _classified_race(db, user)
    mittel = next(c for c in categories if c.key == "mittel")

    patched = client.patch(f"/api/races/{race.id}", json={"race_category_id": mittel.id})
    assert patched.status_code == 200
    assert patched.json()["category"]["key"] == "mittel"
    assert patched.json()["classification_source"] == "manual"


def test_reclassifying_does_not_disturb_a_manual_choice(
    client: TestClient, db: Session, categories
):
    """The inferred answer keeps moving underneath, so the UI can show the disagreement
    — usually the first sign that a distance is wrong."""
    user = factories.make_user(db)
    _login(client, user.email)
    race = _classified_race(db, user)
    mittel = next(c for c in categories if c.key == "mittel")
    client.patch(f"/api/races/{race.id}", json={"race_category_id": mittel.id})

    response = client.post(f"/api/races/{race.id}/classify")

    assert response.status_code == 200
    body = response.json()
    assert body["category"]["key"] == "mittel", "manual choice must survive"
    assert body["auto_category"]["key"] == "kurz", "inference stays visible underneath"
    assert body["classification_source"] == "manual"


def test_clearing_the_override_hands_control_back(client: TestClient, db: Session, categories):
    user = factories.make_user(db)
    _login(client, user.email)
    race = _classified_race(db, user)
    mittel = next(c for c in categories if c.key == "mittel")
    client.patch(f"/api/races/{race.id}", json={"race_category_id": mittel.id})

    response = client.patch(f"/api/races/{race.id}", json={"race_category_id": None})

    assert response.status_code == 200
    assert response.json()["category"]["key"] == "kurz"
    assert response.json()["classification_source"] == "auto"


def test_reclassify_picks_up_a_corrected_distance(client: TestClient, db: Session, categories):
    user = factories.make_user(db)
    _login(client, user.email)
    race = _classified_race(db, user)
    assert client.get(f"/api/races/{race.id}").json()["category"]["key"] == "kurz"

    bike = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race.id, RecordedActivity.segment == Segment.BIKE)
        .one()
    )
    bike.distance_m = 19_991.6
    swim = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race.id, RecordedActivity.segment == Segment.SWIM)
        .one()
    )
    swim.distance_m = 540.16
    run = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race.id, RecordedActivity.segment == Segment.RUN)
        .one()
    )
    run.distance_m = 5_110.11
    db.commit()

    response = client.post(f"/api/races/{race.id}/classify")
    assert response.json()["category"]["key"] == "sprint"


def test_classify_is_scoped_to_the_owner(client: TestClient, db: Session, categories):
    owner = factories.make_user(db, email="owner@example.com")
    intruder = factories.make_user(db, email="intruder@example.com")
    race = _classified_race(db, owner)
    _login(client, intruder.email)

    assert client.post(f"/api/races/{race.id}/classify").status_code == 404


@pytest.mark.parametrize("source", [ClassificationSource.AUTO, ClassificationSource.UNCLASSIFIED])
def test_non_manual_sources_are_always_recomputed(
    client: TestClient, db: Session, categories, source
):
    user = factories.make_user(db)
    _login(client, user.email)
    race = _classified_race(db, user)
    race.classification_source = source
    race.race_category_id = None
    db.commit()

    response = client.post(f"/api/races/{race.id}/classify")
    assert response.json()["category"]["key"] == "kurz"
    assert response.json()["classification_source"] == "auto"
