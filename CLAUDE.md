# race-app — working conventions

A race-history web app: one athlete per instance, run privately or published on your own
domain with an owner-only edit mode. **`docs/` is the spec.** Start at `docs/README.md`,
then read the feature file you are working on. `docs/reference/fit-files.md` holds
hard-won detail about real FIT files that can't be worked out from the code.

## The rewrite

The app is being rebuilt nearly from scratch (see `docs/roadmap.md`):

- **Backend:** Python 3.12+, run with `uv` (`uv add`, `uv run pytest`). Never pip, never a bare
  venv. Small dependency set; every package justified in `docs/architecture.md`.
- **Frontend:** plain HTML + CSS + ES-module JS, no build step, no framework. Leaflet
  (vendored) is the only frontend library. Charts are hand-written SVG.
- **Database:** PostgreSQL, always, including local runs.
- Ruff, line length 100. JS modules start with `// @ts-check`. Design tokens are CSS
  custom properties in `tokens.css`; never raw hex. Numeric columns get `.tnum`.

The `backend/` (FastAPI/SQLAlchemy) and `frontend/` (React/Vite) trees are **v1** and
are being replaced. Don't extend them. Their `§N` comments refer to the old, deleted
`PLAN.md`.

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
