from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserRead(BaseModel):
    """`GET /auth/me` and any embedded owner reference. Never includes
    `password_hash`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str | None
    is_active: bool
    created_at: datetime
