"""The Strava connection and the activities a pull has seen (docs/features/strava.md#data).

Tokens live here and only here: no response shape includes them, and they never reach
the browser.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class StravaAccount(Base):
    """Exactly one row while Strava is connected: the owner's athlete."""

    __tablename__ = "strava_account"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    athlete_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    athlete_name: Mapped[str | None]
    access_token: Mapped[str]
    refresh_token: Mapped[str]
    expires_at: Mapped[datetime]
    scope: Mapped[str]  # what the athlete actually granted, comma-separated
    connected_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_pull_at: Mapped[datetime | None]
    # Set when Strava refused the token (revoked in Strava's settings): reconnect.
    needs_reconnect: Mapped[bool] = mapped_column(default=False)

    def granted(self, scope: str) -> bool:
        return scope in self.scope.split(",")


class StravaActivity(Base):
    """One row per race-tagged activity a pull has handled, kept when its race is deleted,
    so deleting a race pulled from Strava also stops it from coming back."""

    __tablename__ = "strava_activity"
    __table_args__ = (Index("ix_strava_activity_race_id", "race_id"),)

    activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str | None]
    sport_type: Mapped[str | None]
    start_time: Mapped[datetime]
    race_id: Mapped[int | None] = mapped_column(ForeignKey("race.id", ondelete="SET NULL"))
    # imported: it is (or was) a recording of `race_id`; skipped: the pull refused it and
    # `message` says why.
    state: Mapped[str]
    message: Mapped[str | None]
    tagline: Mapped[str | None]  # the line last written to the activity's description
    tagline_at: Mapped[datetime | None]
    seen_at: Mapped[datetime] = mapped_column(default=utcnow)
