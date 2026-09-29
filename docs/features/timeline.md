# Timeline

**Priority:** core

## Goal

The home page: every race in date order, newest first, each shown as a card with just
enough to recognise it (name, date, category, total time, and the swim/bike/run proportions
at a glance). For a public site, this is the page visitors land on.

## Visibility

Owner: all races, with a "not public" marker on hidden ones in public mode. Visitors:
public races only, and the stat strip counts only those
([access](access-and-visibility.md#enforcement-lives-in-two-places)).

## Data

None of its own. Reads `race`, `official_result`, and the primary `recorded_activity`
rows' totals.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/races` | list, newest first, paginated. Light payload: name, date, category, `is_public`, official and recorded segment totals for the bar. **No tracks, no laps.** |
| `GET` | `/api/stats/summary` | race count, total distance, total elevation; optional date range |
| `GET` | `/feed.xml` | Atom feed of public races ([feed](#feed)); public mode only |

## UI

`index.html`:

```
┌─────────────────────────────────────────┐
│  site title · athlete name              │
│  stat strip: races · distance · elev    │  ← thin, one line, quiet
├─────────────────────────────────────────┤
│  │                                      │
│  ●──[ Race card: name, date, badge ]    │
│  │   total time · S/B/R proportion bar  │
│  ●──[ Race card ]                       │
│  ●──[ Race card ]                       │
└─────────────────────────────────────────┘
```

- **Timeline spine**: a vertical line with one dot per race. It's the signature element
  ([visual-system](../design/visual-system.md#signature-element)).
- **Race card**: name, date, category badge, total time, segment bar, and the cover photo
  across the top when the race has one ([photos](photos.md#ui)). The whole card links
  to `/races/{id}`.
- **Segment bar**: swim/bike/run widths proportional to time; transitions are neutral
  hairline slivers, and derived ones are hatched. The detail page reuses it.
- **Stat strip**: deliberately a single line, not a dashboard. Browsing the timeline is the main thing
  people do here, not reading stats.

## Rules

- The segment bar uses official splits when present, and recorded primary totals otherwise.
  The card says which, quietly.
- Aggregates skip nulls and report the denominator: "412 km across 9 of 11 races", never
  a total silently computed over missing data.
- Empty states: the owner sees "Import your first race"; a visitor to a public site with
  no public races sees the site title and a short "Nothing here yet".

### Feed

`/feed.xml` is an Atom feed of the latest 50 public races, newest `published_at` first,
so a reader picks up a race when it is published rather than when it was run.

- It is built as an anonymous visitor sees the site, through `visible_races`, whoever
  asks: the owner's feed reader gets the public feed too.
- A private instance has no feed: `/feed.xml` is a 404 there. Feed readers can't log in.
- Each entry: `id` and `link` are the race URL, `title` as in the link preview,
  `summary` is the preview description, `content` is the rendered race report.
- Public page shells carry `<link rel="alternate" type="application/atom+xml">`, so
  readers discover it from the home page.
- Built with `xml.etree` from the standard library; no feed package.

## Open questions

- Should the stat strip be all-time only, or filterable by date range?
- Filtering by year or category on the timeline, now that visitors browse it.

## Acceptance

- Order is `date desc`, then `id desc` as a tie-break.
- The list payload has no `gps_track`, `raw_data` or laps.
- An anonymous visitor in public mode gets only public races, and stats computed over only those.
- `/feed.xml` lists public races only, for the owner too, and is a 404 in private mode.
