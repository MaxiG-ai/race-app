"""Declarative base, shared column types and the domain enums."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator


class Base(DeclarativeBase):
    pass


class JSONBType(TypeDecorator):
    """JSONB on Postgres, plain JSON elsewhere so tests can run on SQLite."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class GUID(TypeDecorator):
    """UUID on Postgres, 36-char string elsewhere."""

    impl = UUID
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        from sqlalchemy.types import String

        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(GUID(), primary_key=True, default=uuid.uuid4)


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def updated_at_col() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Segment(StrEnum):
    """The five legs of a triathlon. Order matters — it is the course order."""

    SWIM = "swim"
    T1 = "t1"
    BIKE = "bike"
    T2 = "t2"
    RUN = "run"

    @property
    def is_transition(self) -> bool:
        return self in (Segment.T1, Segment.T2)


#: Course order, used for sequence validation and for laying out the gap chart.
SEGMENT_ORDER: tuple[Segment, ...] = (
    Segment.SWIM,
    Segment.T1,
    Segment.BIKE,
    Segment.T2,
    Segment.RUN,
)

#: The three disciplines that carry a distance signal for classification (§9).
SCORING_SEGMENTS: tuple[Segment, ...] = (Segment.SWIM, Segment.BIKE, Segment.RUN)


class ActivitySource(StrEnum):
    FIT_UPLOAD = "fit_upload"
    STRAVA = "strava"


class UploadStatus(StrEnum):
    PARSED = "parsed"
    GROUPED = "grouped"
    FAILED = "failed"


class UploadKind(StrEnum):
    MULTISPORT = "multisport"
    SINGLE_SPORT = "single_sport"


class DetectedSport(StrEnum):
    SWIM = "swim"
    BIKE = "bike"
    RUN = "run"
    OTHER = "other"


class CategoryKind(StrEnum):
    """Only DISTANCE categories are eligible for automatic classification (§9)."""

    DISTANCE = "distance"
    AGE_CLASS = "age_class"
    RELAY = "relay"


class ClassificationSource(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    UNCLASSIFIED = "unclassified"


class GearKind(StrEnum):
    BIKE = "bike"
    SHOES = "shoes"
    WETSUIT = "wetsuit"
    OTHER = "other"


class GearAssignedBy(StrEnum):
    AUTO_DEVICE = "auto_device"
    MANUAL = "manual"
    INHERITED = "inherited"
