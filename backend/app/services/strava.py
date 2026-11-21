"""Strava: the connection, pulling races, the tagline, and deleting Strava data
(docs/features/strava.md#rules).

`strava_api` talks HTTP and `strava_map` turns an activity into a `ParsedFile`. This module
decides what to fetch and hands the results to `imports.place`, the same code that files
dropped FIT files, so a race pulled from Strava fills its legs exactly like dropped files.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.models import (
    Owner,
    Race,
    RecordedActivity,
    SiteSettings,
    StravaAccount,
    StravaActivity,
    Upload,
)
from app.models.enums import RaceType, Sport, UploadSource
from app.services import imports, strava_api, strava_map
from app.services.imports import GroupingError, Outcome, Received
from app.services.strava_api import StravaAuthError, StravaError

log = logging.getLogger(__name__)

# Refresh the access token when it has less than this left.
REFRESH_MARGIN = timedelta(minutes=5)
# An incremental pull looks back this far before the last pull, so a race tagged a few
# weeks after it was uploaded is still found. "Search all of Strava" covers the rest.
LOOKBACK = timedelta(days=60)
# Two legs further apart than this are not one race (a transition, not a lunch break).
MAX_CHAIN_GAP_S = 1800
# Requests a pull keeps in reserve below Strava's limits, for the tagline and the next try.
RATE_MARGIN = 10
_LEG_ORDER = {Sport.SWIM: 0, Sport.BIKE: 1, Sport.RUN: 2}

# The tagline's first words. A line of the description that starts like this is ours:
# replaced on the next write, never duplicated.
TAGLINE_PREFIX = "🏁 Race #"
_TAGLINE_LINE = re.compile(r"^🏁 Race #\d+ of \d{4}\b")
PURGED_HINT = "Strava data was deleted"


class NotConnected(StravaError):
    pass


# --- the connection ---------------------------------------------------------------------


def account(db: Session) -> StravaAccount | None:
    return db.scalar(select(StravaAccount))


def connect(db: Session, token: dict[str, Any], granted: str) -> StravaAccount:
    """Store the tokens of a completed authorization (login or connect). The caller has
    already checked that the athlete is the owner's."""
    athlete = token["athlete"]
    row = account(db)
    if row is not None and row.athlete_id != athlete["id"]:
        db.delete(row)
        db.flush()
        row = None
    if row is None:
        row = StravaAccount(id=1, athlete_id=athlete["id"])
        db.add(row)
    name = " ".join(p for p in (athlete.get("firstname"), athlete.get("lastname")) if p)
    row.athlete_name = name or None
    row.access_token = token["access_token"]
    row.refresh_token = token["refresh_token"]
    row.expires_at = datetime.fromtimestamp(int(token["expires_at"]), tz=utcnow().tzinfo)
    row.scope = granted
    row.needs_reconnect = False
    return row


def access_token(db: Session, row: StravaAccount) -> str:
    """A valid access token, refreshed when it is about to expire. Strava may rotate the
    refresh token on every refresh; the newest one is stored at once."""
    if row.expires_at - utcnow() > REFRESH_MARGIN:
        return row.access_token
    settings = get_settings()
    try:
        body = strava_api.refresh_token(
            settings.strava_client_id, settings.strava_client_secret, row.refresh_token
        )
    except StravaAuthError:
        revoked(db)
        raise
    row.access_token = body["access_token"]
    row.refresh_token = body.get("refresh_token") or row.refresh_token
    row.expires_at = datetime.fromtimestamp(int(body["expires_at"]), tz=utcnow().tzinfo)
    db.commit()
    return row.access_token


def revoked(db: Session) -> None:
    """Strava refused our token: the athlete revoked access. Their Strava data goes
    (Strava API Agreement: delete on revocation); logging in with Strava again reconnects."""
    db.rollback()
    log.warning("Strava access was revoked; deleting Strava data")
    purge(db)


