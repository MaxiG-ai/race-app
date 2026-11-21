"""Owner, sessions and site settings (docs/features/access-and-visibility.md#data)."""

from datetime import datetime

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class Owner(Base):
    __tablename__ = "owner"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    # An argon2 hash, or a placeholder that never verifies for an owner who logs in
    # with Strava only.
    password_hash: Mapped[str]
    # Set: the owner logs in with this Strava athlete, and the password login is off
    # (docs/features/strava.md#login).
    strava_athlete_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
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
    # Append the race tagline to the description of activities pulled from Strava.
    strava_tagline: Mapped[bool] = mapped_column(default=True)
    # When the owner agreed that public races may show data that came from Strava
    # (docs/features/strava.md#publishing). Null: never asked, or not agreed.
    strava_public_consent_at: Mapped[datetime | None]
