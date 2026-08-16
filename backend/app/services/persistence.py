"""Turns a `ParsedFile` (app.services.fit_import) into persisted rows.

Three responsibilities live here:

1. Building `Upload.summary` — the parse preview the tray renders from, so it never
   has to re-read a file (§2 `Upload`).
2. Building `RecordedActivity` + `LapSplit` rows for one upload's sessions under a
   race, replacing rather than duplicating on re-persist (§3: "re-importing the
   same file for the same race replaces the activities").
3. `is_primary` reconciliation — richest data first (§2), exactly one primary row
   per segment, never transiently violating the partial unique index that
   enforces it.

Pure ORM/dataclass logic: no HTTP concerns (no `HTTPException`) and no file I/O
beyond what the caller already did — routers and `services/grouping.py` own
reading bytes off disk; this module only ever receives an already-parsed
`ParsedFile`.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.enums import Segment, UploadSource
from app.models.lap_split import LapSplit
from app.models.recorded_activity import RecordedActivity
from app.services.fit_import import ParsedDevice, ParsedFile, ParsedLap, ParsedSession
from app.services.gear import resolve_gear_for_activity


def sha256_hex(data: bytes) -> str:
    """Content hash `Upload.content_hash` dedupes on (§3 Strava export case)."""
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Upload.summary — parse preview for the tray
# --------------------------------------------------------------------------- #


def build_summary(parsed: ParsedFile) -> dict[str, Any]:
    """JSON preview stored on `Upload.summary` (§2) so `GET /uploads` renders the
    tray — and `services.grouping.propose_groupings` clusters same-day uploads —
    without re-reading the original file."""
    return {
        "kind": parsed.kind,
        "local_date": parsed.local_date.isoformat(),
        "utc_offset_s": parsed.utc_offset_s,
        "orphan_record_count": parsed.orphan_record_count,
        "warnings": parsed.warnings,
        "total_distance_m": parsed.total_distance_m,
        "devices": [
            {
                "manufacturer": d.manufacturer,
                "serial_number": d.serial_number,
                "product": d.product,
                "label": d.label,
            }
            for d in parsed.devices
        ],
        "sessions": [
            {
                "segment": s.segment,
                "sport": s.sport,
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
                "elapsed_time": s.elapsed_time,
                "distance_m": s.distance_m,
                "device_keys": s.device_keys,
            }
            for s in parsed.sessions
        ],
    }


# --------------------------------------------------------------------------- #
# Session/lap -> ORM row construction
# --------------------------------------------------------------------------- #


def _round_or_none(value: float | None) -> int | None:
    return round(value) if value is not None else None


def _pace_s_per_km(speed_m_s: float | None) -> float | None:
    """Seconds per kilometre. Derived from distance/time (via the parser's
    `avg_speed`, itself never the FIT `avg_speed` field per §3 Traps) at import
    time and stored — `RecordedActivity.avg_pace`/`LapSplit.avg_pace` are
    documented as holding this session/lap-level derived value rather than being
    recomputed on every read."""
    if not speed_m_s:
        return None
    return 1000.0 / speed_m_s


def _device_key(device_keys: list[str]) -> str | None:
    """A session can rarely see more than one paired device; `RecordedActivity`
    keeps a single `device_key` for gear inference (§3 Gear detection), so the
    first (already-sorted, hence stable) key wins. The full list survives in
    `raw_data` regardless."""
    return device_keys[0] if device_keys else None


def _session_devices(parsed: ParsedFile, session: ParsedSession) -> list[ParsedDevice]:
    """The `ParsedDevice` records behind one session's `device_keys`.

    The parser collects devices file-wide and references them per session by
    `"manufacturer:serial"` key, so gear resolution — which needs manufacturer and
    serial separately — has to map back. A key with no matching device is skipped
    rather than raising: a session naming a device the file never described is odd
    but harmless, and an import must not fail over gear metadata.
    """
    by_key = {f"{d.manufacturer}:{d.serial_number}": d for d in parsed.devices}
    return [by_key[key] for key in session.device_keys if key in by_key]


def _json_safe_track(gps_track: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """The parser's own `_json_safe` only runs over `raw_data` — `gps_track`
    points still carry a raw `datetime` `timestamp` (see `fit_import._build_
    session`), which `json.dumps` (and hence a JSONB insert) rejects outright.
    Verified against a real Postgres round-trip in tests, not just constructed
    in memory: an unconverted timestamp here fails at INSERT time, not at
    parse time, which is exactly the bug this exists to catch."""
    if not gps_track:
        return None
    return [
        {
            "lat": p["lat"],
            "lng": p["lng"],
            "elevation": p["elevation"],
            "timestamp": p["timestamp"].isoformat() if p["timestamp"] is not None else None,
        }
        for p in gps_track
    ]


def _activity_from_session(
    *,
    race_id: int,
    upload_id: int | None,
    segment: Segment,
    session: ParsedSession,
    source: UploadSource,
) -> RecordedActivity:
    return RecordedActivity(
        race_id=race_id,
        upload_id=upload_id,
        segment=segment,
        is_primary=False,  # reconciled afterward by reconcile_primary
        device_key=_device_key(session.device_keys),
        is_derived=False,
        source=source,
        external_id=None,
        start_time=session.start_time,
        elapsed_time=session.elapsed_time,
        moving_time=session.moving_time,
        distance_m=session.distance_m,
        elevation_gain_m=_round_or_none(session.elevation_gain_m),
        avg_hr=session.avg_hr,
        max_hr=session.max_hr,
        avg_power=session.avg_power,
        normalized_power=session.normalized_power,
        avg_pace=_pace_s_per_km(session.avg_speed),
        avg_speed=session.avg_speed,
        avg_cadence=_round_or_none(session.avg_cadence),
        avg_temp_c=session.avg_temp_c,
        rpe_estimated=session.rpe_estimated,
        avg_mets=session.avg_mets,
        bounds_n_lat=session.bounds_n,
        bounds_s_lat=session.bounds_s,
        bounds_e_lng=session.bounds_e,
        bounds_w_lng=session.bounds_w,
        gps_track=_json_safe_track(session.gps_track),
        raw_data=session.raw_data,  # already _json_safe from the parser
    )


def _lap_from_parsed(recorded_activity_id: int, segment: Segment, lap: ParsedLap) -> LapSplit:
    return LapSplit(
        recorded_activity_id=recorded_activity_id,
        sport=segment,
        lap_index=lap.lap_index,
        start_time=lap.start_time,
        total_time=lap.total_time,
        distance_m=lap.distance_m,
        avg_hr=lap.avg_hr,
        max_hr=lap.max_hr,
        avg_power=lap.avg_power,
        normalized_power=lap.normalized_power,
        avg_pace=_pace_s_per_km(lap.avg_speed),
        avg_speed=lap.avg_speed,
        elevation_gain_m=_round_or_none(lap.elevation_gain_m),
        cadence_avg=_round_or_none(lap.cadence_avg),
        is_partial=lap.is_partial,
        raw_data=lap.raw_data,
    )


def persist_upload_sessions(
    db: Session,
    *,
    race_id: int,
    upload_id: int,
    source: UploadSource,
    parsed: ParsedFile,
    segment_override: dict[int, Segment] | None = None,
) -> list[RecordedActivity]:
    """Persist every session of `parsed` as a `RecordedActivity` (+ its laps) under
    `race_id`, attributed to `upload_id`.

    `segment_override` maps a session's index in `parsed.sessions` to the `Segment`
    it should be filed under. Multisport files don't need it (the parser already
    validated the swim/t1/bike/t2/run order — §3 Traps). Grouping a single-sport
    upload does: its lone session's segment must come from the user's assignment
    (`services.grouping.resolve_segments`), since the parser's own guess is only a
    coarse `Sport` (swim/bike/run/other) and can't distinguish "an upload the user
    is filing as T1" from a genuine swim.

    Re-persisting the same (race_id, upload_id) pair — the same upload attached to
    the same race twice, e.g. because grouping is retried — *replaces* rather than
    duplicates: every existing `RecordedActivity` for this pair is deleted first
    (cascading to its laps) before the new rows are inserted, which is what makes
    the `(race_id, segment, upload_id)` uniqueness constraint mean "replace, don't
    duplicate" (§3) instead of raising on the second attempt.
    """
    db.query(RecordedActivity).filter(
        RecordedActivity.race_id == race_id,
        RecordedActivity.upload_id == upload_id,
    ).delete(synchronize_session=False)
    db.flush()

    activities: list[RecordedActivity] = []
    touched_segments: set[Segment] = set()
    for i, session in enumerate(parsed.sessions):
        segment = (
            segment_override[i]
            if segment_override and i in segment_override
            else Segment(session.segment)
        )
        activity = _activity_from_session(
            race_id=race_id,
            upload_id=upload_id,
            segment=segment,
            session=session,
            source=source,
        )
        db.add(activity)
        db.flush()  # need activity.id before laps can reference it
        for lap in session.laps:
            db.add(_lap_from_parsed(activity.id, segment, lap))

        # Sensor serials paired to this session resolve to gear automatically
        # (§3 Gear detection). A serial nobody has named yet is not an error — it
        # surfaces in /gear/unknown-devices for the user to name once, after which
        # POST /gear/{id}/devices backfills every past activity carrying it.
        resolve_gear_for_activity(db, activity, _session_devices(parsed, session))

        activities.append(activity)
        touched_segments.add(segment)

    db.flush()
    for segment in touched_segments:
        reconcile_primary(db, race_id=race_id, segment=segment)
    return activities


# --------------------------------------------------------------------------- #
# is_primary reconciliation
# --------------------------------------------------------------------------- #


def _primacy_key(activity: RecordedActivity) -> tuple[bool, bool, int]:
    """Sort key for picking the primary recording of a segment (§2 `is_primary`):
    richest data first — power beats no power, then longer duration — with a real
    recording always outranking a derived transition (relevant once an actual T1/T2
    is attached alongside a synthesized one, so the measurement wins over the
    inference)."""
    return (
        activity.avg_power is not None,
        not activity.is_derived,
        activity.elapsed_time or 0,
    )


def reconcile_primary(db: Session, *, race_id: int, segment: Segment) -> None:
    """Ensure exactly one `RecordedActivity` for (race_id, segment) is
    `is_primary=True` — the invariant a partial unique index enforces at the DB
    level, so this must never emit a state where two rows are briefly both True
    within the same transaction.

    Every candidate is flipped to `False` first and flushed (always safe: zero
    `True` rows can never violate a uniqueness index), *then* the richest
    candidate is flipped `True` and flushed. That ordering is deliberate — doing
    it in one pass (flip old off and new on together) risks a moment where the
    index sees two rows with `is_primary=True` if the flips land in the same
    statement/flush in the wrong order; splitting into two flushes removes the
    ordering dependency entirely rather than relying on getting it right.
    """
    candidates = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race_id, RecordedActivity.segment == segment)
        .all()
    )
    if not candidates:
        return
    for activity in candidates:
        activity.is_primary = False
    db.flush()
    winner = max(candidates, key=_primacy_key)
    winner.is_primary = True
    db.flush()


# --------------------------------------------------------------------------- #
# Derived transitions (§3 Single-sport uploads and manual grouping)
# --------------------------------------------------------------------------- #

# Generous upper bound for a genuine T1/T2 gap: long enough to span even a slow
# transition, short enough that two "legs" hours apart are obviously unrelated
# activities rather than one race. PLAN §3 asks for "negative or implausibly
# large" to be rejected without pinning a number; this is that number.
MAX_TRANSITION_GAP_S = 3600


class TransitionError(ValueError):
    """A synthesized transition's gap is negative or implausibly large (§3) — the
    grouping is wrong and the caller must reject it rather than write a nonsense
    transition."""


def build_derived_transition(
    *,
    race_id: int,
    segment: Segment,
    prev_end: datetime,
    next_start: datetime,
    source: UploadSource,
) -> RecordedActivity:
    """Synthesize a T1/T2 `RecordedActivity` from the gap between two grouped
    single-sport legs (§3). Carries only a duration — no distance, GPS track, or
    HR, since it is an inference from two timestamps, not a measurement."""
    gap_s = (next_start - prev_end).total_seconds()
    if gap_s < 0:
        raise TransitionError(
            f"{segment.value} gap is negative ({gap_s:.0f}s) — these legs overlap, "
            "so this grouping is wrong."
        )
    if gap_s > MAX_TRANSITION_GAP_S:
        raise TransitionError(
            f"{segment.value} gap is implausibly large ({gap_s / 60:.1f} min) — "
            "these legs don't look like they belong to the same race."
        )
    return RecordedActivity(
        race_id=race_id,
        upload_id=None,
        segment=segment,
        is_primary=False,
        device_key=None,
        is_derived=True,
        source=source,
        external_id=None,
        start_time=prev_end,
        elapsed_time=round(gap_s),
        moving_time=None,
        distance_m=None,
        elevation_gain_m=None,
        avg_hr=None,
        max_hr=None,
        avg_power=None,
        normalized_power=None,
        avg_pace=None,
        avg_speed=None,
        avg_cadence=None,
        avg_temp_c=None,
        rpe_estimated=None,
        avg_mets=None,
        bounds_n_lat=None,
        bounds_s_lat=None,
        bounds_e_lng=None,
        bounds_w_lng=None,
        gps_track=None,
        raw_data=None,
    )


def replace_derived_transition(db: Session, activity: RecordedActivity) -> RecordedActivity:
    """Insert a synthesized transition, first deleting any existing derived row for
    the same (race_id, segment).

    Derived transitions always have `upload_id=NULL`, so the DB's
    `(race_id, segment, upload_id)` uniqueness constraint can't catch a second
    grouping pass creating a second one — Postgres treats every NULL as distinct
    from every other NULL for uniqueness purposes. This is the application-level
    equivalent for the one column that constraint can't cover.
    """
    db.query(RecordedActivity).filter(
        RecordedActivity.race_id == activity.race_id,
        RecordedActivity.segment == activity.segment,
        RecordedActivity.is_derived.is_(True),
    ).delete(synchronize_session=False)
    db.flush()
    db.add(activity)
    db.flush()
    reconcile_primary(db, race_id=activity.race_id, segment=activity.segment)
    return activity


# --------------------------------------------------------------------------- #
# Race location from session bounding boxes
# --------------------------------------------------------------------------- #


def race_location_from_sessions(sessions: list[ParsedSession]) -> tuple[float | None, float | None]:
    """Centroid of every session's bounding box (§3 import flow step 5): simpler
    and more robust than picking one session's box, and there's no requirement
    that it be exactly the swim start — it's a draft location the user can
    correct."""
    lats = [v for s in sessions for v in (s.bounds_n, s.bounds_s) if v is not None]
    lngs = [v for s in sessions for v in (s.bounds_e, s.bounds_w) if v is not None]
    if not lats or not lngs:
        return None, None
    return (min(lats) + max(lats)) / 2, (min(lngs) + max(lngs)) / 2
