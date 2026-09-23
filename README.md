# race-app

A personal triathlon racing-history web app. It ingests FIT files exported from
Garmin/Apple watches (and Strava re-exports), groups multisport sessions into
races, and gives you a timeline with per-segment pacing, gear tracking, and
weather enrichment for each race. The spec lives in [`docs/`](docs/README.md), and
`CLAUDE.md` holds the working conventions.

> **Being rewritten.** The app is being rebuilt as a single-owner instance with a
> private and a public mode, a small Python backend and a plain HTML/CSS/JS frontend
> (see [`docs/roadmap.md`](docs/roadmap.md)). The instructions below describe the
> current v1 code (FastAPI + React) until the rewrite replaces it.

**This repository is private and must stay that way.** `backend/tests/fixtures/`
holds real, unanonymized race recordings (GPS tracks, device serials, personal
profile data) — see that directory's own README before ever making this repo
public.

## Stack

```
backend/    uv project — FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2
frontend/   Vite + React 19 + TS + Tailwind v4 + TanStack Query
```

The only coupling between them is the OpenAPI schema: the frontend's
`src/types/api.d.ts` is generated from the backend's `/openapi.json`, never
hand-written.

## Local development

Requires only Docker.

```bash
docker compose up
```

That brings up Postgres, the API and the Vite dev server together. Migrations
run automatically before the API serves, and both source trees are
bind-mounted, so edits reload in place without a rebuild.

| | |
|---|---|
| Frontend | http://localhost:5173 |
| API | http://localhost:8000 (docs at `/docs`) |

Registration is deliberately closed, so create your account once with the
CLI:

```bash
docker compose exec backend python -m app.cli create-user --email you@example.com
```

Rebuild the images only when dependencies change:

```bash
docker compose up --build
```

<details>
<summary>Running natively instead (requires <code>uv</code> and Node 22+)</summary>

The host defaults in `app/config.py` and `vite.config.ts` point at
`localhost`, so the native workflow still works against the containerised
database alone:

```bash
docker compose up -d db

cd backend && uv sync && uv run alembic upgrade head
uv run uvicorn app.main:app --reload

cd frontend && npm install && npm run dev
```
</details>

Regenerate frontend API types whenever the backend's request/response shapes
change:

```bash
cd frontend && npm run gen:api
```

### Tests & linting

With the stack up, both toolchains are available inside the containers — the
backend dev image installs dev dependencies:

```bash
docker compose exec backend pytest
docker compose exec backend ruff check .
docker compose exec frontend npm run lint
```

Or natively:

```bash
cd backend  && uv run pytest && uv run ruff check . && uv run ruff format .
cd frontend && npm run typecheck && npm run lint
```

Backend DB-backed tests need Postgres running (`docker compose up -d db`).
FIT-parsing tests run against the five real fixtures in
`backend/tests/fixtures/` and assert the ingestion traps in
`docs/reference/fit-files.md`
directly.

## Deployment

Single VPS, `docker compose`, four services:

| Service | Image / build | Role |
|---|---|---|
| `caddy` | `caddy:2-alpine` | Reverse proxy + automatic Let's Encrypt TLS. Serves the built SPA from a shared volume and proxies `/api/*` to `backend`. |
| `backend` | `backend/Dockerfile` | gunicorn + uvicorn workers. Entrypoint runs `alembic upgrade head` before serving. No published host port — only reachable from `caddy` on the compose network. |
| `db` | `postgres:17-alpine` | Named volume, healthcheck. Never publishes a host port. |
| `frontend` | `frontend/Dockerfile` | Build-only: `npm ci && npm run build`, then syncs `dist/` onto the volume `caddy` serves, and exits. No Node process in production. |

### One-time server setup

```bash
git clone <this-repo> /opt/race-app && cd /opt/race-app
cp .env.example .env      # fill in DOMAIN, ACME_EMAIL, POSTGRES_*, JWT_SECRET, ...
```

`.env` must at minimum set, for production:

- `DOMAIN` / `ACME_EMAIL` — Caddy's site address and Let's Encrypt contact.
- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` — the `db` service's
  own credentials.
- `DATABASE_URL` — pointed at the **`db` service hostname**, not `localhost`
  (e.g. `postgresql+psycopg://race:<password>@db:5432/race`), matching the
  `POSTGRES_*` values above.
- `JWT_SECRET` — a real random secret, not the dev default.
- `COOKIE_SECURE=true` and `COOKIE_DOMAIN` — cookies are `HttpOnly`, `Secure`,
  `SameSite=Lax` in production.
- `CORS_ORIGINS` and `VITE_API_BASE_URL` — `https://$DOMAIN` (the frontend is
  served same-origin behind Caddy, so `VITE_API_BASE_URL=/api` also works).

### Deploy

```bash
cd /opt/race-app
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

No CI/CD in v1 — this is the whole deploy. `docker compose`
rebuilds only what changed; the `frontend` container is expected to exit 0
after syncing `dist/` — that's success, not a crash. Seed/manage accounts via
the backend's CLI (no public registration).

Validate the compose file and Caddy config without touching a real
environment:

```bash
docker compose -f docker-compose.prod.yml config
docker run --rm -v "$PWD/Caddyfile:/etc/caddy/Caddyfile" \
  -e DOMAIN=example.com -e ACME_EMAIL=a@b.c \
  caddy:2 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

### Backups

`scripts/backup.sh` takes a `pg_dump` (custom format) of the database and
mirrors the retained original FIT uploads volume into `$BACKUP_DIR` on the
host (default `/opt/race-app/backups`), rotating dumps older than
`$RETENTION_DAYS` (default 14 days). It's a plain host directory on purpose —
whatever offsite backup the VPS already runs can pick it up without knowing
anything about Docker.

Wire it to cron for a nightly run:

```cron
15 3 * * * cd /opt/race-app && ./scripts/backup.sh >> /var/log/race-app-backup.log 2>&1
```

Restore the database from a dump:

```bash
docker compose -f docker-compose.prod.yml exec -T db \
  pg_restore -U race -d race --clean --if-exists < /opt/race-app/backups/db/race-<timestamp>.dump
```

Restore FIT originals by copying `$BACKUP_DIR/uploads/` back onto the
`uploads` named volume (e.g. via a throwaway container bind-mounting both).
