# Architecture

## Shape

```
 browser                           one Python process                    PostgreSQL
┌───────────────────┐   HTTP    ┌────────────────────────────┐        ┌──────────────┐
│ HTML pages        │ ────────► │ /, /races/…  page shells   │        │              │
│ ES modules, CSS   │  JSON     │ /static/*    static files  │ ─────► │  races, ...  │
│ Leaflet, Chart.js │ ◄──────── │ /api/*       JSON API      │        │  (jsonb)     │
│ (vendored)        │           │ import pipeline, weather   │        └──────────────┘
└───────────────────┘           └────────────────────────────┘
                                          │
                                  uploaded FIT originals
                                  on a mounted volume
```

- **One origin.** The backend serves both the static frontend and `/api`. There is no
  CORS configuration and no separate frontend server. In a public deployment, a reverse
  proxy in front adds TLS and nothing else ([deployment](operations/deployment.md)).
- **The frontend is files, not a build.** HTML pages load ES modules straight from
  `/static/js/`. What is in the repository is exactly what the browser gets.
- **Public pages are served through a thin shell.** `/`, `/races/{id}`, `/compare` and
  `/trends` are routes on the backend that return the page's static HTML with `<title>` and
  Open Graph tags filled in, so shared links get a proper preview
  ([Link previews](#link-previews)). Everything else on the page is still rendered by JS
  from the JSON API.
- **The JSON API is the only coupling.** Pages fetch JSON and render it. The public site and
  the owner's editing views use the same read endpoints; write endpoints are
  owner-only ([access-and-visibility](features/access-and-visibility.md)).

## Dependency policy

Every dependency has to justify itself in the table below. Adding one means adding a row
that says what it replaces and why writing it ourselves would be worse. "Convenient" is
not a reason.

### Frontend

| Package | Why | How it ships |
|---|---|---|
| Leaflet | Tiled slippy maps are real work to get right | vendored into `static/vendor/leaflet/`, pinned version |
| Chart.js | Line charts with axes, tooltips and fills; plain JS, no dependencies, and it covers the gap chart's needs out of the box ([visual-system](design/visual-system.md#charts)) | vendored `chart.umd.min.js` in `static/vendor/chartjs/`, pinned version |

That is the whole list. Libraries are vendored rather than loaded from a CDN: a
self-hosted instance shouldn't depend on a third party being up, and a pinned file in the
repository can be reviewed. The segment bar isn't a chart: it's a row of `<div>`s with
widths set in CSS. Forms use native HTML validation. There is no client-side router;
every page is its own HTML file.

### Backend

The backend can carry more than the frontend. A well-known framework stack is worth
its weight here, because it handles validation, partial updates and migrations, which
are fiddly to write by hand.

| Package | Why |
|---|---|
| FastAPI + uvicorn | HTTP framework; router-level dependencies are how owner-only writes are enforced ([access](features/access-and-visibility.md#enforcement-lives-in-two-places)); `/api/docs` for free |
| Pydantic v2 | request/response models; `exclude_unset` gives the "field left out vs explicit null" distinction partial writes need; separate public and owner response shapes |
| SQLAlchemy 2.0 | ORM, `Mapped[...]` / `mapped_column` style, `DeclarativeBase` |
| Alembic | migrations; run on startup |
| `psycopg` (3) | PostgreSQL driver |
| `fitdecode` | FIT parsing; handles Garmin's undecoded proprietary messages without errors ([fit-files](reference/fit-files.md#library)) |
| `argon2-cffi` | password hashing for the owner login |
| `pydantic-settings` | env-driven config |
| `python-multipart` | FastAPI needs it to accept multipart FIT uploads |
| `timezonefinder` | coordinate → timezone, offline, for the local race date ([Trap 11](reference/fit-files.md#trap-11)) and the weather hour. A lookup service would make every import depend on the network |

Standard library instead of a package wherever possible: `urllib.request` for Open-Meteo,
`secrets` + a `session` table instead of a JWT library, `hashlib` for content hashes,
`zoneinfo` for local race dates, `xml.etree` for the Atom feed, and a hand-written
Markdown subset for race reports (a library would still need a sanitiser after it; see
[race reports](features/races-and-results.md#race-reports)).

Dev-only: `pytest`, `ruff`, `httpx` (FastAPI's test client), `fit-tool` (writes the
synthetic FIT fixtures; [fit-files](reference/fit-files.md#testing-without-the-originals)).
Everything is run through `uv`.

## Repository layout

```
race-app/
  backend/
    pyproject.toml, uv.lock
    app/
      main.py           app factory; mounts /api, /static and the page shells
      config.py         pydantic-settings (SITE_MODE, DATABASE_URL, ...)
      db.py             engine, session dependency, DeclarativeBase
      access.py         owner session, viewer resolution, visible_race(s)
      models/           SQLAlchemy models, one module per feature
      schemas/          Pydantic models; owner and public shapes side by side
      routers/          one module per feature; read and owner-only write routers
      pages.py          page shells with <title> / Open Graph tags
      feed.py           /feed.xml, the Atom feed of public races
      services/         fit_import (bytes → dataclasses), imports (uploads, grouping,
                        primaries), results, classify, gear, weather, comparison, geo,
                        markdown (the race-report subset)
      migrations/       Alembic
      cli.py            create-owner, set-password, reimport
    tests/
      fixtures/         synthetic FIT files + the fit-tool script that generates them
      fixtures/private/ gitignored — your own real files, tests skip when absent
  frontend/
    index.html          timeline            served at /
    race.html           race detail         served at /races/{id}
    compare.html        comparison          served at /compare?a=&b=
    trends.html         trends              served at /trends
    login.html
    admin/              import, race-edit, gear, site — owner only, served at /admin/<name>
    static/
      css/              tokens.css, base.css, one file per page
      js/               api.js, format.js, charts.js, one module per page
      vendor/leaflet/, vendor/chartjs/
  docs/
  Dockerfile            one image: backend + frontend
  docker-compose.yml    app + db, local and self-hosted
  docker-compose.public.yml, docker-compose.dev.yml   Caddy in front; bind-mounted source
  Caddyfile             only for the public deployment
  .github/workflows/    CI: lint and tests on pull requests
```

## Conventions

These apply across every feature and do not get restated in feature files.

- **Durations are integer seconds.** Distances and elevation are metres. Formatting is a
  frontend concern (`static/js/format.js`).
- **Timestamps are UTC and timezone-aware.** `Race.date` is a plain *local* date, derived
  from the start location's timezone ([Trap 11](reference/fit-files.md#trap-11)).
- **Pace is derived on read**, never stored.
- **Unknown is null, and shows as "—".** Never `0`, never `0:00`.
- **Partial updates:** a write body that leaves out a field leaves that field alone; an
  explicit `null` clears it. The two cases must stay distinct in every write endpoint.
- **Postgres-specific types are fine** (`jsonb`, arrays, partial indexes). Postgres is
  the only target, locally too.
- **Python:** 3.12+, ruff, line length 100.
- **JS:** ES modules, no globals. Start every module with `// @ts-check`, and add JSDoc
  typedefs for API shapes in `static/js/types.js`. Editors then type-check the code without
  a build step.
- **CSS:** design tokens as custom properties in `tokens.css`; never raw hex outside it.
  Numeric columns get `.tnum` (tabular numerals).

## Link previews

A link to a public race should unfurl in a chat or a social post with the race name,
date and result, not a blank "Race history". Crawlers don't run JS, so these tags have to
be in the HTML the server sends.

- `pages.py` serves `index.html`, `race.html` and `compare.html` through backend routes.
  It reads the static file and fills a placeholder in `<head>` with `<title>`,
  `<meta name="description">`, and `og:title` / `og:description` / `og:url` /
  `og:type` / `og:site_name`, all `html.escape`d. That's string substitution, not a
  template engine.
- Example for a race: `og:title` "Example Triathlon — Sprintdistanz",
  `og:description` "21 Jun 2026 · 1:12:40 · swim 12:03 · bike 35:10 · run 21:40".
- **Visibility applies to previews too.** A race the viewer can't see gets the generic
  site shell, exactly the same as a nonexistent id, so the preview never leaks a hidden
  race's name. In private mode every shell is generic.
- Compare pages preview as "A vs B" only when both races are public.
- No `og:image` in the first version. A rendered course thumbnail is in the
  [backlog](backlog.md).

## Open questions

- **JSDoc types from OpenAPI.** FastAPI publishes an OpenAPI schema. A small script could
  generate the JSDoc typedefs in `static/js/types.js` from it, so the frontend's types
  can't drift from the Pydantic models. Worth doing once the API settles; until then,
  write them by hand.
