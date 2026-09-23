# Classification

**Priority:** next

## Goal

A race's category (Sprint, Kurzdistanz, …) is **worked out from the measured distances**
instead of being picked by hand. The owner only overrides it when the inference is wrong,
and the inferred value is kept next to the override, so a disagreement stays visible.

## Visibility

Visitors see the resolved category as a badge. How it was resolved (auto or manual) and
why a race is unclassified are shown to the owner only.

## Data

```
race_category                 seeded; editable as data, so a boundary fix needs no deploy
  id (PK)
  key            slug: sprint, kurz, mittel, lang, ...
  label          display name
  kind           enum: distance, age_class, relay — only `distance` auto-classifies
  sort_order
  swim_min_m / swim_max_m     null bound = open-ended
  bike_min_m / bike_max_m
  run_min_m  / run_max_m
  tolerance_pct  default 10 — applied outward to both bounds at match time
  is_active
```

On `race`:

```
race_category_id       FK nullable — the assigned category
auto_category_id       FK nullable — what the classifier chose, kept even when overridden
classification_source  enum: auto, manual, unclassified
classified_at          nullable
classification_detail  jsonb nullable — nearest category and the furthest-out discipline
```

Seed: the [DTU table](../reference/dtu-categories.md), with the published ranges exactly as
written. The tolerance is a visible, adjustable column, not a fudge factor hidden in the ranges.

## API

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/api/categories` | reader | the table, for pickers and badges |
| `POST` | `/api/races/{id}/classify` | owner | re-run; updates `auto_category_id`, and `race_category_id` only if the source is `auto` |
| `PATCH` | `/api/races/{id}` | owner | `{race_category_id}` sets a manual override; `null` clears it and re-runs |

## UI

The category field on the race edit page shows the resolved category, one line explaining
where it came from, and a picker:

- auto → "Calculated from the measured distances."
- manual → "Set by you." and, when the inference disagrees, "The measured distances
  suggest Kurzdistanz." That disagreement is usually the first sign that a *distance* is
  wrong, not the category.
- unclassified → the reason: "No band matched. Closest is Kurzdistanz, but the bike is
  15.6 % short of it."

An overridden race gets a "Use the calculated category" link that clears the override.

## Rules

1. Take the **primary** recording per segment and read `distance_m` for swim, bike and
   run. Transitions are ignored.
2. Only `kind = distance` categories can match. A category matches when all three
   distances fall inside its bounds, each widened by `tolerance_pct`.
3. One or more matches → pick the **narrowest**, measured by summed range width.
   Overlaps are deliberate: Supersprint sits inside Sprint, and the most specific band
   that contains the race is the right answer.
4. No match → `unclassified`, and record the nearest category and which discipline was
   furthest out.
5. Runs on import and whenever a segment's primary recording changes: switching the bike
   from a watch (33.7 km) to an Edge (40.5 km) can legitimately change the category.
6. A manual override is **never** silently replaced. Re-classifying updates
   `auto_category_id` only.
7. No recorded distances (a manually entered race) → stays `unclassified` until the owner
   picks a category by hand.

Age classes and relays can only be picked by hand: the course can't tell you how old the field
was.

## Open questions

- **Other category sets.** DTU labels are German, and other athletes race under World
  Triathlon or Ironman naming. Options: ship several seed files and choose one at setup,
  or ship DTU and let the table be edited. A `category_set` column would allow both.

## Acceptance

The four measured races in [dtu-categories](../reference/dtu-categories.md#measured-races-against-this-table-10--tolerance)
classify as listed there: two Kurzdistanz, one Sprintdistanz, one unclassified with
"bike −15.6 %". A manual override survives re-classification; clearing it restores the
auto result.
