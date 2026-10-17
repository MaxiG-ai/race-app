"""The database side of import and grouping (docs/features/import.md#rules,
docs/features/grouping.md#rules).

`fit_import` parses bytes; this module decides what a parse becomes. Files are always
dropped onto a race (a new one, or the race page they belong to): each one becomes
recordings of that race, or waits on it for the owner to pick its leg.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LapSplit, Race, RecordedActivity, Upload
from app.models.enums import RaceType, Segment, Sport, UploadKind, UploadStatus
from app.services import classify, gear, results, weather
from app.services.fit_import import FitParseError, ParsedFile, ParsedSession, parse_fit

log = logging.getLogger(__name__)

# --- grouping constants ------------------------------------------------------------

# A file starting or ending further than this from the race's other recordings is from
# another day, or another session: it belongs to a different race.
SAME_RACE_WINDOW = timedelta(hours=8)
# A gap longer than this between two legs means the grouping is wrong.
MAX_TRANSITION_S = 3600

_SPORT_SEGMENT = {Sport.SWIM: Segment.SWIM, Sport.BIKE: Segment.BIKE, Sport.RUN: Segment.RUN}
_TRANSITIONS = ((Segment.SWIM, Segment.T1, Segment.BIKE), (Segment.BIKE, Segment.T2, Segment.RUN))
_SEGMENT_LABELS = {"swim": "swim", "t1": "T1", "bike": "bike", "t2": "T2", "run": "run"}


class GroupingError(ValueError):
    """The file or grouping doesn't fit the race; nothing was written."""


def type_label(race_type: RaceType) -> str:
    return "triathlon" if race_type is RaceType.TRIATHLON else f"{race_type.value} race"


def infer_type(files: list[ParsedFile]) -> RaceType:
    """For a race made from files: files that all name one sport make that single-sport
    race. Anything else (a multisport file, several sports, a file that doesn't say its
    sport) is a triathlon, where an unknown file waits for the owner to pick its leg
    instead of being guessed into the one leg of a single-sport race."""
    sports = {f.detected_sport for f in files}
    if len(sports) == 1 and (sport := sports.pop()) in _SPORT_SEGMENT:
        return RaceType(sport.value)  # type: ignore[union-attr]
    return RaceType.TRIATHLON


def segment_for(race: Race, parsed: ParsedFile) -> Segment | None:
    """The leg a single-sport file fills, from its detected sport. None: a multisport file
    (its sessions say), or a file the owner must place (an `other` file in a triathlon)."""
    allowed = race.segments()
    if parsed.kind is UploadKind.MULTISPORT:
        if race.race_type is not RaceType.TRIATHLON:
            raise GroupingError(
                f"This is a multisport file, but this is a {type_label(race.race_type)}. "
                "Make it a triathlon to add the file."
            )
        return None
    segment = _SPORT_SEGMENT.get(parsed.detected_sport)  # type: ignore[arg-type]
    if segment is None:
        return allowed[0] if len(allowed) == 1 else None
    if segment not in allowed:
        raise GroupingError(
            f"This is a {segment} file, but this is a {type_label(race.race_type)}. "
            "Change the race type to add it."
        )
    return segment


def check_same_race(race: Race, parsed: ParsedFile) -> None:
    recorded = [a for a in race.activities if not a.is_derived]
    if not recorded:
        return
    start = min(a.start_time for a in recorded)
    end = max(a.end_time for a in recorded)
    if parsed.start_time > end + SAME_RACE_WINDOW or parsed.end_time < start - SAME_RACE_WINDOW:
        day = parsed.local_date
        raise GroupingError(
            f"This file was recorded on {day.day} {day:%b %Y}, hours away from this race's "
            "other recordings. It looks like a different race."
        )


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


def assign(db: Session, race: Race, given: dict[int, Segment]) -> None:
    """POST /api/races/{id}/attach: file the uploads that waited on this race for the owner
    to pick their leg. The caller commits; any GroupingError means roll back."""
    if not given:
        raise GroupingError("Pick a leg for at least one file.")
    uploads = list(db.scalars(select(Upload).where(Upload.id.in_(given))))
    if len(uploads) != len(given):
        raise GroupingError("Some of these files don't exist.")
    for u in uploads:
        if u.status is not UploadStatus.PARSED or u.race_id not in (None, race.id):
            raise GroupingError(f"{u.filename or 'This file'} is already part of a race.")
        if u.kind is not UploadKind.SINGLE_SPORT:
            raise GroupingError(f"{u.filename or 'This file'} is a multisport file.")
        segment = given[u.id]
        if segment not in race.segments():
            raise GroupingError(f"A {type_label(race.race_type)} has no {segment} leg.")
        parsed = parse_fit(read_original(u))
        check_same_race(race, parsed)
        first = not _has_recordings(race)
        add_recordings(db, race, u, parsed, segment)
        if first:
            _take_date_and_place(race, parsed)
    synthesize_transitions(db, race)
    refresh(db, race)


