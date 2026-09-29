"""docs/features/access-and-visibility.md#acceptance."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.races import RaceDetailPublic, RaceListItem, SegmentPublic
from tests.conftest import PASSWORD
from tests.factories import make_race

WRITE_METHODS = {"post", "put", "patch", "delete"}
ANYONE = {("post", "/api/auth/login")}


def _routes(methods):
    """Every mounted API route, from the OpenAPI schema, so a new one can't slip past."""
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            if method in methods and (method, path) not in ANYONE:
                yield (
                    method,
                    path.replace("{race_id}", "1")
                    .replace("{a_id}", "1")
                    .replace("{b_id}", "2")
                    .replace("{upload_id}", "1")
                    .replace("{activity_id}", "1")
                    .replace("{gear_id}", "1")
                    .replace("{photo_id}", "1")
                    .replace("{size}", "thumb"),
                )


WRITE_ROUTES = sorted(_routes(WRITE_METHODS))
READ_ROUTES = sorted(r for r in _routes({"get"}) if r[1] != "/api/auth/me")
OWNER_ONLY_READS = {
    "/api/uploads",
    "/api/gear",
    "/api/gear/unknown-devices",
    "/api/gear/last-used-shoes",
}


def test_there_are_write_routes_to_check():
    assert len(WRITE_ROUTES) > 15


@pytest.mark.parametrize("mode", ["private", "public"])
@pytest.mark.parametrize(("method", "path"), WRITE_ROUTES)
def test_every_write_route_needs_the_owner(anon, db, monkeypatch, mode, method, path):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "site_mode", mode)
    make_race(db, public=True)
    response = anon.request(method, path, json={}, headers={"X-Requested-With": "x"})
    assert response.status_code == 401, (method, path, response.text)


@pytest.mark.parametrize(("method", "path"), READ_ROUTES)
def test_private_mode_reads_need_a_session(anon, method, path):
    assert anon.request(method, path).status_code == 401


@pytest.mark.parametrize(("method", "path"), READ_ROUTES)
def test_public_mode_hides_hidden_races_everywhere(anon, db, public_mode, method, path):
    make_race(db, name="hidden", times={"swim": 600, "bike": 2000, "run": 1500})  # id 1
    make_race(db, name="shown", public=True, times={"swim": 600, "bike": 2000, "run": 1500})
    response = anon.request(method, path)
    if path in OWNER_ONLY_READS:
        assert response.status_code == 401
    elif "/1" in path.replace("/api/races/1/compare/2", "/api/races/1"):
        assert response.status_code == 404, path
    else:
        assert "hidden" not in response.text


def test_hidden_race_and_missing_race_are_indistinguishable(anon, db, public_mode):
    make_race(db, name="hidden")
    hidden, missing = anon.get("/api/races/1"), anon.get("/api/races/999")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


def test_timeline_and_stats_count_public_races_only(anon, db, public_mode):
    make_race(
        db,
        public=True,
        distances={"swim": 750, "bike": 20000, "run": 5000},
        times={"swim": 600, "bike": 2000, "run": 1500},
    )
    make_race(
        db,
        distances={"swim": 1500, "bike": 40000, "run": 10000},
        times={"swim": 1200, "bike": 4000, "run": 3000},
    )
    assert [r["id"] for r in anon.get("/api/races").json()["items"]] == [1]
    stats = anon.get("/api/stats/summary").json()
    assert stats["race_count"] == 1
    assert stats["distance_m"] == 25750


def test_compare_with_one_hidden_race_is_404(anon, db, public_mode):
    make_race(db, public=True)
    make_race(db)
    assert anon.get("/api/races/1/compare/2").status_code == 404
    assert anon.get("/api/races/2/compare/1").status_code == 404


# --- redaction: assert the exact public key sets ----------------------------------------


def test_public_detail_has_exactly_the_public_keys(anon, db, public_mode):
    make_race(
        db,
        public=True,
        times={"swim": 600, "bike": 2000, "run": 1500},
        official={"total_time": 4200},
    )
    body = anon.get("/api/races/1").json()
    assert (
        set(body)
        == set(RaceDetailPublic.model_fields)
        == {
            "id",
            "name",
            "date",
            "location_name",
            "location_lat",
            "location_lng",
            "notes",
            "notes_html",
            "is_public",
            "category",
            "weather",
            "official_result",
            "segments",
            "fueling",
            "photos",
        }
    )
    assert {s["segment"] for s in body["segments"]} == {"swim", "bike", "run"}
    for segment in body["segments"]:
        assert set(segment) == set(SegmentPublic.model_fields)
        assert not {"device_key", "upload_id", "raw_data", "sensors", "is_primary"} & set(segment)
    for key in ("completeness", "recordings", "raw_data"):
        assert key not in body


def test_public_list_item_keys(anon, db, public_mode):
    make_race(db, public=True)
    item = anon.get("/api/races").json()["items"][0]
    assert set(item) == set(RaceListItem.model_fields)


def test_owner_gets_completeness_and_recordings(owner, db):
    make_race(db, times={"swim": 600})
    body = owner.get("/api/races/1").json()
    assert "completeness" in body and "recordings" in body


def test_track_is_simplified_for_visitors(anon, db, public_mode):
    race = make_race(db, public=True, times={"bike": 100})
    points = [
        {"lat": 49 + i * 1e-6, "lng": 8.4, "elevation": None, "t": i, "distance_m": i}
        for i in range(500)
    ]  # a straight line: simplifies to two points
    race.activities[0].gps_track = points
    db.commit()
    for precision in ("full", "high", "low"):
        track = anon.get(f"/api/races/1/track?precision={precision}").json()
        assert len(track[0]["points"]) == 2


# --- new data is private ----------------------------------------------------------------


def test_new_manual_race_is_private(owner):
    assert owner.post("/api/races", json={}).json()["is_public"] is False


def test_publishing_sets_published_at_once(owner, db):
    make_race(db)
    owner.patch("/api/races/1/visibility", json={"is_public": True})
    first = owner.get("/api/races/1").json()["published_at"]
    owner.patch("/api/races/1/visibility", json={"is_public": False})
    owner.patch("/api/races/1/visibility", json={"is_public": True})
    assert first and owner.get("/api/races/1").json()["published_at"] == first


# --- sessions ---------------------------------------------------------------------------


def test_login_logout(owner):
    assert owner.get("/api/auth/me").json() == {"owner": True, "site_mode": "private"}
    cookie = owner.cookies.get("race_session")
    assert cookie and len(cookie) >= 40
    assert owner.post("/api/auth/logout").status_code == 204
    assert owner.get("/api/auth/me").json()["owner"] is False


def test_wrong_password_and_unknown_user_look_the_same(owner, anon):
    wrong = anon.post("/api/auth/login", json={"username": "owner", "password": "nope"})
    unknown = anon.post("/api/auth/login", json={"username": "who", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_is_rate_limited(anon):
    codes = [
        anon.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code
        for _ in range(6)
    ]
    assert codes == [401] * 5 + [429]


def test_writes_need_a_csrf_header(owner):
    bare = TestClient(app, base_url="http://localhost", cookies=dict(owner.cookies))
    assert (
        bare.post(
            "/api/races",
            content=b"date=2026-01-01",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 403
    )
    assert bare.post("/api/races", json={}).status_code == 201  # JSON content type is enough


def test_cookie_flags(anon, owner):
    response = TestClient(app, base_url="https://race.example.com").post(
        "/api/auth/login", json={"username": "owner", "password": PASSWORD}
    )
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "secure" in header and "samesite=lax" in header
