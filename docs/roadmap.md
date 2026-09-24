# Roadmap — the rewrite

Build order for the rewrite. Each phase ends with something that runs end to end. The
feature files hold the detail; this page only sequences them.

## Phase 0 — a publishable starting point

- [x] Synthetic FIT fixtures + the `fit-tool` generator script
      ([fit-files](reference/fit-files.md#testing-without-the-originals)).
- [x] Move the real recordings out of the repository into your own
      `tests/fixtures/private/` (gitignored).
- [x] ~~Clean history.~~ Dropped: the real recordings and v1's `PLAN.md` stay in git
      history, and the owner is fine publishing them. Only the working tree has to be
      free of real recordings.
- [x] `LICENSE` check, `README` for self-hosters.

## Phase 1 — foundation (core)

- [x] SQLAlchemy models + initial Alembic migration; the tables from [data-model](data-model.md).
- [x] [access-and-visibility](features/access-and-visibility.md): owner CLI, sessions,
      `SITE_MODE`, the write-router guard, `visible_races`, and the enumerating access tests.
- [x] App serves `/static` and `/api` from one process; `docker compose up` works
      ([deployment](operations/deployment.md#local)).
- [x] Frontend skeleton: `tokens.css`, `api.js`, `format.js`, `charts.js` defaults,
      vendored Leaflet and Chart.js, the login page, the owner bar.
- [x] Page shells with Open Graph tags ([architecture](architecture.md#link-previews)),
      visibility-aware from the start.

**Done when** a private instance starts from scratch with one command and you can log in.

## Phase 2 — getting data in (core)

- [x] [import](features/import.md): parser against every synthetic fixture, the traps
      asserted, originals stored, `cli reimport`.
- [x] [grouping](features/grouping.md): tray, proposals, derived transitions, primary switch.
- [x] Check against your private real files (the parser reproduces every figure in
      fit-files.md on the five v1 recordings).

## Phase 3 — looking at races (core)

- [x] [timeline](features/timeline.md): spine, cards, segment bar, stat strip.
- [x] [race-detail](features/race-detail.md): splits table, Leaflet map, pacing chart.
- [x] [races-and-results](features/races-and-results.md): edit page, autosave, official
      total derivation, completeness.

**Done when** a public instance can be deployed and a visitor can browse published races.
That's the first release worth putting on a domain.

## Phase 4 — the rest of the race (next)

- [x] [classification](features/classification.md)
- [x] [weather](features/weather.md)
- [x] [fueling](features/fueling.md)
- [x] [gear](features/gear.md)

## Phase 5 — analysis (later)

- [x] [comparison](features/comparison.md): `progress_series`, alignment, gap chart, diffs.

## Phase 6 — the public site, polished

- [x] Race reports in Markdown: a tiny hand-written, escape-first subset
      ([races-and-results](features/races-and-results.md#race-reports)).
- [x] Atom feed of public races at `/feed.xml` ([timeline](features/timeline.md#feed)).
- [x] CI: lint and tests on pull requests, plus the mechanical convention checks
      ([deployment](operations/deployment.md#ci)).

## Phase 7 — analysis across races

- [x] [trends](features/trends.md): pacing per sport over time, weather against
      performance.
- [x] Garmin `time_in_zone` promoted from the FIT file into the model and shown on the
      race page ([import](features/import.md), [race-detail](features/race-detail.md)).

## Phase 8 — dark mode

- [x] One `prefers-color-scheme` token block, the palette re-checked on the dark surface
      ([visual-system](design/visual-system.md#theme)).

## Phase 9 — race photos

- [ ] [photos](features/photos.md): upload, EXIF stripping, thumbnails, the cover photo
      on the timeline card and as the link preview's `og:image`.

## After that

[backlog](backlog.md), [strava](features/strava.md).

## What v1 got right, and carries over

v1 (the React/FastAPI version) validated most of the domain rules this spec keeps:
session totals over lap sums, half-open clamped bucketing, device-dependent power,
per-segment primary recordings, derived transitions, serial-based gear detection,
tolerant narrowest-band classification, and derived official totals. What's new in the
rewrite is the product shape (single owner, public/private) and the stack, not the
domain logic.
