# race-app

Your own triathlon race history, on your own machine or your own domain. Drop in the FIT
files from your watch and each race becomes a timeline entry that opens into official vs
recorded splits, the course map, pacing per lap, weather, fueling and gear. Two races of
the same category can be compared with a gap-to-reference chart.

One instance holds one athlete's races. It runs in one of two modes:

- **private**: every page needs your login. For looking at your own races.
- **public**: anyone can browse the races you publish; editing still needs your login.
  New races are always private until you publish them.

The spec lives in [`docs/`](docs/README.md); [`CLAUDE.md`](CLAUDE.md) holds the working
conventions.

## Run your own instance

You need Docker with Compose.

```bash
git clone <this repository> race-app && cd race-app
cp .env.example .env            # set POSTGRES_PASSWORD; SITE_MODE=private or public
docker compose up -d --build
docker compose exec app python -m app.cli create-owner --username you
# → http://localhost:8000, then New race to drop your first FIT files
```

The app runs its database migrations on start. Forgot the password?
`docker compose exec app python -m app.cli set-password` (the CLI is the reset flow; there
are no reset emails).

**On your own domain**, set `SITE_MODE=public`, `DOMAIN` and `ACME_EMAIL` in `.env`, point
the domain's DNS at the server, and add the Caddy override, which fetches a TLS
certificate automatically:

```bash
docker compose -f docker-compose.yml -f docker-compose.public.yml up -d --build
```

**Backups:** `scripts/backup.sh` dumps the database and mirrors the original FIT files
into `$BACKUP_DIR`; run it nightly from cron. See
[deployment](docs/operations/deployment.md) for restore steps and every setting.

**Parser improved?** Every original upload is kept, so
`docker compose exec app python -m app.cli reimport` re-reads all of them with the new
parser. Your edits, primary-recording choices and gear assignments are kept.

## Develop

```
backend/    Python 3.12, FastAPI + Pydantic v2 + SQLAlchemy 2.0 + Alembic, run with uv
frontend/   plain HTML + CSS + ES-module JS, no build step; Leaflet and Chart.js vendored
```

With a local PostgreSQL (the tests create and drop their own `race_test` database):

```bash
cd backend
uv sync
uv run pytest                    # DATABASE_URL defaults to race:race@localhost:5432/race
uv run ruff check . && uv run ruff format --check .
uv run alembic upgrade head
uv run python -m app.cli create-owner
uv run uvicorn app.main:app --reload     # serves ../frontend too
```

Or everything in Docker with the source bind-mounted and auto-reload:
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up`.

The frontend is exactly what is in `frontend/`: edit a file, reload the page. There is
nothing to build.

### Test fixtures

`backend/tests/fixtures/` holds **synthetic** FIT files that reproduce the vendor quirks
documented in [fit-files](docs/reference/fit-files.md); `make_fixtures.py` regenerates
them. Real recordings never go in the repository: put your own in
`backend/tests/fixtures/private/` (gitignored), and the real-file test runs when they are
present.

## License

MIT, see [LICENSE](LICENSE).