def _has_recordings(race: Race) -> bool:
    return any(not a.is_derived for a in race.activities)


def _take_date_and_place(race: Race, parsed: ParsedFile) -> None:
    """A race takes its date and place from its first recording (Trap 11: local date)."""
    race.date = parsed.local_date
    if race.location_lat is None and race.location_lng is None:
        race.location_lat, race.location_lng = parsed.location


def remove_upload(db: Session, upload: Upload) -> str | None:
    """DELETE /api/uploads/{id}: the upload and every recording made from it. Returns the
    stored file for the caller to unlink once the transaction has committed."""
    race = db.get(Race, upload.race_id) if upload.race_id is not None else None
    if race is not None:
        gone = [a for a in race.activities if a.upload_id == upload.id]
        for a in gone:
            race.activities.remove(a)
        db.flush()
        for segment in {a.segment for a in gone}:
            reconcile_primary(db, race, segment)
        synthesize_transitions(db, race)
        refresh(db, race)
    path = upload.stored_path
    db.delete(upload)
    return path


def discard_uploads(db: Session, race: Race) -> list[str]:
    """A deleted race takes its uploads with it: there is no tray to send them back to."""
    uploads = list(db.scalars(select(Upload).where(Upload.race_id == race.id)))
    for u in uploads:
        db.delete(u)
    return [u.stored_path for u in uploads if u.stored_path]


# --- uploads ----------------------------------------------------------------------------


@dataclass
class Outcome:
    filename: str | None
    result: str  # attached | needs_segment | duplicate | failed
    message: str
    upload_id: int | None = None
    race_id: int | None = None


@dataclass
class Received:
    """A file that passed the checks and parsed; nothing is written yet."""

    filename: str | None
    data: bytes
    content_hash: str
    parsed: ParsedFile
    upload: Upload | None  # a row to reuse: an earlier failed copy, or a leftover


def _is_fit(data: bytes) -> bool:
    return len(data) >= 12 and data[8:12] == b".FIT"


def _stored_path(upload_id: int) -> Path:
    return get_settings().upload_dir / f"{upload_id}.fit"


def read_original(upload: Upload) -> bytes:
    if not upload.stored_path or not Path(upload.stored_path).exists():
        raise FitParseError(f"The original file of upload {upload.id} is missing.")
    return Path(upload.stored_path).read_bytes()


def summary(parsed: ParsedFile) -> dict:
    """Parse preview, so the file list renders without re-reading files."""
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
    upload.kind = parsed.kind
    upload.detected_sport = parsed.detected_sport
    upload.detected_start_time = parsed.start_time
    upload.detected_end_time = parsed.end_time
    upload.detected_distance_m = parsed.distance_m
    upload.summary = summary(parsed)
    upload.error = None


def check(db: Session, race: Race | None, filename: str | None, data: bytes) -> Received | Outcome:
    """Size, header, deduplication and the parse. Writes nothing."""
    if len(data) > get_settings().max_upload_bytes:
        return Outcome(filename, "failed", "The file is larger than the upload limit.")
    if not _is_fit(data):
        return Outcome(filename, "failed", "This is not a FIT file.")

    content_hash = hashlib.sha256(data).hexdigest()
    upload = db.scalar(select(Upload).where(Upload.content_hash == content_hash))
    if (
        upload is not None
        and upload.race_id is not None
        and upload.status is not UploadStatus.FAILED
    ):
        if race is not None and upload.race_id == race.id:
            return Outcome(
                filename, "duplicate", "Already part of this race.", upload.id, upload.race_id
            )
        other = db.get(Race, upload.race_id)
        assert other is not None
        return Outcome(
            filename,
            "duplicate",
            f"Identical to {upload.filename or 'a file'} in “{other.name}” "
            f"({other.date.day} {other.date:%b %Y}), which you already have.",
            upload.id,
            other.id,
        )
    # Otherwise the file is new, an identical copy failed before (parse it again: the
    # parser may have improved, and the owner needs the real error), or it was left over
    # without a race by an earlier version of the app (adopt it).
    try:
        parsed = parse_fit(data)
    except FitParseError as exc:
        return Outcome(filename, "failed", str(exc), upload.id if upload else None)
    return Received(filename, data, content_hash, parsed, upload)


