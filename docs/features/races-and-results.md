# Races and results

**Priority:** core

## Goal

A race is the unit everything hangs off, and one page holds all of it: the race's files at
the top, with whether they are complete for its race type, and below that everything else
(name, date, location, official result, placings, notes, fueling, gear, photos). The owner
can create a race by hand, with no file, or from its files, and fill it in over time.
Nothing has to be complete to be saved: a race missing a file is saved and tagged
*incomplete*, and the owner can mark it complete when the file is lost for good. The
official result starts out filled in from the recordings, and the owner ticks
*Confirmed* once it's checked against the published results.

## Visibility

Visitors see public races: metadata, race type, official result and notes. An unconfirmed
result is labelled as such. The `completeness` checklist, the files, the incomplete tag
and the override are owner only ([redaction](access-and-visibility.md#redaction)).

## Data

```
race
  id (PK)
  name                  not null, default "Untitled race"
  date                  not null — local race date; the timeline orders by it
  location_name         nullable
  location_lat / location_lng   nullable
  notes                 nullable — the race report, in the Markdown subset below
  race_type             enum: triathlon, swim, bike, run — see race types; default triathlon
  marked_complete       bool — the owner's override: the files are as complete as they get
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
  is_prefilled          bool — taken from the recordings, follows them (below)
  is_confirmed          bool — the owner checked it against the published results
  entered_at
```

## API

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/api/races` | owner | create a race by hand (`date` defaults to today, `race_type` to triathlon) |
| `PATCH` | `/api/races/{id}` | owner | any subset of metadata fields, `race_type`, `marked_complete` |
| `DELETE` | `/api/races/{id}` | owner | delete the race with its uploads (and stored files), activities, laps, fueling, photos |
| `PUT` | `/api/races/{id}/official-result` | owner | upsert; `is_confirmed` ticks the checkbox |
| `GET` | `/api/races/{id}` | reader | full detail (see [race-detail](race-detail.md)) |

## UI

`admin/race-edit.html?id=` (owner only, so it needs no link preview), one page per race:

1. **Files**, at the top: the race type, one line per leg of that type (recorded with
   its device, derived from the gap, or missing), a *Complete* / *Incomplete* badge with
   the *Mark as complete anyway* checkbox, the drop zone ([import](import.md#ui)), the
   race's files with the legs each fills (and a leg picker or *Remove*), and the primary
   recording switch ([grouping](grouping.md#ui)).
2. A quiet completeness checklist.
3. Sections for metadata, official result (with *Confirmed*), visibility, category
   (triathlons only), weather, fueling, gear and photos.

Each field **autosaves on blur**; there is no submit button and no form-level validation
step. Without an `id` the page is the new-race page: a race type (or *Detect from the
files*), the drop zone, and *Create the race without files*. The navbar links it as
*New race*; the timeline shows an owner-only *incomplete* marker on incomplete races.

## Rules

### Race types

| Type | Segments | Complete when |
|---|---|---|
| triathlon | swim, T1, bike, T2, run | every segment has a primary recording (a derived transition counts) |
| swim / bike / run | that one | it has a recording |

There are no partial or repeated-leg types (aquathlon, duathlon): a triathlon missing a
leg is an incomplete triathlon. The type decides which files fit
([import](import.md#which-race-a-file-goes-to)), which official splits exist, which
fueling segments are offered, and whether the race is classified (only triathlons: the
categories are triathlon distances) and what it can be compared with (same type only).
Changing the type is refused while a recording doesn't fit the new one (*"This race has
swim, bike recordings, which a run race doesn't have. Remove those files first."*).

**Incomplete** = a segment of the type has no recording and the owner hasn't set
`marked_complete`. It's owner-only (on the race page, the timeline card and in
completeness), never an error state, and never blocks saving.

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

A **single-sport race has one split, and it is the total**: the form asks for the total
only, and the split is kept equal to it.

This means three splits without a total can't be saved half-entered. That's the price of
a total that is always present and always trustworthy. It applies only to
`official_result`; the race, its fueling and its gear all stay partially saveable.

### Prefilled results

So the owner never types what the watch already knows, a race's official result starts
out **prefilled from its primary recordings**: each split is that segment's
`elapsed_time`; the total is their sum when every segment is there, else the clock time
from the first recorded start to the last recorded finish (a lost middle leg is still
inside it). Missing splits stay null.

- It **follows the recordings** (`is_prefilled = true`): a dropped or removed file, a
  primary switch or a type change recomputes it. Removing the last file removes it.
- **Any owner write ends that**: editing a value or ticking *Confirmed* sets
  `is_prefilled = false`, and from then on it is an ordinary typed result. Only a
  changed value saves, so tabbing through the form doesn't end it by accident.
- **Confirmed** (`is_confirmed`) is the owner's word that the result matches the
  published results. Until then visitors see the official times marked *unconfirmed*,
  "taken from the recording" while prefilled, and the checklist lists `confirmation`.
- Results typed before prefilling existed were confirmed by migration 0004; races that had
  none got a prefilled one.

### Unknown is not zero

A missing value shows as "—", never as `0:00`. That's what makes partial data feel
normal instead of broken.

### Completeness

Every race read by the owner includes a computed `completeness` object, grouped by what
each missing piece unlocks:

```json
{
  "recordings":      {"missing": ["run"], "usable": false},
  "official_result": {"missing": ["t1_time", "age_group_place", "confirmation"], "usable": true},
  "fueling":         {"missing": ["run"], "usable": true},
  "classification":  {"missing": ["recorded_distances"], "usable": false},
  "comparison":      {"usable": true},
  "public":          {"missing": ["name"], "usable": true}
}
```

The groups follow the race type: `classification` only for a triathlon, `fueling` only
where fuel is logged (not a swim race). `recordings.usable` is false while the race is
incomplete.

It's shown as a checklist, not a warning banner: a race with an unknown age-group place
isn't in an error state. The `public` group lists what a visitor would see as blank, so
publishing is an informed choice.

### Race reports

`race.notes` is the race report. On a public site visitors read it, so it is stored as
Markdown and rendered to HTML on read (`notes_html` on the race detail, and the Atom
feed's `content`). A note shown to visitors is a stored-XSS vector, so the renderer is a
deliberately tiny hand-written subset (`services/markdown.py`) rather than a library plus
a sanitiser:

| Markdown | HTML |
|---|---|
| `# ` / `## ` / `### ` heading | `h3` / `h4` / `h5` (the page owns `h1`/`h2`) |
| `- item`, `* item` / `1. item` | `ul` / `ol` |
| `> quote` | `blockquote` |
| `**bold**`, `*italic*` / `_italic_`, `` `code` `` | `strong`, `em`, `code` |
| `[text](url)` | `a href rel="nofollow noopener"` |
| blank line / single newline | new paragraph / `br` |

- **Escape first.** Every character of the input is HTML-escaped before any markup is
  applied, and the only tags in the output are the ones in the table. There is no raw
  HTML pass-through.
- **Links** keep `http(s)://`, `mailto:`, same-site `/path` and `#anchor` targets only.
  Any other link (`javascript:`, `data:`, protocol-relative `//host`) stays as its
  literal text.
- Anything the subset doesn't know is shown as typed. `notes` itself is returned
  unchanged, so the edit form round-trips exactly.

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
- Deleting a race deletes its uploads and their stored files; the same file can make a
  race again.
- An import prefills an unconfirmed result; a primary switch updates it; an edit or
  *Confirmed* stops it following; removing the only file removes it.
- A run race's result is its total, mirrored into `run_time`; the timeline uses it.
- A type change with recordings that don't fit → 422; a typed result is reshaped (a
  single-sport split is set to the total, a triathlon's splits cleared).
- A race missing legs is incomplete for the owner until `marked_complete`; visitors never
  see the tag, the files or the override.
- `notes_html` only ever contains the subset's tags, with `href`/`rel` as the only
  attributes; script, event-handler and `javascript:` inputs come out as text.
