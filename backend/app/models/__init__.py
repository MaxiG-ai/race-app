"""Importing this package registers every table on `Base.metadata`."""

from app.models.access import Owner, OwnerSession, SiteSettings
from app.models.classification import RaceCategory
from app.models.fueling import RaceFueling
from app.models.gear import ActivityGear, Gear, GearDevice
from app.models.imports import LapSplit, RecordedActivity, Upload
from app.models.race import OfficialResult, Race

__all__ = [
    "ActivityGear",
    "Gear",
    "GearDevice",
    "LapSplit",
    "OfficialResult",
    "Owner",
    "OwnerSession",
    "Race",
    "RaceCategory",
    "RaceFueling",
    "RecordedActivity",
    "SiteSettings",
    "Upload",
]
