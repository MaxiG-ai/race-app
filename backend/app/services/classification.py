"""Applying classification to a stored race (§9).

`classify.py` holds the pure decision; this is the part that reads the database, writes
the result, and honours a manual override.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClassificationSource, Race, RaceCategory, RecordedActivity, Segment
from app.services.classify import SCORING_SEGMENTS, classify


def primary_distances(db: Session, race_id: int) -> dict[Segment, float | None]:
    """Measured distance per scoring discipline, from the primary recording of each.

    Primary, not any recording: on a leg captured by two devices the Edge and the watch
    disagree by hundreds of metres, and the one that counts is the one that counts
    everywhere else (§3).
    """
    rows = db.execute(
        select(RecordedActivity.segment, RecordedActivity.distance_m).where(
            RecordedActivity.race_id == race_id,
            RecordedActivity.is_primary.is_(True),
            RecordedActivity.segment.in_(SCORING_SEGMENTS),
        )
    ).all()
    found = {segment: distance for segment, distance in rows}
    return {segment: found.get(segment) for segment in SCORING_SEGMENTS}


def active_categories(db: Session) -> list[RaceCategory]:
    return list(
        db.scalars(
            select(RaceCategory)
            .where(RaceCategory.is_active.is_(True))
            .order_by(RaceCategory.sort_order)
        )
    )


def apply_classification(db: Session, race: Race) -> Race:
    """Re-run classification for one race.

    `auto_category_id` always moves; `race_category_id` moves only when the user has not
    overridden it. That separation is the whole point: the inferred answer stays visible
    underneath a manual choice, so the UI can flag a disagreement — which is usually how
    a mistyped or mis-measured distance gets noticed.
    """
    categories = active_categories(db)
    result = classify(primary_distances(db, race.id), categories)
    matched = next((c for c in categories if c.key == result.category_key), None)

    race.auto_category_id = matched.id if matched else None
    race.classification_detail = result.as_detail()
    race.classified_at = datetime.now(UTC)

    if race.classification_source != ClassificationSource.MANUAL:
        race.race_category_id = matched.id if matched else None
        race.classification_source = (
            ClassificationSource.AUTO if matched else ClassificationSource.UNCLASSIFIED
        )

    db.flush()
    return race


def classify_race_id(db: Session, race_id: int) -> Race | None:
    race = db.get(Race, race_id)
    return apply_classification(db, race) if race is not None else None
