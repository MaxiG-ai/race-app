"""Page shells and link previews (architecture.md#link-previews, race-detail.md)."""

from tests.factories import make_race


def test_private_mode_pages_redirect_to_login(anon):
    for path in ("/", "/races/1", "/compare?a=1&b=2", "/admin/race-edit"):
        response = anon.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"].startswith("/login")
    old = anon.get("/admin/import", follow_redirects=False)  # importing moved onto the race page
    assert old.status_code == 301 and old.headers["location"] == "/admin/race-edit"
    assert anon.get("/login").status_code == 200


def test_admin_pages_are_owner_only_in_public_mode(anon, owner, public_mode):
    assert anon.get("/admin/race-edit", follow_redirects=False).status_code == 303
    assert owner.get("/admin/race-edit").status_code == 200
    assert anon.get("/").status_code == 200


def test_public_race_preview(anon, db, public_mode):
    make_race(
        db,
        name="Example Triathlon",
        public=True,
        official={"total_time": 4360, "swim_time": 723, "bike_time": 2110, "run_time": 1300},
    )
    html = anon.get("/races/1").text
    assert '<meta property="og:title" content="Example Triathlon">' in html
    assert "21 Jun 2026 · 1:12:40 · swim 12:03 · bike 35:10 · run 21:40" in html
    assert "<title>Example Triathlon · Race history</title>" in html


def test_hidden_race_shell_is_identical_to_missing(anon, owner, db, public_mode):
    make_race(db, name="Secret <Race>")
    hidden, missing = anon.get("/races/1").text, anon.get("/races/999").text
    assert hidden == missing and "Secret" not in hidden
    # the owner's browser gets the generic shell too; the page's JS fills in the name
    assert "Secret" not in owner.get("/races/1").text


def test_private_mode_shells_are_generic(owner, db):
    make_race(db, name="Private race", public=True)
    assert "Private race" not in owner.get("/races/1").text


def test_compare_preview_needs_both_public(anon, db, public_mode):
    make_race(db, name="A", public=True)
    make_race(db, name="B")
    make_race(db, name="C", public=True)
    assert "A vs C" in anon.get("/compare?a=1&b=3").text
    assert "A vs B" not in anon.get("/compare?a=1&b=2").text


def test_preview_text_is_escaped(anon, db, public_mode):
    make_race(db, name='"><script>x</script>', public=True)
    html = anon.get("/races/1").text
    assert "<script>x" not in html and "&lt;script&gt;" in html


def test_health(anon):
    assert anon.get("/health").json() == {"status": "ok"}


# --- Atom feed (timeline.md#feed) --------------------------------------------------------


def test_feed_lists_public_races_only(anon, owner, db, public_mode):
    from datetime import UTC, datetime
    from xml.etree.ElementTree import fromstring

    make_race(db, name="Hidden race")
    make_race(
        db,
        name="Shown <race>",
        public=True,
        notes="**PB**",
        published_at=datetime(2026, 6, 22, tzinfo=UTC),
        official={"total_time": 4360},
    )
    for client in (anon, owner):  # the owner's reader gets the public feed too
        response = client.get("/feed.xml")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/atom+xml")
        assert "Hidden race" not in response.text
    ns = {"a": "http://www.w3.org/2005/Atom"}
    feed = fromstring(anon.get("/feed.xml").content)
    entries = feed.findall("a:entry", ns)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.findtext("a:title", namespaces=ns) == "Shown <race>"
    assert entry.findtext("a:id", namespaces=ns) == "http://localhost/races/2"
    assert entry.findtext("a:summary", namespaces=ns) == "21 Jun 2026 · 1:12:40"
    assert entry.findtext("a:content", namespaces=ns) == "<p><strong>PB</strong></p>"
    assert entry.findtext("a:published", namespaces=ns) == "2026-06-22T00:00:00+00:00"


def test_feed_is_linked_from_public_shells_only(anon, owner, public_mode, monkeypatch):
    assert 'href="/feed.xml"' in anon.get("/").text
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "site_mode", "private")
    assert 'href="/feed.xml"' not in owner.get("/").text


def test_private_instance_has_no_feed(anon, owner, db):
    make_race(db, name="Anything", public=True)
    assert anon.get("/feed.xml").status_code == 404
    assert owner.get("/feed.xml").status_code == 404


def test_empty_feed_is_valid(anon, public_mode):
    from xml.etree.ElementTree import fromstring

    feed = fromstring(anon.get("/feed.xml").content)
    assert feed.findtext("{http://www.w3.org/2005/Atom}updated")


def test_trends_page_is_a_reader_page(anon, owner, public_mode, monkeypatch):
    assert "<title>Trends · Race history</title>" in anon.get("/trends").text
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "site_mode", "private")
    assert anon.get("/trends", follow_redirects=False).status_code == 303
    assert owner.get("/trends").status_code == 200
