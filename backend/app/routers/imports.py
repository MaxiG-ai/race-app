"""Files onto races, the leg picker, removing a file and recording switches (import.md,
grouping.md). There is no tray: every file is dropped onto a race."""

from pathlib import Path
from typing import Annotated

from fastapi import Form, HTTPException, UploadFile, status
from sqlalchemy import select

from app.access import DB, Viewer, owner_router, visible_race
from app.models import Gear, RecordedActivity, Upload
from app.models.enums import RaceType
from app.routers.races import owner_detail
from app.schemas.imports import ActivityPatch, AssignIn, ImportResult, Outcome
from app.schemas.races import RaceDetailOwner
from app.services import classify, gear, imports, results
from app.services.fit_import import FitParseError

write = owner_router()  # everything here is owner only, reads included


async def _read(files: list[UploadFile]) -> list[tuple[str | None, bytes]]:
    return [(f.filename, await f.read()) for f in files]


def _result(db: DB, race, outcomes) -> ImportResult:
    return ImportResult(
        outcomes=[Outcome(**vars(o)) for o in outcomes],
        race=owner_detail(db, race.id) if race is not None else None,
    )


@write.post("/races/import", response_model=ImportResult)
async def import_race(
    files: list[UploadFile], db: DB, race_type: Annotated[RaceType | None, Form()] = None
):
    """A new race from files. Without `race_type`, the files decide (imports.infer_type)."""
    race, outcomes = imports.receive(db, await _read(files), race_type=race_type)
    return _result(db, race, outcomes)


@write.post("/races/{race_id}/uploads", response_model=ImportResult)
async def add_files(race_id: int, files: list[UploadFile], db: DB):
    race = visible_race(db, Viewer(is_owner=True), race_id)
    race, outcomes = imports.receive(db, await _read(files), race=race)
    return _result(db, race, outcomes)


def _grouping_errors(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@write.post("/races/{race_id}/attach", response_model=RaceDetailOwner)
def attach(race_id: int, body: AssignIn, db: DB):
    """File the uploads that wait on the race for the owner to pick their leg."""
    race = visible_race(db, Viewer(is_owner=True), race_id)
    try:
        imports.assign(db, race, body.segments)
    except (imports.GroupingError, FitParseError) as exc:
        db.rollback()
        raise _grouping_errors(exc) from exc
    db.commit()
    return owner_detail(db, race_id)


@write.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_upload(upload_id: int, db: DB):
    """Remove a file from its race, with every recording made from it."""
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(404, "Upload not found.")
    try:
        path = imports.remove_upload(db, upload)
    except imports.GroupingError as exc:
        db.rollback()
        raise _grouping_errors(exc) from exc
    db.commit()
    if path:
        Path(path).unlink(missing_ok=True)


@write.patch("/activities/{activity_id}", response_model=RaceDetailOwner)
def patch_activity(activity_id: int, body: ActivityPatch, db: DB):
    activity = db.get(RecordedActivity, activity_id)
    if activity is None:
        raise HTTPException(404, "Recording not found.")
    values = body.model_dump(exclude_unset=True)
    if values.get("is_primary"):
        imports.set_primary(db, activity)
        try:
            imports.synthesize_transitions(db, activity.race)
        except imports.GroupingError as exc:
            db.rollback()
            raise _grouping_errors(exc) from exc
        classify.classify_race(db, activity.race)
        results.prefill(activity.race)
    if "gear_ids" in values:
        gear_ids = values["gear_ids"] or []
        found = db.scalars(select(Gear.id).where(Gear.id.in_(gear_ids))).all()
        if len(found) != len(set(gear_ids)):
            raise HTTPException(422, "Unknown gear id.")
        gear.assign_manually(activity, gear_ids)
    db.commit()
    return owner_detail(db, activity.race_id)
