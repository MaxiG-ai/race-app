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
| Fueling | `RaceFueling` table, one row per segment: carbs, products used, notes |
| Grouping | In v1 (M2.5); synthesized transitions flagged `is_derived` |
| Gear | Tracked per segment (M6.5); auto-detected from sensor serials, shoes manual |
| Duplicate recordings | Kept, one marked `is_primary` per segment |
| v1 scope | M0–M7; then M8–M10 for this feature set; Strava (M11) deferred |
| Fixtures | Real unanonymized race data — **repo must stay private** (see fixtures README) |
| Classification | Auto from measured distances against a seeded `RaceCategory` table; manual override kept alongside the inferred value (§9) |
| Partial data | Everything nullable but `Race.date` and `OfficialResult.total_time`, which is summed from the splits rather than demanded (§10) |
| Comparison | Same-category only; checkpoint gap chart vs a reference race (§11) |

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
      models/            # SQLAlchemy ORM: user, race, race_fueling, official_result,
                         # upload, recorded_activity, lap_split, gear
      schemas/           # Pydantic request/response models
      routers/           # auth, races, fueling, uploads, gear, stats, weather
      auth/              # password hashing, JWT issue/verify, current_user dependency
      services/
        fit_import.py    # fitdecode parsing → normalized dicts (vendor-agnostic)
        grouping.py      # group single-sport uploads into a race (§3 Grouping)
        gear.py          # device_info serial → gear resolution (§3 Gear detection)
        weather.py       # Open-Meteo historical enrichment
        classify.py      # distance ranges → race category (§9)
        comparison.py    # align two races onto a common checkpoint axis (§11)
        aggregates.py    # summary stats queries
        geo.py           # polyline simplification / downsampling
      migrations/        # Alembic
    tests/
      fixtures/          # 5 real files: 3 multisport (2 Apple, 1 Garmin), 1 Edge 540
                         # single-sport with sensors, 1 Strava re-export (§3)
    pyproject.toml
  frontend/
    src/
      api/               # typed fetch functions + TanStack Query hooks
      components/        # RaceCard, SegmentBar, StatStrip, TimelineSpine, ...
      pages/             # Timeline, RaceDetail, RaceEdit, Import, Login, Gear, Compare
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
name                  defaults to "Untitled race" — never blocks a save (§10)
date                  the one required field besides user_id; timeline orders by it
location_name         nullable
location_lat / location_lng   nullable
race_category_id (FK) nullable — assigned category (§9)
auto_category_id (FK) nullable — what the classifier chose, kept even when overridden
classification_source enum: auto, manual, unclassified
classified_at         nullable
classification_detail jsonb, nullable — why classification failed, for the UI to explain
weather_temp_c        nullable, from weather API enrichment
weather_humidity_pct  nullable, from weather API (FIT humidity field is never populated)
weather_conditions    nullable, e.g. "clear", "overcast"
notes                 nullable, free text
created_at / updated_at
```

### `RaceFueling` (many:1 with Race — one row per fuelled segment)
What was consumed during a race. Hangs off `Race` rather than `RecordedActivity` because
fueling is entered by hand and must be recordable for a race with no import yet.
```
id (PK)
race_id (FK, indexed)
segment       enum: swim, t1, bike, t2, run — the UI offers bike and run by default,
              but the column takes any segment rather than blocking "ate a gel in T2"
carbs_g       nullable
products      jsonb — [{name, quantity}], e.g. [{"name": "Maurten 160", "quantity": 2}]
notes         text, nullable — free text: how it sat, what ran out, what to change
created_at / updated_at
unique (race_id, segment)
```

Total carbs is derived by summing rows, not stored. `products` is JSONB rather than a
normalised catalogue: v1 needs to *record* what was used, and free-form entry does that
without a product table and its picker. Promoting products to their own table is the
upgrade path if per-product analysis ("did I run better on X?") becomes a real question —
see §12.

### `RaceCategory`
Distance bands used to classify races (§9). Seeded by migration and editable as data, so
correcting a boundary doesn't need a deploy.
```
id (PK)
key            slug: sprint, kurz, mittel, lang, ...
label          display name
kind           enum: distance, age_class, relay — only `distance` auto-classifies
sort_order     for pickers and grouping
swim_min_m / swim_max_m        null bound = open-ended
bike_min_m / bike_max_m
run_min_m  / run_max_m
tolerance_pct  default 10 — applied outward to both bounds at match time
is_active
```

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

### `Gear` and `GearDevice`
Equipment tracking — which bike and which shoes were used for a race, and how much
distance each has accumulated.
```
Gear
  id (PK)
  user_id (FK, indexed)  own ownership root, like Upload — gear outlives any one race
  kind                 enum: bike, shoes, wetsuit, other
  name                 e.g. "Canyon Speedmax (race)", "Vaporfly 3"
  brand / model        nullable
  purchased_at         nullable
  retired_at           nullable — retired gear is hidden from pickers, kept in history
  distance_offset_m    default 0 — mileage accrued before tracking started
  notes                nullable

