"""Categories and re-classification (docs/features/classification.md)."""

from sqlalchemy import select

from app.access import DB, Viewer, owner_router, reader_router, visible_race
from app.models import RaceCategory
from app.routers.races import owner_detail
from app.schemas.classification import CategoryOut
from app.schemas.races import RaceDetailOwner
from app.services import classify

read = reader_router()
write = owner_router()


@read.get("/categories", response_model=list[CategoryOut])
def categories(db: DB):
    return db.scalars(select(RaceCategory).order_by(RaceCategory.sort_order)).all()


@write.post("/races/{race_id}/classify", response_model=RaceDetailOwner)
def reclassify(race_id: int, db: DB):
    classify.classify_race(db, visible_race(db, Viewer(is_owner=True), race_id))
    db.commit()
    return owner_detail(db, race_id)