def purge(db: Session) -> list[str]:
    """Delete everything that came from Strava: its recordings, their stored originals,
    the pulled-activity list and the tokens. Races, typed results, reports, photos and
    recordings from FIT files stay. Commits; returns nothing to clean up afterwards."""
    uploads = list(db.scalars(select(Upload).where(Upload.source == UploadSource.STRAVA)))
    paths = []
    for upload in uploads:
        try:
            with db.begin_nested():
                path = imports.remove_upload(db, upload)
        except GroupingError:
            # The legs left behind don't make derived transitions; drop the recordings anyway.
            path = upload.stored_path
            db.execute(delete(RecordedActivity).where(RecordedActivity.upload_id == upload.id))
            db.delete(upload)
        if path:
            paths.append(path)
    db.execute(delete(RecordedActivity).where(RecordedActivity.source == UploadSource.STRAVA))
    db.execute(delete(StravaActivity))
    db.execute(delete(StravaAccount))
    db.commit()
    for path in paths:
        Path(path).unlink(missing_ok=True)
    return paths


def disconnect(db: Session) -> None:
    """Revoke our access at Strava, then delete the Strava data."""
    row = account(db)
    if row is not None:
        settings = get_settings()
        strava_api.revoke(
            settings.strava_client_id,
            settings.strava_client_secret,
            row.refresh_token,
            row.access_token,
        )
    purge(db)


def unpublish_strava_races(db: Session) -> list[int]:
    """Consent withdrawn: every public race with Strava data goes back to private."""
    races = db.scalars(select(Race).where(Race.is_public)).all()
    hidden = [r.id for r in races if has_strava_data(r)]
    for r in races:
        if r.id in hidden:
            r.is_public = False
    return hidden


def consented(db: Session) -> bool:
    site = db.get(SiteSettings, 1)
    return site is not None and site.strava_public_consent_at is not None


def has_strava_data(race: Race) -> bool:
    return any(a.source is UploadSource.STRAVA for a in race.activities)


def activity_url(activity: RecordedActivity) -> str | None:
    """ "View on Strava" for a recording pulled from Strava (brand guidelines)."""
    if activity.source is not UploadSource.STRAVA or not activity.raw_data:
        return None
    strava = (activity.raw_data.get("session") or {}).get("strava") or {}
    return strava_api.ACTIVITY_URL.format(id=strava["id"]) if strava.get("id") else None


# --- finding races among activities ---------------------------------------------------


def _gap(before: dict[str, Any], after: dict[str, Any]) -> float:
    return (strava_map.start_time(after) - strava_map.end_time(before)).total_seconds()


