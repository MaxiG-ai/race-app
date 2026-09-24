"""Fueling per segment (docs/features/fueling.md)."""

from fastapi import HTTPException

from app.access import DB, Viewer, owner_router, visible_race
from app.models import RaceFueling
from app.routers.races import owner_detail
from app.schemas.fueling import FuelingIn
from app.schemas.races import RaceDetailOwner

write = owner_router()


@write.put("/races/{race_id}/fueling", response_model=RaceDetailOwner)
def put_fueling(race_id: int, body: list[FuelingIn], db: DB):
    """Replaces the whole set: a segment left out is removed."""
    race = visible_race(db, Viewer(is_owner=True), race_id)
    segments = [row.segment for row in body]
    if len(segments) != len(set(segments)):
        raise HTTPException(422, "Each segment can appear once.")
    existing = {f.segment: f for f in race.fueling}
    rows = []
    for item in body:
        row = existing.get(item.segment) or RaceFueling(segment=item.segment)
        row.carbs_g = item.carbs_g
        row.products = [p.model_dump() for p in item.products]
        row.notes = item.notes
        rows.append(row)
    race.fueling = rows
    db.commit()
    return owner_detail(db, race_id)
