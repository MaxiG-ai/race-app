# DTU distance categories

Source: the DTU (Deutsche Triathlon Union) distance list. Distances are **km, as
published** (swim / bike / run). This is the seed data for `race_category`; how it is used
is in [classification](../features/classification.md).

| key | label | swim | bike | run | kind |
|---|---|---|---|---|---|
| `schueler` | Schüler (A/B/C/D) | 0.05–0.4 | 1–10 | 0.2–2.5 | age class |
| `jugend` | Jugend (A und B) | 0.4–0.75 | 10–20 | 2.5–5 | age class |
| `junioren` | Junioren | 0.75–1.5 | 20–40 | 5–10 | age class |
| `supersprint` | Supersprintdistanz | 0.25–0.5 | 6.5–13 | 1.7–3.5 | distance |
| `sprint` | Sprintdistanz | ≤ 0.75 | ≤ 20 | ≤ 5 | distance |
| `kurz` | Kurzdistanz | 1.5 | 40 | 10 | distance |
| `mittel` | Mitteldistanz | 1.9–3.0 | 80–90 | 20–21 | distance |
| `lang` | Langdistanz | 1.0–4.0 | 100–200 | 10.0–42.2 | distance |
| `mixed_relay` | Mixed Relay | 0.25–0.3 | 5–8 | 1.5–2 | relay |

## Three properties that drive the classifier's design

**1. It mixes three kinds of category.** Schüler, Jugend and Junioren are *age classes*:
they describe the athlete, not the course, and their distances overlap the open distance
classes almost entirely (Junioren 0.75–1.5 / 20–40 / 5–10 against Kurzdistanz
1.5 / 40 / 10). Mixed Relay is a team format. Only `kind = distance` rows can be
auto-classified.

**2. The ranges overlap by design.** Supersprint sits entirely inside Sprint (whose
bounds are "bis", i.e. up to). Every supersprint race matches both, so "exactly one match"
never happens.

**3. Nominal distances are exact values** (Kurzdistanz 1.5 / 40 / 10), and measured courses
never hit them exactly. A tolerance is needed.

## Measured races against this table (±10 % tolerance)

| Race | Measured (km) | Result |
|---|---|---|
| 2026-08-09 | 1.55 / 40.57 / 9.93 | **Kurzdistanz**, all within 3 % |
| 2024-08-03 | 1.65 / 40.87 / 9.24 | **Kurzdistanz**, swim +9.7 %, just inside |
| 2026-06-21 | 0.54 / 19.99 / 5.11 | **Sprintdistanz**, run 2 % over the 5 km bound |
| 2026-07-11 | 1.57 / 33.75 / 9.17 | **unclassified**, bike −15.6 %, genuinely short |

Without tolerance, three of the four misclassify. With it, the only unclassified race is
the one that should be: a 33.7 km bike is not a 40 km course.
