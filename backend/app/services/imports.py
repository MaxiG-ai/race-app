"""The database side of import and grouping (docs/features/import.md#rules,
docs/features/grouping.md#rules).

`fit_import` parses bytes; this module decides what a parse becomes: an upload in the
tray, a new race, or extra recordings on an existing race.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LapSplit, Race, RecordedActivity, Upload
from app.models.enums import Segment, Sport, UploadKind, UploadStatus
from app.services import classify, gear, weather
from app.services.fit_import import FitParseError, ParsedFile, ParsedSession, parse_fit

log = logging.getLogger(__name__)

# --- grouping constants ------------------------------------------------------------

# Proposal window: legs within this span on the same local day. Proposals are only
# ever suggestions: a Sunday brick session looks exactly like a race.
PROPOSAL_WINDOW = timedelta(hours=8)
# A gap longer than this between two legs means the grouping is wrong.
MAX_TRANSITION_S = 3600

_SPORT_SEGMENT = {Sport.SWIM: Segment.SWIM, Sport.BIKE: Segment.BIKE, Sport.RUN: Segment.RUN}
_TRANSITIONS = ((Segment.SWIM, Segment.T1, Segment.BIKE), (Segment.BIKE, Segment.T2, Segment.RUN))


class GroupingError(ValueError):
    """The requested grouping is wrong; nothing was written."""


def propose(uploads: list[Upload]) -> list[dict]:
    """Suggested groups among tray uploads. Reads only; never writes."""
    tray = sorted(
        (u for u in uploads if u.kind is UploadKind.SINGLE_SPORT and u.detected_start_time),
        key=lambda u: u.detected_start_time,  # type: ignore[arg-type, return-value]
    )
    clusters: list[list[Upload]] = []
    for u in tray:
        head = clusters[-1][0] if clusters else None
        if (
            head
            and u.detected_start_time - head.detected_start_time <= PROPOSAL_WINDOW  # type: ignore[operator]
            and (u.summary or {}).get("local_date") == (head.summary or {}).get("local_date")
        ):
            clusters[-1].append(u)
        else:
            clusters.append([u])
    proposals = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        sports = [u.detected_sport for u in cluster]
        segments = {
            u.id: _SPORT_SEGMENT[u.detected_sport]
            for u in cluster
            if u.detected_sport in _SPORT_SEGMENT and sports.count(u.detected_sport) == 1
        }
        proposals.append(
            {
                "upload_ids": [u.id for u in cluster],
                "segments": segments,
                "ambiguous": len(segments) < len(cluster),
            }
        )
    return proposals


def resolve_segments(uploads: list[Upload], given: dict[int, Segment]) -> dict[int, Segment]:
    """Segments from the detected sport, ordered by start time; ask when ambiguous."""
    result: dict[int, Segment] = {}
    for u in uploads:
        if u.status is not UploadStatus.PARSED:
            raise GroupingError(f"Upload {u.id} is not in the tray.")
        if u.kind is not UploadKind.SINGLE_SPORT:
            raise GroupingError(f"Upload {u.id} is a multisport file; it groups itself.")
        segment = given.get(u.id) or _SPORT_SEGMENT.get(u.detected_sport)  # type: ignore[arg-type]
        if segment is None:
            raise GroupingError(f"Pick a segment for upload {u.id} ({u.filename}).")
        result[u.id] = segment
    auto = [s for uid, s in result.items() if uid not in given]
    if len(auto) != len(set(auto)):
        raise GroupingError("Two uploads have the same sport; pick their segments.")
    return result


def load_tray(db: Session, upload_ids: list[int]) -> list[Upload]:
    if not upload_ids:
        raise GroupingError("Select at least one upload.")
    uploads = list(db.scalars(select(Upload).where(Upload.id.in_(upload_ids))))
    if len(uploads) != len(set(upload_ids)):
        raise GroupingError("Some of the selected uploads don't exist.")
    return uploads


def reconcile_primary(db: Session, race: Race, segment: Segment) -> None:
    """Keep exactly one primary per segment; default to the richest recording (power
    beats none, a measurement beats a derived transition, then the longer duration).
    An existing real primary is kept, so attaching a second device never steals it."""
    candidates = [a for a in race.activities if a.segment == segment]
    if not candidates:
        return
    current = [a for a in candidates if a.is_primary]
    if len(current) == 1 and not current[0].is_derived:
        return
    winner = max(
        candidates, key=lambda a: (a.avg_power is not None, not a.is_derived, a.elapsed_time)
    )
    set_primary(db, winner)


def set_primary(db: Session, activity: RecordedActivity) -> None:
    """Move the flag in two flushes, so the partial unique index never sees two."""
    siblings = [a for a in activity.race.activities if a.segment == activity.segment]
    for a in siblings:
        a.is_primary = False
    db.flush()
    activity.is_primary = True
    db.flush()


def synthesize_transitions(db: Session, race: Race) -> None:
    """T1/T2 from the gaps between primary legs, where no real transition was recorded.
    Duration only: no distance, no track, no HR."""
    for before, transition, after in _TRANSITIONS:
        rows = [a for a in race.activities if a.segment == transition]
        if any(not a.is_derived for a in rows):
            continue
        primaries = race.primary_activities()
        prev, nxt = primaries.get(before), primaries.get(after)
        for stale in rows:
            race.activities.remove(stale)
        db.flush()
        if prev is None or nxt is None:
            continue
        gap = (nxt.start_time - prev.end_time).total_seconds()
        if gap < 0:
            raise GroupingError(f"The {before} and {after} legs overlap; they can't be one race.")
        if gap > MAX_TRANSITION_S:
            raise GroupingError(
                f"The {before} and {after} legs are {gap / 3600:.1f} h apart; "
                "they don't look like one race."
            )
        race.activities.append(
            RecordedActivity(
                segment=transition,
                is_derived=True,
                is_primary=True,
                source=prev.source,
                start_time=prev.end_time,
                elapsed_time=round(gap),
            )
        )
        db.flush()


def group(db: Session, upload_ids: list[int], given: dict[int, Segment]) -> Race:
    """POST /api/races/group. The caller commits; any GroupingError means roll back."""
    tray = load_tray(db, upload_ids)
    if len(tray) == 1 and tray[0].kind is UploadKind.MULTISPORT:
        # A multisport file sent back to the tray by a race deletion.
        upload = tray[0]
        if upload.status is not UploadStatus.PARSED:
            raise GroupingError(f"Upload {upload.id} is not in the tray.")
        parsed = parse_fit(read_original(upload))
        race = create_race(db, parsed)
        add_recordings(db, race, upload, parsed)
    else:
        segments = resolve_segments(tray, given)
        parsed = {u.id: parse_fit(read_original(u)) for u in tray}
        first = min(parsed.values(), key=lambda p: p.start_time)
        race = create_race(db, first)
        for u in sorted(tray, key=lambda u: parsed[u.id].start_time):
            add_recordings(db, race, u, parsed[u.id], segments[u.id])
    synthesize_transitions(db, race)
    enrich(db, race)
    return race


def attach(db: Session, race: Race, upload_ids: list[int], given: dict[int, Segment]) -> None:
    """POST /api/races/{id}/attach: backfill a leg or add a second device."""
    tray = load_tray(db, upload_ids)
    segments = resolve_segments(tray, given)
    for u in tray:
        parsed = parse_fit(read_original(u))
        add_recordings(db, race, u, parsed, segments[u.id])
    synthesize_transitions(db, race)
    enrich(db, race, with_weather=False)


# --- uploads ----------------------------------------------------------------------------


@dataclass
class Outcome:
    filename: str | None
    result: str  # created_race | in_tray | duplicate | failed
    message: str
    upload_id: int | None = None
    race_id: int | None = None


def _is_fit(data: bytes) -> bool:
    return len(data) >= 12 and data[8:12] == b".FIT"


def _stored_path(upload_id: int) -> Path:
    return get_settings().upload_dir / f"{upload_id}.fit"


def read_original(upload: Upload) -> bytes:
    if not upload.stored_path or not Path(upload.stored_path).exists():
        raise FitParseError(f"The original file of upload {upload.id} is missing.")
    return Path(upload.stored_path).read_bytes()


def summary(parsed: ParsedFile) -> dict:
    """Parse preview for the tray, so it renders without re-reading files."""
    return {
        "local_date": parsed.local_date.isoformat(),
        "orphan_record_count": parsed.orphan_record_count,
        "warnings": parsed.warnings,
        "device_key": parsed.device_key,
        "sessions": [
            {
                "segment": s.segment,
                "sport": s.sport,
                "start_time": s.start_time.isoformat(),
                "elapsed_time": s.elapsed_time,
                "distance_m": s.distance_m,
            }
            for s in parsed.sessions
        ],
    }


def apply_parse(upload: Upload, parsed: ParsedFile) -> None:
    upload.status = UploadStatus.GROUPED if upload.race_id else UploadStatus.PARSED
    upload.kind = parsed.kind
    upload.detected_sport = parsed.detected_sport
    upload.detected_start_time = parsed.start_time
    upload.detected_end_time = parsed.end_time
    upload.detected_distance_m = parsed.distance_m
    upload.summary = summary(parsed)
    upload.error = None


def receive(db: Session, filename: str | None, data: bytes) -> Outcome:
    """One file of a POST /api/uploads. Commits per file, so one bad file doesn't undo
    the others."""
    if len(data) > get_settings().max_upload_bytes:
        return Outcome(filename, "failed", "The file is larger than the upload limit.")
    if not _is_fit(data):
        return Outcome(filename, "failed", "This is not a FIT file.")

    content_hash = hashlib.sha256(data).hexdigest()
    upload = db.scalar(select(Upload).where(Upload.content_hash == content_hash))
    if upload is not None and upload.status is not UploadStatus.FAILED:
        return Outcome(
            filename,
            "duplicate",
            f"Identical to an upload you already have ({upload.filename or 'unnamed'}, "
            f"uploaded {upload.uploaded_at:%d %b %Y}).",
            upload_id=upload.id,
            race_id=upload.race_id,
        )
    if upload is None:
        upload = Upload(filename=filename, content_hash=content_hash, status=UploadStatus.FAILED)
        db.add(upload)
        db.flush()
        path = _stored_path(upload.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        upload.stored_path = str(path)
    # else: an identical file failed before. Parse it again: the parser may have improved,
    # and the owner needs the real error, not "you already have this".

    try:
        parsed = parse_fit(data)
    except FitParseError as exc:
        upload.error = str(exc)
        db.commit()
        return Outcome(filename, "failed", str(exc), upload_id=upload.id)

    apply_parse(upload, parsed)
    if parsed.kind is UploadKind.SINGLE_SPORT:
        db.commit()
        return Outcome(filename, "in_tray", "Waiting in the tray to be grouped.", upload.id)

    race = create_race(db, parsed)
    add_recordings(db, race, upload, parsed)
    try:
        synthesize_transitions(db, race)  # a watch can skip recording a transition
    except GroupingError as exc:
        # The file's own legs are implausibly far apart; keep the race without the guess.
        log.warning("No derived transition for race %s: %s", race.id, exc)
    enrich(db, race)
    db.commit()
    return Outcome(filename, "created_race", f"Created race {race.id}.", upload.id, race.id)


def create_race(db: Session, parsed: ParsedFile) -> Race:
    lat, lng = parsed.location
    race = Race(date=parsed.local_date, location_lat=lat, location_lng=lng)  # private by default
    db.add(race)
    db.flush()
    return race


def add_recordings(
    db: Session,
    race: Race,
    upload: Upload,
    parsed: ParsedFile,
    segment: Segment | None = None,
) -> None:
    """Persist every session of `parsed` under `race`. `segment` files a single-sport
    upload. Re-running for the same upload updates its rows in place, so primary flags
    and manual gear survive a re-import."""
    upload.race_id = race.id
    upload.status = UploadStatus.GROUPED
    existing = {
        a.segment: a
        for a in db.scalars(
            select(RecordedActivity).where(
                RecordedActivity.race_id == race.id, RecordedActivity.upload_id == upload.id
            )
        )
    }
    touched = set()
    for session in parsed.sessions:
        seg = session.segment or segment
        if seg is None:
            raise ValueError("A single-sport upload needs a segment.")
        activity = existing.get(seg)
        if activity is None:
            activity = RecordedActivity(upload_id=upload.id)
            race.activities.append(activity)
        _fill(activity, Segment(seg), session, parsed)
        db.flush()
        gear.detect(db, activity)
        touched.add(Segment(seg))
    for seg in touched:
        reconcile_primary(db, race, seg)


def _fill(activity: RecordedActivity, segment: Segment, s: ParsedSession, f: ParsedFile) -> None:
    activity.segment = segment
    activity.is_derived = False
    activity.device_key = f.device_key
    activity.start_time = s.start_time
    activity.elapsed_time = s.elapsed_time
    activity.moving_time = s.moving_time
    activity.distance_m = s.distance_m
    activity.elevation_gain_m = s.elevation_gain_m
    activity.avg_hr = s.avg_hr
    activity.max_hr = s.max_hr
    activity.avg_power = s.avg_power
    activity.normalized_power = s.normalized_power
    activity.avg_cadence = s.avg_cadence
    activity.avg_temp_c = s.avg_temp_c
    activity.rpe_estimated = s.rpe_estimated
    activity.time_in_zone = s.time_in_zone
    activity.bounds_n, activity.bounds_s, activity.bounds_e, activity.bounds_w = s.bounds or (
        None,
        None,
        None,
        None,
    )
    activity.gps_track = s.gps_track or None
    activity.progress_series = s.progress_series or None
    activity.raw_data = {
        "session": s.raw_data,
        "devices": [
            {"manufacturer": d.manufacturer, "serial_number": d.serial_number, "label": d.label}
            for d in s.devices
        ],
    }
    activity.laps = [
        LapSplit(
            sport=segment,
            lap_index=lap.lap_index,
            start_time=lap.start_time,
            total_time=lap.total_time,
            distance_m=lap.distance_m,
            elevation_gain_m=lap.elevation_gain_m,
            avg_hr=lap.avg_hr,
            max_hr=lap.max_hr,
            avg_power=lap.avg_power,
            normalized_power=lap.normalized_power,
            cadence_avg=lap.cadence_avg,
            is_partial=lap.is_partial,
            raw_data=lap.raw_data,
        )
        for lap in s.laps
    ]


def enrich(db: Session, race: Race, *, with_weather: bool = True) -> None:
    """Classification and weather. Never fails the import (import.md step 6)."""
    try:
        classify.classify_race(db, race)
    except Exception:
        log.exception("Classification failed for race %s", race.id)
    if with_weather:
        weather.refresh(race)


def reimport(db: Session, upload: Upload) -> str:
    """Re-parse a stored original with the current parser (`cli reimport`)."""
    parsed = parse_fit(read_original(upload))
    apply_parse(upload, parsed)
    race = db.get(Race, upload.race_id) if upload.race_id is not None else None
    if race is not None:
        segment = db.scalar(
            select(RecordedActivity.segment).where(RecordedActivity.upload_id == upload.id)
        )
        add_recordings(db, race, upload, parsed, segment)
        synthesize_transitions(db, race)
        enrich(db, race, with_weather=False)
    db.commit()
    return "updated"
