"""Race reports: the Markdown subset and its safety (races-and-results.md#race-reports)."""

from html.parser import HTMLParser

import pytest

from app.services.markdown import render
from tests.factories import make_race

ALLOWED_TAGS = {
    "p", "br", "h3", "h4", "h5", "ul", "ol", "li", "blockquote", "strong", "em", "code", "a",
}  # fmt: skip


class _Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))


def structure(html: str) -> list[tuple[str, list[tuple[str, str | None]]]]:
    parser = _Collect()
    parser.feed(html)
    return parser.tags


def assert_safe(html: str | None) -> None:
    """Only the subset's tags; only href/rel attributes; only whitelisted link targets."""
    for tag, attrs in structure(html or ""):
        assert tag in ALLOWED_TAGS, (tag, html)
        names = {name for name, _ in attrs}
        if tag == "a":
            assert names == {"href", "rel"}, html
            href = dict(attrs)["href"] or ""
            assert href.startswith(("https://", "http://", "mailto:", "/", "#")), href
            assert not href.startswith("//"), href
        else:
            assert not names, html


def test_blank_is_none():
    assert render(None) is None
    assert render("  \n\n ") is None


def test_paragraphs_and_line_breaks():
    assert render("First line\nsecond line\n\nNew paragraph") == (
        "<p>First line<br>second line</p>\n<p>New paragraph</p>"
    )


def test_headings_are_demoted_below_the_page_headings():
    assert render("# Swim\n## Bike\n### Run") == "<h3>Swim</h3>\n<h4>Bike</h4>\n<h5>Run</h5>"
    assert render("#1 in my age group") == "<p>#1 in my age group</p>"


def test_lists():
    assert render("- gel\n- bar\n\n1. swim\n2. bike") == (
        "<ul><li>gel</li><li>bar</li></ul>\n<ol><li>swim</li><li>bike</li></ol>"
    )
    assert render("Intro\n* one\n* two\nAfter") == (
        "<p>Intro</p>\n<ul><li>one</li><li>two</li></ul>\n<p>After</p>"
    )


def test_blockquote():
    assert render("> Never again.\n> Until next year.") == (
        "<blockquote><p>Never again.<br>Until next year.</p></blockquote>"
    )


def test_inline_markup():
    assert render("**hard** *easy* _steady_ `4:15/km`") == (
        "<p><strong>hard</strong> <em>easy</em> <em>steady</em> <code>4:15/km</code></p>"
    )
    # Arithmetic and snake_case aren't emphasis.
    assert render("2 * 3 * 4 and some_file_name") == "<p>2 * 3 * 4 and some_file_name</p>"


def test_links():
    assert render("[results](https://example.com/r?a=1&b=2)") == (
        '<p><a href="https://example.com/r?a=1&amp;b=2" rel="nofollow noopener">results</a></p>'
    )
    assert render("[other race](/races/3)") == (
        '<p><a href="/races/3" rel="nofollow noopener">other race</a></p>'
    )
    # Underscores in a URL are not emphasis.
    assert 'href="https://example.com/a_b_c"' in render("[x](https://example.com/a_b_c)")


def test_code_is_not_formatted():
    assert render("`**not bold**`") == "<p><code>**not bold**</code></p>"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:x",
        "//evil.example.com",
        "&#106;avascript:alert(1)",
        "https:",
    ],
)
def test_unsafe_links_stay_as_text(url):
    html = render(f"[click]({url})")
    assert "<a" not in html
    assert_safe(html)


XSS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '"><svg onload=alert(1)>',
    '[x](https://a.example"onmouseover=alert(1))',
    "[x](https://a.example'onmouseover='alert(1))",
    "**<b>bold</b>**",
    "# <iframe src=//evil>",
    "- <a href=javascript:x>y</a>",
    "> <style>body{}</style>",
    "`</code><script>x</script>`",
    "[<img src=x onerror=y>](https://example.com)",
    "\x00<script>\x00",
    "*[a](https://x.example)* **[b](/c)**",
    "<!-- comment --><![CDATA[x]]>",
]


@pytest.mark.parametrize("text", XSS)
def test_no_input_becomes_markup(text):
    html = render(text)
    assert_safe(html)
    assert "<script" not in (html or "").lower()


def test_detail_carries_rendered_notes(anon, owner, db, public_mode):
    make_race(db, public=True, notes="**PB** <b>")
    body = anon.get("/api/races/1").json()
    assert body["notes"] == "**PB** <b>"
    assert body["notes_html"] == "<p><strong>PB</strong> &lt;b&gt;</p>"
    patched = owner.patch("/api/races/1", json={"notes": "# Report"}).json()
    assert patched["notes_html"] == "<h3>Report</h3>"
