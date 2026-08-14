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
| FIT upload | Multisport files auto-create a draft race; single-sport files stage for manual grouping |
| Carbs | Per-segment (bike / run) |
| Grouping | In v1 (M2.5); synthesized transitions flagged `is_derived` |
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
        fit_import.py    # fitdecode parsing → normalized dicts (vendor-agnostic)
        grouping.py      # group single-sport uploads into a race (§3 Grouping)
        weather.py       # Open-Meteo historical enrichment
        aggregates.py    # summary stats queries
        geo.py           # polyline simplification / downsampling
      migrations/        # Alembic
    tests/
      fixtures/          # three real multisport FIT exports: 2x Apple, 1x Garmin (§3)
    pyproject.toml
  frontend/
    src/
      api/               # typed fetch functions + TanStack Query hooks
      components/        # RaceCard, SegmentBar, StatStrip, TimelineSpine, ...
      pages/             # Timeline.tsx, RaceDetail.tsx, RaceEdit.tsx, Import.tsx, Login.tsx
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

### `Upload`
Staging row for every file or Strava activity, created *before* it belongs to a race.
This is what makes manual grouping possible.
```
id (PK)
user_id (FK, indexed)  owner — uploads exist before any race, so they carry their own
                       owner rather than inheriting one through race_id
source                 enum: fit_upload, strava
filename               nullable (null for Strava-sourced)
stored_path            nullable — original file retained on disk (§8)
content_hash           sha256 of the file — rejects re-uploading the same file twice
external_id            nullable, Strava activity id
status                 enum: parsed, grouped, failed
kind                   enum: multisport, single_sport — 5-session file vs. one leg
detected_sport         enum: swim, bike, run, other — null for multisport
detected_start_time / detected_end_time
detected_distance_m
summary                jsonb — parse preview for the grouping UI, so the tray renders
                       without re-reading files
error                  nullable, parse failure message
race_id                FK nullable — set once grouped
uploaded_at
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
is_derived        bool — true for transitions synthesized from gaps between grouped
                  single-sport uploads; false for anything actually recorded (§3 Grouping)
source            enum: fit_upload, strava
external_id       nullable (Strava activity id)
start_time
elapsed_time      wall-clock, session total_elapsed_time
moving_time       nullable, session total_timer_time — differs from elapsed on Garmin
distance_m        nullable (transitions are short but real: 20 m-550 m observed)
elevation_gain_m  nullable (session total_ascent; absent on swim/transitions)
avg_hr / max_hr   nullable
avg_power         nullable — device-dependent, NOT sport-dependent (see §3 Traps 2)
normalized_power  nullable — Garmin bike only
avg_pace / avg_speed           nullable — derive from distance/time, not the FIT field
avg_cadence       nullable — reliable on Garmin, sparse-to-absent on Apple
avg_temp_c        nullable — Garmin device temperature; NOT air temp, see §3 Traps 8
rpe_estimated     nullable, Apple developer field only
avg_mets          nullable, Apple developer field only
bounds_*          nullable — session bounding box (n/s/e/w lat/lng) for map fitting
gps_track         jsonb — array of {lat, lng, elevation, timestamp}
raw_data          jsonb — full parsed FIT/Strava payload, safety net for unmapped fields
imported_at
```

`gps_track` stays JSONB for now. Migrate to a PostGIS `geometry` column only if spatial
queries ("races whose bike course passes near X") become a real need.

### `LapSplit` (many:1 with RecordedActivity — auto-laps: 1 km run, 5 km bike; swim varies by vendor)
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
avg_power         nullable — device-dependent, NOT sport-dependent (see §3 Traps 2)
normalized_power  nullable — Garmin bike only
avg_pace / avg_speed          nullable — derive from distance/time
elevation_gain_m  nullable
cadence_avg       nullable — reliable on Garmin, sparse-to-absent on Apple
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

Verified against three real multisport exports in `backend/tests/fixtures/` — two Apple
Watch, one Garmin. The vendor split matters more than any single file's quirks.

