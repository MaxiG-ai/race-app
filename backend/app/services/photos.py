"""Race photos: decode, strip, re-encode, store (docs/features/photos.md#rules).

Metadata is removed by construction rather than by deleting known tags: only the pixels
are copied into a fresh image, which is then encoded as JPEG. The uploaded bytes are
hashed for deduplication and then discarded. EXIF (GPS, camera serial, timestamps), XMP,
IPTC and comments never reach the disk. The ICC colour profile is kept, because it
describes colours, not the photographer.
"""

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings

FORMATS = {"JPEG", "PNG", "WEBP"}
FULL_EDGE = 2400  # px, longest edge of the stored full-size copy
THUMB_EDGE = 800  # px, enough for a timeline card at 2x
FULL_QUALITY, THUMB_QUALITY = 85, 80
MAX_PIXELS = 60_000_000  # decompression-bomb guard; a 60 MP photo is already large
SIZES = ("thumb", "full")
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class PhotoError(Exception):
    """User-facing: what was wrong with the file."""


@dataclass
class Processed:
    content_hash: str
    full: bytes
    thumb: bytes
    width: int
    height: int


def photo_dir() -> Path:
    return get_settings().upload_dir / "photos"


def path(photo_id: int, size: str) -> Path:
    return photo_dir() / (f"{photo_id}.jpg" if size == "full" else f"{photo_id}-thumb.jpg")


def _encode(pixels: Image.Image, edge: int, quality: int, icc: bytes | None) -> bytes:
    copy = pixels.copy()
    copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)  # never enlarges
    out = io.BytesIO()
    options = {"icc_profile": icc} if icc else {}
    copy.save(out, "JPEG", quality=quality, optimize=True, progressive=True, **options)
    return out.getvalue()


def process(data: bytes) -> Processed:
    if len(data) > get_settings().max_upload_bytes:
        raise PhotoError("The photo is larger than the upload limit.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in FORMATS:
                raise PhotoError(
                    f"{img.format or 'This'} files aren't supported; use JPEG, PNG or WebP."
                )
            if img.width * img.height > MAX_PIXELS:
                raise PhotoError("The photo has too many pixels.")
            icc = img.info.get("icc_profile")
            # Apply the EXIF orientation to the pixels, since the tag itself won't survive.
            upright = ImageOps.exif_transpose(img)
            upright.load()
    except PhotoError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise PhotoError("Not a readable image.") from exc

    if upright.mode in ("RGBA", "LA") or "transparency" in upright.info:
        rgba = upright.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
    else:
        flat = upright.convert("RGB")
    # A fresh image holds the pixels only: no info dict, no EXIF, no XMP.
    pixels = Image.new("RGB", flat.size)
    pixels.paste(flat)

    full = _encode(pixels, FULL_EDGE, FULL_QUALITY, icc)
    with Image.open(io.BytesIO(full)) as stored:
        width, height = stored.size
    return Processed(
        content_hash=hashlib.sha256(data).hexdigest(),
        full=full,
        thumb=_encode(pixels, THUMB_EDGE, THUMB_QUALITY, icc),
        width=width,
        height=height,
    )


def write(photo_id: int, processed: Processed) -> None:
    photo_dir().mkdir(parents=True, exist_ok=True)
    path(photo_id, "full").write_bytes(processed.full)
    path(photo_id, "thumb").write_bytes(processed.thumb)


def remove(photo_id: int) -> None:
    for size in SIZES:
        path(photo_id, size).unlink(missing_ok=True)
