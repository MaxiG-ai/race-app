"""The distance-category table (§9).

Read-only over the API. Editing a boundary is a deliberate act with consequences for
every race that matched it, so it is done as data — a migration or an UPDATE — rather
than through a form.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import User
from app.schemas.race import RaceCategoryRead
from app.services.classification import active_categories

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[RaceCategoryRead])
def list_categories(db: DbSession, user: CurrentUser) -> list[RaceCategoryRead]:
    """Active categories in display order — backs the override picker and lets the UI
    show a category's published range next to a race's measured distances."""
    return [RaceCategoryRead.model_validate(c) for c in active_categories(db)]
