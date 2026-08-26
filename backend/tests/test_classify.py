"""Classification tests, driven by the real fixture distances (§9)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.models.base import SCORING_SEGMENTS, CategoryKind, Segment
from app.services.categories import CATEGORY_SEEDS
from app.services.classify import classify
from app.services.fit_import import parse_fit

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass
class SeedCategory:
    """Adapter giving the seed tuples the shape the classifier expects."""

    key: str
    kind: CategoryKind
    tolerance_pct: float
    is_active: bool
    _bounds: dict[Segment, tuple[float | None, float | None]]

    def bounds(self, segment: Segment):
        return self._bounds[segment]


def build_categories(tolerance_pct: float = 10.0) -> list[SeedCategory]:
    return [
        SeedCategory(
            key=seed.key,
            kind=seed.kind,
            tolerance_pct=tolerance_pct,
            is_active=True,
            _bounds={Segment.SWIM: seed.swim, Segment.BIKE: seed.bike, Segment.RUN: seed.run},
        )
        for seed in CATEGORY_SEEDS
    ]


def distances_from(path: Path) -> dict[Segment, float | None]:
    fit = parse_fit(str(path))
    return {
        seg: (fit.segment(seg).distance_m if fit.segment(seg) else None)
        for seg in SCORING_SEGMENTS
    }


CATEGORIES = build_categories()


# --- the real races ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("multisport_2026-08-09.fit", "kurz"),
        ("multisport_2024-08-03_garmin.fit", "kurz"),
        ("strava_export_2026-06-21_multisport.fit", "sprint"),
        # A 33.7 km bike is not a 40 km course. Flagging it beats forcing a bucket.
        ("multisport_2026-07-11.fit", None),
    ],
)
def test_fixture_races_classify_as_predicted(filename, expected):
    result = classify(distances_from(FIXTURES / filename), CATEGORIES)
    assert result.category_key == expected


def test_the_short_bike_race_reports_why_it_failed():
    result = classify(distances_from(FIXTURES / "multisport_2026-07-11.fit"), CATEGORIES)
    assert not result.matched
    assert result.nearest_key == "kurz"
    assert result.worst_miss is not None
    assert result.worst_miss.segment == Segment.BIKE
    assert result.worst_miss.delta_pct < -5  # measured short of the widened lower bound

    detail = result.as_detail()
    assert detail["nearest"] == "kurz"
    assert detail["worst_segment"] == "bike"


def test_without_tolerance_three_of_four_real_races_misclassify():
    """The argument for tolerance, made with the actual data rather than in the abstract."""
    strict = build_categories(tolerance_pct=0.0)
    names = [
        "multisport_2026-08-09.fit",
        "multisport_2024-08-03_garmin.fit",
        "strava_export_2026-06-21_multisport.fit",
        "multisport_2026-07-11.fit",
    ]
    strict_results = [classify(distances_from(FIXTURES / n), strict).category_key for n in names]
    tolerant = [classify(distances_from(FIXTURES / n), CATEGORIES).category_key for n in names]

    assert strict_results == [None, None, None, None]
    assert tolerant == ["kurz", "kurz", "sprint", None]


# --- the overlap rules ---------------------------------------------------------------


def test_supersprint_beats_sprint_because_it_is_narrower():
    """Supersprint sits entirely inside Sprint, so both match and the specific one wins."""
    result = classify(
        {Segment.SWIM: 400.0, Segment.BIKE: 10_000.0, Segment.RUN: 3_000.0},
        CATEGORIES,
    )
    assert result.category_key == "supersprint"


def test_sprint_catches_what_supersprint_does_not():
    result = classify(
        {Segment.SWIM: 700.0, Segment.BIKE: 19_000.0, Segment.RUN: 4_800.0},
        CATEGORIES,
    )
    assert result.category_key == "sprint"


def test_age_classes_are_never_auto_assigned():
    """A Junioren course is dimensionally a Kurzdistanz. Only the athlete's age separates
    them, and a FIT file does not know how old the field was."""
    result = classify(
        {Segment.SWIM: 1_000.0, Segment.BIKE: 30_000.0, Segment.RUN: 7_000.0},
        CATEGORIES,
    )
    assert result.category_key != "junioren"
    # Nothing in the open distance list covers it either, so it stays unclassified.
    assert result.category_key is None


def test_relay_is_never_auto_assigned():
    result = classify(
        {Segment.SWIM: 280.0, Segment.BIKE: 6_000.0, Segment.RUN: 1_800.0},
        CATEGORIES,
    )
    assert result.category_key != "mixed_relay"


def test_middle_and_long_are_separated_by_the_bike():
    """Their swim and run ranges overlap; only the bike distinguishes them."""
    middle = classify(
        {Segment.SWIM: 1_900.0, Segment.BIKE: 85_000.0, Segment.RUN: 21_000.0}, CATEGORIES
    )
    long = classify(
        {Segment.SWIM: 3_800.0, Segment.BIKE: 180_000.0, Segment.RUN: 42_200.0}, CATEGORIES
    )
    assert middle.category_key == "mittel"
    assert long.category_key == "lang"


# --- partial data ---------------------------------------------------------------------


def test_missing_distances_stay_unclassified_rather_than_guessing():
    result = classify(
        {Segment.SWIM: 1_500.0, Segment.BIKE: None, Segment.RUN: 10_000.0}, CATEGORIES
    )
    assert result.category_key is None
    assert result.nearest_key is None


def test_inactive_categories_are_ignored():
    categories = build_categories()
    for category in categories:
        if category.key == "kurz":
            category.is_active = False
    result = classify(
        {Segment.SWIM: 1_500.0, Segment.BIKE: 40_000.0, Segment.RUN: 10_000.0}, categories
    )
    assert result.category_key != "kurz"
