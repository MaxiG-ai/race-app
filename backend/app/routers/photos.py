"""Race photos (docs/features/photos.md). Images are served through `visible_race`, so a
hidden race's photos are a 404 exactly like the race itself."""

from typing import Literal

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.access import DB, CurrentViewer, Viewer, owner_router, reader_router, visible_race
from app.models import Race, RacePhoto
from app.schemas.photos import PhotoOut, PhotoOutcome, PhotoPatch
from app.services import photos

read = reader_router()
write = owner_router()

_OWNER = Viewer(is_owner=True)


def photo_out(photo: RacePhoto) -> PhotoOut:
    base = f"/api/races/{photo.race_id}/photos/{photo.id}"
    return PhotoOut(
        id=photo.id,
        caption=photo.caption,
        width=photo.width,
        height=photo.height,
        is_cover=photo.is_cover,
        thumb_url=f"{base}/thumb",
        full_url=f"{base}/full",
    )


def _photo(db: DB, race: Race, photo_id: int) -> RacePhoto:
    photo = db.get(RacePhoto, photo_id)
    if photo is None or photo.race_id != race.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found.")
    return photo


def _ensure_cover(db: DB, race: Race) -> None:
    db.flush()
    db.refresh(race, ["photos"])
    if race.photos and not any(p.is_cover for p in race.photos):
        race.photos[0].is_cover = True


@read.get("/races/{race_id}/photos/{photo_id}/{size}", response_class=FileResponse)
def get_image(
    race_id: int, photo_id: int, size: Literal["thumb", "full"], db: DB, viewer: CurrentViewer
):
    race = visible_race(db, viewer, race_id)
    file = photos.path(_photo(db, race, photo_id).id, size)
    if not file.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found.")
    # no-cache: every reuse is revalidated, so hiding a race hides its photos at once.
    return FileResponse(file, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@write.post("/races/{race_id}/photos", response_model=list[PhotoOutcome])
async def upload_photos(race_id: int, files: list[UploadFile], db: DB):
    race = visible_race(db, _OWNER, race_id)
    outcomes = []
    next_position = max((p.position for p in race.photos), default=-1) + 1
    for file in files:
        data = await file.read()
        try:
            processed = photos.process(data)
        except photos.PhotoError as exc:
            outcomes.append(
                PhotoOutcome(filename=file.filename, result="failed", message=str(exc), photo=None)
            )
            continue
        existing = db.scalar(
            select(RacePhoto).where(
                RacePhoto.race_id == race.id, RacePhoto.content_hash == processed.content_hash
            )
        )
        if existing:
            outcomes.append(
                PhotoOutcome(
                    filename=file.filename,
                    result="duplicate",
                    message="This photo is already on the race.",
                    photo=photo_out(existing),
                )
            )
            continue
        photo = RacePhoto(
            race_id=race.id,
            content_hash=processed.content_hash,
            width=processed.width,
            height=processed.height,
            position=next_position,
        )
        next_position += 1
        db.add(photo)
        db.flush()
        photos.write(photo.id, processed)
        _ensure_cover(db, race)
        db.commit()
        outcomes.append(
            PhotoOutcome(
                filename=file.filename, result="added", message="Added.", photo=photo_out(photo)
            )
        )
    return outcomes


@write.patch("/races/{race_id}/photos/{photo_id}", response_model=list[PhotoOut])
def patch_photo(race_id: int, photo_id: int, body: PhotoPatch, db: DB):
    race = visible_race(db, _OWNER, race_id)
    photo = _photo(db, race, photo_id)
    values = body.model_dump(exclude_unset=True)
    if "is_cover" in values:
        if values.pop("is_cover") is not True:
            raise HTTPException(422, "Choose another photo as the cover instead.")
        for other in race.photos:
            other.is_cover = False
        db.flush()  # the partial unique index allows one cover per race
        photo.is_cover = True
    if "position" in values:
        if values["position"] is None:
            raise HTTPException(422, "position can't be empty.")
        photo.position = values.pop("position")
    if "caption" in values:
        caption = values.pop("caption")
        photo.caption = (caption.strip() or None) if caption else None
    db.commit()
    db.refresh(race, ["photos"])
    return [photo_out(p) for p in race.photos]


@write.delete("/races/{race_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_photo(race_id: int, photo_id: int, db: DB):
    race = visible_race(db, _OWNER, race_id)
    photo = _photo(db, race, photo_id)
    db.delete(photo)
    _ensure_cover(db, race)
    db.commit()
    photos.remove(photo_id)
