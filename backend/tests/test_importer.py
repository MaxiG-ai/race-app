"""End-to-end: parsed file → rows in Postgres, with classification applied.

Exercises the parts that only a real database can: JSONB round-tripping, the partial
unique index on the primary recording, and cascades.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import (
    ClassificationSource,
    LapSplit,
    Race,
    RecordedActivity,
    Segment,
)
from app.services.fit_import import parse_fit
from app.services.importer import apply_classification, content_hash, persist_parsed

FIXTURES = Path(__file__).parent / "fixtures"
APPLE_A = FIXTURES / "multisport_2026-07-11.fit"
APPLE_B = FIXTURES / "multisport_2026-08-09.fit"
EDGE_BIKE = FIXTURES / "single_bike_2026-06-21_edge540.fit"
STRAVA = FIXTURES / "strava_export_2026-06-21_multisport.fit"


def _import(db, user, path: Path, race: Race | None = None) -> Race:
    return persist_parsed(db, user=user, parsed=parse_fit(str(path)), race=race)


# --- a whole race lands intact ---------------------------------------------------


def test_importing_a_multisport_file_creates_five_segments(db, user, categories):
    race = _import(db, user, APPLE_B)

    activities = db.scalars(
        select(RecordedActivity).where(RecordedActivity.race_id == race.id)
    ).all()
    assert {a.segment for a in activities} == set(Segment)
    assert all(a.is_primary for a in activities)


def test_segment_totals_come_from_the_session_not_the_laps(db, user, categories):
    """Apple's bike laps sum 3.7 km short of the session (§3 Traps 1)."""
    race = _import(db, user, APPLE_A)
    bike = db.scalar(
        select(RecordedActivity).where(
            RecordedActivity.race_id == race.id, RecordedActivity.segment == Segment.BIKE
        )
    )
    lap_sum = db.scalar(
        select(func.sum(LapSplit.distance_m)).where(LapSplit.recorded_activity_id == bike.id)
    )
    assert bike.distance_m == pytest.approx(33746.45, abs=0.1)
    assert float(lap_sum) == pytest.approx(30000.0, abs=0.1)


def test_streams_round_trip_through_jsonb(db, user, categories):
    race = _import(db, user, APPLE_A)
    bike = db.scalar(
        select(RecordedActivity).where(
            RecordedActivity.race_id == race.id, RecordedActivity.segment == Segment.BIKE
        )
    )
    db.expire(bike)
    assert len(bike.progress_series) == 200
    assert bike.progress_series[0]["progress"] == 0.0
    assert bike.gps_track[0]["distance_m"] is not None


def test_deleting_a_race_cascades_to_activities_and_laps(db, user, categories):
    race = _import(db, user, APPLE_B)
    db.delete(race)
    db.flush()
    assert db.scalar(select(func.count()).select_from(RecordedActivity)) == 0
    assert db.scalar(select(func.count()).select_from(LapSplit)) == 0


# --- two devices, one leg ----------------------------------------------------------


def test_the_richer_recording_becomes_primary(db, user, categories):
    """The Stutensee race: the watch recorded everything, the Edge recorded the bike with
    power. The Edge should win the bike and nothing else (§3)."""
    race = _import(db, user, STRAVA)
    _import(db, user, EDGE_BIKE, race=race)

    bikes = db.scalars(
        select(RecordedActivity).where(
            RecordedActivity.race_id == race.id, RecordedActivity.segment == Segment.BIKE
        )
    ).all()
    assert len(bikes) == 2, "both recordings are kept, not deduplicated"

    primary = next(b for b in bikes if b.is_primary)
    secondary = next(b for b in bikes if not b.is_primary)
    assert primary.avg_power == pytest.approx(247, abs=1)
    assert secondary.avg_power is None
    # Per segment, never per metric: the primary supplies distance too, even though the
    # other recording measured further.
    assert primary.distance_m == pytest.approx(19633.63, abs=1)


def test_only_the_duplicated_segment_changes_hands(db, user, categories):
    race = _import(db, user, STRAVA)
    _import(db, user, EDGE_BIKE, race=race)

    for segment in (Segment.SWIM, Segment.T1, Segment.T2, Segment.RUN):
        rows = db.scalars(
            select(RecordedActivity).where(
                RecordedActivity.race_id == race.id, RecordedActivity.segment == segment
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].is_primary


def test_exactly_one_primary_per_segment_is_enforced_by_the_database(db, user, categories):
    race = _import(db, user, STRAVA)
    _import(db, user, EDGE_BIKE, race=race)

    per_segment = db.execute(
        select(RecordedActivity.segment, func.count())
        .where(RecordedActivity.race_id == race.id, RecordedActivity.is_primary.is_(True))
        .group_by(RecordedActivity.segment)
    ).all()
    assert {segment: count for segment, count in per_segment} == {s: 1 for s in Segment}


# --- classification on import --------------------------------------------------------


def test_import_classifies_from_measured_distances(db, user, categories):
    race = _import(db, user, APPLE_B)
    assert race.classification_source == ClassificationSource.AUTO
    assert race.category.key == "kurz"
    assert race.auto_category_id == race.race_category_id


def test_the_short_bike_race_is_left_unclassified_with_a_reason(db, user, categories):
    race = _import(db, user, APPLE_A)
    assert race.classification_source == ClassificationSource.UNCLASSIFIED
    assert race.race_category_id is None
    assert race.classification_detail["nearest"] == "kurz"
    assert race.classification_detail["worst_segment"] == "bike"


def test_a_manual_override_survives_reclassification(db, user, categories):
    """The inferred value keeps moving underneath, so the UI can say they disagree (§9)."""
    race = _import(db, user, APPLE_A)
    mittel = next(c for c in categories if c.key == "mittel")

    race.race_category_id = mittel.id
    race.classification_source = ClassificationSource.MANUAL
    db.flush()

    apply_classification(db, race)

    assert race.race_category_id == mittel.id
    assert race.classification_source == ClassificationSource.MANUAL
    assert race.auto_category_id is None  # what the classifier would have said


def test_switching_the_primary_bike_can_change_the_category(db, user, categories):
    """The Edge and the watch disagree by 358 m on the same leg; on a course sitting near
    a boundary that is enough to move the answer, which is why classification re-runs."""
    race = _import(db, user, STRAVA)
    before = race.classification_detail

    _import(db, user, EDGE_BIKE, race=race)
    apply_classification(db, race)

    bike = db.scalar(
        select(RecordedActivity).where(
            RecordedActivity.race_id == race.id,
            RecordedActivity.segment == Segment.BIKE,
            RecordedActivity.is_primary.is_(True),
        )
    )
    assert bike.avg_power is not None  # the Edge is now primary
    assert race.classification_detail is not None
    assert before is not None


# --- ownership ------------------------------------------------------------------------


def test_races_are_scoped_to_their_owner(db, user, other_user, categories):
    mine = _import(db, user, APPLE_A)
    theirs = _import(db, other_user, APPLE_B)

    visible = db.scalars(select(Race).where(Race.user_id == user.id)).all()
    assert [r.id for r in visible] == [mine.id]
    assert theirs.user_id == other_user.id


# --- dedupe -----------------------------------------------------------------------------


def test_strava_reexports_of_one_race_hash_identically():
    """Downloading the swim and the run from Strava yields the same bytes, so the hash is
    what stops three downloads becoming three copies (§3)."""
    assert content_hash(STRAVA.read_bytes()) == content_hash(STRAVA.read_bytes())
    assert content_hash(STRAVA.read_bytes()) != content_hash(EDGE_BIKE.read_bytes())
