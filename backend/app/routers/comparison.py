"""Race comparison (docs/features/comparison.md). Both races go through visible_race,
so one hidden race makes the whole comparison a 404."""

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from app.access import DB, CurrentViewer, reader_router, visible_race, visible_races
from app.models import Race
from app.routers.timeline import list_item
from app.schemas.races import RaceListItem
from app.services import comparison

read = reader_router()


class Comparison(BaseModel):
    a: RaceListItem
    b: RaceListItem
    checkpoints: list[dict[str, Any]]
    normalised: bool
    coarse: bool
    segment_times: dict[str, dict[str, int | None]]
    deltas: dict[str, int | None]
    conditions: list[dict[str, Any]]
    fueling: list[dict[str, Any]]


@read.get("/races/{a_id}/compare/{b_id}", response_model=Comparison)
def compare(a_id: int, b_id: int, db: DB, viewer: CurrentViewer):
    a, b = visible_race(db, viewer, a_id), visible_race(db, viewer, b_id)
    try:
        body = comparison.compare(a, b, owner=viewer.is_owner)
    except comparison.NotComparable as exc:
        raise HTTPException(422, str(exc)) from exc
    return Comparison(a=list_item(a), b=list_item(b), **body)


@read.get("/races/{race_id}/comparable", response_model=list[RaceListItem])
def comparable(race_id: int, db: DB, viewer: CurrentViewer):
    race = visible_race(db, viewer, race_id)
    others = db.scalars(visible_races(viewer).order_by(Race.date.desc(), Race.id.desc()))
    return [list_item(o) for o in others if comparison.comparable(race, o)]
