# Deployment

Three ways to run an instance. All three use the same image and PostgreSQL, and the
same `docker compose` file at their core.

| | Local | Self-hosted (private) | Public on your domain |
|---|---|---|---|
| `SITE_MODE` | `private` | `private` | `public` |
| Services | app, db | app, db | app, db, caddy |
| TLS | none (localhost) | your reverse proxy / VPN | Caddy, Let's Encrypt |
| Cookie `Secure` | off on `localhost` | on | on |

## Local

```bash
cp .env.example .env
docker compose up
docker compose exec app python -m app.cli create-owner
# → http://localhost:8000
```

The app container runs migrations before it starts serving, so a checkout can't drift
from its schema. For development, `docker-compose.dev.yml` bind-mounts the source with auto-reload.

## Public

```bash
cp .env.example .env    # DOMAIN, ACME_EMAIL, SITE_MODE=public, POSTGRES_*
docker compose -f docker-compose.yml -f docker-compose.public.yml up -d --build
docker compose exec app python -m app.cli create-owner
```

`docker-compose.public.yml` only adds Caddy in front: it terminates TLS, raises the request
body limit for uploads, and proxies everything to `app`. The app serves the static
frontend itself, so Caddy serves no files.

## Services

| Service | Notes |
|---|---|
| `app` | the Python server; serves `/`, `/static` and `/api`. No published port in public mode, so it's reachable only from Caddy. |
| `db` | Postgres with a named volume. Never publishes a host port. |
| `caddy` | public mode only. Reverse proxy + automatic TLS. |

## Configuration

Twelve-factor: everything comes from env vars, and `.env.example` documents each one.

| Var | Purpose |
|---|---|
| `SITE_MODE` | `private` \| `public`, see [access](../features/access-and-visibility.md) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | read by the `db` service and by the app, which builds its database URL from them (`POSTGRES_HOST`, default `localhost`, is set to `db` by compose) |
| `DATABASE_URL` | optional; used as is instead of the `POSTGRES_*` values |
| `APP_PORT` | host port binding for local/self-hosted runs, default `127.0.0.1:8000` |
| `UPLOAD_DIR` | where original FIT files are kept (a mounted volume) |
| `MAX_UPLOAD_BYTES` | per-file upload limit, default 20 MB |
| `OPEN_METEO_BASE_URL` | weather API base |
| `DOMAIN`, `ACME_EMAIL` | public mode only |

No CORS settings are needed: the frontend and the API share an origin.

## Uploads

- Original FIT files are kept verbatim on the `uploads` volume, keyed by upload id. They're
  the only way to re-import the whole collection when the parser improves
  (`cli reimport`).
- Race photos live on the same volume under `photos/`: two re-encoded JPEGs per photo
  (full ≤ 2400 px and a thumbnail), typically 0.5–1 MB together. The originals aren't
  kept ([photos](../features/photos.md#stripping-metadata)). Budget for them when sizing
  the volume and the backup target; 500 photos is roughly half a gigabyte.
- The app rejects oversized files and files that aren't FIT before parsing. Caddy's body
  limit must be raised explicitly to match.

## Backups

A race history has no other source once the watch data ages out, so back it up.

- `scripts/backup.sh`: a nightly `pg_dump` (custom format), plus a mirror of the uploads
  volume, into `$BACKUP_DIR` on the host, with dumps rotated after `$RETENTION_DAYS`
  (default 14). It's a plain host directory on purpose, so whatever offsite backup the
  server already runs picks it up.
- Cron: `15 3 * * * cd /opt/race-app && ./scripts/backup.sh`
- Restore: `pg_restore --clean --if-exists` from a dump; copy `uploads/` back onto the volume.
  That includes `uploads/photos/`: the database rows without their files show as broken
  images.
- Local instances: the same script works; point `BACKUP_DIR` somewhere that gets backed up.

## Updating

`git pull && docker compose ... up -d --build`. No CD is needed to deploy.

## CI

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`:

- **backend**: `uv sync --frozen`, `ruff check`, `ruff format --check` and `pytest`
  against a `postgres:16` service container, the same database the app always runs on.
- **frontend**: `node --check` on every ES module. There is no build and no
  `package.json`, so this only proves each file parses.

The test suite also asserts the repository conventions that are cheap to check
mechanically (`tests/test_conventions.py`): every JS module starts with `// @ts-check`,
no raw hex colours outside `tokens.css`, and no FIT files outside the synthetic fixtures.
Tests that need your private recordings skip in CI, since `fixtures/private/` is
gitignored.

### Coming from v1

The rewrite starts a fresh migration history (`0001_initial_schema`) and doesn't migrate v1
data: v1 was multi-tenant and its schema doesn't map onto the single-owner one. A v1
database stops the app at startup with Alembic's "Can't locate revision" error; nothing is
changed or lost. To move over:

1. Back up (`scripts/backup.sh`), and keep your original FIT files.
2. `docker compose down -v` removes the old database and uploads volumes.
3. `docker compose up -d --build`, `create-owner`, then re-upload the FIT files.
