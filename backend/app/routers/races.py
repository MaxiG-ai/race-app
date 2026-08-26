"""M3 Read API + manual-entry write endpoints (§4 Endpoints).

Tenancy (§4): every lookup by id goes through `get_owned_race`, which 404s (not
403) for someone else's race. The list endpoint filters `Race.user_id`
directly, and per-race aggregates flow through `services.aggregates`, which
takes `user_id` explicitly — no endpoint here builds an unscoped `Race`/
`RecordedActivity` query for aggregate purposes.

Payload note (§4): `gps_track` never appears in `/races` or `/races/{id}` —
`RecordedActivityRead` has no such field, and the detail endpoint additionally
`defer()`s the column so it isn't even fetched from Postgres. Only
`/races/{id}/track` ever reads it.
"""

from __future__ import annotations

from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, contains_eager, defer, selectinload

from app.auth.deps import current_user, get_owned_race
from app.db import get_db
from app.models import (
    ActivityGear,
    Gear,
    LapSplit,
    OfficialResult,
    Race,
    RaceFueling,
    RecordedActivity,
    Segment,
    User,
)
from app.schemas.activity import (
    LapSplitRead,
    RaceTrackResponse,
    RecordedActivityRead,
    SegmentTrack,
    TrackBoundsRead,
    TrackPoint,
)
from app.schemas.fueling import RaceFuelingRead, RaceFuelingUpsert
from app.schemas.gear import ActivityGearRead
from app.schemas.race import (
    OfficialResultRead,
    OfficialResultUpsert,
    RaceCreate,
    RaceDetail,
    RaceListItem,
    RaceListResponse,
    RaceRead,
    RaceUpdate,
)
from app.services.aggregates import RACE_SEGMENT_ORDER, RaceAggregates, race_aggregates_by_id
from app.services.geo import simplify_for_precision, track_bounds
from app.services.official_result import (
    SPLIT_FIELDS,
    TotalTimeUnresolvable,
    resolve_total_time,
)
from app.services.tz import timezone_at
from app.services.weather import (
    apply_weather_to_race,
    fetch_weather,
    utc_anchor_for_local_date,
)

router = APIRouter(prefix="/races", tags=["races"], dependencies=[Depends(current_user)])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]


def _segment_sort_key(segment: Segment) -> int:
    return RACE_SEGMENT_ORDER.index(segment)


def _race_list_item(race: Race, aggregates: RaceAggregates) -> RaceListItem:
    # Single badge for now: a human label for race_type ("half_iron" ->
    # "Half Iron"). The RaceCard mockup (§5) shows one badge per card; nothing
    # else in the schema (PR, DNF, ...) exists yet to add a second one.
    badge = race.race_type.value.replace("_", " ").title()
    return RaceListItem(
        id=race.id,
        name=race.name,
        date=race.date,
        race_type=race.race_type,
        location_name=race.location_name,
        badges=[badge],
        total_official_time_s=aggregates.total_official_time_s,
        total_recorded_time_s=aggregates.total_recorded_time_s,
        segments=aggregates.segments,
    )


