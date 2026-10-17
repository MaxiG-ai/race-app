# race-app — working conventions

A race-history web app: one athlete per instance, run privately or published on your own
domain with an owner-only edit mode. **`docs/` is the spec.** Start at `docs/README.md`,
then read the feature file you are working on. `docs/reference/fit-files.md` holds
hard-won detail about real FIT files that can't be worked out from the code.

## The rewrite

The app is being rebuilt nearly from scratch (see `docs/roadmap.md`):

- **Backend:** Python 3.12+, FastAPI + Pydantic v2 + SQLAlchemy 2.0 (`Mapped[...]`,
  `mapped_column`) + Alembic, run with `uv` (`uv add`, `uv run pytest`). Never pip, never
  a bare venv. Every package is justified in `docs/architecture.md`.
- **Frontend:** plain HTML + CSS + ES-module JS, no build step, no framework. The only
  libraries are Leaflet (maps) and Chart.js (charts), both vendored and pinned. Every chart
  goes through `static/js/charts.js`.
- **Link previews:** public pages are served through backend shells that fill in
  `<title>`/Open Graph tags; a hidden race gets the generic shell.
- **Database:** PostgreSQL, always, including local runs.
- Ruff, line length 100. JS modules start with `// @ts-check`. Design tokens are CSS
  custom properties in `tokens.css`; never raw hex. Numeric columns get `.tnum`.

The rewrite has replaced v1 (FastAPI + React, multi-tenant) in the working tree; v1 is
only in git history. Where to find things: access rules in `backend/app/access.py`
(router factories + `visible_race(s)`), the FIT parser in `services/fit_import.py`, what
a parse becomes in the database (which race and leg, primaries, derived transitions) in
`services/imports.py`; prefilled official results in `services/results.py`.

## Non-negotiables

- **Units:** durations are integer seconds; distances and elevation are metres; timestamps
  are UTC and timezone-aware. `race.date` is a plain *local* date. Pace is derived on
  read, never stored. Unknown is null and shows as "—", never 0.
- **Access** (`docs/features/access-and-visibility.md`): one owner, no tenancy. Every
  write needs the owner session, enforced at router level. Every race read goes
  through `visible_race(s)`, which returns **404** for races the viewer can't see.
  Public responses use explicit public shapes; never-public fields (uploads,
  `raw_data`, serials) are never in them. New races start `is_public = false`.
- **Session totals over lap sums** (fit-files Trap 1).
- **Half-open, clamped `[start, end)` record bucketing** (Trap 3). Orphans are dropped
  and counted (Trap 4); a record matching two windows fails the import.
- **Nothing device-specific inferred from the sport** (Trap 2).
- **No real personal recordings in the repository.** Tests use the synthetic fixtures;
  real files go in `backend/tests/fixtures/private/` (gitignored).
