from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.gear import Gear
    from app.models.race import Race
    from app.models.upload import Upload


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Three ownership roots (§4 Tenancy): a race, an upload and a piece of gear each
    # carry their own user_id rather than inheriting through one another.
    races: Mapped[list[Race]] = relationship(back_populates="user")
    uploads: Mapped[list[Upload]] = relationship(back_populates="user")
    gear: Mapped[list[Gear]] = relationship(back_populates="user")
