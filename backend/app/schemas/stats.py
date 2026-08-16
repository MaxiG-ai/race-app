from pydantic import BaseModel


class StatsSummary(BaseModel):
    """`GET /stats/summary` — the aggregate strip above the timeline. Computed
    per-`user_id`, never an unscoped aggregate (§4 Tenancy)."""

    race_count: int
    total_distance_m: float
    total_elevation_gain_m: float
