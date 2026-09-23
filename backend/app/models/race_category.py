from __future__ import annotations

from sqlalchemy import Boolean, Enum, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import CategoryKind, Segment


class RaceCategory(Base):
    """A distance band a race can be classified into (§9).

    Seeded by migration and edited as data rather than code: a boundary can be corrected
    with an UPDATE instead of a deploy, and races hold a foreign key to the row they
    matched, so a later correction can be replayed.

    Bounds are stored exactly as published. `tolerance_pct` is applied outward at match
    time instead of being baked into the numbers, which keeps the fudge factor visible
    and tunable — and it is not optional: Kurzdistanz is published as exact values
    (1.5 / 40 / 10 km) that no measured course ever hits.
    """

    __tablename__ = "race_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[CategoryKind] = mapped_column(
        Enum(CategoryKind, name="category_kind"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # A null bound is open-ended — Sprintdistanz's "bis 0,75" has no floor.
    swim_min_m: Mapped[float | None] = mapped_column(Float)
    swim_max_m: Mapped[float | None] = mapped_column(Float)
    bike_min_m: Mapped[float | None] = mapped_column(Float)
    bike_max_m: Mapped[float | None] = mapped_column(Float)
    run_min_m: Mapped[float | None] = mapped_column(Float)
    run_max_m: Mapped[float | None] = mapped_column(Float)

    tolerance_pct: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def bounds(self, segment: Segment) -> tuple[float | None, float | None]:
        """Published (min, max) for one discipline, before tolerance."""
        prefix = {Segment.SWIM: "swim", Segment.BIKE: "bike", Segment.RUN: "run"}[segment]
        return getattr(self, f"{prefix}_min_m"), getattr(self, f"{prefix}_max_m")

    @property
    def range_width_m(self) -> float:
        """Total span of the three ranges — smaller means more specific.

        An open bound counts as effectively infinite, which is what makes Sprintdistanz
        lose to Supersprintdistanz for a race that matches both.
        """
        total = 0.0
        for segment in (Segment.SWIM, Segment.BIKE, Segment.RUN):
            low, high = self.bounds(segment)
            total += 1e9 if low is None or high is None else high - low
        return total