| | Apple `2026-07-11` | Apple `2026-08-09` | Garmin `2024-08-03` |
|---|---|---|---|
| Records | 9 829 | 9 413 | 6 389 |
| Laps | 19 | 21 | 25 |
| Swim | 1 572 m / 28:59 | 1 548 m / 31:12 | 1 645 m / 20:42 |
| T1 | 552 m / 3:36 | 105 m / 2:27 | 273 m / 3:33 |
| Bike | 33 746 m / 1:13:08, +558 m | 40 570 m / 1:09:39, +22 m | 40 868 m / 1:12:35, +327 m |
| T2 | 218 m / 2:00 | 20 m / 0:08 | 27 m / 1:10 |
| Run | 9 172 m / 56:03, +166 m | 9 933 m / 53:24, +48 m | 9 239 m / 46:43, +59 m |

### What holds across both vendors

- **One multisport FIT = one race**, containing 5 `session` messages in order: swim
  (open_water), transition (T1), cycling, transition (T2), running. Import splits the
  `record` stream by session boundaries — it does *not* expect 5 separate uploads.
- **`record`** messages carry the position stream (99.8–100 % GPS coverage, open-water
  swim included) — source for `gps_track`.
- **`session`** carries `total_ascent` and an `nec`/`swc` bounding box, enough to fit the
  map viewport without touching the point stream.
- **Runs auto-lap at 1 km, bikes at 5 km.** Consistent across all three files.
- **No nutrition or carbs data anywhere**, on either vendor. Carbs stays manual entry.
- **`avg_speed` is unreliable on laps** — absent on every Garmin lap and on Apple swim
  laps. Derive pace from distance ÷ time rather than reading the field.
- **Timestamps are UTC.** Apple filenames encode *local* time (`...131906` against a
  `time_created` of 11:19:06 UTC — CEST, +2). `Race.date` must be the local race date
  derived from the start location's timezone, or an early start lands on the wrong day.

### Where the vendors diverge

This is the part that breaks naive importers. **Nothing device-specific may be assumed
from the sport alone.**

| | Apple Watch | Garmin |
|---|---|---|
| `manufacturer` | `development` (converter rewrites it) | `garmin` |
| Bike power | absent — no power meter | **`avg_power` 225 W, `normalized_power` 250 W**, plus full cycling dynamics (L/R power phase, torque effectiveness, pedal smoothness, PCO) |
| Run power | **295–307 W** (watch-estimated) | absent |
| Swim laps | 1 lap for the whole swim | **4 × 500 m laps** |
| Trailing partial bike lap | **dropped** | present; all laps reconcile exactly |
| Sampling | uniform 1 Hz | **smart recording**, variable — the run holds 699 points for 2 803 s (~0.25 Hz) while the bike holds 4 349 for 4 355 s (1 Hz) |
| Temperature | absent | **present on 100 % of records**, 24–35 °C |
| Cadence | 35 % of records in one file, absent in the other | 100 % of records |
| Developer fields | `SESSION UUID`, `INDOOR`, `ACTIVITY TYPE`, `RPE ESTIMATED`, `AVG METs`, `WEATHER HUMIDITY` | none at all |
| `elapsed` vs `timer` | identical — no pauses | **differ** (bike 4 354.9 s vs 4 348.9 s) |

### Traps

1. **Never derive segment totals by summing laps.** Apple drops the bike's trailing
   partial lap: 6 × 5 000 m against a 33 746 m session, understating the bike by 3.7 km.
   Garmin includes it and reconciles exactly — so validating against a Garmin file, or
   against Apple's *run*, would suggest lap-summing is safe. It isn't. Session totals are
   authoritative; laps carry pacing shape only.
2. **Power presence is a property of the device, not the sport.** Apple gives running
   power and no bike power; Garmin gives bike power (with `normalized_power`) and no
   running power. Schema keeps `avg_power` on every segment and the UI shows the series
   only where data exists.
3. **Consecutive session windows overlap sub-second** (one ends 11:51:42.054, the next
   starts 11:51:42) — inclusive bucketing double-counts. Use half-open `[start, end)`.
4. **Records can fall outside every session window.** The Garmin file has one orphan at
   12:39:07, in the 1.1 s gap between bike end and T2 start. Assert-every-record-lands
   would reject a valid file; orphans are dropped and counted, not fatal.
