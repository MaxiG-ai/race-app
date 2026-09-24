"""Repository conventions that are cheap to check mechanically (CLAUDE.md, architecture.md)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
JS = sorted((FRONTEND / "static" / "js").glob("*.js"))
CSS = sorted((FRONTEND / "static" / "css").glob("*.css"))
HTML = sorted(FRONTEND.glob("*.html")) + sorted((FRONTEND / "admin").glob("*.html"))

# A hex colour in a CSS declaration value, or as a quoted string in JS/HTML.
_CSS_HEX = re.compile(r":[^;{}]*#[0-9a-fA-F]{3,8}\b")
_QUOTED_HEX = re.compile(r"""["'`]#[0-9a-fA-F]{3,8}["'`]""")


@pytest.mark.parametrize("path", JS, ids=lambda p: p.name)
def test_js_modules_are_type_checked(path):
    assert path.read_text(encoding="utf-8").startswith("// @ts-check"), path


@pytest.mark.parametrize("path", [p for p in CSS if p.name != "tokens.css"], ids=lambda p: p.name)
def test_css_uses_tokens_not_hex(path):
    assert not _CSS_HEX.findall(path.read_text(encoding="utf-8")), path


@pytest.mark.parametrize("path", JS + HTML, ids=lambda p: p.name)
def test_no_hex_colours_in_js_or_html(path):
    assert not _QUOTED_HEX.findall(path.read_text(encoding="utf-8")), path


def test_no_real_recordings_outside_private():
    fixtures = ROOT / "backend" / "tests" / "fixtures"
    for fit in ROOT.rglob("*.fit"):
        if {".git", ".venv", "private"} & set(fit.parts):
            continue
        assert fit.parent == fixtures, f"FIT file outside the synthetic fixtures: {fit}"
