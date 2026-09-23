# Backlog

Ideas and deferred work, tagged with the feature they would extend. Nothing here is
scheduled. Promoting an item means moving it into the feature file (Rules, Data, API) and
onto the [roadmap](roadmap.md).

## Public site

- **Race reports in Markdown** (races-and-results). `race.notes` is plain text. On a public
  site it becomes the race report, so formatting (headings, lists, links) matters more
  now. Store Markdown and render a known-safe subset. The sanitiser is the design
  decision: a note shown to visitors is a stored-XSS vector otherwise. It needs either a
  small, vetted Markdown library or a deliberately tiny hand-written subset.
- **Race photos** (race-detail, timeline). Upload photos per race, and show one on the timeline
  card *instead of* the course map when it exists. The map is the better default for a race
  you're analysing; a photo is better for a race you're remembering. Needs a `race_photo`
  table, storage on the uploads volume, thumbnails, **EXIF stripping** (GPS in the photo
  metadata, which matters on a public site) and backup sizing.
- **Link previews** (architecture). Open Graph tags per race page, see the architecture
  open question on server-rendered shells.
- **Public HR toggle, GPS privacy trim, unlisted races** (access-and-visibility, open questions).
- **RSS/Atom feed** of public races.

## Analysis

- **Cross-race trends** (timeline). Pacing per sport over time, and weather against
  performance. The schema supports both; no UI is specified.
- **Comparing more than two races** (comparison). Needs a categorical palette; the fill goes.
- **Garmin `time_in_zone` and cycling dynamics** (import). L/R balance, torque
  effectiveness and HR zones are parsed into `raw_data` but not modelled. They're cheap to
  promote.

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
- **CI** (operations). Tests and lint on pull requests once the repository is public.
