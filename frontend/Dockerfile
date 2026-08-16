# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build — the only stage that ever runs Node.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# Dependency layer first, so source edits don't bust the npm ci cache.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vite inlines VITE_* vars into the built bundle at build time (not runtime),
# so this has to be an ARG (settable via `docker build --build-arg` / compose
# `build.args`) promoted to an ENV before `npm run build` runs.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: publish — NOT a server. §8: "No Node process in production." This
# image's only job is to own a copy of dist/ and, on every `docker compose up
# --build`, sync it onto the named volume that Caddy serves static files
# from. It then exits 0 — in `docker compose ps` this container is expected
# to show "Exited (0)", which is success, not a crash (see restart: "no" and
# the comment on the `frontend` service in docker-compose.prod.yml).
#
# Re-running this stage (e.g. `docker compose up -d --build frontend`) is how
# a frontend-only deploy ships, without ever running a Node process.
# ---------------------------------------------------------------------------
FROM alpine:3.20 AS publish

COPY --from=build /app/dist /dist

# /shared is where compose mounts the volume Caddy reads from. Wipe it first
# so a deploy that removes/renames a hashed asset doesn't leave the stale
# file behind forever, then mirror the fresh build onto it.
CMD ["sh", "-c", "find /shared -mindepth 1 -delete && cp -a /dist/. /shared/ && echo 'frontend: dist synced to /shared, exiting'"]
