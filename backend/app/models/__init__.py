"""SQLAlchemy models, one module per aggregate. Importing this package registers
every mapped class on `app.db.Base.metadata` — required by Alembic's `env.py` and
by anything constructing a fresh schema (e.g. tests)."""

from app.models.enums import (
    GearAssignedBy,
    GearKind,
    RaceType,
    Segment,
    Sport,
    UploadKind,
    UploadSource,
    UploadStatus,
)
from app.models.gear import ActivityGear, Gear, GearDevice
from app.models.lap_split import LapSplit
from app.models.race import OfficialResult, Race
from app.models.race_fueling import RaceFueling
from app.models.recorded_activity import RecordedActivity
from app.models.upload import Upload
from app.models.user import User

__all__ = [
    "ActivityGear",
    "Gear",
    "GearAssignedBy",
    "GearDevice",
    "GearKind",
    "LapSplit",
    "OfficialResult",
    "Race",
    "RaceFueling",
    "RaceType",
    "RecordedActivity",
    "Segment",
    "Sport",
    "Upload",
    "UploadKind",
    "UploadSource",
    "UploadStatus",
    "User",
]
