"""docs/features/photos.md#acceptance. Every image here is generated in the test."""

import io

from PIL import Image

from app.services import photos
from tests.factories import make_race

GPS_IFD, ORIENTATION, BODY_SERIAL = 0x8825, 0x0112, 0xA431


def jpeg(width=64, height=32, *, colour=(200, 60, 40), exif=True, orientation=None) -> bytes:
    img = Image.new("RGB", (width, height), colour)
    out = io.BytesIO()
    options = {}
    if exif:
        data = img.getexif()
        data[BODY_SERIAL] = "SERIAL-123456"
        data[0x010F] = "ExampleCam"  # Make
        data.get_ifd(GPS_IFD).update({1: "N", 2: (49.0, 1.0, 2.0), 3: "E", 4: (8.0, 24.0, 0.0)})
        if orientation:
            data[ORIENTATION] = orientation
        options["exif"] = data.tobytes()
    img.save(out, "JPEG", comment=b"secret comment", **options)
    return out.getvalue()


def add(client, race_id=1, *files):
    body = [("files", (name, data)) for name, data in files]
    response = client.post(f"/api/races/{race_id}/photos", files=body)
    assert response.status_code == 200, response.text
    return response.json()


def stored(photo_id, size="full") -> bytes:
    return photos.path(photo_id, size).read_bytes()


def test_metadata_is_stripped_and_orientation_applied(owner, db):
    make_race(db)
    original = jpeg(orientation=6)  # "rotate 90° clockwise to display"
    assert b"SERIAL-123456" in original and b"ExampleCam" in original
    [outcome] = add(owner, 1, ("race.jpg", original))
    assert outcome["result"] == "added"
    assert (outcome["photo"]["width"], outcome["photo"]["height"]) == (32, 64)
    for size in ("full", "thumb"):
        data = stored(outcome["photo"]["id"], size)
        with Image.open(io.BytesIO(data)) as img:
            assert img.format == "JPEG"
            assert len(img.getexif()) == 0, size
            assert img.size == (32, 64)
            assert "exif" not in img.info and "comment" not in img.info
        for leak in (b"SERIAL-123456", b"ExampleCam", b"secret comment", b"Exif", b"xmp"):
            assert leak not in data, (size, leak)


def test_large_photos_are_scaled_and_thumbnailed(owner, db):
    make_race(db)
    [outcome] = add(owner, 1, ("big.jpg", jpeg(3000, 1500, exif=False)))
    assert (outcome["photo"]["width"], outcome["photo"]["height"]) == (2400, 1200)
    with Image.open(io.BytesIO(stored(outcome["photo"]["id"], "thumb"))) as thumb:
        assert thumb.size == (800, 400)


def test_png_with_transparency_becomes_a_flat_jpeg(owner, db):
    make_race(db)
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    out = io.BytesIO()
    img.save(out, "PNG")
    [outcome] = add(owner, 1, ("clear.png", out.getvalue()))
    with Image.open(io.BytesIO(stored(outcome["photo"]["id"]))) as flat:
        assert flat.mode == "RGB" and flat.getpixel((5, 5)) == (255, 255, 255)


def test_bad_files_are_refused_one_by_one(owner, db, monkeypatch):
    make_race(db)
    gif = io.BytesIO()
    Image.new("RGB", (4, 4)).save(gif, "GIF")
    outcomes = add(
        owner,
        1,
        ("notes.txt", b"hello"),
        ("anim.gif", gif.getvalue()),
        ("ok.jpg", jpeg(exif=False)),
    )
    assert [o["result"] for o in outcomes] == ["failed", "failed", "added"]
    assert "GIF files aren't supported" in outcomes[1]["message"]
    monkeypatch.setattr(photos, "MAX_PIXELS", 100)
    [huge] = add(owner, 1, ("huge.jpg", jpeg(20, 20, colour=(1, 2, 3), exif=False)))
    assert huge["result"] == "failed" and "too many pixels" in huge["message"]


