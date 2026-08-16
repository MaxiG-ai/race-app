from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import Segment


class FuelingProduct(BaseModel):
    """One entry of `RaceFueling.products` — free-form, not a normalised catalogue
    (§9 Open Decisions)."""

    name: str
    quantity: float | None = None


class RaceFuelingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    race_id: int
    segment: Segment
    carbs_g: int | None
    products: list[FuelingProduct] | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class RaceFuelingUpsertItem(BaseModel):
    """One row of the `PUT /races/{id}/fueling` request body."""

    segment: Segment
    carbs_g: int | None = None
    products: list[FuelingProduct] | None = None
    notes: str | None = None


class RaceFuelingUpsert(BaseModel):
    """Full-replace request body: the complete set of fuelled segments for a race."""

    fuelings: list[RaceFuelingUpsertItem]
