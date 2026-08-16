"""`GET /stats/summary` — the aggregate strip above the timeline (§4, §5
`StatStrip`). Delegates entirely to `services.aggregates.compute_stats_summary`,
which takes `user_id` explicitly — no unscoped `SELECT ... FROM races` (§4
Tenancy)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import User
from app.schemas.stats import StatsSummary
from app.services.aggregates import compute_stats_summary

router = APIRouter(prefix="/stats", tags=["stats"], dependencies=[Depends(current_user)])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]


@router.get("/summary", response_model=StatsSummary)
def get_stats_summary(
    db: DbSession,
    user: CurrentUser,
    date_from: Annotated[
        date | None, Query(description="Inclusive lower bound on Race.date")
    ] = None,
    date_to: Annotated[date | None, Query(description="Inclusive upper bound on Race.date")] = None,
) -> StatsSummary:
    return compute_stats_summary(db, user.id, date_from=date_from, date_to=date_to)