def test_same_photo_twice_is_one_photo(owner, db):
    make_race(db)
    data = jpeg()
    add(owner, 1, ("a.jpg", data))
    [again] = add(owner, 1, ("b.jpg", data))
    assert again["result"] == "duplicate"
    assert len(owner.get("/api/races/1").json()["photos"]) == 1


def test_first_photo_is_the_cover_and_the_cover_moves(owner, db):
    make_race(db)
    first, second = (o["photo"] for o in add(
        owner, 1, ("a.jpg", jpeg(colour=(1, 1, 1))), ("b.jpg", jpeg(colour=(9, 9, 9)))
    ))  # fmt: skip
    assert first["is_cover"] and not second["is_cover"]
    listing = owner.patch(f"/api/races/1/photos/{second['id']}", json={"is_cover": True}).json()
    assert [p["is_cover"] for p in listing] == [False, True]
    assert (
        owner.patch(f"/api/races/1/photos/{second['id']}", json={"is_cover": False}).status_code
        == 422
    )
    # deleting the cover hands it to the next photo; its files go too
    assert owner.delete(f"/api/races/1/photos/{second['id']}").status_code == 204
    assert not photos.path(second["id"], "full").exists()
    assert [p["is_cover"] for p in owner.get("/api/races/1").json()["photos"]] == [True]


def test_caption_and_order(owner, db):
    make_race(db)
    a, b = (o["photo"] for o in add(
        owner, 1, ("a.jpg", jpeg(colour=(1, 1, 1))), ("b.jpg", jpeg(colour=(9, 9, 9)))
    ))  # fmt: skip
    owner.patch(f"/api/races/1/photos/{a['id']}", json={"caption": "  Swim exit  ", "position": 5})
    listing = owner.get("/api/races/1").json()["photos"]
    assert [p["id"] for p in listing] == [b["id"], a["id"]]
    assert listing[1]["caption"] == "Swim exit"
    owner.patch(f"/api/races/1/photos/{a['id']}", json={"caption": ""})
    assert owner.get("/api/races/1").json()["photos"][1]["caption"] is None


def test_photos_of_a_hidden_race_are_404(owner, anon, db, public_mode):
    make_race(db, name="hidden")
    make_race(db, name="shown", public=True)
    [hidden] = add(owner, 1, ("h.jpg", jpeg(colour=(1, 1, 1))))
    [shown] = add(owner, 2, ("s.jpg", jpeg(colour=(9, 9, 9))))
    hidden_url, shown_url = hidden["photo"]["thumb_url"], shown["photo"]["full_url"]
    assert owner.get(hidden_url).status_code == 200
    assert anon.get(hidden_url).status_code == 404
    response = anon.get(shown_url)
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-cache"
    # a photo id under the wrong race is a 404, not a way around visibility
    assert anon.get(f"/api/races/2/photos/{hidden['photo']['id']}/full").status_code == 404


def test_timeline_card_and_preview_use_the_cover(owner, anon, db, public_mode):
    make_race(db, name="With photo", public=True)
    [outcome] = add(owner, 1, ("a.jpg", jpeg(3000, 1500, exif=False)))
    card = anon.get("/api/races").json()["items"][0]
    assert card["cover_photo"]["thumb_url"] == outcome["photo"]["thumb_url"]
    html = anon.get("/races/1").text
    assert (
        f'<meta property="og:image" content="http://localhost/api/races/1/photos/'
        f'{outcome["photo"]["id"]}/full">' in html
    )
    assert '<meta property="og:image:width" content="2400">' in html


def test_hidden_race_shell_has_no_image(owner, anon, db, public_mode):
    make_race(db, name="hidden")
    add(owner, 1, ("a.jpg", jpeg()))
    assert anon.get("/races/1").text == anon.get("/races/999").text


def test_deleting_the_race_deletes_its_photo_files(owner, db):
    make_race(db)
    [outcome] = add(owner, 1, ("a.jpg", jpeg()))
    assert owner.delete("/api/races/1").status_code == 204
    assert not photos.path(outcome["photo"]["id"], "thumb").exists()
