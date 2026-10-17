# Grouping and multiple recordings

**Priority:** core

## Goal

Not every race arrives as one multisport file. Strava has no multisport activity type,
and some watches write one file per leg. The owner drops a race's files onto its race
page, together or one at a time, and each file fills the leg its sport says. When two
devices recorded the same leg, both recordings are kept, and the owner chooses which one
counts.

## Visibility

Files, the leg picker and the recording switch are owner only. Visitors see only the
primary recording of each segment and never learn that others exist
([redaction](access-and-visibility.md#redaction)).

## Data

No tables of its own. It works on `upload` and `recorded_activity` ([import](import.md#data)),
using `is_primary`, `is_derived` and `upload.race_id`.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/races/import`, `/api/races/{id}/uploads` | files onto a new or existing race ([import](import.md#api)) |
| `POST` | `/api/races/{id}/attach` | `{segments: {upload_id: segment}}`: file uploads that wait for their leg |
| `DELETE` | `/api/uploads/{id}` | take a file and its recordings off the race |
| `PATCH` | `/api/activities/{id}` | `{is_primary: true}`: switch the primary recording for that segment |

## UI

All on the race page's Files section ([races-and-results](races-and-results.md#ui)):

- **Leg picker**: on a file whose sport is unknown, in a triathlon. Picks one of the
  race type's segments.
- **Recording switch**: shown when a segment has more than one recording; pick the
  primary one.
- **Remove**: per file; the race keeps everything else.

## Rules

### Flow

```
  drop files on a race ─► parse ─┬─ multisport    ─► every leg
                                 ├─ swim/bike/run ─► that leg (a 2nd file: a 2nd device)
                                 └─ other         ─► owner picks the leg (a single-sport
                                                     race has only one: no question)
```

1. Files detected as multisport fill every leg ([import](import.md#import-flow-multisport)).
2. A single-sport file fills the leg of its detected sport. A second file of the same
   sport is a second device on that leg, not another leg: there are no repeated legs.
3. A file that doesn't say its sport waits on the race (`status = parsed`) for the
   owner to pick its leg, instead of being guessed.
4. A file must fit the race: its sport must be a leg of the race type, and it must be
   recorded within hours of the race's other files
   ([import](import.md#which-race-a-file-goes-to)). Grouping is never inferred across
   races: a Sunday brick session looks exactly like a race, so the owner always says
   which race a file belongs to by dropping it there.

### Derived transitions

Three single-sport files contain no T1/T2. Grouping creates them from the gaps:
`t1.elapsed_time = bike.start_time − swim.end_time`, with `is_derived = true`. They
carry a duration only: no distance, no track, no HR. The segment bar draws them hatched
and the splits table annotates them, so they are never mistaken for measurements.
Without them, the recorded total wouldn't match the official total, and that mismatch
would look like a bug.

A negative gap (overlapping activities) or one that's implausibly large (legs hours apart) means
the grouping is wrong. The API rejects it instead of writing a nonsense transition.

### Multiple recordings of one segment

The same leg can be recorded by two devices at once, e.g. a watch and a bike computer
([fit-files](../reference/fit-files.md#strava-exports)). Each gets its own
`recorded_activity` row; exactly one per segment is `is_primary` (enforced by a partial
unique index).

- **Default primary:** the richest recording: has power > has none, then the longer duration.
- **Assembly is per segment, never per metric.** Every number shown for a leg comes from
  its primary recording. Mixing fields across devices (Edge power with watch distance)
  would leave two distances claiming to be *the* bike distance, and every series would
  need a footnote about which device it came from.
- Switching the primary re-runs [classification](classification.md#rules). Different
  devices can legitimately measure different distances.

## Acceptance

- Dropping three single-sport files gives 5 segments, and T1/T2 have `is_derived = true`;
  legs dropped one at a time give the same race.
- An overlapping or hours-apart leg is refused and nothing of it is written; the other
  legs are kept.
- A file of unknown sport waits for its leg; a leg outside the race type → 422.
- Removing a file removes its recordings and the transition derived from them.
- Attaching a second bike recording keeps the old primary; switching it moves the flag
  atomically and never leaves two primaries or none.
