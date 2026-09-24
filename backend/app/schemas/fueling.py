from pydantic import BaseModel, Field

from app.models.enums import Segment
from app.schemas.races import FuelingProduct


class FuelingIn(BaseModel):
    segment: Segment
    carbs_g: float | None = Field(default=None, ge=0)
    products: list[FuelingProduct] = []
    notes: str | None = None
