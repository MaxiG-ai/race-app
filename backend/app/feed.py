"""Atom feed of public races, at /feed.xml (docs/features/timeline.md#feed).

Like the link previews, the feed is always built as an anonymous visitor sees the site,
through `visible_races`: it only ever lists public races, whoever asks. A private
instance has no feed at all (feed readers can't log in), so there it is a 404.
"""

from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import nulls_last
from sqlalchemy.orm import selectinload

from app.access import DB, Viewer, visible_races
from app.config import get_settings
from app.db import utcnow
from app.models import Race, RecordedActivity
from app.pages import race_description
from app.routers.access import site_settings
from app.services import markdown

router = APIRouter(include_in_schema=False)

ATOM = "http://www.w3.org/2005/Atom"
FEED_LIMIT = 50
_ANONYMOUS = Viewer(is_owner=False)


def _text(parent: Element, tag: str, value: str, **attrs: str) -> Element:
    el = SubElement(parent, tag, attrs)
    el.text = value
    return el


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def race_entry(feed: Element, race: Race, root: str) -> datetime:
    url = f"{root}races/{race.id}"
    updated = max(filter(None, [race.updated_at, race.published_at]))
    entry = SubElement(feed, "entry")
    _text(entry, "id", url)
    title = race.name + (f" — {race.category.label}" if race.category else "")
    _text(entry, "title", title)
    SubElement(entry, "link", rel="alternate", type="text/html", href=url)
    _text(entry, "published", _stamp(race.published_at or race.created_at))
    _text(entry, "updated", _stamp(updated))
    _text(entry, "summary", race_description(race))
    if html := markdown.render(race.notes):
        _text(entry, "content", html, type="html")  # escaped once more by the serialiser
    return updated


@router.get("/feed.xml")
def feed(request: Request, db: DB) -> Response:
    if get_settings().site_mode != "public":
        raise HTTPException(404)
    site = site_settings(db)
    root = str(request.base_url)
    races = db.scalars(
        visible_races(_ANONYMOUS)
        .options(
            selectinload(Race.official_result),
            selectinload(Race.category),
            # race_description needs the primary totals only: no tracks, no raw_data.
            selectinload(Race.activities).load_only(
                RecordedActivity.segment,
                RecordedActivity.is_primary,
                RecordedActivity.elapsed_time,
                RecordedActivity.start_time,
            ),
        )
        .order_by(nulls_last(Race.published_at.desc()), Race.date.desc(), Race.id.desc())
        .limit(FEED_LIMIT)
    ).all()

    feed = Element("feed", xmlns=ATOM)
    _text(feed, "id", root)
    _text(feed, "title", site.site_title)
    if site.about:
        _text(feed, "subtitle", site.about)
    author = SubElement(feed, "author")
    _text(author, "name", site.athlete_name or site.site_title)
    SubElement(feed, "link", rel="self", type="application/atom+xml", href=f"{root}feed.xml")
    SubElement(feed, "link", rel="alternate", type="text/html", href=root)
    updated_el = _text(feed, "updated", "")
    stamps = [race_entry(feed, race, root) for race in races]
    updated_el.text = _stamp(max(stamps, default=utcnow()))
    body = tostring(feed, encoding="unicode", xml_declaration=False)
    return Response(
        '<?xml version="1.0" encoding="utf-8"?>\n' + body,
        media_type="application/atom+xml",
    )
