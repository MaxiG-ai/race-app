"""M2.5 Grouping (PLAN.md §3 "Single-sport uploads and manual grouping"): turning
a handful of ungrouped `Upload` rows into a `Race`, plus the tray's suggested
groupings and the derived T1/T2 transitions a grouped race needs.

This is the path Strava will eventually reuse (§3, §9) — everything here operates
on `Upload` rows regardless of `source`, never on FIT bytes directly except to
re-parse a single-sport upload's stored file (the tray only keeps a preview in
`Upload.summary`, not the full session/lap data `RecordedActivity` needs).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enums import RaceType, Segment, Sport, UploadKind, UploadStatus
from app.models.race import Race
from app.models.recorded_activity import RecordedActivity
from app.models.upload import Upload
from app.models.user import User
from app.schemas.upload import UploadGroupProposal
from app.services import persistence
from app.services.fit_import import FitParseError, ParsedFile, parse_fit
from app.services.weather import apply_weather_to_race, fetch_weather

# Proposal clustering window: uploads on the same local day, starting within this
# many hours of the earliest upload in the cluster, are suggested as one group.
# PLAN §3 says "within a few hours of each other" without pinning a number; this
# is generous enough to span a full triathlon (swim start to run finish is
# routinely 3-6h) without reaching into a same-day-but-unrelated activity.
#
# Proposals are SUGGESTIONS ONLY and must never be auto-committed anywhere in
# this module or its caller — a Sunday brick session (swim, then bike, then run,
# hours apart, same watch) looks identical to a race from the data alone, and
# only the user knows which it is. Do not "improve" this into an auto-group.
PROPOSAL_WINDOW = timedelta(hours=8)

_SPORT_TO_SEGMENT: dict[Sport, Segment] = {
    Sport.SWIM: Segment.SWIM,
    Sport.BIKE: Segment.BIKE,
    Sport.RUN: Segment.RUN,
}

# (leading segment, transition segment, trailing segment) — the two gaps a
# grouped race can synthesize a transition for.
_TRANSITION_PAIRS: tuple[tuple[Segment, Segment, Segment], ...] = (
    (Segment.SWIM, Segment.T1, Segment.BIKE),
    (Segment.BIKE, Segment.T2, Segment.RUN),
)


# --------------------------------------------------------------------------- #
# Tray proposals (GET /uploads)
# --------------------------------------------------------------------------- #


def propose_groupings(uploads: list[Upload]) -> list[UploadGroupProposal]:
    """Suggest groupings among ungrouped, single-sport uploads: same local day,
    clustered within `PROPOSAL_WINDOW` of each other. Never commits anything —
    purely advisory, for the tray to render and the user to confirm or ignore.

    Segment assignment in a proposal is only filled in where it's unambiguous
    (each detected sport appears at most once in the cluster); an ambiguous
    upload (two runs, an `other`) is included in `upload_ids` but left out of
    `segments`, so the UI falls back to `SegmentAssign` for it.
    """
    candidates = [
        u
        for u in uploads
        if u.status == UploadStatus.PARSED
        and u.kind == UploadKind.SINGLE_SPORT
        and u.detected_start_time is not None
    ]
    candidates.sort(key=lambda u: u.detected_start_time)

    clusters: list[list[Upload]] = []
    for u in candidates:
        if clusters and u.detected_start_time - clusters[-1][0].detected_start_time <= (
            PROPOSAL_WINDOW
        ):
            clusters[-1].append(u)
        else:
            clusters.append([u])

    proposals: list[UploadGroupProposal] = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        sport_counts: dict[Sport, int] = {}
        for u in cluster:
            if u.detected_sport is not None:
                sport_counts[u.detected_sport] = sport_counts.get(u.detected_sport, 0) + 1

        segments: dict[int, Segment] = {}
        for u in cluster:
            sport = u.detected_sport
            segment = _SPORT_TO_SEGMENT.get(sport) if sport is not None else None
            if segment is not None and sport_counts.get(sport, 0) == 1:
                segments[u.id] = segment

        proposals.append(UploadGroupProposal(upload_ids=[u.id for u in cluster], segments=segments))
    return proposals


# --------------------------------------------------------------------------- #
# Ownership + segment resolution shared by group/attach
# --------------------------------------------------------------------------- #


def assert_owned(db: Session, user: User, upload_ids: list[int]) -> list[Upload]:
    """§4 Tenancy: uploads are their own ownership root, so every id must be
    confirmed to belong to the caller *before* anything is created — never
    partially. Missing/foreign ids come back as 404, matching `get_owned_race`'s
    convention of not confirming to a caller whether a given id exists at all."""
    if not upload_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No uploads selected.")
    uploads = db.query(Upload).filter(Upload.id.in_(upload_ids), Upload.user_id == user.id).all()
    found_ids = {u.id for u in uploads}
    missing = [uid for uid in upload_ids if uid not in found_ids]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Upload(s) not found: {missing}")
    # Preserve the caller's requested order isn't required; keep DB order but make
    # it deterministic for downstream sorting by start time.
    return uploads


def resolve_segments(
    uploads: list[Upload], segment_assignments: dict[int, Segment] | None
) -> dict[int, Segment]:
    """upload.id -> Segment for every upload being grouped/attached.

    An explicit assignment (from `RaceGroupRequest.segment_assignments` /
    `RaceAttachRequest.segment_assignments`) always wins — that's exactly the
    escape hatch for an ambiguous upload (§3: "the API asks rather than
    guessing"). Absent an explicit assignment, the upload's own `detected_sport`
    is used, but only if it is unique among the uploads being grouped that also
    lack an explicit assignment — two autodetected uploads claiming the same
    sport (two runs) is rejected outright, naming the ambiguity, rather than
    guessing an order.

    Two *different* uploads legitimately resolving to the same segment (e.g. the
    two-device bike case, §3 Assembly) is fine and expected — that is not an
    ambiguity, it's two recordings of one segment, and `is_primary` picks a
    winner afterward.
    """
    assignments = dict(segment_assignments or {})
    result: dict[int, Segment] = {}
    auto_by_sport: dict[Sport, list[Upload]] = {}

    for u in uploads:
        if u.status != UploadStatus.PARSED:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Upload {u.id} is not available to group (status={u.status.value}).",
            )
        if u.kind != UploadKind.SINGLE_SPORT:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Upload {u.id} is a multisport file and groups itself on import; "
                "it can't be combined with other uploads.",
            )
        if u.id in assignments:
            result[u.id] = assignments[u.id]
            continue
        sport = u.detected_sport
        segment = _SPORT_TO_SEGMENT.get(sport) if sport is not None else None
        if segment is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Upload {u.id} has an ambiguous or unrecognised sport "
                f"({sport.value if sport else 'unknown'}) and needs an explicit "
                "segment assignment.",
            )
        auto_by_sport.setdefault(sport, []).append(u)
        result[u.id] = segment

    for sport, group in auto_by_sport.items():
        if len(group) > 1:
            ids = [u.id for u in group]
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Ambiguous grouping: uploads {ids} are all detected as "
                f"{sport.value}; assign segments explicitly.",
            )
    return result


def _reparse(upload: Upload) -> ParsedFile:
    """Single-sport uploads only keep a preview in `Upload.summary` — grouping
    needs the full session/lap data, so it re-reads the file retained on disk
    (§8 original-file retention exists exactly for this)."""
    if not upload.stored_path:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Upload {upload.id} has no stored file to group."
        )
    path = Path(upload.stored_path)
    if not path.exists():
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Upload {upload.id}'s stored file is missing on disk."
        )
    try:
        return parse_fit(path, filename=upload.filename)
    except FitParseError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Upload {upload.id} failed to re-parse: {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# Derived transitions
# --------------------------------------------------------------------------- #


def synthesize_transitions(db: Session, race_id: int) -> None:
    """Create/refresh T1 (swim->bike) and T2 (bike->run) as derived
    `RecordedActivity` rows, from the gap between each pair's *primary*
    recordings.

    Never touches a segment that already has a real (non-derived) recording —
    grouped-from-scratch races have none yet, but attaching to a race that came
    from a genuine multisport file (whose T1/T2 are real sessions, not
    inferences) must not clobber them with a guess. Only derived rows are ever
    replaced, which is what makes re-running this after an `attach` (e.g. the
    primary bike recording changed, shifting the gap) safe.
    """
    activities = db.query(RecordedActivity).filter(RecordedActivity.race_id == race_id).all()
    primary_by_segment = {a.segment: a for a in activities if a.is_primary}
    has_real_recording = {
        seg
        for seg in (Segment.T1, Segment.T2)
        if any(a.segment == seg and not a.is_derived for a in activities)
    }

    for prev_seg, t_seg, next_seg in _TRANSITION_PAIRS:
        if t_seg in has_real_recording:
            continue
        prev = primary_by_segment.get(prev_seg)
        nxt = primary_by_segment.get(next_seg)
        if prev is None or nxt is None:
            continue
        prev_end = prev.start_time + timedelta(seconds=prev.elapsed_time)
        try:
            transition = persistence.build_derived_transition(
                race_id=race_id,
                segment=t_seg,
                prev_end=prev_end,
                next_start=nxt.start_time,
                source=prev.source,
            )
        except persistence.TransitionError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        persistence.replace_derived_transition(db, transition)


# --------------------------------------------------------------------------- #
# Weather (only triggered when a race is newly created — §3 import flow step 7)
# --------------------------------------------------------------------------- #


def _enrich_weather(db: Session, race: Race, *, client=None) -> None:
    """`fetch_weather` already degrades to `None` on every failure path (network,
    timeout, malformed response, archive lag) — nothing here needs its own
    try/except, a `None` result is simply not applied by `apply_weather_to_race`,
    so a weather outage can never fail the surrounding import."""
    if race.location_lat is None or race.location_lng is None:
        return
    earliest = (
        db.query(RecordedActivity.start_time)
        .filter(RecordedActivity.race_id == race.id)
        .order_by(RecordedActivity.start_time.asc())
        .first()
    )
    if earliest is None:
        return
    result = fetch_weather(race.location_lat, race.location_lng, earliest[0], client=client)
    apply_weather_to_race(race, result)


# --------------------------------------------------------------------------- #
# POST /races/group
# --------------------------------------------------------------------------- #


def create_race_from_uploads(
    db: Session,
    *,
    user: User,
    upload_ids: list[int],
    segment_assignments: dict[int, Segment] | None,
    weather_client=None,
) -> Race:
    """`POST /races/group`: create a `Race` from chosen `upload_ids` + segment
    assignment (§3 step 5), synthesize T1/T2, and enrich weather."""
    uploads = assert_owned(db, user, upload_ids)
    segments = resolve_segments(uploads, segment_assignments)
    parsed_by_upload = {u.id: _reparse(u) for u in uploads}

    ordered = sorted(uploads, key=lambda u: parsed_by_upload[u.id].start_time)
    race_date = parsed_by_upload[ordered[0].id].local_date
    lat, lng = persistence.race_location_from_sessions(
        [parsed_by_upload[u.id].sessions[0] for u in uploads]
    )

    race = Race(
        user_id=user.id,
        name="Untitled race",
        date=race_date,
        location_name=None,
        location_lat=lat,
        location_lng=lng,
        race_type=RaceType.CUSTOM,
    )
    db.add(race)
    db.flush()

    for u in uploads:
        parsed = parsed_by_upload[u.id]
        persistence.persist_upload_sessions(
            db,
            race_id=race.id,
            upload_id=u.id,
            source=u.source,
            parsed=parsed,
            segment_override={0: segments[u.id]},
        )
        u.status = UploadStatus.GROUPED
        u.race_id = race.id

    synthesize_transitions(db, race.id)
    _enrich_weather(db, race, client=weather_client)
    db.flush()
    return race


# --------------------------------------------------------------------------- #
# POST /races/{id}/attach
# --------------------------------------------------------------------------- #


def attach_uploads_to_race(
    db: Session,
    *,
    user: User,
    race: Race,
    upload_ids: list[int],
    segment_assignments: dict[int, Segment] | None,
) -> Race:
    """`POST /races/{id}/attach`: attach further uploads to an existing race —
    backfilling a forgotten leg, or replacing one recorded from the wrong device.
    Re-evaluates `is_primary` for any segment that now has two recordings (via
    `persist_upload_sessions` -> `reconcile_primary`), and re-derives transitions
    in case the attach changed which recording is primary for a bounding leg."""
    uploads = assert_owned(db, user, upload_ids)
    segments = resolve_segments(uploads, segment_assignments)

    for u in uploads:
        parsed = _reparse(u)
        persistence.persist_upload_sessions(
            db,
            race_id=race.id,
            upload_id=u.id,
            source=u.source,
            parsed=parsed,
            segment_override={0: segments[u.id]},
        )
        u.status = UploadStatus.GROUPED
        u.race_id = race.id

    synthesize_transitions(db, race.id)
    db.flush()
    return race
