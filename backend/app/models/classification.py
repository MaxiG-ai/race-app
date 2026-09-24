"""Race categories (docs/features/classification.md#data)."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import CategoryKind, Segment


class RaceCategory(Base):
    __tablename__ = "race_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    label: Mapped[str]
    kind: Mapped[CategoryKind]
    sort_order: Mapped[int] = mapped_column(default=0)
    swim_min_m: Mapped[float | None]
    swim_max_m: Mapped[float | None]
    bike_min_m: Mapped[float | None]
    bike_max_m: Mapped[float | None]
    run_min_m: Mapped[float | None]
    run_max_m: Mapped[float | None]
    tolerance_pct: Mapped[float] = mapped_column(default=10)
    is_active: Mapped[bool] = mapped_column(default=True)

    def bounds(self, segment: Segment) -> tuple[float | None, float | None]:
        return getattr(self, f"{segment}_min_m"), getattr(self, f"{segment}_max_m")
