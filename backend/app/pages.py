"""HTML pages, with <title> and Open Graph tags filled in for link previews
(docs/architecture.md#link-previews).

Previews are always built as an anonymous visitor would see them, through
`visible_race`, so a hidden race gets exactly the generic shell a nonexistent id gets.
"""

from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.access import DB, CurrentViewer, Viewer, is_reader, visible_race
from app.config import get_settings
from app.models import Race
from app.models.enums import SEGMENTS
from app.routers.access import site_settings

router = APIRouter(include_in_schema=False)

PLACEHOLDER = "<!-- head -->"
ADMIN_PAGES = {"import", "race-edit", "gear", "site"}
_ANONYMOUS = Viewer(is_owner=False)


def clock(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _file(name: str) -> str:
    return (get_settings().frontend_dir / name).read_text(encoding="utf-8")


def _shell(
    file: str,
    site_name: str,
    root_url: str,
    title: str | None = None,
    description: str | None = None,
    url: str | None = None,
    og_type: str = "website",
) -> str:
    title = title or site_name
    tags = [
        f"<title>{escape(title if title == site_name else f'{title} · {site_name}')}</title>",
        f'<meta property="og:title" content="{escape(title)}">',
        f'<meta property="og:site_name" content="{escape(site_name)}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:url" content="{escape(url or root_url)}">',
    ]
    if description:
        tags.insert(1, f'<meta name="description" content="{escape(description)}">')
        tags.append(f'<meta property="og:description" content="{escape(description)}">')
    if get_settings().site_mode == "public":
        tags.append(
            '<link rel="alternate" type="application/atom+xml" '
            f'title="{escape(site_name)}" href="/feed.xml">'
        )
    return _file(file).replace(PLACEHOLDER, "\n    ".join(tags), 1)


def race_description(race: Race) -> str:
    parts = [f"{race.date.day} {race.date:%b %Y}"]
    result = race.official_result
    primaries = race.primary_activities()
    if result:
        parts.append(clock(result.total_time))
        splits = {s: result.split(s) for s in ("swim", "bike", "run")}
    else:
        if primaries:
            parts.append(clock(sum(a.elapsed_time for a in primaries.values())))
        splits = {s: a.elapsed_time if (a := primaries.get(s)) else None for s in SEGMENTS}
    parts += [f"{s} {clock(v)}" for s in ("swim", "bike", "run") if (v := splits.get(s))]
    return " · ".join(parts)


def _public_race(db: DB, race_id: int) -> Race | None:
    try:
        return visible_race(db, _ANONYMOUS, race_id)
    except HTTPException:
        return None


def _gate(request: Request, viewer: Viewer, owner_only: bool = False) -> RedirectResponse | None:
    allowed = viewer.is_owner if owner_only else is_reader(viewer)
    if allowed:
        return None
    target = quote(str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""))
    return RedirectResponse(f"/login?next={target}", status_code=303)


@router.get("/", response_class=HTMLResponse)
def timeline(request: Request, db: DB, viewer: CurrentViewer):
    if redirect := _gate(request, viewer):
        return redirect
    site = site_settings(db)
    description = site.about or (
        f"Race history of {site.athlete_name}" if site.athlete_name else None
    )
    if get_settings().site_mode == "private":
        description = None
    return _shell("index.html", site.site_title, str(request.base_url), description=description)


@router.get("/races/{race_id}", response_class=HTMLResponse)
def race_page(race_id: int, request: Request, db: DB, viewer: CurrentViewer):
    if redirect := _gate(request, viewer):
        return redirect
    site = site_settings(db)
    race = _public_race(db, race_id)
    if race is None:
        return _shell("race.html", site.site_title, str(request.base_url))
    title = race.name + (f" — {race.category.label}" if race.category else "")
    return _shell(
        "race.html",
        site.site_title,
        str(request.base_url),
        title,
        race_description(race),
        str(request.url),
        "article",
    )


@router.get("/compare", response_class=HTMLResponse)
def compare_page(
    request: Request, db: DB, viewer: CurrentViewer, a: int | None = None, b: int | None = None
):
    if redirect := _gate(request, viewer):
        return redirect
    site = site_settings(db)
    races = [_public_race(db, i) for i in (a, b) if i is not None]
    if len(races) == 2 and all(races):
        title = f"{races[0].name} vs {races[1].name}"  # type: ignore[union-attr]
        return _shell(
            "compare.html", site.site_title, str(request.base_url), title, url=str(request.url)
        )
    return _shell("compare.html", site.site_title, str(request.base_url))


@router.get("/trends", response_class=HTMLResponse)
def trends_page(request: Request, db: DB, viewer: CurrentViewer):
    if redirect := _gate(request, viewer):
        return redirect
    site = site_settings(db)
    return _shell("trends.html", site.site_title, str(request.base_url), "Trends")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: DB):
    return _shell("login.html", site_settings(db).site_title, str(request.base_url), "Log in")


@router.get("/admin/{page}", response_class=HTMLResponse)
def admin_page(page: str, request: Request, db: DB, viewer: CurrentViewer):
    if page not in ADMIN_PAGES:
        raise HTTPException(404)
    if redirect := _gate(request, viewer, owner_only=True):
        return redirect
    return _shell(
        str(Path("admin") / f"{page}.html"), site_settings(db).site_title, str(request.base_url)
    )
