# Grouping and multiple recordings

**Priority:** core

## Goal

Not every race arrives as one multisport file. Strava has no multisport activity type,
and some watches write one file per leg. The owner picks which staged uploads belong
together, and the app builds a race from them. When two devices recorded the same leg,
both recordings are kept, and the owner chooses which one counts.

## Visibility

The tray and grouping actions are owner only. Visitors see only the primary recording of
each segment and never learn that others exist
([redaction](access-and-visibility.md#redaction)).

## Data

No tables of its own. It works on `upload` and `recorded_activity` ([import](import.md#data)),
using `is_primary`, `is_derived` and `upload.race_id`.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/uploads?status=parsed` | ungrouped tray plus proposed groupings |
| `POST` | `/api/races/group` | `{upload_ids, segments?}` → new race |
| `POST` | `/api/races/{id}/attach` | add uploads to an existing race (backfill a leg, add a second device) |
| `PATCH` | `/api/activities/{id}` | `{is_primary: true}`: switch the primary recording for that segment |

## UI

- **Grouping tray** (on `admin/import.html`): ungrouped uploads as rows (sport, start,
  distance, device). Proposed groups are highlighted; select uploads, then confirm.
- **Segment picker**: shown only when detection is ambiguous.
- **Recording switch** (on the race edit page): shown when a segment has more than one
  recording; pick the primary one.

## Rules

### Flow

```
  upload N files ─► parse ─► upload rows ─┬─ multisport   ─► auto-group ─► race
                                          └─ single-sport ─► tray
                                                              │ owner selects, confirms
                                                              ▼
                                                    assign segments ─► race
```

1. Files detected as multisport group themselves ([import](import.md#import-flow-multisport)).
2. Everything else lands in the tray.
3. The tray **proposes** groupings: uploads on the same local day, within a few hours of
   each other, in swim → bike → run order. Proposals are never committed automatically:
   a Sunday brick session looks exactly like a race.
4. Segments come from each upload's detected sport, ordered by start time. If that's
   ambiguous (two runs, or an `other`), the UI asks instead of guessing.

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

- Grouping three single-sport uploads gives 5 segments, and T1/T2 have `is_derived = true`.
- Overlapping or hours-apart legs → 422, and nothing is written.
- Attaching a second bike recording keeps the old primary; switching it moves the flag
  atomically and never leaves two primaries or none.
- A proposal is only ever a suggestion: `GET /uploads` doesn't write anything.
