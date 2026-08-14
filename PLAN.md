# Triathlon Racing History — Implementation Plan

Personal, single-user analytics web app for triathlon racing history. Timeline-first
browsing of past races, drilling into per-race detail: official vs. recorded splits,
course map, pacing charts, weather and fueling.

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
| Hosting | Deployed (not localhost-only) |
| Auth | Real login with user accounts |
| FIT upload | Auto-creates a draft race |
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
      fixtures/          # sample FIT file(s)
    pyproject.toml
  frontend/
    src/
      api/               # typed fetch functions + TanStack Query hooks
      components/        # RaceCard, SegmentBar, StatStrip, TimelineSpine, ...
      pages/             # Timeline.tsx, RaceDetail.tsx, RaceEdit.tsx, Import.tsx
      types/             # generated from FastAPI OpenAPI schema
      lib/               # formatting helpers (pace, time, duration)
    package.json
  docker-compose.yml     # postgres (+ optional backend/frontend services)
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

Registration is **not** open — accounts are seeded by a CLI command
(`python -m app.cli create-user`). A public signup form on a personal tool is a liability
with no upside.

### `Race`
Event shell. One row per race event.
```
id (PK)
user_id (FK)          owner; every query is scoped by it — see §8 [Q]
name
date
location_name
location_lat / location_lng
race_type             enum: sprint, olympic, half_iron, full_iron, custom
weather_temp_c        nullable, from weather API enrichment
weather_humidity_pct  nullable, prefer FIT value if present, else API
weather_conditions    nullable, e.g. "clear", "overcast"
carbs_g               nullable, manual entry (fueling isn't in FIT data)
notes                 nullable, free text
created_at / updated_at
```

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
distance_m        nullable (transitions often 0/short)
elevation_gain_m  nullable
avg_hr / max_hr   nullable
avg_power / normalized_power   nullable, bike
avg_pace / avg_speed           nullable
rpe_estimated     nullable, from FIT dev field
avg_mets          nullable, from FIT dev field
bounds_*          nullable — session bounding box (n/s/e/w lat/lng) for map fitting
gps_track         jsonb — array of {lat, lng, elevation, timestamp}
raw_data          jsonb — full parsed FIT/Strava payload, safety net for unmapped fields
imported_at
```

`gps_track` stays JSONB for now. Migrate to a PostGIS `geometry` column only if spatial
queries ("races whose bike course passes near X") become a real need.

### `LapSplit` (many:1 with RecordedActivity — ~1 km auto-laps)
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
avg_power / normalized_power  nullable, bike
avg_pace / avg_speed          nullable
elevation_gain_m  nullable
cadence_avg       nullable
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

Findings from inspecting a real Apple Watch multisport export with `fitdecode`:

- **One FIT file = one race**, containing 5 `session` messages in order: swim
  (open_water), transition (T1), cycling, transition (T2), running. Import logic must
  split the `record` stream (9k+ per-second points) by session `start_time` +
  `total_elapsed_time` boundaries — *not* expect 5 separate file uploads.
- **`lap`** messages give real ~1 km auto-lap splits (pace, HR, cadence, power) per segment.
- **`record`** messages give the full per-second GPS + sensor stream — source for
  `gps_track` / map rendering.
- **`session`** also carries `total_ascent` and a bounding box — useful for map fitting
  without processing every point.
- **No nutrition/carbs data anywhere** in the file (all frame types checked, including
  developer fields). Apple Watch native workout tracking doesn't log fueling. **Carbs is
  a manual entry field.**
- **Developer fields present**: session UUID, indoor flag, activity type,
  `WORKOUT RPE ESTIMATED`, `AVG METs`, `SESSION WEATHER HUMIDITY`. No temperature field
  anywhere in the file.

**Library: `fitdecode`** (over `fitparse`) — cleaner API, better handling of malformed
files, which matters given the Apple Watch → third-party converter → FIT pipeline.

**Import flow:**
1. Upload FIT via `POST /api/imports/fit` (multipart), optionally with `race_id` to
   attach to an existing race.
2. Parse: read `session`, `lap`, `record` messages in one pass.
3. Segment: map the 5 sessions to `swim, t1, bike, t2, run` by order + sport field;
   bucket `record` points into sessions by time window.
4. If no `race_id` given, create a draft `Race` from the first session's start time and
   bounding-box centroid (name/type left for the user to fill in).
5. Persist `RecordedActivity` × 5 + their `LapSplit` rows; store the full parsed payload
   in `raw_data`.
6. Trigger weather enrichment (below).
7. Return the created/updated race so the frontend can navigate straight to it.

Re-importing the same file for the same race replaces (not duplicates) the activities,
via the uniqueness constraint above.

### Weather enrichment

Only humidity comes from the watch, and inconsistently. Weather is therefore its own
enrichment step at import time: call **Open-Meteo's historical archive API** (free, no
key) with race lat/lng + date/hour, and store the result on `Race` — not per segment,
since conditions don't meaningfully change across one morning. FIT humidity wins over
the API value when present. Re-runnable via `POST /api/races/{id}/weather/refresh` for
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
| `PATCH` | `/races/{id}` | Edit race metadata, incl. `carbs_g`, location, notes. |
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
| `SplitsChart` | Recharts line chart, filterable by sport (pace/HR/power) |
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
- Manual carbs tracking per race.

---

## 7. Milestones

| # | Milestone | Contents |
|---|---|---|
| M0 | Scaffold | Repo layout, `docker-compose` with Postgres, FastAPI hello-world, Vite app, lint/format config |
| M1 | Data model | SQLAlchemy models, Alembic initial migration, Pydantic schemas |
| M1.5 | Auth | `User` model, argon2 hashing, JWT cookie sessions, `current_user` dependency, seed-user CLI, login page + auth-gated routing |
| M2 | FIT import | `fit_import.py` + `POST /imports/fit`, tested against a real fixture file |
| M3 | Read API | `/races`, `/races/{id}`, `/laps`, `/track`, `/stats/summary`; OpenAPI type generation wired up |
| M4 | Timeline UI | `StatStrip`, `TimelineSpine`, `RaceCard`, `SegmentBar`, design tokens |
| M5 | Detail UI | `SplitsComparison`, `SplitsChart`, `CourseMap` |
| M6 | Manual entry | Race form, official result form, carbs; FIT upload page |
| M7 | Weather | Open-Meteo enrichment at import + refresh endpoint |
| M8 | Strava | Deferred backfill path (see §8) |

M2 is the highest-risk step and should be built against a real file early; everything
downstream depends on its normalized output. M1.5 comes before it so no endpoint is ever
written unauthenticated and retrofitted later.

---

## 8. Open Decisions / Deferred

Carried forward from both source plans, plus seams that surfaced while merging them.
Items marked **[Q]** are open and asked directly alongside this plan.

- **[Q] Multi-user semantics** — real login is decided, but not what it implies. Is this
  one account with a login gate (auth is purely a lock on the door), or genuinely
  multi-tenant with other athletes having their own races? The plan assumes `Race.user_id`
  scoping either way, which costs little now and is painful to retrofit — but if others
  will use it, `/stats/summary` and every query need per-user scoping tests, and account
  seeding becomes a recurring chore rather than a one-off.
- **[Q] Deploy target** — a VPS with docker compose + Caddy/nginx for TLS, a PaaS
  (Fly.io / Railway / Render), or something you already run? Determines how migrations,
  secrets, backups, and the FIT upload size limit are handled.
- **[Q] Carbs granularity** — per-race total vs. per-segment (bike/run only). Leaning
  per-race for simplicity unless segment-level fueling analysis becomes a priority.
- **`gps_track` storage** — JSONB now; PostGIS `geometry` only if spatial queries become
  a real need.
- **Strava import** — build as secondary backfill once FIT import is solid, using
  name-matching + same-day clustering heuristics.
- **`StatStrip` filtering** — all-time summary, or date-range filterable.
- **Mobile layout** — single column should degrade naturally, but not yet validated.
- **Dark mode** — not planned for v1.
- **Cross-race trend views** — the schema supports pacing trends and weather correlation,
  but no UI is specified for them. Post-v1.
