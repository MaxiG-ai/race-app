"""`GET/PATCH /activities/{id}` (§4 endpoint table, §5 `RecordingSwitch`/`GearPicker`).

Tenancy: `RecordedActivity` has no `user_id` of its own (§4) — ownership is
reached through its race, via `get_owned_race`, which 404s (not 403) for a race
owned by someone else. A missing activity id also 404s, so the two cases are
indistinguishable to the caller.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user, get_owned_race
from app.db import get_db
from app.models import ActivityGear, Gear, GearAssignedBy, GearKind, RecordedActivity, Segment, User
from app.schemas.activity import LapSplitRead, RecordedActivityRead, RecordedActivityUpdate
from app.schemas.gear import ActivityGearRead
from app.services.gear import suggest_gear_for_segment

router = APIRouter(prefix="/activities", tags=["activities"], dependencies=[Depends(current_user)])


class RecordedActivityDetail(RecordedActivityRead):
    """`RecordedActivityRead` plus the `GearPicker`'s last-used-shoes default
    (§3: shoes can't be auto-detected, so the UI needs a suggestion to
    pre-select) — `None` for any segment other than `run`, or once shoes are
    already assigned."""

    suggested_gear_id: int | None = None


def _get_owned_activity(db: Session, user: User, activity_id: int) -> RecordedActivity:
    activity = db.get(RecordedActivity, activity_id)
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    # Reuses the one place race ownership is checked (§4 Tenancy) — 404, never
    # 403, for someone else's activity.
    get_owned_race(db, user, activity.race_id)
    return activity


def _suggested_gear_id(db: Session, user: User, activity: RecordedActivity) -> int | None:
    if activity.segment != Segment.RUN:
        return None
    already_has_shoes = any(
        ag.gear.kind == GearKind.SHOES for ag in activity.activity_gear if ag.gear is not None
    )
    if already_has_shoes:
        return None
    suggestion = suggest_gear_for_segment(
        db,
        user.id,
        kind=GearKind.SHOES,
        segment=Segment.RUN,
        exclude_activity_id=activity.id,
    )
    return suggestion.id if suggestion is not None else None


def _to_detail(db: Session, user: User, activity: RecordedActivity) -> RecordedActivityDetail:
    # Built field-by-field rather than a bare `model_validate(activity,
    # from_attributes=True)`: the ORM's relationship names (`lap_splits`,
    # `activity_gear`) don't match the schema's (`laps`, `gear`).
    base = RecordedActivityRead.model_validate(activity, from_attributes=True).model_dump(
        exclude={"laps", "gear"}
    )
    laps = [LapSplitRead.model_validate(lap, from_attributes=True) for lap in activity.lap_splits]
    gear = [
        ActivityGearRead.model_validate(ag, from_attributes=True) for ag in activity.activity_gear
    ]
    return RecordedActivityDetail(
        **base,
        laps=laps,
        gear=gear,
        suggested_gear_id=_suggested_gear_id(db, user, activity),
    )


@router.get("/{activity_id}", response_model=RecordedActivityDetail)
def get_activity(
    activity_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> RecordedActivityDetail:
    activity = _get_owned_activity(db, user, activity_id)
    return _to_detail(db, user, activity)


def _promote_primary(db: Session, activity: RecordedActivity) -> None:
    """Promote `activity` to `is_primary` for its (race, segment) without ever
    transiently violating the partial unique index — the current primary (if
    any, and if it isn't `activity` itself) is demoted and flushed *first*, then
    `activity` is promoted and flushed."""
    if activity.is_primary:
        return
    current_primary = (
        db.query(RecordedActivity)
        .filter(
            RecordedActivity.race_id == activity.race_id,
            RecordedActivity.segment == activity.segment,
            RecordedActivity.is_primary.is_(True),
        )
        .one_or_none()
    )
    if current_primary is not None and current_primary.id != activity.id:
        current_primary.is_primary = False
        db.flush()
    activity.is_primary = True
    db.flush()


def _assign_gear(db: Session, activity: RecordedActivity, gear: Gear) -> None:
    """The manual half of gear assignment (§3 step 4: this endpoint *is* the
    override — always `assigned_by=manual`). Replaces any existing assignment of
    the same `Gear.kind` on this activity first, so a `GearPicker` selection
    reads as "this is now the shoes for this run" rather than accreting a second
    pair alongside it."""
    for existing in list(activity.activity_gear):
        if existing.gear_id != gear.id and existing.gear.kind == gear.kind:
            activity.activity_gear.remove(existing)
    db.flush()

    existing = next((ag for ag in activity.activity_gear if ag.gear_id == gear.id), None)
    if existing is not None:
        existing.assigned_by = GearAssignedBy.MANUAL
    else:
        db.add(
            ActivityGear(
                recorded_activity_id=activity.id,
                gear_id=gear.id,
                assigned_by=GearAssignedBy.MANUAL,
            )
        )
    db.flush()


@router.patch("/{activity_id}", response_model=RecordedActivityDetail)
def update_activity(
    activity_id: int,
    payload: RecordedActivityUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> RecordedActivityDetail:
    """Set `is_primary` and/or assign gear to this segment (§4, §5
    `RecordingSwitch` + `GearPicker`)."""
    activity = _get_owned_activity(db, user, activity_id)

    # Validate the gear id (if any) *before* mutating anything else, so a bad
    # gear id 404s without leaving a half-applied `is_primary` change behind.
    if payload.gear_id is not None:
        gear = (
            db.query(Gear).filter(Gear.id == payload.gear_id, Gear.user_id == user.id).one_or_none()
        )
        if gear is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gear not found")
    else:
        gear = None

    if payload.is_primary is True:
        _promote_primary(db, activity)
    elif payload.is_primary is False:
        activity.is_primary = False
        db.flush()

    if gear is not None:
        _assign_gear(db, activity, gear)

    db.commit()
    db.refresh(activity)
    return _to_detail(db, user, activity)
