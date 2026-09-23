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
from its schema. For development, the source is bind-mounted with auto-reload.

## Public

```bash
cp .env.example .env    # DOMAIN, ACME_EMAIL, SITE_MODE=public, POSTGRES_*, SECRET
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
| `DATABASE_URL` | points at the `db` service host |
| `SECRET` | random secret; used to sign anything that needs signing |
| `UPLOAD_DIR` | where original FIT files are kept (a mounted volume) |
| `OPEN_METEO_BASE_URL` | weather API base |
| `DOMAIN`, `ACME_EMAIL` | public mode only |

No CORS settings are needed: the frontend and the API share an origin.

## Uploads

- Original FIT files are kept verbatim on the `uploads` volume, keyed by upload id. They're
  the only way to re-import the whole collection when the parser improves
  (`cli reimport`).
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
- Local instances: the same script works; point `BACKUP_DIR` somewhere that gets backed up.

## Updating

`git pull && docker compose ... up -d --build`. No CI/CD is needed to deploy. CI for tests
and lint on pull requests is worth adding once the repository is public.
