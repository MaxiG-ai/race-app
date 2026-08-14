# Triathlon Racing History — Implementation Plan

Personal analytics web app for triathlon racing history. Timeline-first browsing of past
races, drilling into per-race detail: official vs. recorded splits, course map, pacing
charts, weather and fueling. Built for one athlete now, with the data model prepared for
opening it to others later.

Two data sources feed the model:
- **Official results** — manually entered by the user (race organizer timing).
- **Recorded activity data** — imported from FIT files (primary) and/or Strava (secondary backfill).

Source of truth for actual performance data (splits, HR, power, pace) is FIT files: far
richer than Strava's API, and independent of how a watch's auto-upload happened to split
things.

Priority throughout: minimal framework overhead, clean and functional over highly
polished. This is a personal tool, not a showcase.

## Decisions

| Decision | Choice |
|---|---|
| Database | PostgreSQL |
| Hosting | Deployed to a VPS via docker compose |
| Auth | Real login with user accounts |
| Tenancy | Multi-tenant by design, single-tenant in operation — you first, others later |
| FIT upload | Auto-creates a draft race |
| Carbs | Per-segment (bike / run) |
| v1 scope | M0–M7; Strava deferred |

---

## 1. Architecture

```
┌──────────────────────────┐         ┌──────────────────────────┐
│  frontend/               │  HTTP   │  backend/                │
│  React + Vite + TS       │ ──────► │  FastAPI + SQLAlchemy    │
│  TanStack Query          │  JSON   │  Pydantic v2             │
│  Recharts · react-leaflet│ ◄────── │  Alembic migrations      │
└──────────────────────────┘         └────────────┬─────────────┘
         ▲                                        │
         │ openapi-typescript                     │
         │ (generated types)                      ▼
         └───────────────────────────  ┌──────────────────────┐
                                       │  PostgreSQL (jsonb)  │
                                       └──────────────────────┘
                                                  ▲
                              ┌───────────────────┴──────────────┐
                              │  Import pipeline                 │
                              │  fitdecode → normalize → persist │
                              │  + Open-Meteo weather enrichment │
                              └──────────────────────────────────┘
```

Monorepo, two top-level directories, no shared build tooling between them. The only
coupling is the OpenAPI schema, from which frontend types are generated.

```
race-app/
  backend/
    app/
      main.py            # FastAPI app, router registration, CORS
      config.py          # pydantic-settings, env-driven
      db.py              # engine, session dependency
      models/            # SQLAlchemy ORM: race, official_result, recorded_activity, lap_split
      schemas/           # Pydantic request/response models
      routers/           # auth, races, imports, stats, weather
      auth/              # password hashing, JWT issue/verify, current_user dependency
      services/
        fit_import.py    # fitdecode parsing → normalized dicts
        weather.py       # Open-Meteo historical enrichment
        aggregates.py    # summary stats queries
        geo.py           # polyline simplification / downsampling
      migrations/        # Alembic
    tests/
      fixtures/          # two real multisport FIT exports (see §3)
    pyproject.toml
  frontend/
    src/
      api/               # typed fetch functions + TanStack Query hooks
      components/        # RaceCard, SegmentBar, StatStrip, TimelineSpine, ...
      pages/             # Timeline.tsx, RaceDetail.tsx, RaceEdit.tsx, Import.tsx
      types/             # generated from FastAPI OpenAPI schema
      lib/               # formatting helpers (pace, time, duration)
    package.json
  docker-compose.yml     # local dev: postgres
  docker-compose.prod.yml # VPS: caddy, backend, db, frontend build (see §8)
  Caddyfile
  .env.example
  PLAN.md
```

---

## 2. Data Model

### `User`
```
id (PK)
email                 unique
password_hash         argon2 (via passlib/argon2-cffi)
display_name          nullable
is_active
created_at
```

Registration is **not** open in v1 — accounts are seeded by a CLI command
(`python -m app.cli create-user`). A public signup form is a liability until there's a
reason for one; opening it up later is an additive change, not a rework.

