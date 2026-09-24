from app.models.enums import CategoryKind
from app.schemas.common import Model


class CategoryOut(Model):
    id: int
    key: str
    label: str
    kind: CategoryKind
    sort_order: int
    swim_min_m: float | None
    swim_max_m: float | None
    bike_min_m: float | None
    bike_max_m: float | None
    run_min_m: float | None
    run_max_m: float | None
    tolerance_pct: float
    is_active: bool
