"""The colour tokens stay legible in both themes (docs/design/visual-system.md#colour).

Six checks per theme, on the sport colours (adjacent in the segment bar) and on the
gap chart's faster/slower pair:

1. ΔE ≥ 15 between every pair under normal vision (OKLab distance × 100);
2–4. ΔE ≥ 8 under simulated protanopia, deuteranopia and tritanopia (Machado 2009);
5–6. contrast ≥ 3:1 against --bg and against --surface (WCAG, for graphics).

Text tokens must reach 4.5:1 (--ink-muted) against both backgrounds.
"""

import re
from itertools import combinations
from pathlib import Path

import pytest

from tests.palette import contrast, delta_e, rgb, simulate

TOKENS = Path(__file__).resolve().parents[2] / "frontend" / "static" / "css" / "tokens.css"
NORMAL_FLOOR, CVD_FLOOR, GRAPHIC_CONTRAST, TEXT_CONTRAST = 15, 8, 3, 4.5
SPORTS = ("swim", "bike", "run")


def themes() -> dict[str, dict[str, str]]:
    css = TOKENS.read_text(encoding="utf-8")
    light_block = css.split("@media", 1)[0]
    dark_block = css.split("prefers-color-scheme: dark", 1)[1]

    def tokens(block: str) -> dict[str, str]:
        return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block))

    light = tokens(light_block)
    return {"light": light, "dark": {**light, **tokens(dark_block)}}


THEMES = themes()
GROUPS = [tuple(combinations(SPORTS, 2)), (("faster", "slower"),)]


def test_dark_theme_overrides_every_colour():
    assert set(THEMES["dark"]) == set(THEMES["light"])
    dark_block = TOKENS.read_text().split("prefers-color-scheme: dark", 1)[1]
    assert all(f"--{name}:" in dark_block for name in THEMES["light"])


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("vision", [None, "protan", "deutan", "tritan"])
def test_data_colours_are_distinguishable(theme, vision):
    t = THEMES[theme]
    floor = NORMAL_FLOOR if vision is None else CVD_FLOOR
    for group in GROUPS:
        for a, b in group:
            ca, cb = rgb(t[a]), rgb(t[b])
            if vision:
                ca, cb = simulate(ca, vision), simulate(cb, vision)
            assert delta_e(ca, cb) >= floor, (theme, vision, a, b, round(delta_e(ca, cb), 1))


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("background", ["bg", "surface"])
def test_data_colours_stand_off_the_background(theme, background):
    t = THEMES[theme]
    for name in (*SPORTS, "faster", "slower", "neutral"):
        ratio = contrast(rgb(t[name]), rgb(t[background]))
        assert ratio >= GRAPHIC_CONTRAST, (theme, name, background, round(ratio, 2))


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("background", ["bg", "surface"])
def test_text_is_readable(theme, background):
    t = THEMES[theme]
    for name in ("ink", "ink-muted", "danger"):
        ratio = contrast(rgb(t[name]), rgb(t[background]))
        assert ratio >= TEXT_CONTRAST, (theme, name, background, round(ratio, 2))


def test_the_published_light_figures_still_hold():
    """visual-system.md quotes these; if a value changes, update the doc too."""
    light = THEMES["light"]
    pair = rgb(light["bike"]), rgb(light["run"])
    assert round(delta_e(*pair), 1) == 15.3
    assert round(delta_e(*(simulate(c, "deutan") for c in pair)), 1) == 9.3
