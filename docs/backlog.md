# Backlog

Ideas and deferred work, tagged with the feature they would extend. Nothing here is
scheduled. Promoting an item means moving it into the feature file (Rules, Data, API) and
onto the [roadmap](roadmap.md).

## Public site

- **Course thumbnails as preview images** (architecture). Races with a cover photo already
  get it as `og:image`; the rest have none. Rendering a map server-side needs a new
  backend dependency, which has to justify itself.
- **Public HR toggle, GPS privacy trim, unlisted races** (access-and-visibility, open questions).

## Analysis

- **Comparing more than two races** (comparison). Needs a categorical palette; the fill goes.
- **Cycling dynamics** (import). L/R balance, torque effectiveness and pedal smoothness
  are in the session's `raw_data` but not modelled. Cheap to promote, like
  [time in zone](features/import.md#time-in-zone) was.
- **HR and power trends** (trends). Average HR or power per sport over time; the
  `/api/trends` rows already carry both.

## Data entry

- **Product catalogue** (fueling). A `product` table with a picker, so "Maurten vs SIS"
  becomes answerable. Costs a catalogue to maintain; wait until the free-text data shows
  it's worth it.
- **Gear components** (gear). Chains, cassettes and tyres as sub-items with their own mileage and
  service intervals. Needs a parent/child tree and a swap history.
- **Other category sets** (classification). World Triathlon, Ironman naming.
- **Strava** ([strava](features/strava.md)).

## Platform

- **PostGIS** (import). Only if spatial queries ("races whose bike course passes near X")
  become real.
