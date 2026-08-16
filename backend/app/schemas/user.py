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


class LoginRequest(BaseModel):
    """`POST /auth/login` body. Deliberately not validated beyond shape — the
    login endpoint gives the same error for a malformed/unknown email as for a
    wrong password (§4), so there's nothing to gain from rejecting it earlier."""

    email: EmailStr
    password: str
