"""Owner, sessions and site settings (docs/features/access-and-visibility.md#data)."""

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class Owner(Base):
    __tablename__ = "owner"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OwnerSession(Base):
    __tablename__ = "session"

    # sha256 of the cookie value; the raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)


class SiteSettings(Base):
    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    site_title: Mapped[str] = mapped_column(default="Race history")
    athlete_name: Mapped[str | None]
    about: Mapped[str | None]
