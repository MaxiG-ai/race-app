# Race detail

**Priority:** core

## Goal

One race in full: official vs recorded splits side by side, the course on a map, pacing
per lap, plus weather, fueling, gear and notes. For a public site, this is the page people
share a link to.

## Visibility

Owner: everything, plus an "Edit" link and the recording switch for segments with several
recordings. Visitors: the public shape
([redaction](access-and-visibility.md#redaction)), meaning primary recordings only, a
simplified track, and no device details.

## Data

None of its own.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/races/{id}` | race, official result, primary recorded activities (no tracks), fueling, gear names, weather, category; plus `completeness` and all recordings for the owner |
| `GET` | `/api/races/{id}/track` | one polyline per segment, Douglas–Peucker simplified (`?precision=`); full resolution for the owner only |
| `GET` | `/api/races/{id}/laps` | lap splits, `?sport=` filter |

**Payload:** ~9 000 GPS points × 5 segments is several MB of JSON if served naively.
`gps_track` is left out of every response except `/track`, which simplifies it to a few
hundred points per segment (`services/geo.py`).

## UI

`/races/{id}` (served through the page shell, so a shared link gets a preview; see
[architecture](../architecture.md#link-previews)), top to bottom:

1. **Header**: name, date, location, race-type badge (single-sport races), category
   badge, total time, segment bar.
2. **Splits comparison**: a table with rows swim, T1, bike, T2, run, total (a
   single-sport race: its one row); columns official, recorded, Δ. Derived transitions
   are annotated. Unknown values show as "—". An unconfirmed official result is marked
   *unconfirmed*, with a note when it is still taken from the recording
   ([races-and-results](races-and-results.md#prefilled-results)).
3. **Course map**: Leaflet with OSM tiles, one polyline per segment in the sport colours,
   fitted to the session bounding boxes.
4. **Pacing chart**: Chart.js line chart, one sport at a time. Which series exist depends on
   what was recorded, not on the sport ([Trap 2](../reference/fit-files.md#trap-2),
   [Trap 7](../reference/fit-files.md#trap-7)): a run may have pace/HR/power, a bike
   6–8 coarse 5 km points, and a one-lap swim is a summary tile rather than a line.
   Below it, **time in zones** (HR, and power where recorded) when the device wrote them
   ([import](import.md#time-in-zone)): a table with a neutral bar per zone, the time and
   the share as text.
5. **Conditions & fueling**: weather, carbs per segment, products, gear names.
6. **Race report**: `notes_html`, the Markdown subset rendered by the backend
   ([race reports](races-and-results.md#race-reports)).
7. **Photos**: the gallery, when there are any ([photos](photos.md#ui)).

## Rules

- Series are drawn only where data exists. A missing series is left out, never drawn
  as zero.
- Each chart has a table fallback (a `<details>` with the same numbers), so nothing
  depends on colour or pointer hover alone.
- The map degrades to "No recorded track" for manually entered races. The rest of the page
  still renders.
- Official and recorded disagree on purpose: transitions are poorly recorded
  ([Trap 6](../reference/fit-files.md#trap-6)), so the Δ column is information, not an error.

## Acceptance

- A manual-only race (no recordings) renders header, splits (recorded column "—") and
  notes without errors.
- The Apple fixture's swim renders as a summary tile; the Garmin fixture's swim renders as
  a 4-point line.
- The Garmin fixture's run shows its HR zones; the Apple fixture shows no zone table.
- `/track` for a visitor is always simplified, whatever `precision` is requested.
- `/races/{id}` for a public race has its name in `og:title`; a hidden race and a
  nonexistent id return byte-identical generic shells.
