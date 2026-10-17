# Data model — overview

Each table is fully defined in the feature file that owns it. This page only shows how
they relate. Units and nullability conventions are in [architecture](architecture.md#conventions).

```
owner (1 row) ── session

site_settings (1 row)

race_category ◄──────┐
                     │ race_category_id / auto_category_id
race ────────────────┤
 │                   │
 ├── official_result (1:1)
 ├── race_fueling    (1:n, one per segment)
 ├── race_photo      (1:n, ≤ 1 is_cover) ── re-encoded JPEGs on disk
 └── recorded_activity (1:n, ≥ 5; one is_primary per segment)
        │   ▲
        │   └── upload (n:1, nullable) ── stored FIT original on disk
        ├── lap_split (1:n)
        └── activity_gear (n:m) ── gear ── gear_device (serial → gear)
```

| Table | Owned by | Notes |
|---|---|---|
| `owner`, `session` | [access-and-visibility](features/access-and-visibility.md#data) | exactly one owner row |
| `site_settings` | [access-and-visibility](features/access-and-visibility.md#data) | site title, athlete name |
| `race` | [races-and-results](features/races-and-results.md#data) | ordered by `date`; carries `is_public` and `race_type` |
| `official_result` | [races-and-results](features/races-and-results.md#data) | prefilled from the recordings, then typed and confirmed by hand |
| `upload` | [import](features/import.md#data) | one row per file, on the race it was dropped onto |
| `recorded_activity` | [import](features/import.md#data) | one row per segment per upload |
| `lap_split` | [import](features/import.md#data) | pacing shape only, never totals |
| `race_category` | [classification](features/classification.md#data) | seeded, editable as data |
| `race_fueling` | [fueling](features/fueling.md#data) | |
| `gear`, `gear_device`, `activity_gear` | [gear](features/gear.md#data) | |
| `race_photo` | [photos](features/photos.md#data) | files on the uploads volume |

Weather columns live on `race` but are written by [weather](features/weather.md).

## No ownership columns

There is one owner per instance, so no table carries a `user_id`. Access control is
about *visibility* (who can see a race), not ownership. It lives in one place:
[access-and-visibility](features/access-and-visibility.md#rules).

## Indexes worth stating

- `race(date)` for the timeline order and date-range filtering.
- `recorded_activity(race_id)` for aggregates.
- `lap_split(recorded_activity_id, lap_index)` to rebuild lap order.
- `lap_split(sport, recorded_activity_id)` for per-sport filtering.
- Unique `recorded_activity(race_id, segment, upload_id)`, so two devices recording the same
  leg can coexist.
- Partial unique `recorded_activity(race_id, segment) WHERE is_primary`, so each segment has
  exactly one primary recording.
- Unique `upload(content_hash)` for idempotent uploads.
- Unique `race_photo(race_id, content_hash)`; partial unique `race_photo(race_id) WHERE is_cover`.
