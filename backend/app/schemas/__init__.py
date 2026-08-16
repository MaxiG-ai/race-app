"""Pydantic v2 request/response models, split by aggregate. `gps_track` never
appears here except inside `activity.SegmentTrack`/`RaceTrackResponse`, which back
only `/races/{id}/track` (§4 Payload note)."""

from app.schemas.activity import (
    LapSplitRead,
    RaceTrackResponse,
    RecordedActivityRead,
    RecordedActivityUpdate,
    SegmentTrack,
    TrackPoint,
)
from app.schemas.fueling import (
    FuelingProduct,
    RaceFuelingRead,
    RaceFuelingUpsert,
    RaceFuelingUpsertItem,
)
from app.schemas.gear import (
    ActivityGearAssign,
    ActivityGearRead,
    GearCreate,
    GearDeviceCreate,
    GearDeviceRead,
    GearListItem,
    GearRead,
    GearUpdate,
    UnknownDeviceRead,
)
from app.schemas.race import (
    OfficialResultRead,
    OfficialResultUpsert,
    RaceCreate,
    RaceDetail,
    RaceListItem,
    RaceRead,
    RaceUpdate,
    SegmentSummary,
)
from app.schemas.stats import StatsSummary
from app.schemas.upload import (
    RaceAttachRequest,
    RaceGroupRequest,
    UploadGroupProposal,
    UploadRead,
)
from app.schemas.user import UserRead

__all__ = [
    "ActivityGearAssign",
    "ActivityGearRead",
    "FuelingProduct",
    "GearCreate",
    "GearDeviceCreate",
    "GearDeviceRead",
    "GearListItem",
    "GearRead",
    "GearUpdate",
    "LapSplitRead",
    "OfficialResultRead",
    "OfficialResultUpsert",
    "RaceAttachRequest",
    "RaceCreate",
    "RaceDetail",
    "RaceFuelingRead",
    "RaceFuelingUpsert",
    "RaceFuelingUpsertItem",
    "RaceGroupRequest",
    "RaceListItem",
    "RaceRead",
    "RaceTrackResponse",
    "RaceUpdate",
    "RecordedActivityRead",
    "RecordedActivityUpdate",
    "SegmentSummary",
    "SegmentTrack",
    "StatsSummary",
    "TrackPoint",
    "UnknownDeviceRead",
    "UploadGroupProposal",
    "UploadRead",
    "UserRead",
]
