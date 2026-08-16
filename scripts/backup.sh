#!/usr/bin/env bash
#
# Nightly backup for race-app (PLAN.md §8): a `pg_dump` (custom format, so
# it's restorable with `pg_restore` and independent of server version quirks)
# plus a mirror of the retained original FIT uploads. Both land under
# $BACKUP_DIR on the host — that's deliberate: it puts them in an ordinary
# directory that whatever offsite backup the VPS already runs (restic,
# borg, a cron rsync to another box, ...) can pick up without knowing
# anything about Docker. This script does not itself ship anything off-box.
#
# Idempotent: safe to re-run; each run adds one timestamped dump and mirrors
# uploads to their current state, then rotates dumps older than
# $RETENTION_DAYS. Exits non-zero (and leaves prior backups untouched) on any
# failure.
#
# Usage:
#   ./scripts/backup.sh
#
# Cron (nightly at 03:15, from the deploy directory):
#   15 3 * * * cd /opt/race-app && ./scripts/backup.sh >> /var/log/race-app-backup.log 2>&1
#
# Config (env, all optional — defaults match docker-compose.prod.yml):
#   COMPOSE_FILE      compose file to target        (docker-compose.prod.yml)
#   COMPOSE_PROJECT   compose -p project name        (compose default: dir name)
#   BACKUP_DIR        host directory to back up into (/opt/race-app/backups)
#   RETENTION_DAYS    days of db dumps to keep        (14)
#   UPLOADS_VOLUME    docker volume holding uploads   (<project>_uploads)
#   POSTGRES_USER     must match the db service       (race)
#   POSTGRES_DB       must match the db service       (race)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-}"
BACKUP_DIR="${BACKUP_DIR:-/opt/race-app/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
POSTGRES_USER="${POSTGRES_USER:-race}"
POSTGRES_DB="${POSTGRES_DB:-race}"

# Docker Compose prefixes volume names with the project name (by default the
# directory the compose file lives in). Override UPLOADS_VOLUME if your
# deploy directory isn't named "race-app" or you set COMPOSE_PROJECT.
DEFAULT_PROJECT="$(basename "$(pwd)")"
UPLOADS_VOLUME="${UPLOADS_VOLUME:-${COMPOSE_PROJECT:-$DEFAULT_PROJECT}_uploads}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DB_BACKUP_DIR="$BACKUP_DIR/db"
UPLOADS_BACKUP_DIR="$BACKUP_DIR/uploads"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }

compose() {
    local args=(-f "$COMPOSE_FILE")
    if [[ -n "$COMPOSE_PROJECT" ]]; then
        args+=(-p "$COMPOSE_PROJECT")
    fi
    docker compose "${args[@]}" "$@"
}

mkdir -p "$DB_BACKUP_DIR" "$UPLOADS_BACKUP_DIR"

# --- Database ----------------------------------------------------------------
DUMP_FILE="$DB_BACKUP_DIR/${POSTGRES_DB}-${TIMESTAMP}.dump"
log "dumping database '$POSTGRES_DB' -> $DUMP_FILE"
if ! compose exec -T db pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "${DUMP_FILE}.tmp"; then
    log "ERROR: pg_dump failed"
    rm -f "${DUMP_FILE}.tmp"
    exit 1
fi
mv "${DUMP_FILE}.tmp" "$DUMP_FILE"
log "database dump complete ($(du -h "$DUMP_FILE" | cut -f1))"

# --- Original FIT uploads -----------------------------------------------------
# Mirror the named volume's contents onto the host via a throwaway container —
# rsync --delete keeps this a true mirror (an upload can be discarded via
# DELETE /uploads/{id}), so re-running never accumulates stale files.
log "mirroring FIT originals from volume '$UPLOADS_VOLUME' -> $UPLOADS_BACKUP_DIR"
if ! docker run --rm \
    -v "${UPLOADS_VOLUME}:/source:ro" \
    -v "${UPLOADS_BACKUP_DIR}:/dest" \
    alpine:3.20 \
    sh -c 'apk add --no-cache rsync >/dev/null && rsync -a --delete /source/ /dest/'; then
    log "ERROR: FIT originals mirror failed"
    exit 1
fi
log "FIT originals mirror complete ($(du -sh "$UPLOADS_BACKUP_DIR" | cut -f1))"

# --- Rotation ------------------------------------------------------------------
log "rotating db dumps older than $RETENTION_DAYS days in $DB_BACKUP_DIR"
find "$DB_BACKUP_DIR" -name '*.dump' -mtime "+${RETENTION_DAYS}" -print -delete

log "backup finished OK"