GearDevice              serial → gear mapping, populated as devices are recognised
  id (PK)
  gear_id (FK)
  manufacturer         e.g. favero_electronics, shimano
  serial_number        the stable identifier from FIT device_info
  label                nullable, e.g. "Assioma pedals"

activity_gear           association: which gear was used for which recorded segment
  recorded_activity_id (FK)
  gear_id (FK)
  assigned_by          enum: auto_device, manual, inherited
```

Distance per gear is a query over `activity_gear` joined to primary `RecordedActivity`
rows, plus `distance_offset_m` — no denormalised counter to drift out of sync.

### `OfficialResult` (1:1 with Race, manually entered)
```
id (PK)
race_id (FK, unique)
total_time        NOT NULL — but never typed by hand when it can be worked out:
                  omit it and the server sums the five splits (§10)
swim_time         nullable
t1_time           nullable — blank if not separately timed
bike_time         nullable
t2_time           nullable
run_time          nullable
overall_place     nullable
age_group_place   nullable
entered_at
```

### `RecordedActivity` (many:1 with Race — 5+ rows per race; see `is_primary`)
```
id (PK)
race_id (FK)
upload_id (FK)    nullable — which upload produced this row
segment           enum: swim, t1, bike, t2, run
is_primary        bool — the recording that counts for this segment. A leg recorded by
                  two devices yields two rows; exactly one is primary. Default: the row
                  with the richest data (power > no power, then longer duration), user
                  overridable from the detail view.
device_key        nullable — manufacturer + serial of the recording device, from
                  device_info; how a segment's gear is inferred (§3 Gear)
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
gps_track         jsonb — array of {lat, lng, elevation, timestamp, distance_m}.
                  distance_m is the device's own odometer; recomputing from coordinates
                  is noisy and disagrees with the watch. Needed by the gap chart (§11)
progress_series   jsonb — ~200 points of {progress, elapsed_s, distance_m}, resampled at
                  import so the browser never aligns two 9000-point streams (§11)
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
- Unique constraint on `RecordedActivity(race_id, segment, upload_id)` — one row per
  segment *per upload*, so two devices recording the same leg coexist (§3). Idempotency
  comes from `Upload.content_hash`, not from this constraint.
- Partial unique index on `RecordedActivity(race_id, segment) WHERE is_primary` — exactly
  one primary recording per segment.

### Conventions
- All durations stored as **seconds (integer)**; formatting is a frontend concern.
- All timestamps stored **UTC, timezone-aware**; `Race.date` is a plain date.
- Distances in metres, elevation in metres, pace derived on read (not stored redundantly).

---

## 3. Ingestion

### FIT files (primary)

Verified against five real files in `backend/tests/fixtures/` — three multisport exports
(two Apple Watch, one Garmin), one single-sport Edge 540 recording and one Strava
re-export. The vendor split matters more than any single file's quirks.