### `Race`
Event shell. One row per race event.
```
id (PK)
user_id (FK, indexed) owner; every read and write is scoped by it — see §4 Tenancy
name
date
location_name
location_lat / location_lng
race_type             enum: sprint, olympic, half_iron, full_iron, custom
weather_temp_c        nullable, from weather API enrichment
weather_humidity_pct  nullable, from weather API (FIT humidity field is never populated)
weather_conditions    nullable, e.g. "clear", "overcast"
carbs_bike_g          nullable, manual entry (fueling isn't in FIT data)
carbs_run_g           nullable, manual entry
notes                 nullable, free text
created_at / updated_at
```

Carbs live on `Race` as two nullable columns rather than on `RecordedActivity`, because
fueling is entered by hand and must be recordable for a race that has no FIT import yet.
Swim is omitted deliberately — nobody fuels mid-swim. Total carbs is derived on read, not
stored. If fueling ever needs finer structure (per-lap, or gel-by-gel), it graduates to
its own `RaceFueling` table; two columns don't justify one now.

### `OfficialResult` (1:1 with Race, manually entered)
```
id (PK)
race_id (FK, unique)
total_time
swim_time         nullable
t1_time           nullable — blank if not separately timed
bike_time         nullable
t2_time           nullable
run_time          nullable
overall_place     nullable
age_group_place   nullable
entered_at
```

### `RecordedActivity` (many:1 with Race — typically 5 rows per race)
```
id (PK)
race_id (FK)
segment           enum: swim, t1, bike, t2, run
source            enum: fit_upload, strava
external_id       nullable (Strava activity id)
start_time
total_time
distance_m        nullable (transitions are short but real: 20 m-550 m observed)
elevation_gain_m  nullable (session total_ascent; absent on swim/transitions)
avg_hr / max_hr   nullable
avg_power         nullable — running power in practice, not bike (see §3.3)
normalized_power  nullable — never populated by this device; kept for Strava
avg_pace / avg_speed           nullable (swim has no avg_speed; derive from dist/time)
rpe_estimated     nullable, from FIT dev field
avg_mets          nullable, from FIT dev field
bounds_*          nullable — session bounding box (n/s/e/w lat/lng) for map fitting
gps_track         jsonb — array of {lat, lng, elevation, timestamp}
raw_data          jsonb — full parsed FIT/Strava payload, safety net for unmapped fields
imported_at
```

`gps_track` stays JSONB for now. Migrate to a PostGIS `geometry` column only if spatial
queries ("races whose bike course passes near X") become a real need.

### `LapSplit` (many:1 with RecordedActivity — auto-laps: 1 km run, 5 km bike, 1 per swim)
```
id (PK)
recorded_activity_id (FK)
sport             enum: swim, t1, bike, t2, run — denormalized from parent for direct
                  per-sport filtering without a join
lap_index         integer
start_time
total_time
distance_m        nullable
avg_hr / max_hr   nullable
avg_power         nullable — running power in practice, not bike (see §3.3)
normalized_power  nullable — never populated by this device; kept for Strava
avg_pace / avg_speed          nullable
elevation_gain_m  nullable
cadence_avg       nullable — unreliable; absent entirely in one of two sample files
is_partial        bool — true when the lap is a trailing remainder (see §3.2)
raw_data          jsonb
```

### Indexes
- `RecordedActivity.race_id`, `Race.date` — aggregate queries (total distance/elevation
  across races, date-range filtering).
