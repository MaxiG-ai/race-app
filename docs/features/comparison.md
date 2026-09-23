# Comparison

**Priority:** later

## Goal

Two races of the same category side by side, at `/compare?a=<id>&b=<id>`, reachable
from a "Compare" action on any race. The centrepiece is a gap chart: one race is pinned
flat at zero as the reference, and the other is drawn as its time gap from it at each
checkpoint.

## Visibility

Available to visitors when **both** races are public; if either is hidden, it's a 404
([access](access-and-visibility.md#enforcement-lives-in-two-places)). The picker only
offers races the viewer can see.

## Data

`recorded_activity.progress_series` ([import](import.md#data)): ~200 points of
`{progress, elapsed_s, distance_m}`, resampled at import so the browser never has to
align two 9 000-point streams.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/races/{a}/compare/{b}` | both aligned checkpoint series, per-segment deltas, conditions diff, fueling diff, in one request |
| `GET` | `/api/races/{id}/comparable` | races eligible to compare with this one (same category) |

Alignment happens on the server (`services/comparison.py`); the page only draws.

## UI

```
┌──────────────────────────────────────────────────────────────────────┐
│  Race A                     2026-08-09  │  Race B       2026-07-11   │
│  2:36:50                                │  2:43:46        +6:56      │
├──────────────────────────────────────────────────────────────────────┤
│  Δ  swim +2:13 │ T1 −1:09 │ bike −3:29 │ T2 −1:52 │ run −2:39        │
├──────────────────────────────────────────────────────────────────────┤
│  GAP TO REFERENCE                       (gap chart, below)           │
├──────────────────────────────────────────────────────────────────────┤
│  CONDITIONS & COURSE                                                 │
│  ⚠ bike 40.6 km vs 33.7 km (+20 %)   ⚠ +22 m vs +558 m elevation     │
├──────────────────────────────────────────────────────────────────────┤
│  FUELING                                                             │
│  bike  90 g · 78 g/h   vs   60 g · 49 g/h        +30 g               │
└──────────────────────────────────────────────────────────────────────┘
```

## Rules

### What can be compared

- Only races in the **same category** ([classification](classification.md)). Other
  categories are left out of the picker, not warned about afterwards.
- Distances still differ within a category (33.7 km vs 40.6 km bikes). That doesn't block
  the comparison; the conditions panel says so prominently.
- Unclassified races are comparable when every segment distance is within ±10 % of the
  other race's.

### The gap chart

Drawn with Chart.js ([visual-system](../design/visual-system.md#charts)). The form
follows TriRating's progression charts: **one panel**, with the reference race flat at
zero.

- **Y axis:** time behind the reference, **increasing downward** (`reverse: true` on the
  y scale). Falling away means losing time, which is how the sport talks about it.
- **X axis:** discrete checkpoints, not continuous distance.
  - Segment boundaries are always checkpoints: Start, Swim, T1, T2, Finish. They exist
    even for a race entered only as official splits.
  - Intra-segment checkpoints come from laps where both races have them (5 km bike, 1 km run).
  - When both races' segment distances agree within 5 %, checkpoints sit at absolute
    distances (`20k`, `40k`). When they don't, they fall back to even fractions of the
    segment, labelled with the reference race's distances and marked as normalised.
- **Straight lines between checkpoints** (`tension: 0`). No smoothing: a curve would
  imply per-second knowledge the checkpoint data doesn't have. Checkpoints are a
  `linear` x scale at their cumulative position, with tick labels set per checkpoint, so
  uneven spacing stays honest. A `category` scale would space them evenly.
- **Fill:** diverging colours: teal where the compared race was faster, brick where it was
  slower, neutral grey zero line ([visual-system](../design/visual-system.md#colour)).
  Chart.js does this natively with `fill: {target: 'origin', above, below}`. Never
  red/green. Segment boundaries are vertical rules with text labels, drawn by a small
  inline plugin in `charts.js`, since hue is already taken by the fill.
- **Invariant:** the gap at each segment boundary equals the cumulative split delta. The Δ
  strip is this curve sampled at five points; if they ever disagree, one of them is wrong.
- **Degradation:** a race with no recorded stream renders from official splits alone
  (five checkpoints, visibly coarser, with a note).
- **Accessibility:** the zero line is labelled with the reference race's name, and the other
  line is labelled at its right end with the finishing gap, so no legend box is needed. There's a
  crosshair tooltip, a checkpoint table fallback, and a control to swap which race is the reference.
- **More than two races** would need a categorical palette and no fill. That's out of scope,
  but don't build the chart in a way that rules it out.

### Fueling comparison

Per segment: absolute carbs and the delta; **g/h** (the number that carries across race
lengths); and products side by side, with items unique to one race marked. Unknown is "—".
The panel only presents numbers: no correlation between carbs and pace. Two races are
not a sample.

### Conditions and course

List only the settings that differ, with material differences promoted and marked:
distance ±5 %, elevation ±50 m, temperature ±5 °C, humidity ±15 pp, a change of gear, or
a change of primary recording device (device changes are shown to the owner only). The
reader wants to know what wasn't like-for-like; a full spec table buries that.

## Acceptance

- Different categories → excluded from `/comparable`; `/compare` → 422.
- Boundary gaps equal the cumulative split deltas (invariant test).
- Official-only on one side still renders five checkpoints.
- Anonymous visitor with one hidden race → 404.