These fixtures are deliberately **not** anonymized: the sensor serials are what gear
detection is tested against, and the GPS tracks are what map rendering is tested against.
That is a decision to keep the repository private — see
`backend/tests/fixtures/README.md`, which records what would have to happen before it
could ever be published.

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
- **No nutrition or carbs data anywhere**, on either vendor. Fueling stays manual entry
  (`RaceFueling`, §2).
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
   *(Refined in M2: half-open alone is not enough. Each session's end must also be
   clamped to the next session's start, or the boundary record still matches two
   windows — the overlap is between one session's derived end and the next's recorded
   start, which half-open bucketing on its own does not resolve. The clamp is a no-op
   wherever a real gap exists, so Trap 4's orphans survive it.)*
4. **Records can fall outside every session window.** The Garmin file has one orphan at
   12:39:07, in the 1.1 s gap between bike end and T2 start. Assert-every-record-lands
   would reject a valid file; orphans are dropped and counted, not fatal. *(Verified in
   M2: the Strava re-export has one too — a trailing run record a fraction of a second
   past the run's own computed end. Orphans are ordinary, not a Garmin peculiarity.)*
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
   gap handling. *(Corrected in M2: the gap is not scattered. Altitude is 100 % absent
   during the open-water swim and 100 % present everywhere else, on every fixture and
   both vendors — the ~20 % is just the swim's share of the record count. So it is one
   contiguous hole per race, and only the swim needs the gap handling.)*
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
   Records landing in no session are dropped and counted, not fatal — the Garmin fixture
   has one such orphan in the 1.1 s gap between bike end and T2 start (§3 Traps 4). A
   record matching more than one window *is* a bug and fails the import.
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

### What Strava exports actually look like

Three files downloaded from Strava for one race (Stutensee, 2026-06-21), named
`Schwimmen`, `Rad` and `Lauf`, turned out to be **not three legs of one race**:

| File | sha256 | Content |
|---|---|---|
| `Stutensee_Schwimmen_.fit` | `ff748711…` | the **full Apple Watch multisport file** — all 5 sessions |
| `Stutensee_Lauf.fit` | `ff748711…` | **byte-identical to the swim file** |
| `Stutensee_Rad_.fit` | `e2c08c08…` | a **different device**: Garmin Edge 540, 1 cycling session |

Two consequences, both structural:

**1. Strava's "export original" returns the uploaded file, not a per-activity file.** The
swim and run downloads are the same bytes — Strava split the *activity list* but hands
back the original combined upload for each. So downloading three activities can yield one
distinct file, or two, or three, with no way to tell from the filename. Content-hash
deduplication catches this, but the tray must **say** "identical to an upload you already
have" rather than silently discarding a file the user deliberately selected.

**2. The same segment can be recorded by two devices at once.** The bike leg exists twice
here:

| | Apple Watch (in multisport file) | Garmin Edge 540 (separate file) |
|---|---|---|
| Window | 07:41:45 – 08:15:58 | 07:43:03 – 08:16:23 |
| Distance | 19 992 m | 19 634 m |
| Power | — | **247 W avg, NP 261** |
| Cadence | 2 (garbage) | **84** |

Neither is wrong; the Edge is simply the better record of that leg, because the power
meter and cadence sensor pair to it. The watch remains the only record of the swim and
run. So a race's best data is **assembled across devices, per segment** — which the
original one-recording-per-segment model could not express (see §2 `RecordedActivity`).

**Assembly is per segment, never per metric.** One recording is primary for a leg and
every displayed number for that leg comes from it. Cherry-picking fields across devices —
the Edge's power with the watch's distance — would mean 19 634 m and 19 992 m both
claiming to be the bike distance, and every chart series needing a device footnote. The
non-primary recording is retained and switchable, not merged.

The Edge file also carries two things the watch files don't:
`timestamp_correlation`, giving the UTC↔local offset directly (+2 here), which removes the
timezone guesswork for Garmin files; and a `sport` message named `TRIATHLON`, the activity
profile.

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

### Gear detection

Tracking which bike and shoes were raced is a stated goal, and the Edge file shows it can
be largely automatic. Its `device_info` messages name every paired sensor by manufacturer
and serial:

| Device | Serial | What it identifies |
|---|---|---|
| `garmin` / `edge_540` | 3449642804 | the head unit |
| `favero_electronics` | 1753485582 | Assioma power meter pedals |
| `shimano` (product 12917) | 2783122544 | Di2 electronic groupset |
| `polar_electro` | 584467901 | HR strap |

Serials are stable across rides, so gear resolves itself after being named once:

1. On import, collect every `device_info` serial in each session.
2. Known serial → look up `GearDevice` → attach its `Gear` to that segment with
   `assigned_by = auto_device`.
3. Unknown serial → surface it in the UI as "new device seen: `favero_electronics`
   1753485582 — which gear is this?" The user names it once; every past and future import
   carrying that serial can then be backfilled.
4. The user can always override; a manual assignment is never overwritten by detection.

**Shoes cannot be detected this way.** Running shoes carry no sensor (barring a footpod),
and the Apple Watch files list only the watch itself — no paired devices at all. So the
run segment's gear is manual, with the last-used pair offered as the default. This is the
same shape as fueling: the data isn't in the file, so the UI has to ask.

The payoff is mileage per item — shoe rotation and drivetrain wear — computed from
`activity_gear`, which is why gear attaches to `RecordedActivity` rather than to `Race`.

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
- `Upload` and `Gear` are the exceptions: both exist independently of any race, so each
  carries its own `user_id`. That makes three ownership roots, not one. Consequences:
  grouping must assert every `upload_id` belongs to the caller before creating anything;
  gear assignment must check the `Gear` *and* the `RecordedActivity`'s race; and both the
  tray and the gear list are scoped by `user_id` directly.
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
| `PATCH` | `/races/{id}` | Edit race metadata: name, type, location, notes. |
| `PUT` | `/races/{id}/fueling` | Upsert the fueling rows for a race (carbs, products, notes per segment). |
| `DELETE` | `/races/{id}` | Delete race and cascade its activities/laps. |
| `PUT` | `/races/{id}/official-result` | Upsert manually entered official times/placings. |
| `GET` | `/races/{id}/track` | GPS polylines for the map, one per segment. Simplified by default (`?precision=`), full stream on request. Split out because it's by far the heaviest payload. |
| `GET` | `/races/{id}/laps` | Lap splits, optional `?sport=` filter, for the pacing chart. |
| `POST` | `/uploads` | Multipart upload of one or many files. Parses each, dedupes by content hash, auto-groups multisport files into a race, leaves single-sport files in the tray. |
| `GET` | `/uploads` | The ungrouped tray, with suggested groupings. `?status=` filter. |
| `DELETE` | `/uploads/{id}` | Discard an upload (and its stored file). |
| `POST` | `/races/group` | Create a race from selected `upload_ids` + segment assignment. |
| `POST` | `/races/{id}/attach` | Attach further uploads to an existing race. |
| `PATCH` | `/activities/{id}` | Set `is_primary`, or assign gear to this segment. |
| `GET` | `/gear` | Gear list with accumulated distance per item. |
| `POST` | `/gear` | Create a gear item. |
| `PATCH` | `/gear/{id}` | Edit or retire a gear item. |
| `GET` | `/gear/unknown-devices` | Sensor serials seen in imports with no gear mapping yet. |
| `POST` | `/gear/{id}/devices` | Map a serial to a gear item; backfills past activities. |
| `POST` | `/races/{id}/weather/refresh` | Re-run weather enrichment. |
| `GET` | `/races/{a}/compare/{b}` | Everything the comparison page needs in one request: both aligned progress series, per-segment deltas, conditions diff, fueling diff (§11). |
| `GET` | `/races/{id}/comparable` | Races eligible to compare against this one — same category, plus the ±10 % rule for `custom`. Backs the picker. |
| `POST` | `/races/{id}/classify` | Re-run classification; updates `auto_category_id`, and `race_category_id` only when not manually overridden. |
| `GET` | `/categories` | The category table, for pickers and for showing range boundaries. |
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
  metadata, fueling). Kept deliberately plain.

### Information Architecture

- **Login** (`/login`) — the only unauthenticated route; everything else redirects here
  on 401.
- **Timeline** (`/`) — chronological feed of races, most recent first, each entry a
  preview card.
- **Race detail** (`/races/:id`) — official vs. recorded comparison, course map, lap
  split chart, weather + fueling metadata.
- **Race edit** (`/races/:id/edit`) — manual entry: race metadata, official result
  times/placings, fueling, per-segment gear.
- **Gear** (`/gear`) — inventory with accumulated distance, plus unmapped sensor serials
  awaiting a name.
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
| Swim accent | `#008C7E` | Teal |
| Bike accent | `#A8862A` | Gold-ochre |
| Run accent | `#B4472A` | Brick |
| Faster | `#008C7E` | Diverging positive pole (gap chart, §11) |
| Slower | `#B4472A` | Diverging negative pole |
| Neutral midpoint | `#6B6B66` | Zero line, diverging midpoint |

**The original accents failed validation and were re-stepped.** The first draft used
`#1B4B4A` / `#8A6D3B` / `#C4622D`, which reads fine as decoration but not as data: bike
and run sat at ΔE 2.0 under protanopia and **10.2 under normal vision** — below the 15
floor, meaning full-colour readers struggle too. Since `SegmentBar` stacks all three
adjacently, that is a legibility bug, not a nitpick. The replacements above pass all six
checks against the `#FAFAF8` surface (worst adjacent pair ΔE 9.3 deutan / 15.3 normal),
and the faster/slower pair passes at ΔE 11.1 / 24.3.

Consequence for the gap chart: **course lanes are not tinted with sport hues.** Hue is
spoken for by the diverging fill, so segment boundaries are drawn as vertical rules with
text labels instead. One colour job per chart.

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
| `RaceForm` | Manual entry: race metadata |
| `FuelingForm` | Per-segment carbs, product list (add/remove rows) and notes |
| `OfficialResultForm` | Manual entry: official times and placings |
| `FitUpload` | Drag/drop upload, multi-file, with per-file parse feedback |
| `GroupingTray` | Ungrouped uploads with suggested groupings; select + confirm to create a race |
| `SegmentAssign` | Per-upload segment picker, shown when detection is ambiguous |
| `GearPicker` | Per-segment gear assignment on the race detail view |
| `GearList` | Gear inventory with accumulated distance; retire/rename |
| `DevicePrompt` | "New sensor seen — which gear is this?" one-time mapping |
| `RecordingSwitch` | Shown when a segment has >1 recording; pick which is primary |
| `CategoryField` | Resolved category with provenance ("auto from measured distances") + override and revert |
| `CompletenessList` | Quiet checklist of what a race is still missing, grouped by what it unlocks |
| `ComparePicker` | Choose the second race; only same-category races are offered |
| `GapChart` | The centrepiece: gap-to-reference over checkpoints, diverging fill, swappable reference |
| `ConditionsDiff` | Only the settings that differ, material ones promoted and marked |
| `FuelingDiff` | Per-segment carbs, g/h and products, side by side |

---

## 6. Features Supported by This Schema

- Official vs. recorded time comparison, per segment (including T1/T2).
- Total distance / elevation gain across all races (aggregate queries, no extra tables).
- Map rendering per race from `gps_track`.
- Pacing trends per sport / distance / time (via `LapSplit`, sliced by `sport` and joined
  to `Race.race_type` / `Race.date`).
- Weather correlation with performance (temp/humidity on `Race`, joined to pacing data).
- Manual fueling tracking per race segment: carbs, products used, free-text notes.
- Gear per race segment, auto-detected from sensor serials where possible, with
  accumulated distance per bike and per pair of shoes.
- Multiple recordings of the same segment from different devices, with one marked primary.
- Automatic race classification from measured distances, with manual override retained
  separately from the inferred value.
- Partial records throughout: any field saveable alone, missing values derived where
  possible and shown as unknown where not.
- Side-by-side comparison of two same-category races: gap-to-reference timing chart,
  fueling comparison, and a diff of conditions and course.

---

## 7. Milestones

| # | Milestone | Contents |
|---|---|---|
| M0 | Scaffold | Repo layout, `docker-compose` with Postgres, FastAPI hello-world, Vite app, lint/format config |
| M1 | Data model | SQLAlchemy models, Alembic initial migration, Pydantic schemas |
| M1.5 | Auth | `User` model, argon2 hashing, JWT cookie sessions, `current_user` dependency, seed-user CLI, login page + auth-gated routing |
| M2 | FIT import | `fit_import.py` + `POST /uploads`, tested against all five fixtures — asserting the §3 traps: session totals over lap sums, half-open bucketing, orphan records tolerated, device-dependent power, elapsed vs moving time, local race date |
| M2.5 | Grouping | `Upload` staging, ungrouped tray, same-day proposals, `POST /races/group`, derived transitions — the path Strava will reuse |
| M3 | Read API | `/races`, `/races/{id}`, `/laps`, `/track`, `/stats/summary`; OpenAPI type generation wired up |
| M4 | Timeline UI | `StatStrip`, `TimelineSpine`, `RaceCard`, `SegmentBar`, design tokens |
| M5 | Detail UI | `SplitsComparison`, `SplitsChart`, `CourseMap` |
| M6 | Manual entry | Race form, official result form, `RaceFueling` form; upload page |
| M6.5 | Gear | `Gear`/`GearDevice`, serial auto-detection with backfill, manual shoe assignment, distance-per-item |
| M7 | Weather | Open-Meteo enrichment at import + refresh endpoint |
| M7.5 | Deploy | Compose stack, Caddy + TLS, migrations on boot, backups (see §8) |
| M8 | Classification | `RaceCategory` seed, `classify.py`, auto-assign on import and on primary change, override UI (§9) |
| M9 | Partial data | `total_time` derivation, `completeness` on read, per-field autosave, unknown-vs-zero rendering (§10) |
| M10 | Comparison | `progress_series` backfill, `comparison.py` alignment, `GapChart`, conditions and fueling diffs (§11) |
| M11 | Strava | Deferred backfill path (see §3) |

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

## 9. Race Classification

A race's category was a free choice on the race form. It is instead **inferred from the
measured distances** and only overridden by hand when the inference is wrong.

Source: the DTU distance list supplied as a photo. Distances below are **km, as
published** (swim / bike / run).

| key | label | swim | bike | run | kind |
|---|---|---|---|---|---|
| `schueler` | Schüler (A/B/C/D) | 0.05–0.4 | 1–10 | 0.2–2.5 | age class |
| `jugend` | Jugend (A und B) | 0.4–0.75 | 10–20 | 2.5–5 | age class |
| `junioren` | Junioren | 0.75–1.5 | 20–40 | 5–10 | age class |
| `supersprint` | Supersprintdistanz | 0.25–0.5 | 6.5–13 | 1.7–3.5 | distance |
| `sprint` | Sprintdistanz | ≤ 0.75 | ≤ 20 | ≤ 5 | distance |
| `kurz` | Kurzdistanz | 1.5 | 40 | 10 | distance |
| `mittel` | Mitteldistanz | 1.9–3.0 | 80–90 | 20–21 | distance |
| `lang` | Langdistanz | 1.0–4.0 | 100–200 | 10.0–42.2 | distance |
| `mixed_relay` | Mixed Relay | 0.25–0.3 | 5–8 | 1.5–2 | relay |

Three things about this list drive the design, and none of them are obvious from a
glance:

**1. It mixes three different kinds of category.** Schüler, Jugend and Junioren are *age
classes* — they describe the athlete, not the course, and their distances overlap the open
distance classes almost entirely (Junioren 0.75–1.5 / 20–40 / 5–10 against Kurzdistanz
1.5 / 40 / 10). Mixed Relay is a team format. **Only `kind = distance` categories are
eligible for automatic classification**, because a course cannot tell you how old the
field was. Age classes and relays remain hand-pickable.

**2. The ranges overlap by design, so "exactly one match" never fires.** Supersprint
(0.25–0.5 / 6.5–13 / 1.7–3.5) sits entirely inside Sprint (≤ 0.75 / ≤ 20 / ≤ 5) — every
supersprint race matches both. The tie-break is therefore **narrowest range wins**, not
nearest midpoint: the most specific category that contains the measurement is the right
answer, and Sprint acts as the catch-all its "bis" bounds imply.

**3. Nominal distances need tolerance, or real races fail to classify.** Kurzdistanz is
published as exact values (1.5 / 40 / 10), and measured courses never hit them. Checking
the four races in the fixtures against the table with a ±10 % tolerance:

| Race | Measured (km) | Result |
|---|---|---|
| 2026-08-09 | 1.55 / 40.57 / 9.93 | **Kurzdistanz** — all within 3 % |
| 2024-08-03 | 1.65 / 40.87 / 9.24 | **Kurzdistanz** — swim +9.7 %, just inside |
| 2026-06-21 | 0.54 / 19.99 / 5.11 | **Sprintdistanz** — run 2 % over the 5 km bound |
| 2026-07-11 | 1.57 / 33.75 / 9.17 | **unclassified** — bike −15.6 %, genuinely short |

Without tolerance, three of the four misclassify. With it, the one that stays
unclassified is the one that *should*: a 33.7 km bike is not a 40 km course, and flagging
it is more useful than forcing it into a bucket. Tolerance is a per-category
`tolerance_pct` column defaulting to 10, so the published ranges stay in the table exactly
as written and the fudge factor is visible and tunable rather than baked into a range.

### How a race is classified

1. Take the **primary** recorded activity per segment (§2 `is_primary`) and read
   `distance_m` for swim, bike and run. Transitions are ignored — they carry no
   category signal.
2. Consider only categories with `kind = distance`. A category matches when all three
   distances fall inside their bounds, each widened by `tolerance_pct`.
3. One or more matches → take the **narrowest** matching category, measured by summed
   range width; `classification_source = auto`.
4. No match → leave `unclassified`, recording the nearest category and which discipline
   was furthest out ("bike −15.6 %"), so the reason is visible rather than mysterious.

Classification runs on import, and re-runs when a segment's primary recording changes —
switching the primary bike recording from the watch (33.7 km) to the Edge (40.5 km) can
legitimately change the category.

### Storage and override

```
Race
  race_category_id (FK, nullable)       the assigned category
  classification_source  enum: auto, manual, unclassified
  classified_at          nullable
  auto_category_id (FK, nullable)       what the classifier would have chosen
```

`auto_category_id` is kept **alongside** a manual override rather than being overwritten.
Two reasons: the UI can show "you set this to Olympic; measurements suggest Sprint",
which is exactly the signal that a distance is wrong; and correcting the range table
later can be replayed against every race without destroying manual choices.

A manual override is **never** silently replaced. Re-running classification updates
`auto_category_id` only; `race_category_id` moves only if `classification_source = auto`.

Races with no recorded distances (manual entry only, §10) stay `unclassified` rather
than being forced into a bucket. The user can pick a category by hand, which sets
`classification_source = manual`.

### UI

The race form shows the category as a resolved value with its provenance —
"Olympic · auto from measured distances" — and a picker to override. Overridden races get
a small revert affordance back to the automatic value. Nothing about this is a modal or
a wizard; it is one line on an existing form.

---

## 10. Partial Data and Drafts

Nothing should require a complete record to be saved. Entering a race with only a name
and a date must work, as must entering official splits without a total time, or a total
time with no splits.

### Principle

**Validation belongs at the point of use, not the point of save.** The database enforces
only what is structurally necessary; everything else is nullable and checked when a
feature actually needs the value.

### What is actually required

| Table | Required | Everything else |
|---|---|---|
| `Race` | `user_id`, `date` | nullable — including `name`, which defaults to `"Untitled race"` |
| `OfficialResult` | `race_id`, `total_time` | splits and placings nullable; `total_time` is derived from the splits rather than demanded, see below |
| `RaceFueling` | `race_id`, `segment` | nullable |
| `RecordedActivity` | `race_id`, `segment`, `start_time` | as already specified |
| `Gear` | `user_id`, `name`, `kind` | nullable |

`Race.date` stays required because the timeline is ordered by it and a dateless race has
nowhere to appear. Import fills it; the manual form defaults it to today. This is the one
place a value is forced, and it is forced to something correctable rather than blocking
the save.

**`total_time` is the one exception to the nullability rule, and it stays NOT NULL.** A
result without a total is not a result. What the user should never have to do is *work it
out* — so the two paths are:

- **All five splits entered** → the total is summed on write. Nothing to type.
- **Otherwise** → nothing is calculated, and the save is refused with a prompt naming the
  gaps: *"Enter the total time, or fill in every split so it can be calculated. Still
  missing: T1, run."* The form shows the same message live, and shows the calculated
  figure as soon as the last split lands, so the arithmetic is visible before saving.

A typed total is **never** overwritten by derivation. The user may be copying the
organizer's figure, which can differ from the sum by a second or two of chip-timing
rounding or by transitions folded into adjacent legs — and that number is the more
authoritative one.

Note what this deliberately gives up: three splits and no total cannot be parked
half-entered. That is the accepted cost of a total that is always present and always
trustworthy, and it applies only to `OfficialResult` — the race itself, its fueling and
its gear all remain partially saveable.

### Derivation, not requirement

Missing values are derived where they can be, and the derivation is marked as such:

- `total_time` absent but all five splits present → sum them, flag `total_is_derived`.
- One split absent but `total_time` and the other four present → the remainder is that
  split, same flag.
- Neither derivable → the field simply reads as "—", not zero. **Nothing is ever
  displayed as `0:00` because it is unknown**, which is the failure mode that makes
  partial data feel broken.

### Completeness, surfaced not enforced

Each race exposes a computed `completeness` object on read: which fields are missing,
grouped by what they unlock.

```json
{
  "official_result": {"missing": ["t1_time", "age_group_place"], "usable": true},
  "fueling":         {"missing": ["run"], "usable": true},
  "classification":  {"missing": ["recorded_distances"], "usable": false},
  "comparison":      {"usable": true}
}
```

The detail view renders this as a quiet checklist, not a warning banner — a race missing
its age-group placing is not in an error state, it is just a race with an unknown placing.

### API and form behaviour

- `PATCH /races/{id}` and the other write endpoints accept **any subset of fields**.
  Pydantic write models use `Optional` throughout with `exclude_unset` semantics, so
  omitting a field leaves it alone and explicitly sending `null` clears it. Those two
  cases must stay distinguishable.
- Forms **autosave per field on blur** rather than gating behind a submit button. No
  form-level validation gate; per-field validation only rejects genuinely invalid input
  (a negative time), never absence.
- Aggregates in `/stats/summary` skip nulls and report the denominator: "412 km across
  9 of 11 races" rather than silently averaging over missing data.

### Consequences elsewhere

Partial data has to be handled by every consumer, and two are worth calling out:

- **Comparison (§11)** must render with either side incomplete — a missing fueling row
  shows as unknown, not as zero carbs, which would read as "took nothing".
- **Classification (§9)** needs recorded distances; without them the race stays
  `unclassified` rather than defaulting to a category.

---

## 11. Comparison View

Two races side by side at `/compare?a=<id>&b=<id>`, reachable from a "compare" action on
any race card or detail view.

### What can be compared

Only races in the **same category** (§9). Different categories are excluded from the
picker rather than warned about after the fact — a Sprint against a Long course produces
a chart whose every number is meaningless.

Within a category, distances still differ — the fixtures show 33 746 m and 40 570 m bikes
in nominally comparable races. That is not a reason to block the comparison; it is a
reason to **say so prominently**, which the conditions panel does.

Two `custom` races are comparable when each segment's distance is within ±10 % of the
other; otherwise the picker excludes them.

### Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  Stutensee Olympic          2026-08-09  │  Karlsruhe    2026-07-11   │
│  4:52:31                                │  5:12:04        +19:33     │
├──────────────────────────────────────────────────────────────────────┤
│  Δ  swim −0:42 │ T1 +1:12 │ bike −8:20 │ T2 −0:05 │ run −11:38       │
├──────────────────────────────────────────────────────────────────────┤
│   GAP TO REFERENCE                                                   │
│  0:00 ●───●───●────────●────────●───●───────●────────●  reference    │
│            ╲                                                         │
│  2:00       ●────●                teal above 0 = faster              │
│                   ╲────●────●                                        │
│  4:00                        ╲─────●──────●  +3:42                   │
│       Start Swim T1    bike       T2     run      Finish             │
├──────────────────────────────────────────────────────────────────────┤
│  CONDITIONS & COURSE                                                 │
│  ⚠ bike 40.5 km vs 33.7 km (+20 %)   ⚠ +22 m vs +558 m elevation     │
│    29 °C / 61 % vs 18 °C / 78 %      race bike vs training bike      │
├──────────────────────────────────────────────────────────────────────┤
│  FUELING                                                             │
│  bike  90 g · 45 g/h   vs   60 g · 33 g/h        +30 g               │
│  run   30 g · 34 g/h   vs   20 g · 21 g/h        +10 g               │
│  A: 3× Maurten 160, 1× gel     B: 2× Maurten 160                     │
└──────────────────────────────────────────────────────────────────────┘
```

### The gap chart

The reference image (TriRating's Ironman Hamburg progression chart) settles the form, and
it is better than the two-panel design this section previously carried. **One panel, not
two.** The reference race is pinned flat at zero and the other race is drawn as its gap
from it — so the residual *is* the chart rather than a supporting strip beneath an
absolute-time plot that nobody reads.

```
        Start   Swim   T1        bike ─────────    T2      run ────────   Finish
  0:00 ─●───────●──────●──────────●────────●────────●───────●──────●────────●──  reference
         ╲                                                                        (Karlsruhe
   1:00   ●──────╲                          teal above 0 = faster                  2026-07-11)
                  ╲────●                                                          
   2:00             ▔▔▔▔ ╲                                                        
                          ●────────●        brick below 0 = slower                
   3:00                              ╲──────●──────●                              
                                                    ╲──────●  ← Stutensee, +3:42
```

**Y axis — time behind the reference, increasing downward.** Inverted relative to a normal
time axis, matching the reference image: falling away visually means losing time, which is
how the sport talks about it. The comparison race crosses above zero wherever it was
ahead.

**X axis — discrete checkpoints, not continuous distance.** The reference chart uses
`Start · Swim · T1 · 50k · Lap 1 · 140k · T2 · Lap 1–3 · Finish`, and that is exactly the
granularity real timing data has. Ours:

- **Segment boundaries are always checkpoints** — Start, Swim, T1, T2, Finish. These exist
  for every race, including one entered by hand with only official splits.
- **Intra-segment checkpoints come from laps** where both races have them (5 km on the
  bike, 1 km on the run, per §3).
- **When the two races' segment distances agree within 5 %**, checkpoints are placed at
  absolute distances and labelled that way — `20k`, `40k`, `Lap 2` — which is what makes
  the reference chart legible.
- **When they don't** (33.7 km against 40.5 km), checkpoints fall back to even fractions
  of each segment, labelled with the reference race's distance and marked as normalised.
  The conditions panel states the distance difference outright, so the axis never quietly
  misleads.

Between checkpoints the line is straight — piecewise linear, as in the reference. No
smoothing: an interpolated curve would imply per-second knowledge the checkpoint data
doesn't have.

**Fill.** The area between the line and zero is filled as a diverging encoding — teal
where the race was faster, brick where slower, with the zero line itself in neutral gray.
That is the polarity job from the palette rules, and it is why the fill reuses the sport
hues rather than introducing red/green: red/green is both off-palette and the worst
possible choice for colour-blind readers.

**Invariant.** The gap at each segment boundary equals the cumulative split delta, so the
Δ strip at the top of the page is literally this curve sampled at five points. If they
ever disagree, one of them is wrong.

**Degradation.** A race with no recorded stream renders from official splits alone — five
checkpoints instead of twenty, visibly coarser, with a note saying so. The comparison
still works.

**Accessibility.** Two series where one is a flat baseline, so the title names them and
the zero line is labelled with the reference race rather than shipping a legend box; the
comparison line is direct-labelled at its right end with the finishing gap. A crosshair
tooltip gives both races' elapsed times and the gap at the hovered checkpoint, and the
same data is available as a checkpoint table — so nothing is conveyed by colour alone.
A control swaps which race is the reference.

**More than two races.** The reference image plots fourteen. The shape generalises for
free, but the colour does not: N races need a validated categorical palette rather than
one diverging fill, and the fill has to go. Out of scope for v1, noted so the chart
component isn't built in a way that forecloses it.

### Fueling comparison

Per segment, three things:

- **Absolute carbs** and the delta.
- **Rate in g/h**, derived from the segment's elapsed time. This is the number that
  actually transfers between races of different lengths — 60 g over a 70-minute bike is
  not the same fuelling as 60 g over a 50-minute bike, and the absolute figure hides that.
- **Products**, listed side by side with items unique to each race marked, plus the
  free-text notes.

Unknown is rendered as "—", never as zero (§10). A race with no fueling row recorded is
not a race where nothing was eaten.

The panel deliberately stops at presentation. It does not compute a correlation between
carbs and pace, and does not claim one caused the other: two races is not a sample, and
a tool that implies otherwise from `n = 2` is worse than one that just shows the numbers.

### Conditions and course panel

A flat list of the settings that differ, with material differences promoted to the top
and marked. Thresholds: distance ±5 %, elevation ±50 m, temperature ±5 °C, humidity
±15 pp, plus any change of gear or of primary recording device.

Showing only differences, rather than a full spec table, is the point — the reader wants
to know what was not like-for-like, and an exhaustive table buries that in rows of
identical values.

---

## 12. Open Decisions / Deferred

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
- **Normalised fuel products** — `RaceFueling.products` is free-form JSONB. A `Product`
  table with a picker would enable "how do I race on Maurten vs. SIS" comparisons, at the
  cost of a catalogue to maintain. Deferred until the free-text data shows it's worth it.
- **Gear component-level tracking** — chains, cassettes and tyres as sub-items of a bike,
  each with their own mileage and service intervals. Decided against for v1: it needs a
  parent/child gear tree and swap history. The `Gear` shape accommodates it later.
- **Garmin `time_in_zone` and cycling dynamics** — L/R balance, torque effectiveness,
  pedal smoothness and HR-zone distributions are all parsed into `raw_data` but not
  modelled or surfaced. Cheap to promote later if they turn out to be interesting.
- **CI/CD** — manual `docker compose up -d --build` for v1.