- `LapSplit(sport, recorded_activity_id)` — fast per-sport filtering (e.g. "all run
  splits under 5:00/km").
- `LapSplit(recorded_activity_id, lap_index)` — reconstruct lap order within one activity.
- Unique constraint on `RecordedActivity(race_id, segment, source)` — makes re-import
  idempotent rather than duplicating rows.

### Conventions
- All durations stored as **seconds (integer)**; formatting is a frontend concern.
- All timestamps stored **UTC, timezone-aware**; `Race.date` is a plain date.
- Distances in metres, elevation in metres, pace derived on read (not stored redundantly).

---

## 3. Ingestion

### FIT files (primary)

Verified against two real Apple Watch multisport exports, both in
`backend/tests/fixtures/`:

| | `multisport_2026-07-11.fit` | `multisport_2026-08-09.fit` |
|---|---|---|
| Sessions | 5 | 5 |
| Records | 9 829 | 9 413 |
| Laps | 19 | 21 |
| Swim | 1 572 m / 28:59 | 1 548 m / 31:12 |
| T1 | 552 m / 3:36 | 105 m / 2:27 |
| Bike | 33 746 m / 1:13:08, +558 m | 40 570 m / 1:09:39, +22 m |
| T2 | 218 m / 2:00 | 20 m / 0:08 |
| Run | 9 172 m / 56:03, +166 m | 9 933 m / 53:24, +48 m |

Confirmed:

- **One FIT file = one race**, containing 5 `session` messages in order: swim
  (open_water), transition (T1), cycling, transition (T2), running. Import logic must
  split the `record` stream (~9–10 k per-second points) by session `start_time` +
  `total_elapsed_time` boundaries — *not* expect 5 separate file uploads.
- **`record`** messages give the full per-second stream — source for `gps_track`.
  GPS coverage is 100 %, including the open-water swim. Bucketing records by session
  window leaves **zero** unassigned points in both files.
- **`session`** carries `total_ascent` and an `nec`/`swc` bounding box — enough to fit the
  map viewport without touching the point stream.
- **No nutrition/carbs data anywhere** (all frame types checked, developer fields
  included). Carbs stays manual entry.
- **Developer fields declared**: `SESSION UUID`, `SESSION INDOOR`,
  `SESSION ACTIVITY TYPE`, `WORKOUT RPE ESTIMATED`, `AVG METs`,
  `SESSION WEATHER HUMIDITY`. No temperature field anywhere.

Corrections to earlier assumptions — each of these is a place naive import logic goes
wrong:

1. **Lap length is not ~1 km across the board.** Runs auto-lap at 1 km, **bikes at 5 km**,
   and the **whole swim is a single lap**. So a swim has exactly one "split" — the pacing
   chart must not render a one-point line for it — and bike splits are coarse (6–8 per race).
2. **Bike laps silently drop the trailing partial lap.** 6 × 5 000 m = 30 000 m against a
   33 746 m session; 8 × 5 000 m = 40 000 m against 40 570 m. Run laps *do* include their
   partial final lap and reconcile exactly. **Session totals are authoritative; laps are
   for pacing shape only.** Summing laps under-reports the bike by up to 3.7 km.
3. **Power is on the run, not the bike.** Both files: running `avg_power` 295 / 307 W
   (Apple Watch running power), bike `avg_power` `None` — no bike power meter.
   `normalized_power` is `None` everywhere. This is the exact inverse of the usual
   assumption, and it drives which chart series exist per sport.
4. **Consecutive session windows overlap by under a second** (session *n* ends
   11:51:42.054, session *n+1* starts 11:51:42). Inclusive bucketing double-counts 1–2
   records per race. Use **half-open** `[start, end)` intervals.
5. **Transitions are real segments**, not near-zero: T1 of 552 m / 3:36 in one race. But
   T2 in the other is 20 m / 7.7 s — the watch barely caught it, so recorded T2 will
   diverge sharply from official T2. Good motivation for the official-vs-recorded view;
   bad news for anyone treating recorded transitions as ground truth.
6. **`SESSION WEATHER HUMIDITY` is declared but never populated** in either file, and
   `AVG METs` likewise. `WORKOUT RPE ESTIMATED` appears in one file only. Treat every
   developer field as optional, and weather as **API-only** in practice.
7. **Field names are the `enhanced_` variants**: `enhanced_speed` (91–96 % of records),
   `enhanced_altitude` (80–82 %). Altitude is missing from a fifth of points, so elevation
   profiles need gap handling. Swim laps have **no `avg_speed`** — swim pace must be
   derived from distance ÷ time.
8. **Cadence is unreliable**: present on 35 % of records in one file, entirely absent from
   the other. Not a field to build a feature on.
9. **Running dynamics are present** — `vertical_oscillation`, `step_length`,
   `stance_time`, `vertical_ratio` (~22–29 % of records). Not modelled; they land in
   `raw_data` and can be promoted later.
10. **`manufacturer` reads `development`**, not `apple` — the third-party converter
    rewrites it, so device detection can't rely on it.
11. **Timestamps are UTC; filenames encode local time** (`...131906` vs `time_created`
    11:19:06 UTC — CEST, +2). `Race.date` must be the *local* race date, derived from the
    start location's timezone, or an early-morning start will land on the wrong day.

Also present and unused for now: `sport` (×5) and `time_in_zone` (×5) messages, the
latter carrying HR-zone distribution per session — a cheap future addition.

**Library: `fitdecode`** — confirmed working on both files with no malformed-frame errors.

**Import flow:**
1. Upload FIT via `POST /api/imports/fit` (multipart), optionally with `race_id` to
   attach to an existing race.
2. Parse: read `session`, `lap`, `record` messages in one pass.
3. Segment: map the 5 sessions to `swim, t1, bike, t2, run` by order + sport field.
   Validate the expected sequence rather than trusting it; a file that doesn't match is
   rejected with a clear error, not silently half-imported.
4. Bucket `record` points into sessions using **half-open `[start, end)` windows** —
   sessions share a boundary sub-second and inclusive matching double-counts (§3.4).
   Assert every record lands in exactly one session; both fixtures satisfy this.
5. If no `race_id` given, create a draft `Race`: date from the first session's start time
   **converted to the start location's local timezone** (§3.11), location from the
   bounding-box centroid, name and `race_type` left for the user.
6. Persist `RecordedActivity` × 5 + their `LapSplit` rows. Segment totals come from the
   `session` message, never from summing laps (§3.2); trailing partial laps are flagged
   `is_partial`. Store the full parsed payload in `raw_data`.
7. Trigger weather enrichment (below).
8. Return the created/updated race so the frontend can navigate straight to it.

Re-importing the same file for the same race replaces (not duplicates) the activities,
via the uniqueness constraint above.

### Weather enrichment

The watch declares a humidity field but never fills it — neither fixture carries a value
(§3.6). Weather is therefore entirely its own enrichment step: call **Open-Meteo's
historical archive API** (free, no key) with race lat/lng + date/hour, and store the
result on `Race` — not per segment, since conditions don't meaningfully change across one
morning. Should a future file actually populate FIT humidity, that value wins; the code
path exists but is untested against real data. Re-runnable via `POST /api/races/{id}/weather/refresh` for
races whose location was corrected after import.

### Strava (secondary, deferred)

Why it isn't primary:
- No native "race" filter/category in the API. `workout_type` exists for runs only
  (`race`/`long_run`/`workout`/default) and relies on manual tagging on Strava — unreliable.
- No native triathlon/multisport activity type in most cases — swim/bike/run legs often
  land as 3+ separate activities unless a combined multisport FIT was uploaded.
- Practical use: pull activities by date range, apply heuristics (name matching,
  `workout_type`, same-day clustering) to reconstruct races. Backfill, not source of truth.
- OAuth required per athlete; rate limits (100/15 min, 1000/day) are fine for personal use.

Build this only once the FIT pipeline is solid. See §8.

---

## 4. Auth & API Surface

### Auth

The app is deployed and reachable from the open internet, so every data endpoint sits
behind a login.

- **Passwords**: argon2 hashes, never stored or logged in plaintext.
- **Sessions**: JWT access token (short-lived) + refresh token, both in `HttpOnly`,
  `Secure`, `SameSite=Lax` cookies. Cookies over `localStorage` so a script injection
  can't exfiltrate the token.
- **Enforcement**: a `current_user` FastAPI dependency on every router except
  `/auth/login` and `/health`. Applied at router level, not per endpoint, so a new
  endpoint is authenticated by default rather than by remembering to add it.
- **Accounts**: seeded via CLI. No public registration, no password-reset email flow
  (reset is a CLI command too).
- **CORS**: locked to the deployed frontend origin, `allow_credentials=True`.
- **Rate limiting**: on `/auth/login` specifically, to blunt credential stuffing.
- **Frontend**: a login page, an auth-gated route wrapper, and a TanStack Query error
  handler that redirects to login on 401.

### Tenancy

The app is built for you first, with the intent to open it to other athletes later. That
argues for **multi-tenant by design, single-tenant in operation**: the isolation is real
from day one, but none of the account-management surface gets built until it's needed.
Data isolation is the one thing that is genuinely expensive to retrofit — every query
written unscoped is a leak to fix later, and the bug it produces is other people's race
data appearing in your timeline.

Concretely:
- `Race.user_id` is the single ownership root. `OfficialResult`, `RecordedActivity` and
  `LapSplit` inherit ownership through their `race_id` — they carry no `user_id` of their
  own, so there is exactly one place isolation can be got wrong.
- Every race lookup goes through one helper (`get_owned_race(db, user, race_id)`) that
  filters by owner and raises 404 — not 403 — for someone else's race, so the API doesn't
  confirm that an id exists. Endpoints never build a `Race` query by hand.
- Aggregates in `services/aggregates.py` take a `user_id` argument. No unscoped
  `SELECT ... FROM races` anywhere, including `/stats/summary`.
- Tests assert isolation directly: a second seeded user must get an empty timeline, a 404
  on the first user's race detail, and zeroed summary stats.

Deferred until it's actually opened up: registration, password reset by email, admin
views, per-user quotas, and any notion of sharing a race between accounts.

### Endpoints

All under `/api`. Response shapes drive the generated TS types.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/login` | Email + password → sets session cookies. |
| `POST` | `/auth/logout` | Clears cookies. |
| `POST` | `/auth/refresh` | Exchanges refresh token for a new access token. |
| `GET` | `/auth/me` | Current user; used by the frontend to decide login state on boot. |
| `GET` | `/races` | Timeline list — paginated, newest first. Lightweight preview payload: name, date, type, badges, official + recorded segment totals for the `SegmentBar`. **No `gps_track`.** |
| `GET` | `/races/{id}` | Full detail: race, official result, recorded activities (without `gps_track`), lap splits. |
| `POST` | `/races` | Create a race manually. |
| `PATCH` | `/races/{id}` | Edit race metadata, incl. per-segment carbs, location, notes. |
| `DELETE` | `/races/{id}` | Delete race and cascade its activities/laps. |
| `PUT` | `/races/{id}/official-result` | Upsert manually entered official times/placings. |
| `GET` | `/races/{id}/track` | GPS polylines for the map, one per segment. Simplified by default (`?precision=`), full stream on request. Split out because it's by far the heaviest payload. |
| `GET` | `/races/{id}/laps` | Lap splits, optional `?sport=` filter, for the pacing chart. |
| `POST` | `/imports/fit` | Multipart FIT upload; optional `race_id`. Returns the race. |
| `POST` | `/races/{id}/weather/refresh` | Re-run weather enrichment. |
| `GET` | `/stats/summary` | Aggregate strip: race count, total distance, total elevation. Optional date range. |

**Payload note:** ~9k GPS points per race × 5 sessions is multi-MB of JSON if served
naively. `gps_track` is excluded from list and detail responses and served only from
`/track`, Douglas-Peucker–simplified (via `geo.py`) to a few hundred points for map
rendering. The full stream stays in the database.

---

## 5. Frontend

### Stack

- **React + Vite + TypeScript** — Vite over Next.js: no SSR, no file-based routing
  complexity needed for a single-user app talking to a separate FastAPI backend. Keeps
  overhead low while retaining React's mature charting/mapping ecosystem.
- **Types**: `openapi-typescript` generated from the FastAPI OpenAPI schema, so frontend
  data shapes stay in sync with the Pydantic models automatically — valuable given how
  many nullable fields and enums the data model has. Regeneration is a documented script,
  not a build step.
- **Routing**: React Router — only a handful of views.
- **Data fetching/caching**: TanStack Query — loading/error states and caching with
  minimal boilerplate.
- **Charts**: Recharts — simplest API sufficient for pacing/HR/power line charts.
- **Maps**: react-leaflet + OpenStreetMap tiles — free, no API key, renders `gps_track`
  polylines directly.
- **Styling**: Tailwind CSS — utility classes, no bespoke CSS.
- **Forms**: React Hook Form + Zod for the manual-entry views (official results, race
  metadata, carbs). Kept deliberately plain.

### Information Architecture

- **Login** (`/login`) — the only unauthenticated route; everything else redirects here
  on 401.
- **Timeline** (`/`) — chronological feed of races, most recent first, each entry a
  preview card.
- **Race detail** (`/races/:id`) — official vs. recorded comparison, course map, lap
  split chart, weather + carbs metadata.
- **Race edit** (`/races/:id/edit`) — manual entry: race metadata, official result
  times/placings, carbs.
- **Import** (`/import`) — FIT upload, with an option to attach to an existing race.
- **Aggregate strip** — thin, single-line summary (total races, distance, elevation)
  above the timeline. Deliberately not a full dashboard: timeline browsing, not aggregate
  stats, is the primary interaction.

```
┌─────────────────────────────────────────┐
│  stat strip: races · distance · elev    │  ← thin, one line, quiet
├─────────────────────────────────────────┤
│  │                                      │
│  ●──[ Race card: name, date, badge ]    │
│  │   total time · S/B/R proportion bar  │
│  │                                      │
│  ●──[ Race card ]                       │
│  │                                      │
│  ●──[ Race card ]                       │
│  │                                      │
└─────────────────────────────────────────┘
```

### Visual Design

**Rationale.** Avoided the common AI-generated-design defaults (cream + terracotta;
near-black + acid accent; broadsheet hairline layout). The palette draws from the sport
itself — water / road / trail — used as small per-segment accents rather than dominating
the page. The signature element is the timeline spine with a proportional swim/bike/run
segment bar per race: functional (shows pacing balance at a glance), not decorative.

**Color**

| Token | Hex | Use |
|---|---|---|
| Background | `#FAFAF8` | Page background (off-white, not stark white) |
| Ink | `#1C2321` | Primary text (warm near-black) |
| Border/hairline | `#E4E1D8` | Dividers, card borders |
| Swim accent | `#1B4B4A` | Deep teal |
| Bike accent | `#8A6D3B` | Warm slate/tan |
| Run accent | `#C4622D` | Clay-orange |

Transitions (T1/T2) render as neutral hairline slivers in the segment bar — present but
visually subordinate to the three sports.

**Type**
- Inter (or similar grotesk) throughout — this is a data tool, not a marketing page, so a
  single well-set utility face is enough.
- All times, paces, HR and power values use `font-variant-numeric: tabular-nums` so
  columns of numbers align (splits tables, lap charts).

**Theme**: light, off-white only. No dark mode in v1.

**Signature element**: vertical timeline spine (left-aligned line + dot per race) with a
proportional swim/bike/run time bar on each card — encoding real pacing-balance
information, not a generic numbered-sequence marker.

### Components

| Component | Purpose |
|---|---|
| `TimelineSpine` | Vertical line + per-race marker dots |
| `RaceCard` | Timeline preview card (name, date, badge, total time, segment bar) |
| `SegmentBar` | Proportional swim/bike/run bar, reused in card + detail view |
| `StatStrip` | Top aggregate line (races / distance / elevation) |
| `RaceDetail` | Full detail page for one race |
| `SplitsComparison` | Official vs. recorded table, per segment incl. T1/T2, with deltas |
| `SplitsChart` | Recharts line chart, filterable by sport. Series availability is sport-dependent (§3): run has pace/HR/power, bike has pace/HR only and just 6–8 coarse 5 km points, swim is a single lap and renders as a summary tile rather than a line. |
| `CourseMap` | react-leaflet wrapper rendering the simplified track polylines |
| `RaceForm` | Manual entry: race metadata + carbs |
| `OfficialResultForm` | Manual entry: official times and placings |
| `FitUpload` | Drag/drop FIT upload with parse feedback |

---

## 6. Features Supported by This Schema

- Official vs. recorded time comparison, per segment (including T1/T2).
- Total distance / elevation gain across all races (aggregate queries, no extra tables).
- Map rendering per race from `gps_track`.
- Pacing trends per sport / distance / time (via `LapSplit`, sliced by `sport` and joined
  to `Race.race_type` / `Race.date`).
- Weather correlation with performance (temp/humidity on `Race`, joined to pacing data).
- Manual carbs tracking per race, split bike vs. run.

---

## 7. Milestones

| # | Milestone | Contents |
|---|---|---|
| M0 | Scaffold | Repo layout, `docker-compose` with Postgres, FastAPI hello-world, Vite app, lint/format config |
| M1 | Data model | SQLAlchemy models, Alembic initial migration, Pydantic schemas |
| M1.5 | Auth | `User` model, argon2 hashing, JWT cookie sessions, `current_user` dependency, seed-user CLI, login page + auth-gated routing |
| M2 | FIT import | `fit_import.py` + `POST /imports/fit`, tested against both fixtures — asserting the §3 traps: session totals over lap sums, half-open bucketing, run-not-bike power, local race date |
| M3 | Read API | `/races`, `/races/{id}`, `/laps`, `/track`, `/stats/summary`; OpenAPI type generation wired up |
| M4 | Timeline UI | `StatStrip`, `TimelineSpine`, `RaceCard`, `SegmentBar`, design tokens |
| M5 | Detail UI | `SplitsComparison`, `SplitsChart`, `CourseMap` |
| M6 | Manual entry | Race form, official result form, carbs; FIT upload page |
| M7 | Weather | Open-Meteo enrichment at import + refresh endpoint |
| M7.5 | Deploy | Compose stack, Caddy + TLS, migrations on boot, backups (see §8) |
| M8 | Strava | Deferred backfill path (see §3) |

M2 is the highest-risk step and should be built against a real file early; everything
downstream depends on its normalized output. M1.5 comes before it so no endpoint is ever
written unauthenticated and retrofitted later.

---

## 8. Deployment

Single VPS, docker compose, four services:

| Service | Notes |
|---|---|
| `caddy` | Reverse proxy, automatic TLS via Let's Encrypt. Serves the built frontend as static files and proxies `/api` to the backend. |
| `backend` | uvicorn behind gunicorn. Entrypoint runs `alembic upgrade head` before serving, so a deploy can't drift from its schema. |
| `db` | Postgres with a named volume. Never publishes a host port — reachable only on the compose network. |
| `frontend` | Build-only stage; emits static assets into a volume Caddy serves. No Node process in production. |

- **Config**: twelve-factor, all via env vars (`DATABASE_URL`, `JWT_SECRET`,
  `CORS_ORIGINS`, `OPEN_METEO_BASE_URL`). A committed `.env.example` documents them; the
  real `.env` is gitignored.
- **Upload limits**: FIT files are small (single-digit MB), but Caddy's default request
  body cap still needs raising explicitly, and the backend rejects oversized or
  non-FIT uploads before parsing.
- **Original FIT retention**: uploaded files are kept verbatim on a mounted volume,
  keyed by import id. `raw_data` holds the *parsed* payload, which is only as good as the
  parser at import time — keeping the original means the whole corpus can be re-imported
  when the parser improves, rather than being re-uploaded by hand.
- **Backups**: nightly `pg_dump` to the same volume with rotation, plus the FIT originals.
  Both belong in whatever offsite copy the VPS already has; a personal race history has no
  other source once the watch data ages out.
- **Deploy**: `git pull && docker compose up -d --build`. No CI/CD pipeline in v1 — a
  GitHub Actions workflow can be added once the thing is worth deploying often.

---

## 9. Open Decisions / Deferred

Carried forward from both source plans, plus seams that surfaced while merging them.

- **`gps_track` storage** — JSONB now; PostGIS `geometry` only if spatial queries become
  a real need.
- **Strava import** — build as secondary backfill once FIT import is solid, using
  name-matching + same-day clustering heuristics.
- **`StatStrip` filtering** — all-time summary, or date-range filterable.
- **Mobile layout** — single column should degrade naturally, but not yet validated.
- **Dark mode** — not planned for v1.
- **Cross-race trend views** — the schema supports pacing trends and weather correlation,
  but no UI is specified for them. Post-v1.
- **Opening to other users** — registration, password reset, admin surface and quotas
  are all deferred (§4 Tenancy); the data model is ready for them, the product isn't.
- **CI/CD** — manual `docker compose up -d --build` for v1.
