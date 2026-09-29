"""Photo shapes (docs/features/photos.md). `PhotoOut` is public: the content hash and
the upload never are."""

from pydantic import BaseModel, Field

from app.schemas.common import Patch


class PhotoOut(BaseModel):
    id: int
    caption: str | None
    width: int
    height: int
    is_cover: bool
    thumb_url: str
    full_url: str


class PhotoPatch(Patch):
    caption: str | None = Field(default=None, max_length=500)
    is_cover: bool | None = None  # only true: the cover moves, it's never just removed
    position: int | None = Field(default=None, ge=0)


class PhotoOutcome(BaseModel):
    filename: str | None
    result: str  # "added" | "duplicate" | "failed"
    message: str
    photo: PhotoOut | None