5. **Store elapsed *and* moving time.** Garmin's differ (pauses exist), Apple's don't. A
   single `total_time` column silently picks one and loses the other.
6. **Transitions are real segments but poorly recorded.** T1 ranges 105–552 m; the Apple
   T2 of 20 m / 7.7 s is essentially a miss. Recorded transitions are not ground truth —
   which is the argument for official-vs-recorded being the centerpiece view.
7. **Lap counts per sport vary by vendor**, so per-sport chart shape can't be hardcoded:
   an Apple swim is one lap (a summary tile, not a line), a Garmin swim is four.
8. **Garmin temperature is device temperature, not air temperature** — a sun-exposed wrist
   sensor reading 35 °C max, mean 29.3 °C. Store it, but let the weather API remain the
   source for `Race.weather_temp_c`.
9. **Field names are the `enhanced_` variants** (`enhanced_speed`, `enhanced_altitude`).
   Altitude is missing from ~20 % of records on both vendors, so elevation profiles need
   gap handling.
10. **Developer fields are Apple-only and sparse** — `AVG METs` is declared but never
    populated, `RPE ESTIMATED` appears in one file of two, and `SESSION WEATHER HUMIDITY`
    is never populated at all. Weather is API-only in practice.

Present and unused: `time_in_zone` (HR-zone distribution per session, both vendors),
Garmin `gps_metadata` (8 963 messages, but no position fields beyond what `record`
already has) and Garmin `split` messages (run/walk detection — `rwd_run`/`rwd_walk`, not
multisport splits). Garmin also emits ~9 500 messages across undecoded `unknown_*` types,
which land in `raw_data` untouched.

**Library: `fitdecode`** — parsed all three files with no malformed-frame errors,
including Garmin's undecoded proprietary messages.

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

### Single-sport uploads and manual grouping

A multisport FIT file is the happy path: 5 sessions, one race, no user decisions. But it
isn't the only shape data arrives in.

**Strava has no multisport activity type** — a triathlon comes back as three or more
separate activities, and the API offers no way to reassemble them. The same is true of any
athlete whose watch wrote three separate files rather than one combined export. So the
importer cannot assume one-file-one-race.

The design: **every upload lands in a staging tray, and the user groups what belongs
together.** Multisport files skip the tray by grouping themselves.

```
  upload N files ─┐
                  ├─► parse ─► Upload rows ─┬─ multisport ─► auto-group ─► Race
  Strava fetch  ──┘                         │
                                            └─ single-sport ─► ungrouped tray
                                                                    │
                                              user selects 3 ───────┤
                                                                    ▼
                                                        assign segments ─► Race
```

1. `POST /api/uploads` accepts one or many files. Each is parsed, hashed, stored, and gets
   an `Upload` row with its detected sport, start/end time and distance.
2. Files detected as **multisport** (5 sessions) group themselves immediately — the
   existing flow, unchanged.
3. Everything else lands in the **ungrouped tray**, `GET /api/uploads?status=parsed`.
4. The tray **proposes** groupings: uploads on the same local day, within a few hours of
   each other, in swim → bike → run order. Proposals are suggestions the user confirms —
   never auto-committed, since a Sunday brick session looks identical to a race.
5. `POST /api/races/group` with the chosen `upload_ids` creates the `Race` and one
   `RecordedActivity` per upload, plus their laps.
6. Segments are assigned from each upload's detected sport, ordered by start time. If the
   detection is ambiguous — two runs in one group, or an `other` sport — the UI asks
   rather than guessing.

Uploads are **deduplicated by `content_hash`**, so re-uploading the same file is a no-op
rather than a second copy in the tray. Strava-sourced uploads dedupe on `external_id`.

An upload can also be attached to an existing race (`POST /api/races/{id}/attach`), which
covers backfilling a leg you forgot, or replacing one recorded from the wrong device.

**Transitions in a grouped race are synthesized.** Three single-sport activities contain
no T1/T2 — those legs were never recorded separately. Grouping therefore creates them from
the gaps between consecutive legs (`t1.total_time = bike.start_time − swim.end_time`),
marked `is_derived = true`. This keeps the segment bar complete and makes recorded total
reconcile against official total, which a missing transition would otherwise break in a
way that reads as a bug.

