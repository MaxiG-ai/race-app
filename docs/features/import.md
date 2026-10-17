# Import

**Priority:** core

## Goal

The owner drops the FIT files of a race onto its race page, the same page where everything
else about the race is edited ([races-and-results](races-and-results.md#ui)). On the
new-race page the drop creates the race; on an existing race the files are added to it. A
multisport file fills every leg; single-sport files fill one leg each
([grouping](grouping.md)). There is no staging tray: every file belongs to a race from the
moment it is dropped. Every file is kept as it was uploaded, so the whole collection can be
re-imported whenever the parser improves.

This is the highest-risk feature: everything downstream depends on what it produces. Build it
first, test it against the synthetic fixtures, and check it against real files locally
([fit-files](../reference/fit-files.md#testing-without-the-originals)).

## Visibility

Owner only. Visitors never see uploads, stored files, `raw_data` or parse errors
([redaction](access-and-visibility.md#redaction)).

## Data

```
upload                        one row per file, always on the race it was dropped onto
  id (PK)
  source              enum: fit_upload, strava
  filename            nullable (null for Strava)
  stored_path         nullable — original file on the uploads volume
  content_hash        sha256, unique — the same file can't be uploaded twice
  external_id         nullable, Strava activity id, unique when set
  status              enum: parsed (waits for the owner to pick its leg), grouped, failed
                      (failed: rows from before 0004 only; failed files are no longer kept)
  kind                enum: multisport, single_sport
  detected_sport      enum: swim, bike, run, other — null for multisport
  detected_start_time / detected_end_time
  detected_distance_m
  summary             jsonb — parse preview so the tray renders without re-reading files
  error               nullable, parse failure message
  race_id             FK nullable — null only on rows left over from the old tray
  uploaded_at

recorded_activity             one row per segment per upload
  id (PK)
  race_id             FK
  upload_id           FK nullable
  segment             enum: swim, t1, bike, t2, run
  is_primary          bool — see grouping.md
  device_key          nullable — manufacturer + serial of the recording device
  is_derived          bool — synthesized transition (grouping.md), false if recorded
  source              enum: fit_upload, strava
  start_time
  elapsed_time        session total_elapsed_time                        (Trap 5)
  moving_time         nullable, session total_timer_time                (Trap 5)
  distance_m          nullable
  elevation_gain_m    nullable — session total_ascent; absent on swim/transitions
  avg_hr / max_hr     nullable
  avg_power           nullable — device-dependent, not sport-dependent   (Trap 2)
  normalized_power    nullable
  avg_cadence         nullable
  avg_temp_c          nullable — device temperature, not air             (Trap 8)
  rpe_estimated       nullable — Apple developer field
  bounds_n/s/e/w      nullable — session bounding box
  gps_track           jsonb — [{lat, lng, elevation, t, distance_m}]; distance_m is the
                      device's own odometer, not recomputed from coordinates
  progress_series     jsonb — ~200 resampled {progress, elapsed_s, distance_m} (comparison.md)
  time_in_zone        jsonb, nullable — {hr, power}, each {high: [bpm|W], seconds: [s]} or null (below)
  raw_data            jsonb — full parsed payload, safety net for unmapped fields
  imported_at

lap_split
  id (PK)
  recorded_activity_id FK
  sport               enum, denormalised from the parent for filtering
  lap_index
  start_time
  total_time
  distance_m / elevation_gain_m          nullable
  avg_hr / max_hr / avg_power / normalized_power / cadence_avg   nullable
  is_partial          bool — trailing remainder lap
  raw_data            jsonb
```

`gps_track` stays jsonb. PostGIS only if spatial queries ever become a real need.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/races/import` | multipart files (+ optional `race_type`): a new race from them |
| `POST` | `/api/races/{id}/uploads` | multipart files: add them to the race |
| `POST` | `/api/races/{id}/attach` | `{segments: {upload_id: segment}}`: file the uploads that wait for a leg |
| `DELETE` | `/api/uploads/{id}` | remove a file from its race, with its recordings and stored original |

Both upload endpoints return `{outcomes, race}`. Each outcome is `attached`,
`needs_segment` (the file doesn't say its sport: pick the leg), `duplicate` (with the race
that already has it) or `failed: <reason>`, in the order the files were sent. `race` is the
owner detail, or null when `/races/import` made no race because nothing fitted.

## UI

No page of its own. The race page (`admin/race-edit.html`) starts with the race's files: a
drop zone, a result line per dropped file, and the list of files with the legs each one
fills ([races-and-results](races-and-results.md#ui)). `/admin/import` redirects to the
new-race page.

## Rules

### Which race a file goes to

- **New race.** The owner picks a race type, or lets the files decide: files that all
  name one sport make that single-sport race, anything else is a triathlon (an unknown
  sport is asked about, not guessed). The race starts as `"Untitled race"`,
  `is_public = false`. If no file fits, no race is made.
- **Every file must fit the race.** A multisport file needs a triathlon; a single-sport
  file's sport must be a leg of the race type; and it must have been recorded within
  8 hours of the race's other recordings (else: *"recorded on 15 Aug 2026, hours away
  from this race's other recordings. It looks like a different race."*). A file that
  doesn't fit is refused and nothing of it is written.
- **First recording.** The race takes its date (local, Trap 11) and, if it has none, its
  location from its first recording, so a race created by hand can get its file later.
- **Per file.** Each file commits on its own, so one bad file doesn't undo the others.
  Enrichment and the [prefilled result](races-and-results.md#prefilled-results) run once
  per drop, after the files.

### Import flow (multisport)

1. Parse `session`, `lap`, `record`, `device_info` and `time_in_zone` in one pass with
   `fitdecode`.
2. Map the sessions to `swim, t1, bike, t2, run` by order and sport field. Check the
   sequence instead of trusting it: swim, bike, run in that order, each transition
   present or missing. A file that doesn't match is rejected with a clear error and
   nothing is written. A missing transition is derived from the gap, exactly like a
   [grouped race's](grouping.md#derived-transitions).
3. Bucket `record`s into sessions with half-open, clamped windows
   ([Trap 3](../reference/fit-files.md#trap-3)). Orphans are dropped and counted
   ([Trap 4](../reference/fit-files.md#trap-4)); the count is kept in `summary`.
4. File it under the race: `date` = first session start converted to the start
   location's local timezone ([Trap 11](../reference/fit-files.md#trap-11)), location =
   bounding-box centroid (see above).
5. Persist 5 `recorded_activity` rows and their laps. **Totals come from `session`, never
   from summing laps** ([Trap 1](../reference/fit-files.md#trap-1)). Mark trailing partial
   laps as `is_partial`. Derive pace from distance ÷ time, never from `avg_speed`.
6. Compute `progress_series`, run [gear detection](gear.md#rules),
   [classification](classification.md#rules) and [weather](weather.md#rules). If
   enrichment fails, log it and still return the race; an enrichment failure never fails the import.
7. Prefill the official result from the recordings
   ([races-and-results](races-and-results.md#prefilled-results)) and return the race.

### Time in zone

Garmin writes a `time_in_zone` message per session and per lap: the watch's zone table
(`hr_zone_high_boundary`, `power_zone_high_boundary`) and the seconds spent in each zone.
The session-level messages are kept on `recorded_activity.time_in_zone`; lap-level ones
are ignored.

- A message refers to its session by **position in the file** (`reference_index`), so the
  mapping is made before the sessions are sorted by start time.
- `high` is each zone's upper bound; zone 0 is everything below zone 1. `seconds` has one
  entry per zone, or one more (an open-ended top zone). Arrays that don't line up, or a
  message with no time in any zone, are stored as null: unknown, not a guess.
- **Nothing is computed from records** when the message is absent (Apple writes none).
  The zone table is the athlete's watch setting, and inventing one from the sport or a
  formula would be Trap 2 again.
- Rows imported before this column existed are null until `cli reimport`.

### Deduplication

- The same bytes produce the same `content_hash`, so re-uploading returns the existing
  upload. It doesn't create a second one. The same file twice in one drop counts once.
- The UI **says** "Already part of this race", or "Identical to <filename> in <race>
  (<date>), which you already have" with a link to that race, rather than silently
  dropping a file the owner chose on purpose. This matters for Strava downloads, where
  three "different" activities can be the same file
  ([fit-files](../reference/fit-files.md#strava-exports)).
- A file that fails to parse is not kept, so its identical copy reports the same failure
  instead of "you already have this". An upload left without a race (the old tray) is
  adopted by the race it is dropped onto.

### Storage and limits

- Originals are stored verbatim on the uploads volume, keyed by upload id. `raw_data` is
  only as good as the parser was at import time; the original is what makes
  `cli reimport` possible.
- Reject oversized files (e.g. > 20 MB) and files without a FIT header before parsing.

## Acceptance

Against the synthetic fixtures ([fit-files](../reference/fit-files.md#testing-without-the-originals)):

- Segment totals equal the session values, not lap sums, on the Apple fixture (Trap 1).
- Power: `avg_power` is null on the Apple bike and set on the Apple run; the reverse on Garmin (Trap 2).
- The boundary fixture has no record in two segments; the Garmin fixture reports exactly one orphan (Traps 3, 4).
- `elapsed_time ≠ moving_time` on the Garmin bike (Trap 5).
- The early-start fixture gets the local date, not the UTC date (Trap 11).
- Uploading the same file twice gives one `upload` row and a "duplicate" result that
  names the race that has it.
- One bike file makes a bike race; with `race_type = triathlon` it makes a triathlon
  missing four legs. A bike file on a run race, a multisport file on a single-sport race
  and a file from another day are refused, and nothing of them is stored.
- A file with the wrong session sequence writes nothing.
- A file without a recorded T2 (`missing_t2.fit`) gets a derived T2 from the gap.
- Garmin sessions carry the zone seconds listed in the fixture README (HR everywhere, power
  on the bike); the lap-level message is ignored; Apple sessions have `time_in_zone = null`.