def chains(activities: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group the race activities of one triathlon: Strava has no multisport activity, so a
    triathlon arrives as a swim, a ride and a run a few minutes apart.

    A chain is swim → bike → run legs, in that order, each starting at most
    MAX_CHAIN_GAP_S after the previous one ends. Its members are the race-tagged legs,
    plus a swim right before a race-tagged leg: Strava's API reports the race flag
    (`workout_type`) on runs and rides only, so an untagged swim can't say it is a race.
    An untagged ride or run could have said so and didn't, so it stays out.
    """
    legs = sorted(
        (a for a in activities if strava_map.sport_of(a) is not None),
        key=strava_map.start_time,
    )
    runs: list[list[dict[str, Any]]] = []
    for a in legs:
        prev = runs[-1][-1] if runs else None
        if (
            prev is not None
            and 0 <= _gap(prev, a) <= MAX_CHAIN_GAP_S
            and _LEG_ORDER[strava_map.sport_of(a)] > _LEG_ORDER[strava_map.sport_of(prev)]  # type: ignore[index]
        ):
            runs[-1].append(a)
        else:
            runs.append([a])
    out = []
    for run in runs:
        kept = [
            a
            for i, a in enumerate(run)
            if strava_map.is_race(a)
            or (
                strava_map.sport_of(a) is Sport.SWIM
                and i + 1 < len(run)
                and strava_map.is_race(run[i + 1])
            )
        ]
        if kept:
            out.append(kept)
    return out


def race_type_of(chain: list[dict[str, Any]]) -> RaceType:
    sports = {strava_map.sport_of(a) for a in chain}
    if len(sports) == 1:
        return RaceType(sports.pop().value)  # type: ignore[union-attr]
    return RaceType.TRIATHLON


_LEG_WORDS = re.compile(
    r"^(swim|schwimmen|bike|rad|radfahren|ride|cycling|run|lauf|laufen|running)$", re.IGNORECASE
)


def race_name(chain: list[dict[str, Any]]) -> str:
    """The words the legs' names share ("Breisgau Triathlon Run" / "… Rad" → "Breisgau
    Triathlon"), else the first leg's name."""
    names = [(a.get("name") or "").strip() for a in chain]
    if len(names) == 1:
        return names[0] or "Untitled race"
    split = [n.split() for n in names]
    prefix = []
    for words in zip(*split, strict=False):
        if len({w.lower() for w in words}) != 1:
            break
        prefix.append(words[0])
    suffix: list[str] = []
    for words in zip(*(reversed(s) for s in split), strict=False):
        if len({w.lower() for w in words}) != 1:
            break
        suffix.insert(0, words[0])
    best = max(prefix, suffix, key=len)
    cleaned = " ".join(w for w in best if not _LEG_WORDS.match(w)).strip(" -–—·|:")
    return cleaned or names[0] or "Untitled race"


# --- pulling ----------------------------------------------------------------------------


@dataclass
class PulledRace:
    race_id: int
    name: str
    created: bool
    outcomes: list[Outcome]


@dataclass
class PullResult:
    races: list[PulledRace] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    taglines: int = 0
    tagline_errors: list[str] = field(default_factory=list)
    scanned: int = 0
    complete: bool = True
    # Something was left for later (waiting for consent): the next pull must look here again.
    retry_later: bool = False
    message: str = ""


class _Budget(Exception):
    """The rate limit is nearly used up: stop here, the next pull continues."""


def _spend() -> None:
    if strava_api.last_rate_limit.nearly_spent(RATE_MARGIN):
        raise _Budget


def _list_races(token: str, after: int | None, result: PullResult) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    page = 1
    while True:
        _spend()
        batch = strava_api.list_activities(token, after=after, page=page)
        result.scanned += len(batch)
        found.extend(batch)
        if len(batch) < strava_api.PER_PAGE:
            return found
        page += 1


def _target_race(db: Session, chain: list[dict[str, Any]], known: dict[int, StravaActivity]):
    """Where a chain's new legs go: the race its other legs were pulled into, or a race
    that already has recordings at that time (from a FIT file). None: a new race.
    `False`: its race was deleted by the owner, so it isn't pulled again."""
    for a in chain:
        row = known.get(int(a["id"]))
        if row is not None and row.state == "imported":
            if row.race_id is None:
                return False
            race = db.get(Race, row.race_id)
            if race is not None:
                return race
    start = strava_map.start_time(chain[0])
    end = strava_map.end_time(chain[-1])
    candidates = db.scalars(
        select(RecordedActivity).where(
            RecordedActivity.is_derived.is_(False),
            RecordedActivity.start_time < end,
            RecordedActivity.start_time > start - timedelta(days=1),
        )
    )
    for rec in candidates:
        if rec.end_time > start:
            return rec.race
    return None


def _fetch(token: str, summary: dict[str, Any]) -> Received | Outcome:
    activity_id = int(summary["id"])
    _spend()
    detail = strava_api.activity(token, activity_id)
    _spend()
    laps = strava_api.laps(token, activity_id)
    _spend()
    streams = strava_api.streams(token, activity_id)
    name = detail.get("name") or summary.get("name")
    try:
        parsed = strava_map.to_parsed(detail, laps, streams)
    except (ValueError, KeyError, TypeError) as exc:
        return Outcome(name, "failed", f"Strava's data for this activity can't be read: {exc}")
    return Received(
        filename=name,
        data=strava_map.original(detail, laps, streams),
        content_hash=None,
        parsed=parsed,
        upload=None,
        source=UploadSource.STRAVA,
        external_id=str(activity_id),
    )


def _remember(
    db: Session, summary: dict[str, Any], race_id: int | None, outcome: Outcome
) -> StravaActivity:
    row = db.get(StravaActivity, int(summary["id"]))
    if row is None:
        row = StravaActivity(activity_id=int(summary["id"]))
        db.add(row)
    row.name = summary.get("name")
    row.sport_type = summary.get("sport_type")
    row.start_time = strava_map.start_time(summary)
    row.race_id = race_id if outcome.result == "attached" else None
    row.state = "imported" if outcome.result == "attached" else "skipped"
    row.message = outcome.message
    return row


def pull(db: Session, *, everything: bool = False) -> PullResult:
    """POST /api/strava/pull: find race-tagged activities not pulled before and file them
    as races, then bring every pulled activity's tagline up to date."""
    row = account(db)
    if row is None:
        raise NotConnected("Strava isn't connected.")
    if not row.granted(strava_api.READ_SCOPE) and not row.granted("activity:read"):
        raise StravaError("Strava didn't grant access to activities. Reconnect and allow it.")
    result = PullResult()
    token = access_token(db, row)
    after = None
    if row.last_pull_at is not None and not everything:
        after = int((row.last_pull_at - LOOKBACK).timestamp())
    started = utcnow()
    try:
        activities = _list_races(token, after, result)
        known = {
            r.activity_id: r
            for r in db.scalars(
                select(StravaActivity).where(
                    StravaActivity.activity_id.in_([int(a["id"]) for a in activities])
                )
            )
        }
        for chain in chains(activities):
            _pull_chain(db, token, chain, known, result)
        _taglines(db, row, token, result)
    except _Budget:
        result.complete = False
        result.message = (
            "Stopped early: Strava's rate limit is nearly used up. Pull again in 15 minutes "
            "to continue where this one stopped."
        )
    except StravaAuthError:
        raise
    if result.complete and not result.retry_later:
        row.last_pull_at = started
    if result.complete:
        if not result.message:
            n = len(result.races)
            result.message = (
                f"{n} race{'s' if n != 1 else ''} pulled from Strava."
                if n
                else "No new races on Strava."
            )
    db.commit()
    return result


def _pull_chain(
    db: Session,
    token: str,
    chain: list[dict[str, Any]],
    known: dict[int, StravaActivity],
    result: PullResult,
) -> None:
    new = [a for a in chain if int(a["id"]) not in known]
    if not new:
        return
    target = _target_race(db, chain, known)
    if target is False:
        for a in new:
            outcome = Outcome(a.get("name"), "skipped", "Its race was deleted here.")
            known[int(a["id"])] = _remember(db, a, None, outcome)
            result.skipped.append(
                {"activity_id": str(a["id"]), "name": a.get("name"), "message": outcome.message}
            )
        db.commit()
        return
    if target and target.is_public and not consented(db):
        message = (
            f"Not added: “{target.name}” is public, and visitors may only see Strava data "
            "once you agree to it on the Site page."
        )
        for a in new:
            result.skipped.append(
                {"activity_id": str(a["id"]), "name": a.get("name"), "message": message}
            )
        result.retry_later = True  # not remembered: the next pull tries again
        return
    checked = [_fetch(token, a) for a in new]
    race, outcomes = imports.place(
        db, list(checked), race=target, race_type=None if target else race_type_of(chain)
    )
    created = target is None and race is not None
    if created:
        race.name = race_name(chain)  # type: ignore[union-attr]
    for a, outcome in zip(new, outcomes, strict=True):
        known[int(a["id"])] = _remember(db, a, race.id if race else None, outcome)
        if outcome.result != "attached":
            result.skipped.append(
                {"activity_id": str(a["id"]), "name": a.get("name"), "message": outcome.message}
            )
    db.commit()
    if race is not None and any(o.result == "attached" for o in outcomes):
        result.races.append(PulledRace(race.id, race.name, created, outcomes))


# --- the tagline --------------------------------------------------------------------------


def clock(seconds: int) -> str:
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def tagline(db: Session, race: Race) -> str:
    """One line: which race of the season it was, its distance and time, and a link when
    visitors can open it. "🏁 Race #7 of 2026 · Olympic distance · 2:31:04 · <link>"."""
    year = select(Race.id).where(
        Race.date >= race.date.replace(month=1, day=1),
        Race.date <= race.date.replace(month=12, day=31),
    )
    ordered = list(db.scalars(year.order_by(Race.date, Race.id)))
    parts = [f"{TAGLINE_PREFIX}{ordered.index(race.id) + 1} of {race.date.year}"]
    if race.category is not None:
        parts.append(race.category.label)
    result = race.official_result
    if result is not None and result.total_time:
        parts.append(clock(result.total_time))
    settings = get_settings()
    if settings.site_mode == "public" and race.is_public and settings.public_base_url:
        parts.append(f"{settings.public_base_url.rstrip('/')}/races/{race.id}")
    return " · ".join(parts)


def merge_description(current: str | None, line: str) -> str:
    """The athlete's own text untouched, our earlier tagline replaced by `line`."""
    kept = [x for x in (current or "").splitlines() if not _TAGLINE_LINE.match(x.strip())]
    while kept and not kept[-1].strip():
        kept.pop()
    body = "\n".join(kept)
    return f"{body}\n\n{line}" if body else line


def _taglines(db: Session, row: StravaAccount, token: str, result: PullResult) -> None:
    site = db.get(SiteSettings, 1)
    if site is None or not site.strava_tagline or not row.granted(strava_api.WRITE_SCOPE):
        return
    pulled = db.scalars(
        select(StravaActivity).where(
            StravaActivity.state == "imported", StravaActivity.race_id.is_not(None)
        )
    ).all()
    for entry in pulled:
        race = db.get(Race, entry.race_id)
        if race is None:
            continue
        line = tagline(db, race)
        if line == entry.tagline:
            continue
        try:
            _spend()
            current = strava_api.activity(token, entry.activity_id).get("description")
            merged = merge_description(current, line)
            if merged != (current or ""):
                _spend()
                strava_api.update_description(token, entry.activity_id, merged)
        except StravaAuthError:
            raise
        except StravaError as exc:
            result.tagline_errors.append(f"{entry.name or entry.activity_id}: {exc}")
            continue
        entry.tagline = line
        entry.tagline_at = utcnow()
        result.taglines += 1
        db.commit()


# --- webhook events -----------------------------------------------------------------------


def handle_event(db: Session, event: dict[str, Any]) -> None:
    """A push event from Strava (docs/features/strava.md#webhook). Events aren't signed, so
    nothing is deleted on an event's word alone: each one is checked against the API."""
    row = account(db)
    if row is None or event.get("owner_id") != row.athlete_id:
        return
    kind, aspect = event.get("object_type"), event.get("aspect_type")
    if kind == "athlete" and (event.get("updates") or {}).get("authorized") == "false":
        try:
            strava_api.get(access_token(db, row), "/athlete")
        except StravaAuthError:
            revoked(db)
        return
    if kind == "activity" and aspect == "delete":
        activity_id = int(event["object_id"])
        try:
            strava_api.activity(access_token(db, row), activity_id)
            return  # still there: not deleted
        except StravaAuthError:
            revoked(db)
            return
        except StravaError as exc:
            if "404" not in str(exc):
                return
        forget_activity(db, activity_id)


def forget_activity(db: Session, activity_id: int) -> None:
    """An activity deleted on Strava: its recording and stored original go too."""
    upload = db.scalar(select(Upload).where(Upload.external_id == str(activity_id)))
    path = None
    if upload is not None:
        try:
            path = imports.remove_upload(db, upload)
        except GroupingError:
            db.rollback()
            return
    entry = db.get(StravaActivity, activity_id)
    if entry is not None:
        db.delete(entry)
    db.commit()
    if path:
        Path(path).unlink(missing_ok=True)


def owner_for_athlete(db: Session, athlete_id: int) -> Owner | None:
    owner = db.scalar(select(Owner))
    if owner is not None and owner.strava_athlete_id == athlete_id:
        return owner
    return None
