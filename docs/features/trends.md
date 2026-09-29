# Trends

**Priority:** later

## Goal

See how racing has gone across seasons, not one race at a time: am I getting faster on
the run, and do I race worse in the heat? Every race is one point; the page answers both
questions for each sport.

## Visibility

Owner: every race. Visitors: public races only, through `visible_races`
([access](access-and-visibility.md#enforcement-lives-in-two-places)). Every field is one
a visitor already sees on the race detail; no new data becomes public.

## Data

None of its own. Reads `race` (date, category, weather) and the primary
`recorded_activity` totals.

## API

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/api/trends` | reader | one row per visible race, oldest first: id, name, date, category, `temp_c`, `humidity_pct`, and per sport (swim, bike, run) the primary recording's `distance_m`, `elapsed_time`, `avg_hr`, `avg_power`, or null. No tracks, no laps. |

## UI

`/trends` (served through the page shell; linked from the site header on every page):

1. **Category filter**: all races, or one category. Courses differ, so pace across a
   sprint and a middle distance isn't comparable; the filter is how you compare like with
   like.
2. **Pace over time**: one chart per sport (small multiples, each in its own sport
   colour), x = race date on a linear axis so uneven gaps between races stay honest.
   Swim /100 m and run /km are drawn with faster up; bike is km/h.
3. **Temperature and pace**: a scatter of start temperature against pace, one sport at a
   time (tabs). Races without weather are left out and counted below the chart.
4. **Table fallback**: every number from both charts in one `<details>` table.

Charts are `xyChart` in `charts.js` ([visual-system](../design/visual-system.md#charts)).

## Rules

- **Pace is derived on read** from the recorded distance and elapsed time of the same
  primary recording, never from the official split (whose distance isn't known).
- A race without a recorded distance for a sport is not a point for that sport. It's
  not zero, and it's not interpolated.
- A chart needs at least two points; below that the page says why instead of drawing
  a single dot.
- The temperature is the race's weather ([weather](weather.md)), fetched for the start
  hour. It's a start-line figure, which the axis title says.

## Open questions

- Normalising pace by course (e.g. against the category's nominal distance) would make
  all races comparable, but a short course would then look fast. Not done.
- HR and power over time: the rows carry them; no chart is specified yet (backlog).

## Acceptance

- Rows are oldest first; a race with no recordings has every sport null.
- Only primary recordings count.
- A visitor gets public races only, and each row has exactly the public `TrendRace` keys.
