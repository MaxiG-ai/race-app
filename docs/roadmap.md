# Roadmap — the rewrite

Build order for the rewrite. Each phase ends with something that runs end to end. The
feature files hold the detail; this page only sequences them.

## Phase 0 — a publishable starting point

- [ ] Settle the open questions in [architecture](architecture.md#open-questions)
      (framework, SQL layer, server-rendered shells). They shape every later phase.
- [ ] Synthetic FIT fixtures + generator ([fit-files](reference/fit-files.md#testing-without-the-originals)),
      and settle the in-repo encoder vs `fit-tool` question.
- [ ] Move the real recordings out of the repository into your own
      `tests/fixtures/private/` (gitignored).
- [ ] **Clean history.** Deleting the fixtures in a later commit isn't enough: the real
      files, and v1's `PLAN.md` with sensor serials, stay in git history. Start the public
      repository from a fresh root commit (or an orphan branch) rather than publishing
      this history.
- [ ] `LICENSE` check, `README` for self-hosters.

## Phase 1 — foundation (core)

- [ ] Schema + migration runner; the tables from [data-model](data-model.md).
- [ ] [access-and-visibility](features/access-and-visibility.md): owner CLI, sessions,
      `SITE_MODE`, the write-router guard, `visible_races`, and the enumerating access tests.
- [ ] App serves `/static` and `/api` from one process; `docker compose up` works
      ([deployment](operations/deployment.md#local)).
- [ ] Frontend skeleton: `tokens.css`, `api.js`, `format.js`, the login page, the owner bar.

**Done when** a private instance starts from scratch with one command and you can log in.

## Phase 2 — getting data in (core)

- [ ] [import](features/import.md): parser against every synthetic fixture, the traps
      asserted, originals stored, `cli reimport`.
- [ ] [grouping](features/grouping.md): tray, proposals, derived transitions, primary switch.
- [ ] Check against your private real files.

## Phase 3 — looking at races (core)

- [ ] [timeline](features/timeline.md): spine, cards, segment bar, stat strip.
- [ ] [race-detail](features/race-detail.md): splits table, Leaflet map, SVG pacing chart.
- [ ] [races-and-results](features/races-and-results.md): edit page, autosave, official
      total derivation, completeness.

**Done when** a public instance can be deployed and a visitor can browse published races.
That's the first release worth putting on a domain.

## Phase 4 — the rest of the race (next)

- [ ] [classification](features/classification.md)
- [ ] [weather](features/weather.md)
- [ ] [fueling](features/fueling.md)
- [ ] [gear](features/gear.md)

## Phase 5 — analysis (later)

- [ ] [comparison](features/comparison.md): `progress_series`, alignment, gap chart, diffs.

## After that

[backlog](backlog.md), [strava](features/strava.md).

## What v1 got right, and carries over

v1 (the React/FastAPI version) validated most of the domain rules this spec keeps:
session totals over lap sums, half-open clamped bucketing, device-dependent power,
per-segment primary recordings, derived transitions, serial-based gear detection,
tolerant narrowest-band classification, and derived official totals. What's new in the
rewrite is the product shape (single owner, public/private) and the stack, not the
domain logic.