The flag matters: a derived transition is an inference from two timestamps, not a
measurement. It carries no distance, no GPS track, no HR — only a duration — and the UI
renders it visually distinct (hatched in the `SegmentBar`, annotated in
`SplitsComparison`) so it is never mistaken for recorded data. Transitions from a real
multisport file have `is_derived = false` and keep their distance and track.

A negative or implausibly large gap (overlapping activities, or legs hours apart) means
the grouping is wrong; the API rejects it rather than writing a nonsense transition.

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
- **No multisport activity type.** A triathlon arrives as 3+ separate activities with no
  link between them — the reason the grouping tray above exists at all.
- No native "race" filter. `workout_type` exists for runs only (`race`/`long_run`/
  `workout`/default) and depends on manual tagging — unreliable.
- The data is thinner than FIT: no per-second developer fields, coarser streams.
- OAuth per athlete; rate limits (100/15 min, 1000/day) are fine for personal use.

When built, it reuses the grouping path rather than adding a parallel one: fetch
activities by date range → create `Upload` rows with `source=strava` and no file → they
appear in the same tray, with the same proposals, and group the same way. The only
Strava-specific code is the OAuth dance and the activity→`Upload` mapping.

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
- `Race.user_id` is the ownership root for race data. `OfficialResult`,
  `RecordedActivity` and `LapSplit` inherit ownership through their `race_id` — they carry
  no `user_id` of their own, so there is one place isolation can be got wrong.
- `Upload` is the one exception: it exists *before* any race, so it carries its own
  `user_id`. That makes two roots, not one — so grouping must assert that every
  `upload_id` in a request is owned by the caller before it creates anything, and the tray
  query is scoped by `user_id` directly.
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
| `POST` | `/uploads` | Multipart upload of one or many files. Parses each, dedupes by content hash, auto-groups multisport files into a race, leaves single-sport files in the tray. |
| `GET` | `/uploads` | The ungrouped tray, with suggested groupings. `?status=` filter. |
| `DELETE` | `/uploads/{id}` | Discard an upload (and its stored file). |
| `POST` | `/races/group` | Create a race from selected `upload_ids` + segment assignment. |
| `POST` | `/races/{id}/attach` | Attach further uploads to an existing race. |
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
- **Import** (`/import`) — multi-file upload, plus the ungrouped tray where single-sport
  activities are grouped into races (§3).
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
| `SegmentBar` | Proportional swim/bike/run bar, reused in card + detail view; derived transitions render hatched |
| `StatStrip` | Top aggregate line (races / distance / elevation) |
| `RaceDetail` | Full detail page for one race |
| `SplitsComparison` | Official vs. recorded table, per segment incl. T1/T2, with deltas |
| `SplitsChart` | Recharts line chart, filterable by sport. Series availability is sport-dependent (§3): run has pace/HR/power, bike has pace/HR only and just 6–8 coarse 5 km points, swim is a single lap and renders as a summary tile rather than a line. |
| `CourseMap` | react-leaflet wrapper rendering the simplified track polylines |
| `RaceForm` | Manual entry: race metadata + carbs |
| `OfficialResultForm` | Manual entry: official times and placings |
| `FitUpload` | Drag/drop upload, multi-file, with per-file parse feedback |
| `GroupingTray` | Ungrouped uploads with suggested groupings; select + confirm to create a race |
| `SegmentAssign` | Per-upload segment picker, shown when detection is ambiguous |

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
| M2 | FIT import | `fit_import.py` + `POST /uploads`, tested against all three fixtures — asserting the §3 traps: session totals over lap sums, half-open bucketing, orphan records tolerated, device-dependent power, elapsed vs moving time, local race date |
| M2.5 | Grouping | `Upload` staging, ungrouped tray, same-day proposals, `POST /races/group`, derived transitions — the path Strava will reuse |
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
- **Garmin `time_in_zone` and cycling dynamics** — L/R balance, torque effectiveness,
  pedal smoothness and HR-zone distributions are all parsed into `raw_data` but not
  modelled or surfaced. Cheap to promote later if they turn out to be interesting.
- **CI/CD** — manual `docker compose up -d --build` for v1.
