# race-app — specification

The spec for the rewrite, split by feature. Start with [vision](vision.md), then
[architecture](architecture.md); after that, read only the feature file you are working on
plus whatever it links to.

## How the files are organised

| Folder | Holds | Changes when |
|---|---|---|
| top level | vision, architecture, data-model index, roadmap, backlog | the product direction changes |
| [`features/`](features/) | one file per feature — the product decisions | the feature is redesigned |
| [`reference/`](reference/) | facts about the outside world: FIT files, distance tables | a new device or source shows up |
| [`design/`](design/) | the visual system shared by every page | the look changes |
| [`operations/`](operations/) | running, deploying and backing up an instance | the deployment story changes |

The split between `features/` and `reference/` is deliberate. What a FIT file actually
contains is hard-won and does not change when the product does; keeping it apart means a
feature rewrite can't dilute it. Feature files link to it (e.g.
[Trap 1](reference/fit-files.md#trap-1)) rather than restating it.

## Index

**Product**
- [vision.md](vision.md) — goal, the two site modes, non-goals, decision log
- [architecture.md](architecture.md) — stack, dependency policy, layout, conventions
- [data-model.md](data-model.md) — entity overview; each table is defined in its feature file
- [roadmap.md](roadmap.md) — build order for the rewrite
- [backlog.md](backlog.md) — ideas and deferred work, tagged by feature

**Features**

| File | Priority | Summary |
|---|---|---|
| [access-and-visibility](features/access-and-visibility.md) | core | owner login, private/public site mode, per-race visibility, public redaction |
| [import](features/import.md) | core | FIT upload, parsing, multisport → race |
| [grouping](features/grouping.md) | core | staging tray, single-sport legs → race, multiple recordings per leg |
| [races-and-results](features/races-and-results.md) | core | race record, official result, manual entry, partial data |
| [timeline](features/timeline.md) | core | the home page: chronological race feed, stat strip |
| [race-detail](features/race-detail.md) | core | official vs recorded splits, pacing chart, course map |
| [classification](features/classification.md) | next | race category inferred from measured distances |
| [weather](features/weather.md) | next | Open-Meteo enrichment |
| [fueling](features/fueling.md) | next | carbs and products per segment |
| [gear](features/gear.md) | next | bikes and shoes, auto-detected from sensor serials, mileage |
| [comparison](features/comparison.md) | later | two races side by side, gap-to-reference chart |
| [photos](features/photos.md) | later | race photos, EXIF stripped, cover on the card and in previews |
| [trends](features/trends.md) | later | pacing per sport over time, temperature against pace |
| [strava](features/strava.md) | deferred | Strava as a secondary source |

**Reference**
- [fit-files.md](reference/fit-files.md) — vendor behaviour and the ten import traps
- [dtu-categories.md](reference/dtu-categories.md) — the DTU distance table as published

**Design & operations**
- [visual-system.md](design/visual-system.md) — colour tokens, type, charts, signature element
- [deployment.md](operations/deployment.md) — local, self-hosted, public; backups

## Priority legend

- **core** — needed for the app to be useful at all; built first.
- **next** — part of the intended product; built once core works end to end.
- **later** — specified, valuable, not blocking a first public release.
- **deferred** — kept on paper so the design doesn't foreclose it; no build planned.

## Feature file template

Every file in `features/` uses these headings, in this order, and stays under ~250 lines.
If a feature outgrows that, split it.

```
Priority     core | next | later | deferred
Goal         one paragraph, from the user's point of view
Visibility   what the owner sees / what a public visitor sees
Data         tables this feature owns (full column list)
API          endpoints this feature owns
UI           pages and UI pieces
Rules        behaviour, edge cases — link reference/ rather than restating
Open questions
Acceptance   what the tests must assert
```
