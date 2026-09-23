"""Page shells and link previews (architecture.md#link-previews, race-detail.md)."""

from tests.factories import make_race


def test_private_mode_pages_redirect_to_login(anon):
    for path in ("/", "/races/1", "/compare?a=1&b=2", "/admin/import"):
        response = anon.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"].startswith("/login")
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
