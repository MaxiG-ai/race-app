"""`POST /uploads`, `GET /uploads`, `DELETE /uploads/{id}` (M2 persistence + the
tray half of M2.5 — see `app/services/persistence.py` and
`app/services/grouping.py` for the logic proper). Router-level auth convention:
see `app/routers/__init__.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user, get_owned_race
from app.config import settings
from app.db import get_db
from app.models.enums import RaceType, Sport, UploadKind, UploadSource, UploadStatus
from app.models.race import Race
from app.models.recorded_activity import RecordedActivity
from app.models.upload import Upload
from app.models.user import User
from app.schemas.race import RaceRead
from app.schemas.upload import UploadRead, UploadResult, UploadTrayResponse
from app.services import grouping, persistence
from app.services.fit_import import FitParseError, ParsedFile, parse_fit
from app.services.weather import apply_weather_to_race, fetch_weather

router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[Depends(current_user)])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]


# --------------------------------------------------------------------------- #
# POST /uploads
# --------------------------------------------------------------------------- #


@router.post("", response_model=list[UploadResult], status_code=status.HTTP_201_CREATED)
def create_uploads(
    files: Annotated[list[UploadFile], File(...)],
    db: DbSession,
    user: CurrentUser,
    race_id: Annotated[int | None, Form()] = None,
) -> list[UploadResult]:
    """Multipart upload of one or many files (§3 import flow). Each file is
    handled independently — one bad or duplicate file in a batch never aborts
    the others — and gets its own `UploadResult` with an explicit `outcome`
    rather than a single pass/fail for the whole request.

    `race_id`, if given, attaches every file directly to that existing race
    instead of staging single-sport ones in the tray or auto-grouping a
    multisport one into a new draft race.
    """
    target_race = get_owned_race(db, user, race_id) if race_id is not None else None

    results = [_ingest_one(db, user=user, file=file, target_race=target_race) for file in files]
    db.commit()
    return results


def _ingest_one(
    db: Session, *, user: User, file: UploadFile, target_race: Race | None
) -> UploadResult:
    filename = file.filename or "upload.fit"
    data = file.file.read()

    # Reject oversized / non-FIT content before parsing (§8) — no Upload row is
    # created for these, since nothing was actually staged; this is a distinct
    # failure mode from a file that looks like a FIT upload but fails to parse
    # (see the FitParseError branch below), which *does* get a row so it's
    # visible in the tray rather than lost to a bare 400.
    if len(data) > settings.max_upload_bytes:
        return UploadResult(
            outcome="rejected",
            filename=filename,
            message=(
                f"{filename} is {len(data)} bytes, over the "
                f"{settings.max_upload_bytes}-byte upload limit."
            ),
        )
    if not filename.lower().endswith(".fit"):
        return UploadResult(
            outcome="rejected",
            filename=filename,
            message=f"{filename} is not a .fit file.",
        )

    content_hash = persistence.sha256_hex(data)

    # Dedupe per user on content_hash (§3 "What Strava exports actually look
    # like"): Strava's "export original" can hand back byte-identical files for
    # different activities, so a user deliberately selecting several files can
    # legitimately produce fewer distinct uploads. Report that explicitly rather
    # than silently discarding the file.
    existing = (
        db.query(Upload)
        .filter(Upload.user_id == user.id, Upload.content_hash == content_hash)
        .one_or_none()
    )
    if existing is not None:
        return UploadResult(
            outcome="duplicate",
            filename=filename,
            message=(
                f"{filename} is identical to an upload you already have "
                f"(#{existing.id}" + (f", {existing.filename}" if existing.filename else "") + ")."
            ),
            upload=UploadRead.model_validate(existing),
            race=RaceRead.model_validate(existing.race) if existing.race else None,
        )

    # Row first, file second: the stored filename is keyed by the upload id, so
    # the row must exist (and have a real id) before anything is written to disk.
    upload = Upload(
        user_id=user.id,
        source=UploadSource.FIT_UPLOAD,
        filename=filename,
        stored_path=None,
        content_hash=content_hash,
        status=UploadStatus.FAILED,
        kind=UploadKind.SINGLE_SPORT,
    )
    db.add(upload)
    db.flush()

    try:
        parsed = parse_fit(data, filename=filename)
    except FitParseError as exc:
        # Still create the row — a failed parse must be visible in the tray, not
        # a silent 400 that loses the file.
        upload.status = UploadStatus.FAILED
        upload.error = str(exc)
        db.flush()
        return UploadResult(
            outcome="failed",
            filename=filename,
            message=str(exc),
            upload=UploadRead.model_validate(upload),
        )

    stored_path = settings.upload_dir / f"{upload.id}.fit"
    try:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        stored_path.write_bytes(data)
    except OSError as exc:
        # Nothing durable was created for this upload — clean the placeholder
        # row up rather than leaving a phantom entry with no file behind it.
        db.delete(upload)
        db.flush()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Could not store {filename}: {exc}"
        ) from exc

    upload.stored_path = str(stored_path)
    upload.kind = UploadKind(parsed.kind)
    upload.detected_sport = Sport(parsed.detected_sport) if parsed.detected_sport else None
    upload.detected_start_time = parsed.start_time
    upload.detected_end_time = parsed.end_time
    upload.detected_distance_m = parsed.total_distance_m
    upload.summary = persistence.build_summary(parsed)
    upload.error = None

    race_obj: Race | None = None
    try:
        # A whole batch commits together at the end of `create_uploads`, so one
        # file's persistence failure must not silently drag along whatever a
        # *different*, already-succeeded file in the same batch just flushed. A
        # real SAVEPOINT (not just a bare try/except) is what makes "roll back
        # this file only" possible while the rest of the batch's work survives to
        # the final `db.commit()`.
        with db.begin_nested():
            if target_race is not None:
                persistence.persist_upload_sessions(
                    db,
                    race_id=target_race.id,
                    upload_id=upload.id,
                    source=upload.source,
                    parsed=parsed,
                )
                upload.status = UploadStatus.GROUPED
                upload.race_id = target_race.id
                race_obj = target_race
            elif parsed.kind == "multisport":
                # Multisport files auto-group into a draft race (§3 import flow
                # step 5) — no user decision needed, unlike single-sport uploads
                # which stage in the tray for manual grouping.
                race_obj = _create_race_from_multisport(db, user=user, upload=upload, parsed=parsed)
                upload.status = UploadStatus.GROUPED
                upload.race_id = race_obj.id
            else:
                upload.status = UploadStatus.PARSED
    except Exception as exc:  # noqa: BLE001 - defensive: a persistence bug must
        # not 500 the whole batch or leave an orphaned file on disk with no
        # Upload row explaining it (§3: parse/import failures stay visible, not
        # silent). The SAVEPOINT above has already undone any partial
        # Race/RecordedActivity rows; this only has to fix up the Upload row.
        race_obj = None
        stored_path.unlink(missing_ok=True)
        upload.stored_path = None
        upload.status = UploadStatus.FAILED
        upload.error = f"Import failed after parsing: {exc}"
        db.flush()
        return UploadResult(
            outcome="failed",
            filename=filename,
            message=upload.error,
            upload=UploadRead.model_validate(upload),
        )

    db.flush()
    return UploadResult(
        outcome="created",
        filename=filename,
        upload=UploadRead.model_validate(upload),
        race=RaceRead.model_validate(race_obj) if race_obj is not None else None,
    )


def _create_race_from_multisport(
    db: Session, *, user: User, upload: Upload, parsed: ParsedFile
) -> Race:
    lat, lng = persistence.race_location_from_sessions(parsed.sessions)
    # "name and race_type are left for the user" (§3 step 5) -- neither column is
    # nullable, so a placeholder is stored (rather than the field being left
    # unset) and the frontend is expected to prompt for the real values on the
    # draft race's first edit.
    race = Race(
        user_id=user.id,
        name="Untitled race",
        date=parsed.local_date,
        location_name=None,
        location_lat=lat,
        location_lng=lng,
        race_type=RaceType.CUSTOM,
    )
    db.add(race)
    db.flush()

    persistence.persist_upload_sessions(
        db,
        race_id=race.id,
        upload_id=upload.id,
        source=upload.source,
        parsed=parsed,
    )
    _trigger_weather(db, race)
    return race


def _trigger_weather(db: Session, race: Race, *, client=None) -> None:
    """Weather enrichment only fires when a *new* race is created by the import
    flow (§3 step 7) — attaching more uploads to an already-enriched race would
    otherwise re-fetch and potentially overwrite `weather_conditions`/`temp_c` on
    every subsequent upload for no benefit. `fetch_weather` degrades to `None` on
    any failure, so a weather outage never fails the import."""
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
# GET /uploads
# --------------------------------------------------------------------------- #


@router.get("", response_model=UploadTrayResponse)
def list_uploads(
    db: DbSession,
    user: CurrentUser,
    status_filter: Annotated[UploadStatus | None, Query(alias="status")] = None,
) -> UploadTrayResponse:
    """The ungrouped tray (§3 step 3), `?status=` filter, scoped to `user_id`
    (§4 Tenancy — uploads are their own ownership root, never joined through a
    race). Suggested groupings (§3 step 4) are always computed from every
    *parsed* upload regardless of the filter, since a proposal only makes sense
    over what's still ungrouped."""
    q = db.query(Upload).filter(Upload.user_id == user.id)
    if status_filter is not None:
        q = q.filter(Upload.status == status_filter)
    uploads = q.order_by(Upload.uploaded_at.desc()).all()

    tray_uploads = (
        db.query(Upload)
        .filter(Upload.user_id == user.id, Upload.status == UploadStatus.PARSED)
        .all()
    )
    proposals = grouping.propose_groupings(tray_uploads)

    return UploadTrayResponse(
        uploads=[UploadRead.model_validate(u) for u in uploads],
        proposals=proposals,
    )


