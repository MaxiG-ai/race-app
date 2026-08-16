#!/usr/bin/env bash
#
# Container entrypoint for the backend image. Runs pending Alembic migrations
# *before* serving traffic (PLAN.md §8: "a deploy can't drift from its
# schema"), then execs gunicorn with uvicorn workers so gunicorn (pid 1's
# child via exec) receives signals directly for clean shutdown.
#
# Assumes an alembic.ini at the working directory (/app) pointing at
# app/migrations — the standard uv/Alembic layout for this project.
set -euo pipefail

echo "[entrypoint] running database migrations (alembic upgrade head)..."
alembic upgrade head
echo "[entrypoint] migrations complete."

WORKERS="${GUNICORN_WORKERS:-2}"
BIND="${GUNICORN_BIND:-0.0.0.0:${PORT:-8000}}"
TIMEOUT="${GUNICORN_TIMEOUT:-30}"

echo "[entrypoint] starting gunicorn (workers=${WORKERS} bind=${BIND} timeout=${TIMEOUT})..."
exec gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    --workers "${WORKERS}" \
    --bind "${BIND}" \
    --timeout "${TIMEOUT}" \
    --access-logfile - \
    --error-logfile -
