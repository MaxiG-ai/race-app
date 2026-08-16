"""`POST /races/group`, `POST /races/{id}/attach` (M2.5 — see
`app/services/grouping.py` for the logic). Router-level auth convention: see
`app/routers/__init__.py`.

Registration order matters (see `app/main.py`): this router owns the literal
`/races/group` path and is registered before `races`, whose `/races/{race_id}`
would otherwise capture `"group"` as an id and fail on int parsing.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user, get_owned_race
from app.db import get_db
from app.models.user import User
from app.schemas.race import RaceRead
from app.schemas.upload import RaceAttachRequest, RaceGroupRequest
from app.services import grouping

router = APIRouter(prefix="/races", tags=["grouping"], dependencies=[Depends(current_user)])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]


@router.post("/group", response_model=RaceRead, status_code=status.HTTP_201_CREATED)
def group_uploads(body: RaceGroupRequest, db: DbSession, user: CurrentUser) -> RaceRead:
    """Create a `Race` from selected `upload_ids` + segment assignment (§3 step
    5). Every upload id is asserted to belong to the caller before anything is
    created (§4 Tenancy); ambiguous sport detection is rejected with a clear
    error naming the ambiguity rather than guessed at."""
    try:
        race = grouping.create_race_from_uploads(
            db,
            user=user,
            upload_ids=body.upload_ids,
            segment_assignments=body.segment_assignments,
        )
    except Exception:
        # A rejection (ambiguous sport, an implausible transition gap) can land
        # after some rows were already flushed (the race itself, or an earlier
        # upload's activities) — roll all of it back so a rejected grouping
        # never leaves a partial race behind.
        db.rollback()
        raise
    db.commit()
    db.refresh(race)
    return RaceRead.model_validate(race)


@router.post("/{race_id}/attach", response_model=RaceRead)
def attach_uploads(
    race_id: int, body: RaceAttachRequest, db: DbSession, user: CurrentUser
) -> RaceRead:
    """Attach further uploads to an existing race — backfilling a forgotten leg,
    or replacing one recorded from the wrong device. `race_id` is resolved
    through `get_owned_race` (404, not 403, for someone else's race); the
    attached uploads go through the same ownership assertion as `/races/group`."""
    race = get_owned_race(db, user, race_id)
    try:
        grouping.attach_uploads_to_race(
            db,
            user=user,
            race=race,
            upload_ids=body.upload_ids,
            segment_assignments=body.segment_assignments,
        )
    except Exception:
        db.rollback()
        raise
    db.commit()
    db.refresh(race)
    return RaceRead.model_validate(race)