@router.get("", response_model=RaceListResponse)
def list_races(
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RaceListResponse:
    """Timeline list, newest first. Four bounded queries regardless of page
    size: total count, the race page, official-result totals for that page,
    and recorded totals for that page — never one query per race."""
    total = db.query(func.count(Race.id)).filter(Race.user_id == user.id).scalar() or 0

    races = (
        db.query(Race)
        .filter(Race.user_id == user.id)
        .order_by(Race.date.desc(), Race.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    race_ids = [race.id for race in races]
    aggregates_by_race = race_aggregates_by_id(db, user.id, race_ids)

    items = [_race_list_item(race, aggregates_by_race[race.id]) for race in races]
    return RaceListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{race_id}", response_model=RaceDetail)
def get_race(race_id: int, db: DbSession, user: CurrentUser) -> RaceDetail:
    """Full detail: race, official result, recorded activities (each with laps
    and gear, no `gps_track`), fueling. Everything the detail page needs
    except the GPS track (`/races/{id}/track`)."""
    race = get_owned_race(db, user, race_id)

    official_result = (
        db.query(OfficialResult).filter(OfficialResult.race_id == race.id).one_or_none()
    )
    fueling_rows = db.query(RaceFueling).filter(RaceFueling.race_id == race.id).all()
    activities = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race.id)
        .options(
            defer(RecordedActivity.gps_track),
            defer(RecordedActivity.raw_data),
            selectinload(RecordedActivity.lap_splits),
            selectinload(RecordedActivity.activity_gear)
            .selectinload(ActivityGear.gear)
            .selectinload(Gear.devices),
        )
        .all()
    )
    activities.sort(key=lambda a: (_segment_sort_key(a.segment), not a.is_primary, a.start_time))

    activity_reads: list[RecordedActivityRead] = []
    for activity in activities:
        read = RecordedActivityRead.model_validate(activity)
        # `laps`/`gear` are renamed from the ORM's `lap_splits`/`activity_gear`
        # relationship attributes, so `model_validate` can't pick them up by
        # attribute-name matching alone — built explicitly here instead.
        read.laps = sorted(
            (LapSplitRead.model_validate(lap) for lap in activity.lap_splits),
            key=lambda lap: lap.lap_index,
        )
        read.gear = [ActivityGearRead.model_validate(ag) for ag in activity.activity_gear]
        activity_reads.append(read)

    base = RaceRead.model_validate(race)
    return RaceDetail(
        **base.model_dump(),
        official_result=(
            OfficialResultRead.model_validate(official_result) if official_result else None
        ),
        recorded_activities=activity_reads,
        fuelings=sorted(
            (RaceFuelingRead.model_validate(row) for row in fueling_rows),
            key=lambda f: _segment_sort_key(f.segment),
        ),
    )


@router.post("", response_model=RaceRead, status_code=status.HTTP_201_CREATED)
def create_race(payload: RaceCreate, db: DbSession, user: CurrentUser) -> RaceRead:
    """A manually created race gets its conditions straight away when it is given a
    location, since it has no import to enrich it and the date is already known."""
    race = Race(user_id=user.id, **payload.model_dump())
    db.add(race)
    db.flush()
    enrich_weather(db, race, overwrite=False)
    db.commit()
    db.refresh(race)
    return RaceRead.model_validate(race)


@router.patch("/{race_id}", response_model=RaceRead)
def update_race(race_id: int, payload: RaceUpdate, db: DbSession, user: CurrentUser) -> RaceRead:
    """Metadata edit. Changing the **date or location re-runs weather enrichment**,
    because those two fields are the entire input to the lookup — leaving the old
    conditions attached would keep showing yesterday's weather for a corrected date,
    which reads as data rather than as staleness. Any other edit leaves weather
    alone.

    The refetch cannot fail the edit: `enrich_weather` swallows every failure path
    and simply leaves the existing values in place, so a slow or down archive costs
    a moment, not the save. `POST /races/{id}/weather/refresh` remains available to
    force a re-run without changing anything.
    """
    race = get_owned_race(db, user, race_id)
    updates = payload.model_dump(exclude_unset=True)

    weather_inputs = {"date", "location_lat", "location_lng"}
    changed_weather_input = any(
        field_name in weather_inputs and getattr(race, field_name) != value
        for field_name, value in updates.items()
    )

    for field_name, value in updates.items():
        setattr(race, field_name, value)

    if changed_weather_input:
        enrich_weather(db, race, overwrite=True)

    db.commit()
    db.refresh(race)
    return RaceRead.model_validate(race)


@router.delete("/{race_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_race(race_id: int, db: DbSession, user: CurrentUser) -> None:
    """Cascades to official result, fuelings, recorded activities, their laps
    and gear assignments — all declared `cascade="all, delete-orphan"` on
    `Race`'s relationships."""
    race = get_owned_race(db, user, race_id)
    db.delete(race)
    db.commit()


@router.put("/{race_id}/official-result", response_model=OfficialResultRead)
def upsert_official_result(
    race_id: int, payload: OfficialResultUpsert, db: DbSession, user: CurrentUser
) -> OfficialResultRead:
    race = get_owned_race(db, user, race_id)
    fields = payload.model_dump()

    splits = {name: fields.get(name) for name in SPLIT_FIELDS}
    try:
        resolved = resolve_total_time(fields.get("total_time"), splits)
    except TotalTimeUnresolvable as exc:
        # 422 with the specific gaps, so the form can point at the fields rather than
        # showing a generic "invalid input".
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": str(exc), "missing_splits": exc.missing},
        ) from exc
    fields["total_time"] = resolved.total_time

    existing = db.query(OfficialResult).filter(OfficialResult.race_id == race.id).one_or_none()
    if existing is None:
        existing = OfficialResult(race_id=race.id, **fields)
        db.add(existing)
    else:
        for field_name, value in fields.items():
            setattr(existing, field_name, value)
    db.commit()
    db.refresh(existing)
    return OfficialResultRead.model_validate(existing)


@router.put("/{race_id}/fueling", response_model=list[RaceFuelingRead])
def upsert_fueling(
    race_id: int, payload: RaceFuelingUpsert, db: DbSession, user: CurrentUser
) -> list[RaceFuelingRead]:
    """Full-replace semantics over the race's fueling rows, keyed by
    `(race_id, segment)`: a segment present in the payload is upserted; a
    segment that currently has a row but is **omitted** from the payload is
    deleted. This is the natural reading of "upsert the whole set" for a form
    that lets the user remove a segment's entry entirely (e.g. "actually I
    didn't eat anything on the run") — a partial-merge semantic would have no
    way to express a deletion at all.
    """
    race = get_owned_race(db, user, race_id)

    existing_rows = db.query(RaceFueling).filter(RaceFueling.race_id == race.id).all()
    existing_by_segment = {row.segment: row for row in existing_rows}
    incoming_segments = {item.segment for item in payload.fuelings}

    for segment, row in existing_by_segment.items():
        if segment not in incoming_segments:
            db.delete(row)

    for item in payload.fuelings:
        products = [p.model_dump() for p in item.products] if item.products is not None else None
        row = existing_by_segment.get(item.segment)
        if row is not None:
            row.carbs_g = item.carbs_g
            row.products = products
            row.notes = item.notes
        else:
            db.add(
                RaceFueling(
                    race_id=race.id,
                    segment=item.segment,
                    carbs_g=item.carbs_g,
                    products=products,
                    notes=item.notes,
                )
            )
    db.commit()

    final_rows = db.query(RaceFueling).filter(RaceFueling.race_id == race.id).all()
    return sorted(
        (RaceFuelingRead.model_validate(row) for row in final_rows),
        key=lambda f: _segment_sort_key(f.segment),
    )


@router.get("/{race_id}/track", response_model=RaceTrackResponse)
def get_race_track(
    race_id: int,
    db: DbSession,
    user: CurrentUser,
    precision: Annotated[
        str | None,
        Query(description="low|medium|high (Douglas-Peucker) or full (unsimplified)"),
    ] = None,
) -> RaceTrackResponse:
    """One polyline per segment, in race order. Only `is_primary` recordings —
    the map shows the leg that counts, not every device that recorded it.
    Derived transitions carry no track (§3 Grouping) and are skipped rather
    than emitting an empty polyline. `?precision=full` returns the unsimplified
    stream; anything else (including omission) goes through
    `simplify_for_precision`."""
    race = get_owned_race(db, user, race_id)

    activities = (
        db.query(RecordedActivity)
        .filter(RecordedActivity.race_id == race.id, RecordedActivity.is_primary.is_(True))
        .all()
    )
    activities.sort(key=lambda a: _segment_sort_key(a.segment))

    segments: list[SegmentTrack] = []
    for activity in activities:
        if activity.is_derived or not activity.gps_track:
            continue

        points = (
            activity.gps_track
            if precision == "full"
            else simplify_for_precision(activity.gps_track, precision)
        )

        bounds: TrackBoundsRead | None = None
        if None not in (
            activity.bounds_n_lat,
            activity.bounds_s_lat,
            activity.bounds_e_lng,
            activity.bounds_w_lng,
        ):
            bounds = TrackBoundsRead(
                n_lat=activity.bounds_n_lat,
                s_lat=activity.bounds_s_lat,
                e_lng=activity.bounds_e_lng,
                w_lng=activity.bounds_w_lng,
            )
        else:
            computed = track_bounds(activity.gps_track)
            if computed is not None:
                bounds = TrackBoundsRead(
                    n_lat=computed.n_lat,
                    s_lat=computed.s_lat,
                    e_lng=computed.e_lng,
                    w_lng=computed.w_lng,
                )

        segments.append(
            SegmentTrack(
                recorded_activity_id=activity.id,
                segment=activity.segment,
                bounds=bounds,
                points=[TrackPoint(**p) for p in points],
            )
        )

    return RaceTrackResponse(segments=segments)


@router.get("/{race_id}/laps", response_model=list[LapSplitRead])
def get_race_laps(
    race_id: int,
    db: DbSession,
    user: CurrentUser,
    sport: Annotated[Segment | None, Query(description="Filter to one segment's laps")] = None,
) -> list[LapSplitRead]:
    """Lap splits for the pacing chart, optionally filtered to one segment.
    Only laps of `is_primary` activities. `sport` is denormalized onto
    `LapSplit` precisely so this filter needs no extra join (§2 Indexes)."""
    race = get_owned_race(db, user, race_id)

    query = (
        db.query(LapSplit)
        .join(RecordedActivity, LapSplit.recorded_activity_id == RecordedActivity.id)
        .options(contains_eager(LapSplit.recorded_activity))
        .filter(
            RecordedActivity.race_id == race.id,
            RecordedActivity.is_primary.is_(True),
        )
    )
    if sport is not None:
        query = query.filter(LapSplit.sport == sport)

    laps = query.all()
    laps.sort(
        key=lambda lap: (
            _segment_sort_key(lap.recorded_activity.segment),
            lap.recorded_activity_id,
            lap.lap_index,
        )
    )
    return [LapSplitRead.model_validate(lap) for lap in laps]


def enrich_weather(db: Session, race: Race, *, overwrite: bool) -> None:
    """Fetch and apply conditions for the race's **entered date**, in place.

    The anchor is `Race.date` — the date the user entered or the import derived —
    resolved to a UTC hour through the race location's timezone. A recorded
    activity gives a precise start time, so it is preferred, but *only when it
    falls on that same local date*: once the user corrects a wrong date, the
    recording is evidence about the wrong day and following it would silently
    ignore the edit.

    Requires a location and does nothing without one. Never raises — weather is
    enrichment, not a precondition (§3), and the service already degrades to
    `None` on every failure path, so a slow or down archive can't block an edit.
    """
    if race.location_lat is None or race.location_lng is None or race.date is None:
        return

    anchor = utc_anchor_for_local_date(race.location_lat, race.location_lng, race.date)

    earliest_start = (
        db.query(RecordedActivity.start_time)
        .filter(RecordedActivity.race_id == race.id, RecordedActivity.is_primary.is_(True))
        .order_by(RecordedActivity.start_time.asc())
        .first()
    )
    if earliest_start is not None:
        tz_name = timezone_at(race.location_lat, race.location_lng)
        recorded_local_date = (
            earliest_start[0].astimezone(ZoneInfo(tz_name)).date()
            if tz_name
            else earliest_start[0].date()
        )
        if recorded_local_date == race.date:
            anchor = earliest_start[0]

    apply_weather_to_race(
        race, fetch_weather(race.location_lat, race.location_lng, anchor), overwrite=overwrite
    )


@router.post("/{race_id}/weather/refresh", response_model=RaceRead)
def refresh_weather(race_id: int, db: DbSession, user: CurrentUser) -> RaceRead:
    """Re-run weather enrichment for a race whose date or location was corrected
    after import — and the way a manually entered race, which has no recording to
    anchor to, gets its conditions at all.

    `overwrite=True`: unlike import-time enrichment this is a deliberate re-run,
    so it replaces whatever is there, including a previously-populated humidity.
    """
    race = get_owned_race(db, user, race_id)

    if race.location_lat is None or race.location_lng is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set the race location before looking up weather.",
        )

    enrich_weather(db, race, overwrite=True)
    db.commit()
    db.refresh(race)
    return RaceRead.model_validate(race)
