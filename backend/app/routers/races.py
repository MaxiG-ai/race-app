"""Race detail, track and laps (race-detail.md), and race writes (races-and-results.md)."""

from datetime import date

from fastapi import HTTPException, Query, status
from sqlalchemy import select, update

from app.access import DB, CurrentViewer, Viewer, owner_router, reader_router, visible_race
from app.models import LapSplit, OfficialResult, Race, RecordedActivity, Upload
from app.models.enums import SEGMENTS, ClassificationSource, Segment, UploadStatus
from app.schemas.races import (
    CategoryRef,
    Classification,
    FuelingOut,
    GearName,
    Lap,
    OfficialResultIn,
    OfficialResultOut,
    RaceCreate,
    RaceDetailOwner,
    RaceDetailPublic,
    RacePatch,
    Recording,
    SegmentPublic,
    TrackSegment,
    Weather,
)
from app.services import classify, gear, geo, markdown, results

read = reader_router()
write = owner_router()


# --- building responses -----------------------------------------------------------------


def category_ref(category) -> CategoryRef | None:
    return CategoryRef.model_validate(category) if category else None


def _bounds(a: RecordedActivity) -> list[float] | None:
    values = [a.bounds_n, a.bounds_s, a.bounds_e, a.bounds_w]
    return values if None not in values else None  # type: ignore[return-value]


def _segment_fields(a: RecordedActivity) -> dict:
    return {
        "id": a.id,
        "segment": a.segment,
        "is_derived": a.is_derived,
        "start_time": a.start_time,
        "elapsed_time": a.elapsed_time,
        "moving_time": a.moving_time,
        "distance_m": a.distance_m,
        "elevation_gain_m": a.elevation_gain_m,
        "avg_hr": a.avg_hr,
        "max_hr": a.max_hr,
        "avg_power": a.avg_power,
        "normalized_power": a.normalized_power,
        "avg_cadence": a.avg_cadence,
        "lap_count": len(a.laps),
        "bounds": _bounds(a),
        "time_in_zone": a.time_in_zone,
        "gear": [GearName.model_validate(link.gear) for link in a.gear_links],
    }


def official_out(result: OfficialResult | None) -> OfficialResultOut | None:
    if result is None:
        return None
    out = OfficialResultOut.model_validate(result)
    remainder = results.remainder_split(result)
    if remainder:
        setattr(out, f"{remainder[0]}_time", remainder[1])
        out.derived_split = Segment(remainder[0])
    return out


def ordered_primaries(race: Race) -> list[RecordedActivity]:
    primaries = race.primary_activities()
    return [primaries[s] for s in SEGMENTS if s in primaries]


def detail(race: Race, viewer: Viewer) -> RaceDetailPublic:
    public = {
        "id": race.id,
        "name": race.name,
        "date": race.date,
        "location_name": race.location_name,
        "location_lat": race.location_lat,
        "location_lng": race.location_lng,
        "notes": race.notes,
        "notes_html": markdown.render(race.notes),
        "is_public": race.is_public,
        "category": category_ref(race.category),
        "weather": Weather(
            temp_c=race.weather_temp_c,
            humidity_pct=race.weather_humidity_pct,
            conditions=race.weather_conditions,
        ),
        "official_result": official_out(race.official_result),
        "segments": [SegmentPublic(**_segment_fields(a)) for a in ordered_primaries(race)],
        "fueling": [
            FuelingOut.model_validate(f)
            for f in sorted(race.fueling, key=lambda f: SEGMENTS.index(f.segment))
        ],
    }
    if not viewer.is_owner:
        return RaceDetailPublic(**public)
    recordings = [
        Recording(
            **_segment_fields(a),
            is_primary=a.is_primary,
            upload_id=a.upload_id,
            device_key=a.device_key,
            avg_temp_c=a.avg_temp_c,
            rpe_estimated=a.rpe_estimated,
            sensors=gear.sensors(a),
        )
        for a in sorted(race.activities, key=lambda a: (SEGMENTS.index(a.segment), a.id))
    ]
    return RaceDetailOwner(
        **public,
        published_at=race.published_at,
        weather_fetched_at=race.weather_fetched_at,
        classification=Classification(
            source=race.classification_source,
            auto_category=category_ref(race.auto_category),
            detail=race.classification_detail,
            classified_at=race.classified_at,
        ),
        recordings=recordings,
        completeness=results.completeness(race),
    )


