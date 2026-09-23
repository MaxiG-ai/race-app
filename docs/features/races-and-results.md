# Races and results

**Priority:** core

## Goal

A race is the unit everything hangs off. The owner can create one by hand, with no file,
and fill it in over time: name, date, location, official splits, placings, notes. Nothing
has to be complete to be saved.

## Visibility

Visitors see public races: metadata, official result and notes. The `completeness`
checklist is owner only ([redaction](access-and-visibility.md#redaction)).

## Data

```
race
  id (PK)
  name                  not null, default "Untitled race"
  date                  not null — local race date; the timeline orders by it
  location_name         nullable
  location_lat / location_lng   nullable
  notes                 nullable — plain text (Markdown: see backlog)
  is_public / published_at      see access-and-visibility.md
  race_category_id / auto_category_id / classification_*   see classification.md
  weather_temp_c / weather_humidity_pct / weather_conditions  see weather.md
  created_at / updated_at

official_result               1:1 with race, typed in by hand
  id (PK)
  race_id               FK, unique
  total_time            not null — summed from splits when not typed (below)
  total_is_derived      bool
  swim_time / t1_time / bike_time / t2_time / run_time   nullable
  overall_place / age_group_place                        nullable
  entered_at
```

## API

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/api/races` | owner | create a race (only `date` is needed; defaults to today) |
| `PATCH` | `/api/races/{id}` | owner | any subset of metadata fields |
| `DELETE` | `/api/races/{id}` | owner | delete the race with its activities, laps and fueling; its uploads go back to the tray |
| `PUT` | `/api/races/{id}/official-result` | owner | upsert |
| `GET` | `/api/races/{id}` | reader | full detail (see [race-detail](race-detail.md)) |

## UI

`admin/race-edit.html?id=`: sections for metadata, official result, fueling, gear, category
and visibility. Each field **autosaves on blur**; there is no submit button and no form-level
validation step. A quiet completeness checklist sits at the side.

## Rules

### Validation at the point of use, not the point of save

The database enforces only what is structurally necessary. Everything else is nullable
and checked by the feature that needs it.

| Table | Required |
|---|---|
| `race` | `date` (and `name`, which has a default) |
| `official_result` | `race_id`, `total_time` |
| `race_fueling` | `race_id`, `segment` |
| `recorded_activity` | `race_id`, `segment`, `start_time` |
| `gear` | `name`, `kind` |

`race.date` is required because the timeline needs it; import fills it in, and the manual
form defaults it to today, so it's forced to a value you can correct, not a blocker.

### Official total time

A result without a total isn't a result, so `total_time` stays NOT NULL. But the owner
should never have to *work it out*:

- **All five splits entered, no total typed** → the total is summed on write and
  `total_is_derived = true`.
- **Total and four splits entered** → the missing split is the remainder, also flagged.
- **Otherwise** → the save is refused with a message that names the gaps: *"Enter the total
  time, or fill in every split so it can be calculated. Still missing: T1, run."* The form
  shows the same message live, and shows the calculated total as soon as the last split is in.

A total the owner typed is **never** overwritten by derivation. The organiser's figure can
differ from the sum by chip-timing rounding, and it's the authoritative one.

This means three splits without a total can't be saved half-entered. That's the price of
a total that is always present and always trustworthy. It applies only to
`official_result`; the race, its fueling and its gear all stay partially saveable.

### Unknown is not zero

A missing value shows as "—", never as `0:00`. That's what makes partial data feel
normal instead of broken.

### Completeness

Every race read by the owner includes a computed `completeness` object, grouped by what
each missing piece unlocks:

```json
{
  "official_result": {"missing": ["t1_time", "age_group_place"], "usable": true},
  "fueling":         {"missing": ["run"], "usable": true},
  "classification":  {"missing": ["recorded_distances"], "usable": false},
  "comparison":      {"usable": true},
  "public":          {"missing": ["name"], "usable": true}
}
```

It's shown as a checklist, not a warning banner: a race with an unknown age-group place
isn't in an error state. The `public` group lists what a visitor would see as blank, so
publishing is an informed choice.

### Partial writes

Every write endpoint accepts any subset of fields. A field left out is left alone; an explicit `null` clears
it ([architecture](../architecture.md#conventions)). Per-field validation only rejects
invalid input (a negative time), never a missing one.

## Acceptance

- A race with only a date can be created and appears on the timeline as "Untitled race".
- Five splits and no total → total derived and flagged; a typed total survives later
  split edits.
- Three splits, no total → 422 with the missing splits named.
- PATCH with a field left out doesn't change it; PATCH with `null` clears it.
- Deleting a race sends its uploads back to the tray instead of deleting them.