# --------------------------------------------------------------------------- #
# DELETE /uploads/{id}
# --------------------------------------------------------------------------- #


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_upload(upload_id: int, db: DbSession, user: CurrentUser) -> None:
    """Discard an upload and its stored file.

    Decision: an upload that is already `grouped` is **refused** (409), not
    silently detached. Two reasons: (1) its stored file is the corpus §8
    retention depends on for future re-imports as the parser improves — deleting
    it out from under an active race's `RecordedActivity` rows would quietly
    break that guarantee for exactly the uploads still in use; (2) detaching
    would leave `RecordedActivity` rows with `upload_id=NULL` that look
    unrecorded/orphaned in the UI despite representing real, still-valid data.
    Removing a grouped upload's contribution to a race is a race-editing
    operation (delete the race, or replace the recording via `attach`), not an
    upload-tray operation.
    """
    upload = (
        db.query(Upload).filter(Upload.id == upload_id, Upload.user_id == user.id).one_or_none()
    )
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    if upload.status == UploadStatus.GROUPED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This upload is already part of a race. Delete the race, or replace "
            "its recording via /races/{id}/attach, instead of discarding the "
            "upload directly.",
        )
    if upload.stored_path:
        Path(upload.stored_path).unlink(missing_ok=True)
    db.delete(upload)
    db.commit()
