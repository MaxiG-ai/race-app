"""The DTU distance categories, as seed data.

Kept as a plain table rather than an enum so a boundary can be corrected without a
deploy, and so a race can hold a foreign key to the category it matched (§9).

Three kinds live in the published list and they are not interchangeable:

* ``distance`` — open categories, inferable from a course.
* ``age_class`` — Schüler / Jugend / Junioren describe the *athlete*. Their distances
  overlap the open categories almost entirely (Junioren 0.75–1.5 / 20–40 / 5–10 against
  Kurzdistanz 1.5 / 40 / 10), so inferring one from a course is not possible.
* ``relay`` — a team format.

Only ``distance`` rows are eligible for automatic classification.
"""

from __future__ import annotations

from typing import NamedTuple

from app.models.enums import CategoryKind


class CategorySeed(NamedTuple):
    key: str
    label: str
    kind: CategoryKind
    sort_order: int
    swim: tuple[float | None, float | None]
    bike: tuple[float | None, float | None]
    run: tuple[float | None, float | None]


def _km(*values: float | None) -> tuple[float | None, float | None]:
    lo, hi = values
    return (lo * 1000 if lo is not None else None, hi * 1000 if hi is not None else None)


#: Distances as published, in km, converted to metres. A ``None`` bound is open-ended —
#: Sprintdistanz's "bis 0,75" has no floor.
CATEGORY_SEEDS: tuple[CategorySeed, ...] = (
    CategorySeed(
        "schueler", "Schüler (A/B/C/D)", CategoryKind.AGE_CLASS, 10,
        _km(0.05, 0.4), _km(1, 10), _km(0.2, 2.5),
    ),
    CategorySeed(
        "jugend", "Jugend (A und B)", CategoryKind.AGE_CLASS, 20,
        _km(0.4, 0.75), _km(10, 20), _km(2.5, 5),
    ),
    CategorySeed(
        "junioren", "Junioren", CategoryKind.AGE_CLASS, 30,
        _km(0.75, 1.5), _km(20, 40), _km(5, 10),
    ),
    CategorySeed(
        "supersprint", "Supersprintdistanz", CategoryKind.DISTANCE, 40,
        _km(0.25, 0.5), _km(6.5, 13), _km(1.7, 3.5),
    ),
    CategorySeed(
        "sprint", "Sprintdistanz", CategoryKind.DISTANCE, 50,
        _km(None, 0.75), _km(None, 20), _km(None, 5),
    ),
    CategorySeed(
        # Published as exact values, which no measured course ever hits — this is the row
        # that makes tolerance mandatory rather than nice to have.
        "kurz", "Kurzdistanz", CategoryKind.DISTANCE, 60,
        _km(1.5, 1.5), _km(40, 40), _km(10, 10),
    ),
    CategorySeed(
        "mittel", "Mitteldistanz", CategoryKind.DISTANCE, 70,
        _km(1.9, 3.0), _km(80, 90), _km(20, 21),
    ),
    CategorySeed(
        "lang", "Langdistanz", CategoryKind.DISTANCE, 80,
        _km(1.0, 4.0), _km(100, 200), _km(10.0, 42.2),
    ),
    CategorySeed(
        "mixed_relay", "Mixed Relay", CategoryKind.RELAY, 90,
        _km(0.25, 0.3), _km(5, 8), _km(1.5, 2),
    ),
)