def _store(db: Session, received: Received) -> Upload:
    upload = received.upload
    if upload is None:
        upload = Upload(
            filename=received.filename,
            content_hash=received.content_hash,
            status=UploadStatus.PARSED,
        )
        db.add(upload)
        db.flush()
    if not upload.stored_path or not Path(upload.stored_path).exists():
        path = _stored_path(upload.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(received.data)
        upload.stored_path = str(path)
    return upload


def _describe(race: Race, upload: Upload, parsed: ParsedFile, segment: Segment | None) -> str:
    if parsed.kind is UploadKind.MULTISPORT:
        legs = ", ".join(_SEGMENT_LABELS[s.segment] for s in parsed.sessions if s.segment)
        return f"Added: {legs}."
    others = [a for a in race.activities if a.segment == segment and a.upload_id != upload.id]
    if others:
        return f"Added as a second {segment} recording. The first one stays primary."
    return f"Added as the {segment} leg."


def attach_file(db: Session, race: Race, received: Received) -> Outcome:
    """One file onto `race`. Commits per file, so one bad file doesn't undo the others."""
    filename, parsed = received.filename, received.parsed
    try:
        segment = segment_for(race, parsed)
        check_same_race(race, parsed)
    except GroupingError as exc:
        return Outcome(filename, "failed", str(exc), race_id=race.id)
    first = not _has_recordings(race)

    upload = _store(db, received)
    new_file = received.upload is None and upload.stored_path
    apply_parse(upload, parsed)
    upload.race_id = race.id
    if parsed.kind is UploadKind.SINGLE_SPORT and segment is None:
        upload.status = UploadStatus.PARSED
        db.commit()
        return Outcome(
            filename, "needs_segment", "Pick the leg this file belongs to.", upload.id, race.id
        )
    try:
        add_recordings(db, race, upload, parsed, segment)
        try:
            synthesize_transitions(db, race)
        except GroupingError:
            if parsed.kind is not UploadKind.MULTISPORT:
                raise
            # A watch can skip recording a transition, and this file's own legs are
            # implausibly far apart: keep the recordings without the guess.
            log.warning("No derived transition for race %s", race.id, exc_info=True)
    except GroupingError as exc:
        db.rollback()
        if new_file:
            Path(new_file).unlink(missing_ok=True)
        return Outcome(filename, "failed", str(exc), race_id=race.id)
    if first:
        _take_date_and_place(race, parsed)
    message = _describe(race, upload, parsed, segment)
    db.commit()
    return Outcome(filename, "attached", message, upload.id, race.id)


def receive(
    db: Session,
    files: list[tuple[str | None, bytes]],
    race: Race | None = None,
    race_type: RaceType | None = None,
) -> tuple[Race | None, list[Outcome]]:
    """POST /api/races/import (no `race`: one is made from the files) and
    POST /api/races/{id}/uploads. Outcomes come back in the order the files were sent."""
    outcomes: dict[int, Outcome] = {}
    received: list[tuple[int, Received]] = []
    seen: dict[str, str | None] = {}
    for i, (filename, data) in enumerate(files):
        result = check(db, race, filename, data)
        if isinstance(result, Received) and result.content_hash in seen:
            first = seen[result.content_hash] or "another file"
            result = Outcome(filename, "duplicate", f"The same file as {first}, sent twice.")
        if isinstance(result, Outcome):
            outcomes[i] = result
            continue
        seen[result.content_hash] = filename
        received.append((i, result))

    created = race is None
    if race is None:
        if not received:
            return None, _in_order(outcomes)
        kind = race_type or infer_type([r.parsed for _, r in received])
        race = Race(date=date.today(), race_type=kind)  # private by default
        db.add(race)
        db.commit()
    had_recordings = _has_recordings(race)

    for i, r in sorted(received, key=lambda item: item[1].parsed.start_time):
        outcomes[i] = attach_file(db, race, r)
    done = [o for o in outcomes.values() if o.result in ("attached", "needs_segment")]
    if created and not done:
        db.delete(race)  # nothing fitted: don't leave an empty race behind
        db.commit()
        for o in outcomes.values():
            o.race_id = None
        return None, _in_order(outcomes)
    if any(o.result == "attached" for o in done):
        refresh(db, race, with_weather=not had_recordings)
        db.commit()
    return race, _in_order(outcomes)


def _in_order(outcomes: dict[int, Outcome]) -> list[Outcome]:
    return [outcomes[i] for i in sorted(outcomes)]


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


def refresh(db: Session, race: Race, *, with_weather: bool = False) -> None:
    """Everything that follows from the recordings, after they changed: classification,
    weather when asked, and a prefilled official result."""
    enrich(db, race, with_weather=with_weather)
    results.prefill(race)


def reimport(db: Session, upload: Upload) -> str:
    """Re-parse a stored original with the current parser (`cli reimport`)."""
    parsed = parse_fit(read_original(upload))
    apply_parse(upload, parsed)
    if upload.status is UploadStatus.FAILED:
        upload.status = UploadStatus.PARSED
    race = db.get(Race, upload.race_id) if upload.race_id is not None else None
    if race is not None and upload.status is UploadStatus.GROUPED:
        segment = db.scalar(
            select(RecordedActivity.segment).where(RecordedActivity.upload_id == upload.id)
        )
        add_recordings(db, race, upload, parsed, segment)
        synthesize_transitions(db, race)
        refresh(db, race)
    db.commit()
    return "updated"
