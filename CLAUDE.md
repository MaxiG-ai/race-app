# race-app — working conventions

Personal triathlon racing-history web app. **`PLAN.md` is the spec** — read the relevant
section before implementing; it encodes hard-won detail about real FIT files that is not
re-derivable from the code.

## Layout

```
backend/    uv project — FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2
frontend/   Vite + React 19 + TS + Tailwind v4 + TanStack Query
```

## Backend

- **uv for everything.** `uv add <pkg>`, `uv run pytest`, `uv run alembic ...`,
  `uv run uvicorn app.main:app --reload`. Never pip, never a bare venv.
- Python 3.12+. SQLAlchemy 2.0 style: `Mapped[...]` / `mapped_column`, `DeclarativeBase`
  (`app.db.Base`). Pydantic v2 (`model_config = ConfigDict(from_attributes=True)`).
- Line length 100, ruff (`uv run ruff check --fix . && uv run ruff format .`).
- Postgres-specific types (`JSONB`, `ARRAY`) are fine — Postgres is the only target.

### Non-negotiables from the plan

- **Durations are integer seconds; distances/elevation are metres; timestamps are UTC and
  timezone-aware.** `Race.date` is a plain local date. Pace is derived on read, never stored.
- **Tenancy (§4).** Three ownership roots: `Race.user_id`, `Upload.user_id`, `Gear.user_id`.
  Everything else inherits through `race_id`. Every race lookup goes through
  `get_owned_race(db, user, race_id)`, which raises **404** (not 403) for someone else's
  race. No unscoped `SELECT ... FROM races` anywhere, aggregates included.
- **Auth by default.** `current_user` is applied at *router* level, not per endpoint, so a
  new endpoint is authenticated unless deliberately exempted (`/auth/login`, `/health`).
- **Session totals over lap sums (§3 Traps 1).** Apple drops the trailing partial bike lap;
  summing laps understates the bike by 3.7 km. Laps carry pacing shape only.
- **Half-open `[start, end)` record bucketing (§3 Traps 3).** Sessions overlap sub-second.
  Records matching no window are dropped *and counted* — not fatal (§3 Traps 4). A record
  matching two windows is a bug and fails the import.
- **Nothing device-specific may be inferred from the sport (§3 Traps 2).** Apple has run
  power and no bike power; Garmin the reverse. Every segment carries `avg_power`.

## Frontend

- `npm run dev` / `npm run build`. Import alias `@/` → `src/`.
- Design tokens live in `src/index.css` as Tailwind v4 `@theme` vars — use
  `bg-bg text-ink border-hairline text-swim/bike/run`, never raw hex.
- Numeric columns get the `.tnum` class (tabular numerals).
- Types are generated, not hand-written: `npm run gen:api` → `src/types/api.d.ts`.
- TanStack Query for all server state; no manual `useEffect` fetching.

## Testing

- `uv run pytest`. FIT parsing tests run against the five real fixtures in
  `backend/tests/fixtures/` and assert the §3 traps directly.
- Fixtures are **real, unanonymized** race data — see their README. The repo stays private.
- DB-backed tests need Postgres (`docker compose up -d db`).
