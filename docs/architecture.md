# Architecture

## Shape

```
 browser                           one Python process                    PostgreSQL
┌───────────────────┐   HTTP    ┌────────────────────────────┐        ┌──────────────┐
│ static HTML pages │ ────────► │ /            static files  │        │              │
│ ES modules, CSS   │  JSON     │ /api/*       JSON API      │ ─────► │  races, ...  │
│ Leaflet (vendored)│ ◄──────── │ import pipeline            │        │  (jsonb)     │
└───────────────────┘           │ weather enrichment         │        └──────────────┘
                                └────────────────────────────┘
                                          │
                                  uploaded FIT originals
                                  on a mounted volume
```

- **One origin.** The backend serves both the static frontend and `/api`. There is no
  CORS configuration and no separate frontend server. In a public deployment, a reverse
  proxy in front adds TLS and nothing else ([deployment](operations/deployment.md)).
- **The frontend is files, not a build.** HTML pages load ES modules straight from
  `/static/js/`. What is in the repository is exactly what the browser gets.
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
| Leaflet | Tiled slippy maps are real work to get right | vendored into `static/vendor/leaflet/`, pinned version, no CDN |

That is the whole list. Charts (segment bar, pacing chart, gap chart) are hand-written
SVG: each one is a few hundred lines at most, and a charting library would be the biggest
thing on every page. Forms use native HTML validation. Routing is separate HTML pages,
with no client-side router.

### Backend

Required, with no reasonable alternative:

| Package | Why |
|---|---|
| `fitdecode` | FIT parsing; handles Garmin's undecoded proprietary messages without errors ([fit-files](reference/fit-files.md#library)) |
| `psycopg` (3) | PostgreSQL driver |
| `argon2-cffi` | password hashing for the owner login |

To decide before implementation starts (see Open questions):

| Need | Option A — smaller | Option B — more done for us |
|---|---|---|
| HTTP framework | Starlette + uvicorn | FastAPI (Starlette + Pydantic + OpenAPI docs) |
| Request validation | small hand-written validators | Pydantic v2 |
| SQL | hand-written SQL through psycopg | SQLAlchemy 2.0 Core |
| Migrations | numbered `.sql` files + a 40-line runner | Alembic |

Standard library instead of a package wherever possible: `urllib.request` for Open-Meteo,
`secrets` + a `session` table instead of a JWT library, `hashlib` for content hashes,
`zoneinfo` for local race dates.

Dev-only: `pytest`, `ruff`. Everything is run through `uv`.

## Repository layout

```
race-app/
  backend/
    pyproject.toml, uv.lock
    app/
      main.py           app factory, mounts /api and /static
      config.py         env-driven settings (SITE_MODE, DATABASE_URL, ...)
      db.py             connection pool, transaction helper
      access.py         owner session, viewer resolution, visibility filter
      api/              one module per feature: races, uploads, gear, ...
      services/         fit_import, grouping, gear, weather, classify, comparison, geo
      migrations/       0001_init.sql, 0002_..., run on startup
      cli.py            create-owner, set-password, reimport
    tests/
      fixtures/         synthetic FIT files, generated (see reference/fit-files.md)
      fixtures/private/ gitignored — your own real files, tests skip when absent
  frontend/
    index.html          timeline
    race.html           race detail (?id=)
    compare.html        comparison (?a=&b=)
    admin/              import, edit, gear — owner only
    static/
      css/              tokens.css, base.css, one file per page
      js/               api.js, format.js, svg.js, one module per page
      vendor/leaflet/
  docs/
  docker-compose.yml    app + db, local and self-hosted
  Caddyfile             only for the public deployment
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

## Open questions

- **Framework and SQL layer.** Pick A or B in each row of the backend table above. A
  suggestion: Starlette + hand-written SQL + `.sql` migrations fits the "few dependencies"
  goal best, and the schema is small enough (about a dozen tables) that an ORM saves
  little. Pydantic is the strongest case for option B: partial-update validation with
  `exclude_unset` is fiddly to write by hand.
- **Server-rendered HTML for public pages.** Public race pages built in JS are invisible
  to link previews and search engines. Option: the backend renders a minimal HTML shell
  with `<title>` and Open Graph tags per race, and JS fills in the rest. Worth deciding
  before the public page templates are built.
