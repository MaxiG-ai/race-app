"""Uploads, the tray, grouping and recording switches (import.md, grouping.md)."""

from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select

from app.access import DB, Viewer, owner_router, visible_race
from app.models import Gear, RecordedActivity, Upload
from app.models.enums import UploadStatus
from app.routers.races import owner_detail
from app.schemas.imports import ActivityPatch, GroupIn, Outcome, Tray, UploadOut
from app.schemas.races import RaceDetailOwner
from app.services import classify, gear, imports
from app.services.fit_import import FitParseError

write = owner_router()  # everything here is owner only, reads included


@write.post("/uploads", response_model=list[Outcome])
async def upload_files(files: list[UploadFile], db: DB):
    outcomes = []
    for file in files:
        data = await file.read()
        outcomes.append(Outcome(**vars(imports.receive(db, file.filename, data))))
    return outcomes


@write.get("/uploads", response_model=Tray)
def tray(db: DB, status: UploadStatus | None = None):
    query = select(Upload).order_by(Upload.detected_start_time.desc().nulls_last())
    if status:
        query = query.where(Upload.status == status)
    uploads = list(db.scalars(query))
    parsed = [u for u in uploads if u.status is UploadStatus.PARSED]
    return Tray(
        uploads=[UploadOut.model_validate(u) for u in uploads],
        proposals=imports.propose(parsed),
    )


@write.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_upload(upload_id: int, db: DB):
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(404, "Upload not found.")
    if upload.race_id is not None:
        raise HTTPException(409, "This upload belongs to a race; delete the race first.")
    if upload.stored_path:
        Path(upload.stored_path).unlink(missing_ok=True)
    db.delete(upload)
    db.commit()


def _grouping_errors(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@write.post("/races/group", response_model=RaceDetailOwner, status_code=201)
def group(body: GroupIn, db: DB):
    try:
        race = imports.group(db, body.upload_ids, body.segments)
    except (imports.GroupingError, FitParseError) as exc:
        db.rollback()
        raise _grouping_errors(exc) from exc
    db.commit()
    return owner_detail(db, race.id)


@write.post("/races/{race_id}/attach", response_model=RaceDetailOwner)
def attach(race_id: int, body: GroupIn, db: DB):
    race = visible_race(db, Viewer(is_owner=True), race_id)
    try:
        imports.attach(db, race, body.upload_ids, body.segments)
    except (imports.GroupingError, FitParseError) as exc:
        db.rollback()
        raise _grouping_errors(exc) from exc
    db.commit()
    return owner_detail(db, race_id)


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
    if "gear_ids" in values:
        gear_ids = values["gear_ids"] or []
        found = db.scalars(select(Gear.id).where(Gear.id.in_(gear_ids))).all()
        if len(found) != len(set(gear_ids)):
            raise HTTPException(422, "Unknown gear id.")
        gear.assign_manually(activity, gear_ids)
    db.commit()
    return owner_detail(db, activity.race_id)
