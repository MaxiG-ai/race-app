# Fueling

**Priority:** next

## Goal

Record what was eaten and drunk during a race, per segment: grams of carbs, which
products, and a note on how it went. FIT files contain nothing about nutrition, so this
is always entered by hand.

## Visibility

Public on public races.

## Data

```
race_fueling                  one row per fuelled segment
  id (PK)
  race_id       FK
  segment       enum: swim, t1, bike, t2, run — bike and run are offered by default,
                but any segment can be used ("gel in T2")
  carbs_g       nullable
  products      jsonb — [{name, quantity}], e.g. [{"name": "Maurten 160", "quantity": 2}]
  notes         nullable — how it sat, what ran out, what to change
  created_at / updated_at
  unique (race_id, segment)
```

It hangs off `race`, not `recorded_activity`, because fueling can be recorded for a race
with no import at all. Total carbs is summed on read, not stored.

## API

| Method | Path | Access | Purpose |
|---|---|---|---|
| `PUT` | `/api/races/{id}/fueling` | owner | upsert all segment rows at once |

Read as part of `GET /api/races/{id}`.

## UI

The fueling section of the race edit page: per segment, a carbs field, a product list
(add or remove rows) and a notes field. The detail page shows carbs and **g/h**, derived
from the segment's elapsed time.

## Rules

- `products` stays free-form jsonb. v1 needs to *record* what was used; a product
  catalogue with a picker is in the [backlog](../backlog.md).
- g/h is derived from the primary recording's elapsed time, or from the official split
  when there's no recording. If neither exists, show "—".
- No row means **unknown**, not "took nothing": show "—", never `0 g`.

## Acceptance

- `PUT` with bike and run rows, then with only bike: the run row is removed (PUT replaces
  the whole set).
- g/h shows "—" when the segment has no time.
