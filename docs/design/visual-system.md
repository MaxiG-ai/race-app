# Visual system

A data tool, not a marketing page. Clean and functional over highly polished. The
palette comes from the sport itself (water, road, trail) and is used as small
per-segment accents rather than taking over the page. It deliberately avoids the common
generated-design defaults (cream + terracotta, near-black + acid accent, broadsheet
hairlines).

All tokens are CSS custom properties in `frontend/static/css/tokens.css`. No raw hex
anywhere else.

## Colour

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#FAFAF8` | page background (off-white) |
| `--ink` | `#1C2321` | primary text (warm near-black) |
| `--hairline` | `#E4E1D8` | dividers, card borders |
| `--swim` | `#008C7E` | teal |
| `--bike` | `#A8862A` | gold-ochre |
| `--run` | `#B4472A` | brick |
| `--faster` | `#008C7E` | diverging positive pole (gap chart) |
| `--slower` | `#B4472A` | diverging negative pole |
| `--neutral` | `#6B6B66` | zero line, diverging midpoint |

**These values were checked for legibility, and the first draft failed.** The original accents
(`#1B4B4A` / `#8A6D3B` / `#C4622D`) read fine as decoration but not as data: bike and run
were ΔE 2.0 apart under protanopia and **10.2 under normal vision**, below the floor of 15.
The segment bar puts all three next to each other, so that's a legibility bug. The values above
pass all six checks against `--bg` (worst adjacent pair ΔE 9.3 deutan / 15.3 normal);
faster/slower passes at ΔE 11.1 / 24.3.

The check is a test, `backend/tests/test_palette.py`, which reads `tokens.css` and runs
for both themes, so a token change that breaks legibility fails CI. ΔE is the OKLab
distance × 100; colour blindness is simulated with Machado et al. (2009) at full
severity. The six checks, for every sport pair and for faster/slower:

1. ΔE ≥ 15 under normal vision;
2–4. ΔE ≥ 8 under protanopia, deuteranopia and tritanopia;
5–6. contrast ≥ 3:1 against `--bg` and against `--surface`.

Text tokens (`--ink`, `--ink-muted`, `--danger`) must reach 4.5:1 on both backgrounds.

### Dark values

| Token | Hex | | Token | Hex |
|---|---|---|---|---|
| `--bg` | `#121517` | | `--swim` / `--faster` | `#4CBDB0` |
| `--surface` | `#1A1F21` | | `--bike` | `#E6C35C` |
| `--ink` | `#E7E5DF` | | `--run` / `--slower` | `#C4553F` |
| `--ink-muted` | `#A3A9A6` | | `--transition` | `#555C5B` |
| `--hairline` | `#2E3437` | | `--neutral` | `#92928C` |

The sport hues are the light ones lifted for a dark surface. Bike against run is again
the hard pair, so they are split by lightness as well as hue: bike is the lightest
colour on the page, run the darkest. Worst adjacent pair ΔE 13.3 protan / 19.6 normal;
faster/slower 16.6 deutan / 28.3 normal; the lowest contrast is run on `--surface`, 3.7:1.

**Each chart uses colour for one thing only.** In the gap chart, hue already means faster/slower,
so course segments are marked with vertical rules and text labels, not tinted with sport
colours.

Transitions (T1/T2) are neutral hairline slivers in the segment bar: present, but visually
secondary to the three sports. Derived transitions are hatched.

## Type

- System font stack (`system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`). No
  webfont download. One well-set utility face is enough for a data tool. If a specific face
  is ever wanted, self-host it; don't load it from a third party.
- Every time, pace, HR and power value uses `font-variant-numeric: tabular-nums` (the `.tnum`
  class) so columns line up.

## Theme

Light and dark, following the operating system: `tokens.css` has one
`@media (prefers-color-scheme: dark)` block that redefines every colour token, and
`color-scheme: light dark` so native controls and scrollbars follow. No page CSS knows
which theme it's in. There's no in-page toggle; the system setting is the switch.

- Charts read their colours from the tokens when the page loads, so a chart drawn before
  the system theme changes keeps its colours until the page is reloaded.
- Chart tooltips are `--ink` with `--bg` text: inverted in both themes.
- Map tiles are OpenStreetMap's and stay light. In the dark theme the lifted sport
  colours (the bike especially) have less contrast on those tiles than the light ones
  do. A dark tile layer would mean a second tile provider; not done.

## Signature element

A vertical timeline spine (a left-aligned line with a dot per race) with a proportional
swim/bike/run time bar on each card. The bar carries real pacing-balance information;
it isn't a generic step marker.

## Charts

Charts use **Chart.js**, vendored and pinned ([architecture](../architecture.md#dependency-policy)).
It renders to `<canvas>` and is ~200 KB minified (~70 KB gzipped), with no dependencies. Every chart is created through one module, `static/js/charts.js`,
which sets the shared defaults once: fonts, the token colours read from CSS custom
properties, grid colour, tooltip style, `animation: false`. Page code never configures
Chart.js from scratch.

It was chosen over the alternatives for these reasons:

| Library | Why not (for this app) |
|---|---|
| uPlot | much smaller (~50 KB) and faster, but tooltips, the diverging fill and direct labels all need custom code; its speed doesn't matter at ~20–200 points per chart. The runner-up. |
| Apache ECharts | very capable, but roughly 1 MB minified, which is too heavy for the "few, small dependencies" rule |
| Frappe Charts | small and SVG-based, but its axis and fill options are too limited for the gap chart |
| Observable Plot / D3 | a toolkit rather than charts: close to writing them by hand |

Rules for every chart:

- Label lines directly instead of using a legend box (Chart.js legend off).
- Every chart has a table fallback in a `<details>` element. A canvas is opaque to
  screen readers, so the table is required, not optional.
- Draw series only where data exists; a gap in the data is a gap in the line (`null` values, `spanGaps: false`).
- Charts are `responsive` inside a container with a fixed aspect ratio. They must still be
  readable at phone width.
- Colours come from the tokens (`getComputedStyle` on `--swim` etc.), never from hex in JS.

## Layout

A single column that works from phone width up, with a 16 px side gutter and no horizontal
scroll. Tables that are too wide scroll inside their own container, not the page.