def owner_detail(db: DB, race_id: int) -> RaceDetailOwner:
    race = visible_race(db, Viewer(is_owner=True), race_id)
    db.refresh(race)
    return detail(race, Viewer(is_owner=True))  # type: ignore[return-value]


# --- reads ------------------------------------------------------------------------------


@read.get("/races/{race_id}", response_model=RaceDetailOwner | RaceDetailPublic)
def get_race(race_id: int, db: DB, viewer: CurrentViewer):
    return detail(visible_race(db, viewer, race_id), viewer)


@read.get("/races/{race_id}/track", response_model=list[TrackSegment])
def get_track(
    race_id: int,
    db: DB,
    viewer: CurrentViewer,
    precision: str = Query("medium", pattern="^(low|medium|high|full)$"),
):
    race = visible_race(db, viewer, race_id)
    if precision == "full" and not viewer.is_owner:
        precision = "high"  # visitors always get a simplified track
    out = []
    for a in ordered_primaries(race):
        if not a.gps_track:
            continue
        points = a.gps_track
        if precision != "full":
            points = geo.simplify(points, geo.PRECISION_M[precision])
        out.append(TrackSegment(segment=a.segment, bounds=_bounds(a), points=points))
    return out


@read.get("/races/{race_id}/laps", response_model=list[Lap])
def get_laps(race_id: int, db: DB, viewer: CurrentViewer, sport: str | None = None):
    race = visible_race(db, viewer, race_id)
    ids = [a.id for a in ordered_primaries(race)]
    query = select(LapSplit).where(LapSplit.recorded_activity_id.in_(ids))
    if sport:
        query = query.where(LapSplit.sport == sport)
    laps = db.scalars(query.order_by(LapSplit.start_time, LapSplit.lap_index))
    return [Lap.model_validate(lap) for lap in laps]


# --- writes -----------------------------------------------------------------------------


@write.post("/races", response_model=RaceDetailOwner, status_code=status.HTTP_201_CREATED)
def create_race(body: RaceCreate, db: DB):
    race = Race(date=body.date or date.today(), name=body.name or "Untitled race")
    db.add(race)
    db.commit()
    return owner_detail(db, race.id)


@write.patch("/races/{race_id}", response_model=RaceDetailOwner)
def patch_race(race_id: int, body: RacePatch, db: DB):
    race = visible_race(db, Viewer(is_owner=True), race_id)
    values = body.model_dump(exclude_unset=True)
    for field in ("name", "date"):
        if field in values and values[field] is None:
            raise HTTPException(422, f"{field} can't be empty.")
    if "race_category_id" in values:
        category_id = values.pop("race_category_id")
        if category_id is None:  # clear the override and re-run
            race.classification_source = ClassificationSource.UNCLASSIFIED
            classify.classify_race(db, race)
        else:
            race.race_category_id = category_id
            race.classification_source = ClassificationSource.MANUAL
    for key, value in values.items():
        setattr(race, key, value)
    db.commit()
    return owner_detail(db, race_id)


@write.delete("/races/{race_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_race(race_id: int, db: DB):
    """Its uploads go back to the tray instead of being deleted."""
    race = visible_race(db, Viewer(is_owner=True), race_id)
    db.execute(
        update(Upload)
        .where(Upload.race_id == race.id)
        .values(race_id=None, status=UploadStatus.PARSED)
    )
    db.delete(race)
    db.commit()


@write.put("/races/{race_id}/official-result", response_model=RaceDetailOwner)
def put_official_result(race_id: int, body: OfficialResultIn, db: DB):
    race = visible_race(db, Viewer(is_owner=True), race_id)
    result = race.official_result or OfficialResult(race_id=race.id)
    try:
        results.apply_result(result, body.model_dump(exclude_unset=True))
    except results.ResultError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    race.official_result = result
    db.commit()
    return owner_detail(db, race_id)
