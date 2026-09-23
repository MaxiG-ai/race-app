# Import

**Priority:** core

## Goal

The owner drops one or more FIT files onto the import page. A multisport file becomes a
race straight away. Single-sport files wait in a tray to be grouped
([grouping](grouping.md)). Every file is kept as it was uploaded, so the whole collection can be
re-imported whenever the parser improves.

This is the highest-risk feature: everything downstream depends on what it produces. Build it
first, test it against the synthetic fixtures, and check it against real files locally
([fit-files](../reference/fit-files.md#testing-without-the-originals)).

## Visibility

Owner only. Visitors never see uploads, stored files, `raw_data` or parse errors
([redaction](access-and-visibility.md#redaction)).

## Data

```
upload                        staging row per file; exists before it belongs to a race
  id (PK)
  source              enum: fit_upload, strava
  filename            nullable (null for Strava)
  stored_path         nullable — original file on the uploads volume
  content_hash        sha256, unique — the same file can't be uploaded twice
  external_id         nullable, Strava activity id, unique when set
  status              enum: parsed, grouped, failed
  kind                enum: multisport, single_sport
  detected_sport      enum: swim, bike, run, other — null for multisport
  detected_start_time / detected_end_time
  detected_distance_m
  summary             jsonb — parse preview so the tray renders without re-reading files
  error               nullable, parse failure message
  race_id             FK nullable — set once grouped
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
| `POST` | `/api/uploads` | multipart, one or many files; parse, hash, store; auto-group multisport |
| `GET` | `/api/uploads` | the tray (`?status=`), with suggested groupings, see [grouping](grouping.md) |
| `DELETE` | `/api/uploads/{id}` | discard an upload and its stored file |
| `POST` | `/api/races/{id}/attach` | attach uploads to an existing race |

The response to `POST /uploads` lists each file's outcome: `created race <id>`,
`in tray`, `duplicate of upload <id>`, or `failed: <reason>`.

## UI

`admin/import.html`: a drop zone for multiple files, a per-file result line as each one is
parsed, and the grouping tray below it.

## Rules

### Import flow (multisport)

1. Parse `session`, `lap`, `record` and `device_info` in one pass with `fitdecode`.
2. Map the 5 sessions to `swim, t1, bike, t2, run` by order and sport field. Check the
   sequence instead of trusting it; a file that doesn't match is rejected with a clear
   error and nothing is written.
3. Bucket `record`s into sessions with half-open, clamped windows
   ([Trap 3](../reference/fit-files.md#trap-3)). Orphans are dropped and counted
   ([Trap 4](../reference/fit-files.md#trap-4)); the count is kept in `summary`.
4. Create the race: `date` = first session start converted to the start location's local
   timezone ([Trap 11](../reference/fit-files.md#trap-11)), location = bounding-box
   centroid, name `"Untitled race"`, `is_public = false`.
5. Persist 5 `recorded_activity` rows and their laps. **Totals come from `session`, never
   from summing laps** ([Trap 1](../reference/fit-files.md#trap-1)). Mark trailing partial
   laps as `is_partial`. Derive pace from distance ÷ time, never from `avg_speed`.
6. Compute `progress_series`, run [gear detection](gear.md#rules),
   [classification](classification.md#rules) and [weather](weather.md#rules). If
   enrichment fails, log it and still return the race; an enrichment failure never fails the import.
7. Return the race id so the page can link straight to it.

### Deduplication

- The same bytes produce the same `content_hash`, so re-uploading returns the existing
  upload. It doesn't create a second one.
- The UI **says** "identical to an upload you already have (<filename>, <date>)" rather
  than silently dropping a file the owner chose on purpose. This matters for Strava
  downloads, where three "different" activities can be the same file
  ([fit-files](../reference/fit-files.md#strava-exports)).

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
- Uploading the same file twice gives one `upload` row and a "duplicate" result.
- A file with the wrong session sequence writes nothing.
