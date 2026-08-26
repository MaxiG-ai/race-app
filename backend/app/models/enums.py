"""Shared Python enums backing native Postgres enum columns.

Subclassing ``str`` alongside ``enum.Enum`` means these serialise cleanly (as plain
strings) through both SQLAlchemy and Pydantic without extra config.
"""

import enum


class Segment(str, enum.Enum):
    """The five legs of a triathlon, in race order. Shared by `RaceFueling.segment`,
    `RecordedActivity.segment` and `LapSplit.sport` (denormalized from its parent) —
    one Postgres enum type (`segment`) backs all three columns."""

    SWIM = "swim"
    T1 = "t1"
    BIKE = "bike"
    T2 = "t2"
    RUN = "run"


class Sport(str, enum.Enum):
    """Coarser sport classification used only for `Upload.detected_sport` — a
    single-sport upload doesn't yet know whether it's a transition leg."""

    SWIM = "swim"
    BIKE = "bike"
    RUN = "run"
    OTHER = "other"


class CategoryKind(str, enum.Enum):
    """What kind of category a `RaceCategory` row is.

    The published DTU list mixes three things. Age classes (Schüler, Jugend, Junioren)
    describe the *athlete*, and their distances overlap the open categories almost
    entirely — Junioren is 0.75-1.5 / 20-40 / 5-10 against Kurzdistanz's 1.5 / 40 / 10 —
    so a course cannot tell you which applies. Mixed Relay is a team format. Only
    `DISTANCE` rows are eligible for automatic classification (§9).
    """

    DISTANCE = "distance"
    AGE_CLASS = "age_class"
    RELAY = "relay"


class ClassificationSource(str, enum.Enum):
    """Where a race's assigned category came from."""

    AUTO = "auto"
    MANUAL = "manual"
    UNCLASSIFIED = "unclassified"


class RaceType(str, enum.Enum):
    """Superseded by `RaceCategory` (§9). Retained only so the migration that backfills
    categories can read the old column; no application code should use it."""

    SPRINT = "sprint"
    OLYMPIC = "olympic"
    HALF_IRON = "half_iron"
    FULL_IRON = "full_iron"
    CUSTOM = "custom"


class UploadSource(str, enum.Enum):
    FIT_UPLOAD = "fit_upload"
    STRAVA = "strava"


class UploadStatus(str, enum.Enum):
    PARSED = "parsed"
    GROUPED = "grouped"
    FAILED = "failed"


class UploadKind(str, enum.Enum):
    MULTISPORT = "multisport"
    SINGLE_SPORT = "single_sport"


class GearKind(str, enum.Enum):
    BIKE = "bike"
    SHOES = "shoes"
    WETSUIT = "wetsuit"
    OTHER = "other"


class GearAssignedBy(str, enum.Enum):
    AUTO_DEVICE = "auto_device"
    MANUAL = "manual"
    INHERITED = "inherited"
