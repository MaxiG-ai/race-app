"""Race reports: a deliberately tiny Markdown subset, rendered to known-safe HTML
(docs/features/races-and-results.md#race-reports).

A note shown to visitors is a stored-XSS vector, so safety comes from the shape of the
renderer, not from a sanitiser run afterwards: every character of the input is escaped
first, and the only tags in the output are the ones written below. Nothing from the input
is ever emitted unescaped.

Supported:

    # Heading / ## Heading / ### Heading   → <h3> / <h4> / <h5> (the page owns h1/h2)
    - item / * item                        → <ul><li>
    1. item                                → <ol><li>
    > quote                                → <blockquote>
    **bold**  *italic*  _italic_  `code`   → <strong> <em> <em> <code>
    [text](https://…)                      → <a href rel="nofollow noopener">
    blank line                             → new paragraph; a single newline is a <br>

Links only keep `http(s)://`, `mailto:` and same-site `/path` or `#anchor` targets; any
other link stays as its literal text. Everything else is plain text.
"""

import re
from html import escape, unescape

_HEADING = re.compile(r"^(#{1,3})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")

# Inline tokens that must not be touched by emphasis: code spans and links.
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^()\s]+)\)")
_STRONG = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_EM_STAR = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")
_EM_UNDERSCORE = re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")
_SAFE_URL = re.compile(r"^(https?://[^/\s]|mailto:[^\s]|/(?!/)|#)", re.IGNORECASE)
# Control characters never belong in a note; \x00 is also the placeholder delimiter.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING_TAGS = {1: "h3", 2: "h4", 3: "h5"}


def safe_url(escaped_url: str) -> bool:
    """`escaped_url` is already HTML-escaped; the check runs on what a browser would see."""
    return bool(_SAFE_URL.match(unescape(escaped_url).strip()))


def _inline(text: str) -> str:
    """Escape one line (or paragraph) and apply inline markup to it."""
    held: list[str] = []

    def hold(html: str) -> str:
        held.append(html)
        return f"\x00{len(held) - 1}\x00"

    def link(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2)
        if not safe_url(url):
            return m.group(0)
        return hold(f'<a href="{url}" rel="nofollow noopener">{_emphasis(label)}</a>')

    out = escape(text, quote=True)
    out = _CODE.sub(lambda m: hold(f"<code>{m.group(1)}</code>"), out)
    out = _LINK.sub(link, out)
    out = _emphasis(out)
    return re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], out)


def _emphasis(escaped: str) -> str:
    out = _STRONG.sub(r"<strong>\1</strong>", escaped)
    out = _EM_STAR.sub(r"<em>\1</em>", out)
    return _EM_UNDERSCORE.sub(r"<em>\1</em>", out)


def render(text: str | None) -> str | None:
    """Markdown subset → HTML. None/blank stays None ("—" on the page)."""
    if text is None or not text.strip():
        return None
    lines = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    list_tag: str | None = None
    quote: list[str] = []

    def flush() -> None:
        nonlocal list_tag
        if paragraph:
            blocks.append(f"<p>{'<br>'.join(_inline(line) for line in paragraph)}</p>")
            paragraph.clear()
        if items:
            body = "".join(f"<li>{_inline(item)}</li>" for item in items)
            blocks.append(f"<{list_tag}>{body}</{list_tag}>")
            items.clear()
            list_tag = None
        if quote:
            inner = "<br>".join(_inline(line) for line in quote)
            blocks.append(f"<blockquote><p>{inner}</p></blockquote>")
            quote.clear()

    for line in lines:
        if not line.strip():
            flush()
            continue
        if heading := _HEADING.match(line):
            flush()
            tag = _HEADING_TAGS[len(heading.group(1))]
            blocks.append(f"<{tag}>{_inline(heading.group(2))}</{tag}>")
            continue
        bullet, ordered = _BULLET.match(line), _ORDERED.match(line)
        if bullet or ordered:
            tag = "ul" if bullet else "ol"
            if paragraph or quote or (items and list_tag != tag):
                flush()
            list_tag = tag
            items.append((bullet or ordered).group(1))  # type: ignore[union-attr]
            continue
        if quoted := _QUOTE.match(line):
            if paragraph or items:
                flush()
            quote.append(quoted.group(1))
            continue
        if items or quote:
            flush()
        paragraph.append(line.strip())
    flush()
    return "\n".join(blocks)
