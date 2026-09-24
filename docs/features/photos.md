# Photos

**Priority:** later

## Goal

A race you're remembering is better shown by a photo than by a course map. The owner adds
photos to a race; the first (or chosen) one becomes the **cover**, which heads the
timeline card and becomes the image of a shared link's preview.

## Visibility

Owner: every photo, on every race. Visitors: the photos of public races only. The image
routes resolve their race through `visible_race`, so a hidden race's photo is a 404
exactly like the race ([access](access-and-visibility.md#enforcement-lives-in-two-places)).
The uploaded originals and their metadata are never public, because they are never kept.

## Data

```
race_photo
  id (PK)
  race_id           FK → race, cascade
  content_hash      sha256 of the uploaded bytes; unique per race (duplicates)
  width / height    of the stored full-size copy, px
  caption           nullable
  position          int — gallery order
  is_cover          bool — partial unique index: at most one per race
  created_at
```

Files live on the uploads volume, at paths derived from the id:
`photos/{id}.jpg` (longest edge ≤ 2400 px) and `photos/{id}-thumb.jpg` (≤ 800 px).

## API

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/api/races/{id}/photos` | owner | multipart `files`; one outcome per file: added, duplicate or failed |
| `PATCH` | `/api/races/{id}/photos/{photo_id}` | owner | `caption`, `position`, `is_cover: true` |
| `DELETE` | `/api/races/{id}/photos/{photo_id}` | owner | delete the row and both files |
| `GET` | `/api/races/{id}/photos/{photo_id}/{thumb\|full}` | reader | the JPEG, `Cache-Control: no-cache` |

`photos` (all, in order) is on the race detail and `cover_photo` on the timeline list
item, each `{id, caption, width, height, is_cover, thumb_url, full_url}`.

## UI

- **Timeline card**: the cover's thumbnail across the top of the card, 16:7, cropped.
  Races without photos keep the plain card.
- **Race detail**: a "Photos" gallery after the race report; each thumbnail links to the
  full image; captions below.
- **Race edit**: a Photos section: add (several at once), caption (autosaves on blur),
  make cover, move up/down, delete.
- **Link preview**: `og:image` (with width and height) and `twitter:card` for a public
  race with a cover.

## Rules

### Stripping metadata

A phone photo's EXIF holds GPS coordinates, often of the athlete's home, the camera's
serial number and timestamps. On a public site that's a leak, so metadata is removed by
construction, not by deleting known tags (`services/photos.py`):

1. Decode with Pillow. JPEG, PNG and WebP only; anything else (GIF, HEIC, a text file)
   fails that one file with a message, and the rest of the batch still goes in.
2. Apply the EXIF orientation to the pixels, since the tag itself won't survive.
3. Copy **only the pixels** into a fresh image (transparency flattened onto white) and
   encode that as JPEG. EXIF, XMP, IPTC and comments have nowhere to come from. The ICC
   colour profile is kept: it describes colours, not the photographer.
4. The uploaded bytes are hashed, then discarded. **The original is never written to
   disk**, unlike FIT originals, because there is nothing to re-import from a photo.

### Limits

- The file size limit is the uploads' `MAX_UPLOAD_BYTES`.
- More than 60 megapixels is refused before decoding (a decompression-bomb guard).

### Cover

- The first photo added to a race becomes its cover. Making another the cover moves the
  flag; it can't be simply unset (`is_cover: false` is a 422).
- Deleting the cover hands it to the next photo in order. Deleting the race deletes its
  photos' files too.

### Caching

Images are served with `Cache-Control: no-cache`: a browser may keep a copy but must
revalidate it, so un-publishing a race hides its photos straight away rather than after
a cache lifetime.

## Open questions

- A rendered course thumbnail as the preview image for races without photos
  ([backlog](../backlog.md)).
- Should the cover replace the segment bar on narrow screens? For now both show.

## Acceptance

- The stored full and thumbnail copies carry no EXIF, GPS, serial, XMP or comment, even
  when the upload had all of them; an orientation tag is applied to the pixels.
- Large photos are scaled to 2400 px and 800 px; nothing is enlarged.
- A GIF, a non-image and an over-limit image fail individually; the same photo twice is
  one photo.
- The first photo is the cover; the cover moves; deleting it hands it on.
- A hidden race's photos are a 404 for visitors, including through another race's id.
- A public race's cover is its `og:image`; a hidden race's shell stays identical to a
  nonexistent one's.
