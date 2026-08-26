"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""

from app.models.activity import LapSplit, RecordedActivity, Upload
from app.models.base import (
    SCORING_SEGMENTS,
    SEGMENT_ORDER,
    ActivitySource,
    Base,
    CategoryKind,
    ClassificationSource,
    DetectedSport,
    GearAssignedBy,
    GearKind,
    Segment,
    UploadKind,
    UploadStatus,
)
from app.models.gear import ActivityGear, Gear, GearDevice
from app.models.race import OfficialResult, Race, RaceCategory, RaceFueling
from app.models.user import User

__all__ = [
    "SCORING_SEGMENTS",
    "SEGMENT_ORDER",
    "ActivityGear",
    "ActivitySource",
    "Base",
    "CategoryKind",
    "ClassificationSource",
    "DetectedSport",
    "Gear",
    "GearAssignedBy",
    "GearDevice",
    "GearKind",
    "LapSplit",
    "OfficialResult",
    "Race",
    "RaceCategory",
    "RaceFueling",
    "RecordedActivity",
    "Segment",
    "Upload",
    "UploadKind",
    "UploadStatus",
    "User",
]
