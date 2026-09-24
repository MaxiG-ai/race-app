"""Enums shared across features. Stored as their string values (see `app.db.Base`)."""

from enum import StrEnum


class Segment(StrEnum):
    SWIM = "swim"
    T1 = "t1"
    BIKE = "bike"
    T2 = "t2"
    RUN = "run"


SEGMENTS: tuple[Segment, ...] = tuple(Segment)
SPORT_SEGMENTS: tuple[Segment, ...] = (Segment.SWIM, Segment.BIKE, Segment.RUN)


class Sport(StrEnum):
    SWIM = "swim"
    BIKE = "bike"
    RUN = "run"
    OTHER = "other"


class UploadSource(StrEnum):
    FIT_UPLOAD = "fit_upload"
    STRAVA = "strava"


class UploadStatus(StrEnum):
    PARSED = "parsed"
    GROUPED = "grouped"
    FAILED = "failed"


class UploadKind(StrEnum):
    MULTISPORT = "multisport"
    SINGLE_SPORT = "single_sport"


class CategoryKind(StrEnum):
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
