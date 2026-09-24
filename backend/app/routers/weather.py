"""Weather refresh (docs/features/weather.md)."""

from fastapi import HTTPException

from app.access import DB, Viewer, owner_router, visible_race
from app.routers.races import owner_detail
from app.schemas.races import RaceDetailOwner
from app.services import weather

write = owner_router()


@write.post("/races/{race_id}/weather/refresh", response_model=RaceDetailOwner)
def refresh(race_id: int, db: DB):
    race = visible_race(db, Viewer(is_owner=True), race_id)
    if race.location_lat is None or race.location_lng is None:
        raise HTTPException(422, "Add a location to fetch weather.")
    if not weather.refresh(race):
        raise HTTPException(502, "The weather service didn't answer; try again later.")
    db.commit()
    return owner_detail(db, race_id)
