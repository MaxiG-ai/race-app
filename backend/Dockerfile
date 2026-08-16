# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: builder — resolves the uv-locked environment and compiles the app
# into it. Nothing from this stage but the finished venv (+ source) survives
# into the runtime image.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer: copy only the lockfiles first, so editing application source
# under app/ never invalidates this layer — `uv sync` here only re-runs when
# pyproject.toml or uv.lock actually change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Now bring in the rest of the project (app/, alembic config, etc. — whatever
# isn't excluded by .dockerignore) and install the project itself.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2: runtime — no uv, no compiler, no build tooling. Just Python, the
# synced venv, and the source tree, run as a non-root user.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    PORT=8000

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Bring over the venv + installed project from the builder in one shot — this
# also carries across whatever migration config lives alongside pyproject.toml
# (e.g. alembic.ini) without the Dockerfile needing to name it explicitly.
COPY --from=builder --chown=app:app /app /app

# Pre-create the upload dir with the right owner so the named volume mounted
# here in compose inherits app:app ownership on first creation (§8: original
# FIT files are retained verbatim on a mounted volume).
RUN mkdir -p /app/var/uploads && chown -R app:app /app/var && chmod +x /app/docker-entrypoint.sh

USER app

EXPOSE 8000

# No curl/wget in the final image (keep it minimal) — Python is already here.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8000') + '/health', timeout=3)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
