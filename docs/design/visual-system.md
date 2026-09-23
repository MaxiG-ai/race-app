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
faster/slower passes at ΔE 11.1 / 24.3. Re-run the check if any value changes.

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

Light only. Dark mode is in the [backlog](../backlog.md). Because every colour is already a token,
adding it later means one `prefers-color-scheme` block, not a refactor.

## Signature element

A vertical timeline spine (a left-aligned line with a dot per race) with a proportional
swim/bike/run time bar on each card. The bar carries real pacing-balance information;
it isn't a generic step marker.

## Charts

All charts are hand-written SVG, built by one small helper module (`static/js/svg.js`:
scales, axes, paths, labels). Rules for every chart:

- Label lines directly instead of using a legend box.
- Every chart has a table fallback in a `<details>` element.
- Draw series only where data exists; a gap in the data is a gap in the line.
- Charts use `viewBox`, so they scale to the container. They must still be readable at phone width.

## Layout

A single column that works from phone width up, with a 16 px side gutter and no horizontal
scroll. Tables that are too wide scroll inside their own container, not the page.
