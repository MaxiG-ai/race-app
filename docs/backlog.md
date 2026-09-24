# Backlog

Ideas and deferred work, tagged with the feature they would extend. Nothing here is
scheduled. Promoting an item means moving it into the feature file (Rules, Data, API) and
onto the [roadmap](roadmap.md).

## Public site

- **Race photos** (race-detail, timeline). Upload photos per race, and show one on the timeline
  card *instead of* the course map when it exists. The map is the better default for a race
  you're analysing; a photo is better for a race you're remembering. Needs a `race_photo`
  table, storage on the uploads volume, thumbnails, **EXIF stripping** (GPS in the photo
  metadata, which matters on a public site) and backup sizing.
- **Preview images** (architecture). An `og:image` per race, e.g. a rendered course
  thumbnail or the race photo. Rendering a map server-side needs a new backend
  dependency, which has to justify itself.
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

- **Dark mode** (design). One `prefers-color-scheme` block once the tokens are stable; the
  palette needs re-checking on a dark surface.
- **PostGIS** (import). Only if spatial queries ("races whose bike course passes near X")
  become real.
