"""Turn a parsed FIT file into rows: race, recordings, laps, classification.

The parser stays pure (`fit_import`); this is where the database and the policy
decisions live.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ClassificationSource,
    LapSplit,
    Race,
    RaceCategory,
    RecordedActivity,
    Segment,
    Upload,
    User,
)
from app.models.base import SCORING_SEGMENTS, ActivitySource
from app.services.classify import classify
from app.services.fit_import import ParsedFit, SegmentData, local_date


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_duplicate(db: Session, user: User, digest: str) -> Upload | None:
    """Strava hands back the same bytes for every activity of one multisport upload, so
    this is a normal outcome rather than an error — the caller reports it, not hides it."""
    return db.scalar(
        select(Upload).where(Upload.user_id == user.id, Upload.content_hash == digest)
    )


def _richness(seg: SegmentData) -> tuple[int, float]:
    """Ordering key for choosing the primary recording of a segment.

    Power first — it is the clearest marker of the better instrument, and the reason the
    Edge beats the watch on a bike leg. Duration breaks ties.
    """
    return (1 if seg.avg_power is not None else 0, seg.elapsed_time)


def persist_parsed(
    db: Session,
    *,
    user: User,
    parsed: ParsedFit,
    upload: Upload | None = None,
    race: Race | None = None,
) -> Race:
    """Create or extend a race from one parsed file."""
    if race is None:
        race = Race(
            user_id=user.id,
            date=local_date(parsed.start_time, parsed.utc_offset_seconds),
            name="Untitled race",
        )
        _apply_location(race, parsed)
        db.add(race)
        db.flush()

    for seg in parsed.segments:
        activity = _build_activity(race, seg, parsed, upload)
        _set_primary(db, race, activity)
        db.add(activity)
        db.flush()
        for lap in seg.laps:
            db.add(
                LapSplit(
                    recorded_activity_id=activity.id,
                    sport=seg.segment,
                    lap_index=lap.lap_index,
                    start_time=lap.start_time,
                    total_time=int(round(lap.total_time)),
                    distance_m=lap.distance_m,
                    avg_hr=lap.avg_hr,
                    max_hr=lap.max_hr,
                    avg_power=lap.avg_power,
                    normalized_power=lap.normalized_power,
                    avg_speed=lap.avg_speed,
                    elevation_gain_m=lap.elevation_gain_m,
                    cadence_avg=lap.cadence_avg,
                    is_partial=lap.is_partial,
                    raw_data=lap.raw,
                )
            )

    if upload is not None:
        upload.race_id = race.id

    db.flush()
    apply_classification(db, race)
    return race


def _apply_location(race: Race, parsed: ParsedFit) -> None:
    for seg in parsed.segments:
        if seg.bounds:
            race.location_lat = (seg.bounds["north"] + seg.bounds["south"]) / 2
            race.location_lng = (seg.bounds["east"] + seg.bounds["west"]) / 2
            return


def _build_activity(
    race: Race, seg: SegmentData, parsed: ParsedFit, upload: Upload | None
) -> RecordedActivity:
    bounds = seg.bounds or {}
    return RecordedActivity(
        race_id=race.id,
        upload_id=upload.id if upload else None,
        segment=seg.segment,
        source=ActivitySource.FIT_UPLOAD,
        device_key=parsed.creator_device_key,
        start_time=seg.start_time,
        # Session totals, never lap sums: Apple drops the trailing partial bike lap and
        # summing would understate that leg by 3.7 km (§3 Traps 1).
        elapsed_time=int(round(seg.elapsed_time)),
        moving_time=int(round(seg.moving_time)) if seg.moving_time is not None else None,
        distance_m=seg.distance_m,
        elevation_gain_m=seg.elevation_gain_m,
        avg_hr=seg.avg_hr,
        max_hr=seg.max_hr,
        avg_power=seg.avg_power,
        normalized_power=seg.normalized_power,
        avg_speed=seg.avg_speed,
        avg_cadence=seg.avg_cadence,
        avg_temp_c=seg.avg_temp_c,
        rpe_estimated=seg.rpe_estimated,
        avg_mets=seg.avg_mets,
        bounds_north=bounds.get("north"),
        bounds_south=bounds.get("south"),
        bounds_east=bounds.get("east"),
        bounds_west=bounds.get("west"),
        gps_track=seg.gps_track or None,
        progress_series=seg.progress_series or None,
        raw_data=seg.raw,
        is_primary=False,  # decided by _set_primary
    )


def _set_primary(db: Session, race: Race, candidate: RecordedActivity) -> None:
    """At most one primary recording per segment.

    Assembly is per segment, never per metric: whichever recording wins supplies every
    displayed number for that leg (§3).
    """
    existing = list(
        db.scalars(
            select(RecordedActivity).where(
                RecordedActivity.race_id == race.id,
                RecordedActivity.segment == candidate.segment,
            )
        )
    )
    if not existing:
        candidate.is_primary = True
        return

    current = next((a for a in existing if a.is_primary), None)
    if current is None:
        candidate.is_primary = True
        return

    current_rank = (1 if current.avg_power is not None else 0, current.elapsed_time)
    candidate_rank = (
        1 if candidate.avg_power is not None else 0,
        candidate.elapsed_time,
    )
    if candidate_rank > current_rank:
        current.is_primary = False
        db.flush()  # release the partial unique index before claiming it
        candidate.is_primary = True


def apply_classification(db: Session, race: Race) -> Race:
    """Classify from the primary recordings, never overwriting a manual choice (§9)."""
    categories = list(db.scalars(select(RaceCategory)))
    distances: dict[Segment, float | None] = {}
    for segment in SCORING_SEGMENTS:
        activity = db.scalar(
            select(RecordedActivity).where(
                RecordedActivity.race_id == race.id,
                RecordedActivity.segment == segment,
                RecordedActivity.is_primary.is_(True),
            )
        )
        distances[segment] = activity.distance_m if activity else None

    result = classify(distances, categories)  # type: ignore[arg-type]
    matched = next((c for c in categories if c.key == result.category_key), None)

    race.auto_category_id = matched.id if matched else None
    race.classification_detail = result.as_detail()
    race.classified_at = datetime.now(UTC)

    # A manual override is never silently replaced; only the inferred value moves.
    if race.classification_source != ClassificationSource.MANUAL:
        race.race_category_id = matched.id if matched else None
        race.classification_source = (
            ClassificationSource.AUTO if matched else ClassificationSource.UNCLASSIFIED
        )
    db.flush()
    return race
