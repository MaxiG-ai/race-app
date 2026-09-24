# One image for every deployment: the Python app serves /api, /static and the pages.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:${PATH}" \
    FRONTEND_DIR=/app/frontend \
    UPLOAD_DIR=/data/uploads
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /data/uploads && chown -R app:app /data
WORKDIR /app/backend
COPY --from=build /app/backend/.venv .venv
COPY backend/alembic.ini ./
COPY backend/app app
COPY frontend /app/frontend
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"
# Migrations run before serving, so an instance can't drift from its schema.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
