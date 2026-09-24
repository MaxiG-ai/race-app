"""Generate synthetic FIT test fixtures for race-app.

Run with:

    uv run python tests/fixtures/make_fixtures.py

from the ``backend/`` directory. Writes five small, deterministic ``.fit`` files into
this directory plus ``README.md`` documenting the exact values a test suite can assert
against. Everything here is synthetic: made-up coordinates near a lake at ~49.0N 8.4E
(Central European time, CEST = UTC+2), made-up serial numbers, no real personal data.

Every number that ends up in a FIT message is a Python constant defined in this file.
``README.md`` is generated from the same constants (and, where noted, from values
computed deterministically from the generated record streams), so the file and the
docs cannot drift apart -- regenerate both by re-running this script.

After writing each file, it is re-read with ``fitdecode`` (the parser the app itself
uses) and checked against the values used to build it. That is the self-check the
project's spec (docs/reference/fit-files.md) requires.

No randomness is used anywhere in this file.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fitdecode
from fit_tool.base_type import BaseType
from fit_tool.developer_field import DeveloperField
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.developer_data_id_message import DeveloperDataIdMessage
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.field_description_message import FieldDescriptionMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.timestamp_correlation_message import TimestampCorrelationMessage
from fit_tool.profile.profile_type import (
    Event,
    EventType,
    FaveroProduct,
    FileType,
    GarminProduct,
    LapTrigger,
    Manufacturer,
    SourceType,
    Sport,
    SubSport,
)

FIXTURES_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# FIT epoch / timestamp helpers
# ---------------------------------------------------------------------------

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def utc(y: int, m: int, d: int, h: int, mi: int, s: int) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def unix_ms(dt: datetime) -> int:
    """fit-tool's ``timestamp`` setters take milliseconds since the Unix epoch."""
    return int((dt - UNIX_EPOCH).total_seconds() * 1000)


def local_timestamp_value(dt_utc: datetime, tz_offset_seconds: int) -> int:
    """fit-tool's ``local_timestamp`` setter (TimestampCorrelationMessage) takes plain
    seconds since the FIT epoch, with the local offset already baked in -- there is no
    separate scale/offset applied by the field itself (unlike ``timestamp``)."""
    fit_epoch_seconds = int((dt_utc - FIT_EPOCH).total_seconds())
    return fit_epoch_seconds + tz_offset_seconds


# Local id numbers, fixed per message type so FitFileBuilder reuses one definition
# message per type instead of redefining on every interleave.
LID_FILE_ID = 0
LID_SESSION = 1
LID_LAP = 2
LID_RECORD = 3
LID_DEVICE_INFO = 4
LID_ACTIVITY = 5
LID_DEV_DATA_ID = 6
LID_FIELD_DESC = 7
LID_TS_CORR = 8


def lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


# ---------------------------------------------------------------------------
# Lap splitting: proportional to distance, from session totals
# ---------------------------------------------------------------------------


@dataclass
class LapPlan:
    distance: float
    elapsed: float
    timer: float
    cum_distance: float
    cum_elapsed: float
    cum_timer: float


def build_autolaps(
    total_elapsed: float,
    total_timer: float,
    total_distance: float,
    lap_size: float,
    drop_trailing: bool = False,
) -> list[LapPlan]:
    """Split a session into auto-laps of ``lap_size`` metres, proportioning elapsed/timer
    time by distance fraction. A trailing partial lap (< lap_size) is included unless
    ``drop_trailing`` is set (Apple's behaviour on the bike -- Trap 1), in which case it is
    left off entirely: the emitted laps then sum to less than the session total.
    """
    if abs(total_distance % lap_size) < 1e-6:
        n_full = int(round(total_distance / lap_size))
    else:
        n_full = int(total_distance // lap_size)
    breakpoints = [lap_size * (i + 1) for i in range(n_full)]
    remainder = round(total_distance - n_full * lap_size, 6)
    if remainder > 1e-6:
        breakpoints.append(total_distance)
        if drop_trailing:
            breakpoints = breakpoints[:-1]
    laps: list[LapPlan] = []
    prev_d = prev_e = prev_t = 0.0
    for bp in breakpoints:
        frac = bp / total_distance
        e = round(total_elapsed * frac, 3)
        t = round(total_timer * frac, 3)
        laps.append(
            LapPlan(
                distance=round(bp - prev_d, 2),
                elapsed=round(e - prev_e, 3),
                timer=round(t - prev_t, 3),
                cum_distance=bp,
                cum_elapsed=e,
                cum_timer=t,
            )
        )
        prev_d, prev_e, prev_t = bp, e, t
    return laps


# ---------------------------------------------------------------------------
# Segment spec: one FIT `session` (+ its laps + its records)
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    name: str  # human label, e.g. "swim", "t1", "bike", "t2", "run"
    sport: Sport
    sub_sport: SubSport | None
    start_offset: int  # seconds from the file's base_dt (integer -- start_time is int-second)
    elapsed: float  # seconds, may carry a sub-second fraction (ms resolution)
    distance: float  # metres
    waypoint_a: tuple[float, float]
    waypoint_b: tuple[float, float]
    timer: float | None = None  # defaults to elapsed
    ascent: float | None = None
    hr_range: tuple[int, int] | None = (0, 0)
    cadence_range: tuple[int, int] | None = None
    power_range: tuple[int, int] | None = None
    altitude_range: tuple[float, float] | None = None  # (base, total_gain); None => absent (Trap 9)
    temperature_range: tuple[int, int] | None = None
    lap_size: float | None = None  # defaults to `distance` (single lap)
    drop_trailing_lap: bool = False
    sample_interval: object = 10  # int seconds, or a list[int] pattern to cycle (variable sampling)
    lap_trigger: LapTrigger = LapTrigger.DISTANCE
    session_avg_power_override: float | None = None
    session_np_override: float | None = None
    lap_power_overrides: list[float] | None = None
    lap_np_overrides: list[float] | None = None

    def __post_init__(self):
        if self.timer is None:
            self.timer = self.elapsed
        if self.lap_size is None:
            self.lap_size = self.distance


def sample_offsets(elapsed: float, interval) -> list[int]:
    # Timestamps are integer-second and must stay strictly inside the session's own
    # half-open window [start, start + elapsed): a sample landing exactly on an
    # integer-valued `elapsed` would coincide with the *next* session's own start_time
    # (and its first sample), so it is excluded here rather than double-emitted.
    offsets: list[int] = []
    if isinstance(interval, list):
        t = 0
        i = 0
        while t < elapsed:
            offsets.append(t)
            t += interval[i % len(interval)]
            i += 1
    else:
        t = 0
        while t < elapsed:
            offsets.append(t)
            t += interval
    if not offsets:
        offsets = [0]
    return offsets


@dataclass
class RecordSample:
    t_offset: int
    timestamp: datetime
    distance: float
    lat: float
    lon: float
    hr: int | None
    cadence: int | None
    power: int | None
    altitude: float | None
    temperature: int | None
    speed: float


def build_segment_records(base_dt: datetime, seg: Segment) -> list[RecordSample]:
    offsets = sample_offsets(seg.elapsed, seg.sample_interval)
    avg_speed = seg.distance / seg.elapsed if seg.elapsed > 0 else 0.0
    out = []
    for t in offsets:
        frac = (t / seg.elapsed) if seg.elapsed > 0 else 0.0
        hr = None
        if seg.hr_range is not None:
            hr = round(lerp(seg.hr_range[0], seg.hr_range[1], frac))
        cadence = None
        if seg.cadence_range is not None:
            cadence = round(lerp(seg.cadence_range[0], seg.cadence_range[1], frac))
        power = None
        if seg.power_range is not None:
            power = round(lerp(seg.power_range[0], seg.power_range[1], frac))
        altitude = None
        if seg.altitude_range is not None:
            base, gain = seg.altitude_range
            altitude = round(base + gain * frac, 1)
        temperature = None
        if seg.temperature_range is not None:
            temperature = round(lerp(seg.temperature_range[0], seg.temperature_range[1], frac))
        lat = lerp(seg.waypoint_a[0], seg.waypoint_b[0], frac)
        lon = lerp(seg.waypoint_a[1], seg.waypoint_b[1], frac)
        out.append(
            RecordSample(
                t_offset=t,
                timestamp=base_dt + timedelta(seconds=seg.start_offset + t),
                distance=round(seg.distance * frac, 2),
                lat=lat,
                lon=lon,
                hr=hr,
                cadence=cadence,
                power=power,
                altitude=altitude,
                temperature=temperature,
                speed=round(avg_speed, 3),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Message emission
# ---------------------------------------------------------------------------


@dataclass
class SegmentStats:
    seg: Segment
    start_time: datetime
    end_time_recorded: datetime  # timestamp of the session message == last record's timestamp
    derived_end: datetime  # start_time + elapsed (may carry a sub-second fraction)
    avg_hr: int | None
    max_hr: int | None
    min_hr: int | None
    avg_cadence: int | None
    max_cadence: int | None
    avg_power: float | None
    max_power: int | None
    normalized_power: float | None
    avg_temperature: float | None
    nec: tuple[float, float]
    swc: tuple[float, float]
    laps: list[LapPlan]
    record_count: int
    records: list[RecordSample]


def emit_segment(
    builder: FitFileBuilder, base_dt: datetime, seg: Segment, developer_fields_for_session=None
) -> SegmentStats:
    records = build_segment_records(base_dt, seg)

    hrs = [r.hr for r in records if r.hr is not None]
    cadences = [r.cadence for r in records if r.cadence is not None]
    powers = [r.power for r in records if r.power is not None]
    temps = [r.temperature for r in records if r.temperature is not None]
    lats = [r.lat for r in records]
    lons = [r.lon for r in records]

    avg_hr = round(statistics.mean(hrs)) if hrs else None
    max_hr = max(hrs) if hrs else None
    min_hr = min(hrs) if hrs else None
    avg_cadence = round(statistics.mean(cadences)) if cadences else None
    max_cadence = max(cadences) if cadences else None
    avg_power = round(statistics.mean(powers), 1) if powers else None
    max_power = max(powers) if powers else None
    avg_temperature = round(statistics.mean(temps), 1) if temps else None

    if seg.session_avg_power_override is not None:
        avg_power = seg.session_avg_power_override
    normalized_power = seg.session_np_override

    start_time = base_dt + timedelta(seconds=seg.start_offset)
    derived_end = start_time + timedelta(seconds=seg.elapsed)
    end_time_recorded = records[-1].timestamp

    laps = build_autolaps(seg.elapsed, seg.timer, seg.distance, seg.lap_size, seg.drop_trailing_lap)

    # session
    session = SessionMessage(local_id=LID_SESSION, developer_fields=developer_fields_for_session)
    session.timestamp = unix_ms(end_time_recorded)
    session.start_time = unix_ms(start_time)
    session.sport = seg.sport
    if seg.sub_sport is not None:
        session.sub_sport = seg.sub_sport
    session.total_elapsed_time = seg.elapsed
    session.total_timer_time = seg.timer
    session.total_distance = seg.distance
    if seg.ascent is not None:
        session.total_ascent = seg.ascent
    if avg_hr is not None:
        session.avg_heart_rate = avg_hr
        session.max_heart_rate = max_hr
    if avg_cadence is not None:
        session.avg_cadence = avg_cadence
        session.max_cadence = max_cadence
    if avg_power is not None:
        session.avg_power = round(avg_power)
        if max_power is not None:
            session.max_power = max_power
    if normalized_power is not None:
        session.normalized_power = round(normalized_power)
    if avg_temperature is not None:
        session.avg_temperature = round(avg_temperature)
    session.nec_lat = max(lats)
    session.nec_long = max(lons)
    session.swc_lat = min(lats)
    session.swc_long = min(lons)
    session.num_laps = len(laps)
    if developer_fields_for_session:
        for df in developer_fields_for_session:
            if df.get_value(0) is not None:
                pass  # value already set by caller before this call
    builder.add(session)

    # laps
    for i, lp in enumerate(laps):
        lap_start = start_time + timedelta(seconds=lp.cum_elapsed - lp.elapsed)
        lap_end = start_time + timedelta(seconds=lp.cum_elapsed)
        window_lo = lp.cum_elapsed - lp.elapsed
        window_hi = lp.cum_elapsed
        in_lap = [r for r in records if window_lo <= r.t_offset <= window_hi]
        l_hrs = [r.hr for r in in_lap if r.hr is not None]
        l_cad = [r.cadence for r in in_lap if r.cadence is not None]
        l_pow = [r.power for r in in_lap if r.power is not None]

        lap = LapMessage(local_id=LID_LAP)
        lap.timestamp = unix_ms(lap_end)
        lap.start_time = unix_ms(lap_start)
        lap.sport = seg.sport
        if seg.sub_sport is not None:
            lap.sub_sport = seg.sub_sport
        lap.total_elapsed_time = lp.elapsed
        lap.total_timer_time = lp.timer
        lap.total_distance = lp.distance
        is_trailing_lap = i == len(laps) - 1
        lap.lap_trigger = (
            LapTrigger.SESSION_END
            if (is_trailing_lap and seg.lap_trigger == LapTrigger.SESSION_END)
            else seg.lap_trigger
        )
        if l_hrs:
            lap.avg_heart_rate = round(statistics.mean(l_hrs))
            lap.max_heart_rate = max(l_hrs)
        if seg.lap_power_overrides is not None:
            lap.avg_power = round(seg.lap_power_overrides[i])
            if seg.lap_np_overrides is not None:
                lap.normalized_power = round(seg.lap_np_overrides[i])
        elif l_pow:
            lap.avg_power = round(statistics.mean(l_pow))
            lap.max_power = max(l_pow)
        if l_cad:
            lap.avg_cadence = round(statistics.mean(l_cad))
            lap.max_cadence = max(l_cad)
        builder.add(lap)

    # records
    for r in records:
        rec = RecordMessage(local_id=LID_RECORD)
        rec.timestamp = unix_ms(r.timestamp)
        rec.position_lat = r.lat
        rec.position_long = r.lon
        rec.distance = r.distance
        if r.hr is not None:
            rec.heart_rate = r.hr
        if r.cadence is not None:
            rec.cadence = r.cadence
        if r.power is not None:
            rec.power = r.power
        if r.altitude is not None:
            rec.enhanced_altitude = r.altitude
        if r.temperature is not None:
            rec.temperature = r.temperature
        rec.enhanced_speed = r.speed
        builder.add(rec)

    return SegmentStats(
        seg=seg,
        start_time=start_time,
        end_time_recorded=end_time_recorded,
        derived_end=derived_end,
        avg_hr=avg_hr,
        max_hr=max_hr,
        min_hr=min_hr,
        avg_cadence=avg_cadence,
        max_cadence=max_cadence,
        avg_power=avg_power,
        max_power=max_power,
        normalized_power=normalized_power,
        avg_temperature=avg_temperature,
        nec=(max(lats), max(lons)),
        swc=(min(lats), min(lons)),
        laps=laps,
        record_count=len(records),
        records=records,
    )


def emit_extra_record(
    builder: FitFileBuilder,
    timestamp: datetime,
    lat: float,
    lon: float,
    distance: float,
    hr: int,
    temperature: int | None = None,
) -> None:
    """An orphan record: not part of any session's generated stream."""
    rec = RecordMessage(local_id=LID_RECORD)
    rec.timestamp = unix_ms(timestamp)
    rec.position_lat = lat
    rec.position_long = lon
    rec.distance = distance
    rec.heart_rate = hr
    if temperature is not None:
        rec.temperature = temperature
    builder.add(rec)


def emit_activity(
    builder: FitFileBuilder, end_time: datetime, total_timer_time: float, num_sessions: int
) -> None:
    act = ActivityMessage(local_id=LID_ACTIVITY)
    act.timestamp = unix_ms(end_time)
    act.total_timer_time = total_timer_time
    act.num_sessions = num_sessions
    act.type = 0  # manual
    act.event = Event.ACTIVITY
    act.event_type = EventType.STOP
    builder.add(act)


def emit_file_id(
    builder: FitFileBuilder, manufacturer: int, product, serial: int, time_created: datetime
) -> None:
    fid = FileIdMessage(local_id=LID_FILE_ID)
    fid.type = FileType.ACTIVITY
    fid.manufacturer = manufacturer
    if product is not None:
        fid.product = product
    fid.serial_number = serial
    fid.time_created = unix_ms(time_created)
    builder.add(fid)


# ---------------------------------------------------------------------------
# Fixture 1: apple_multisport.fit
# ---------------------------------------------------------------------------


def build_apple_multisport() -> tuple[bytes, dict]:
    base_dt = utc(2026, 7, 11, 9, 0, 0)
    builder = FitFileBuilder(auto_define=True)

    emit_file_id(builder, Manufacturer.DEVELOPMENT.value, 0, 100001, base_dt)

    # Developer fields: Apple-style. "AVG METs" is declared but never given a value
    # anywhere in the file (Trap 10) -- its FieldDescriptionMessage is emitted and
    # nothing else references it.
    ddid = DeveloperDataIdMessage(local_id=LID_DEV_DATA_ID)
    ddid.developer_data_index = 0
    ddid.application_id = list(range(16))
    builder.add(ddid)

    fdesc_rpe = FieldDescriptionMessage(local_id=LID_FIELD_DESC)
    fdesc_rpe.developer_data_index = 0
    fdesc_rpe.field_definition_number = 0
    fdesc_rpe.fit_base_type_id = BaseType.UINT8
    fdesc_rpe.field_name = "RPE ESTIMATED"
    builder.add(fdesc_rpe)

    fdesc_mets = FieldDescriptionMessage(local_id=LID_FIELD_DESC)
    fdesc_mets.developer_data_index = 0
    fdesc_mets.field_definition_number = 1
    fdesc_mets.fit_base_type_id = BaseType.FLOAT32
    fdesc_mets.field_name = "AVG METs"
    builder.add(fdesc_mets)

    rpe_values = [4, 3, 7, 3, 6]  # swim, t1, bike, t2, run

    segments = [
        Segment(
            name="swim",
            sport=Sport.SWIMMING,
            sub_sport=SubSport.OPEN_WATER,
            start_offset=0,
            elapsed=1800.0,
            distance=1500.0,
            waypoint_a=(49.0000, 8.4000),
            waypoint_b=(49.0020, 8.4015),
            hr_range=(120, 150),
            altitude_range=None,  # Trap 9: no altitude during swim
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="t1",
            sport=Sport.TRANSITION,
            sub_sport=None,
            start_offset=1800,
            elapsed=180.0,
            distance=300.0,
            waypoint_a=(49.0020, 8.4015),
            waypoint_b=(49.0022, 8.4017),
            hr_range=(100, 120),
            altitude_range=(360.0, 2.0),
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="bike",
            sport=Sport.CYCLING,
            sub_sport=None,
            start_offset=1980,
            elapsed=4388.0,
            distance=33746.0,
            ascent=558.0,
            waypoint_a=(49.0022, 8.4017),
            waypoint_b=(49.0400, 8.4550),
            hr_range=(125, 168),
            cadence_range=(75, 95),
            power_range=None,  # Trap 2: no bike power
            altitude_range=(362.0, 558.0),
            lap_size=5000.0,
            drop_trailing_lap=True,  # Trap 1: trailing partial dropped
            lap_trigger=LapTrigger.DISTANCE,
        ),
        Segment(
            name="t2",
            sport=Sport.TRANSITION,
            sub_sport=None,
            start_offset=6368,
            elapsed=120.0,
            distance=218.0,
            waypoint_a=(49.0400, 8.4550),
            waypoint_b=(49.0398, 8.4548),
            hr_range=(105, 125),
            altitude_range=(915.0, -3.0),
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="run",
            sport=Sport.RUNNING,
            sub_sport=None,
            start_offset=6488,
            elapsed=3363.0,
            distance=9172.0,
            ascent=166.0,
            waypoint_a=(49.0398, 8.4548),
            waypoint_b=(49.0000, 8.4000),
            hr_range=(130, 172),
            cadence_range=(80, 92),
            power_range=(290, 310),  # Trap 2: run power
            altitude_range=(912.0, 166.0),
            lap_size=1000.0,
            drop_trailing_lap=False,  # trailing partial included
            lap_trigger=LapTrigger.DISTANCE,
        ),
    ]

    stats = []
    for i, seg in enumerate(segments):
        dev_field = DeveloperField(
            field_id=0,
            name="RPE ESTIMATED",
            base_type=BaseType.UINT8,
            developer_data_index=0,
            growable=True,
            size=0,
        )
        dev_field.set_value(0, rpe_values[i])
        s = emit_segment(builder, base_dt, seg, developer_fields_for_session=[dev_field])
        stats.append(s)

    emit_activity(builder, stats[-1].end_time_recorded, sum(s.seg.timer for s in stats), len(stats))

    fit_bytes = builder.build_bytes()
    meta = {
        "manufacturer": "development",
        "serial_number": 100001,
        "time_created": base_dt,
        "rpe_values": rpe_values,
        "segments": stats,
    }
    return fit_bytes, meta


# ---------------------------------------------------------------------------
# Fixture 2: garmin_multisport.fit
# ---------------------------------------------------------------------------


def build_garmin_multisport() -> tuple[bytes, dict]:
    base_dt = utc(2026, 8, 15, 7, 0, 0)
    builder = FitFileBuilder(auto_define=True)

    emit_file_id(builder, Manufacturer.GARMIN.value, GarminProduct.FENIX7.value, 500500500, base_dt)

    def add_watch_device_info(ts: datetime):
        di = DeviceInfoMessage(local_id=LID_DEVICE_INFO)
        di.timestamp = unix_ms(ts)
        di.device_index = 0
        di.manufacturer = Manufacturer.GARMIN.value
        di.garmin_product = GarminProduct.FENIX7.value
        di.serial_number = 500500500
        di.source_type = SourceType.LOCAL.value
        builder.add(di)

    add_watch_device_info(base_dt)

    bike = Segment(
        name="bike",
        sport=Sport.CYCLING,
        sub_sport=None,
        start_offset=1590,
        elapsed=1205.9,
        timer=1200.0,  # Trap 5: elapsed != timer
        distance=20000.0,
        ascent=210.0,
        waypoint_a=(49.0027, 8.4022),
        waypoint_b=(49.0300, 8.4400),
        hr_range=(128, 165),
        cadence_range=(78, 92),
        power_range=(190, 260),
        altitude_range=(361.0, 210.0),
        temperature_range=(28, 33),  # Trap 8
        lap_size=5000.0,
        drop_trailing_lap=False,
        sample_interval=2,  # "smart recording" proxy: 1 record / 2 s
        lap_trigger=LapTrigger.SESSION_END,
        session_avg_power_override=225.0,
        session_np_override=250.0,
        lap_power_overrides=[210.0, 220.0, 230.0, 240.0],
        lap_np_overrides=[225.0, 235.0, 245.0, 255.0],
    )

    segments_before_bike = [
        Segment(
            name="swim",
            sport=Sport.SWIMMING,
            sub_sport=SubSport.OPEN_WATER,
            start_offset=0,
            elapsed=1500.0,
            distance=2000.0,
            waypoint_a=(49.0000, 8.4000),
            waypoint_b=(49.0025, 8.4020),
            hr_range=(118, 145),
            altitude_range=None,
            temperature_range=(24, 26),
            lap_size=500.0,  # Trap 7: 4 x 500 m laps
            lap_trigger=LapTrigger.DISTANCE,
        ),
        Segment(
            name="t1",
            sport=Sport.TRANSITION,
            sub_sport=None,
            start_offset=1500,
            elapsed=90.0,
            distance=150.0,
            waypoint_a=(49.0025, 8.4020),
            waypoint_b=(49.0027, 8.4022),
            hr_range=(100, 115),
            altitude_range=(360.0, 1.0),
            temperature_range=(27, 28),
            lap_trigger=LapTrigger.SESSION_END,
        ),
    ]

    t2 = Segment(
        name="t2",
        sport=Sport.TRANSITION,
        sub_sport=None,
        # starts 1.1 s after the bike's derived end (Trap 4)
        start_offset=2797,
        elapsed=70.0,
        distance=90.0,
        waypoint_a=(49.0300, 8.4400),
        waypoint_b=(49.0298, 8.4398),
        hr_range=(108, 120),
        altitude_range=(571.0, -2.0),
        temperature_range=(30, 30),
        lap_trigger=LapTrigger.SESSION_END,
    )
    run = Segment(
        name="run",
        sport=Sport.RUNNING,
        sub_sport=None,
        start_offset=2867,
        elapsed=2803.0,
        distance=9239.0,
        ascent=59.0,
        waypoint_a=(49.0298, 8.4398),
        waypoint_b=(49.0000, 8.4000),
        hr_range=(132, 170),
        cadence_range=(82, 90),
        power_range=None,  # Trap 2: no run power
        altitude_range=(569.0, 59.0),
        temperature_range=(29, 31),
        lap_size=1000.0,
        drop_trailing_lap=False,
        sample_interval=[3, 5, 3, 5],  # variable ~4 s average sampling
        lap_trigger=LapTrigger.SESSION_END,
    )

    stats = []
    for seg in segments_before_bike:
        stats.append(emit_segment(builder, base_dt, seg))

    bike_stats = emit_segment(builder, base_dt, bike)
    stats.append(bike_stats)

    # Trap 4: exactly one orphan record, inside the 1.1 s gap between the bike's
    # derived end (start + total_elapsed_time) and T2's recorded start_time.
    orphan_offset = 2796  # bike derived end = 1590 + 1205.9 = 2795.9; T2 starts at 2797
    orphan_ts = base_dt + timedelta(seconds=orphan_offset)
    emit_extra_record(builder, orphan_ts, 49.0301, 8.4401, 20050.0, 150, temperature=33)

    add_watch_device_info(base_dt + timedelta(seconds=2800))

    stats.append(emit_segment(builder, base_dt, t2))
    stats.append(emit_segment(builder, base_dt, run))

    emit_activity(builder, stats[-1].end_time_recorded, sum(s.seg.timer for s in stats), len(stats))

    fit_bytes = builder.build_bytes()
    meta = {
        "manufacturer": "garmin",
        "watch_serial": 500500500,
        "watch_product": "fenix7",
        "time_created": base_dt,
        "segments": stats,
        "orphan_offset": orphan_offset,
        "orphan_timestamp": orphan_ts,
    }
    return fit_bytes, meta


# ---------------------------------------------------------------------------
# Fixture 3: overlap_boundary.fit
# ---------------------------------------------------------------------------


def build_overlap_boundary() -> tuple[bytes, dict]:
    base_dt = utc(2026, 5, 1, 6, 0, 0)
    builder = FitFileBuilder(auto_define=True)
    emit_file_id(builder, Manufacturer.GARMIN.value, GarminProduct.FENIX7.value, 600600600, base_dt)

    OVERLAP = 0.054  # Trap 3: each session's derived end runs this far past the next start

    segments = [
        Segment(
            name="swim",
            sport=Sport.SWIMMING,
            sub_sport=SubSport.OPEN_WATER,
            start_offset=0,
            elapsed=20 + OVERLAP,
            distance=50.0,
            waypoint_a=(49.0000, 8.4000),
            waypoint_b=(49.0002, 8.4001),
            hr_range=(120, 125),
            altitude_range=None,
            sample_interval=5,
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="t1",
            sport=Sport.TRANSITION,
            sub_sport=None,
            start_offset=20,
            elapsed=10 + OVERLAP,
            distance=15.0,
            waypoint_a=(49.0002, 8.4001),
            waypoint_b=(49.0003, 8.4002),
            hr_range=(100, 102),
            altitude_range=(360.0, 0.5),
            sample_interval=5,
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="bike",
            sport=Sport.CYCLING,
            sub_sport=None,
            start_offset=30,
            elapsed=30 + OVERLAP,
            distance=200.0,
            ascent=2.0,
            waypoint_a=(49.0003, 8.4002),
            waypoint_b=(49.0010, 8.4010),
            hr_range=(130, 140),
            altitude_range=(361.0, 2.0),
            sample_interval=5,
            lap_trigger=LapTrigger.SESSION_END,
        ),
        Segment(
            name="t2",
            sport=Sport.TRANSITION,
            sub_sport=None,
            start_offset=60,
            elapsed=10 + OVERLAP,
            distance=10.0,
            waypoint_a=(49.0010, 8.4010),
            waypoint_b=(49.0009, 8.4009),
            hr_range=(100, 101),
            altitude_range=(363.0, -0.5),
            sample_interval=5,
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="run",
            sport=Sport.RUNNING,
            sub_sport=None,
            start_offset=70,
            elapsed=20.0,
            distance=80.0,
            ascent=1.0,
            waypoint_a=(49.0009, 8.4009),
            waypoint_b=(49.0000, 8.4000),
            hr_range=(135, 142),
            altitude_range=(362.0, 1.0),
            sample_interval=5,
            lap_trigger=LapTrigger.SESSION_END,
        ),
    ]

    stats = [emit_segment(builder, base_dt, seg) for seg in segments]
    emit_activity(builder, stats[-1].end_time_recorded, sum(s.seg.timer for s in stats), len(stats))

    fit_bytes = builder.build_bytes()
    meta = {
        "manufacturer": "garmin",
        "time_created": base_dt,
        "segments": stats,
        "overlap_seconds": OVERLAP,
    }
    return fit_bytes, meta


# ---------------------------------------------------------------------------
# Fixture 4: edge_single_bike.fit
# ---------------------------------------------------------------------------


def build_edge_single_bike() -> tuple[bytes, dict]:
    base_dt = utc(2026, 4, 10, 8, 0, 0)
    builder = FitFileBuilder(auto_define=True)
    emit_file_id(
        builder, Manufacturer.GARMIN.value, GarminProduct.EDGE_540.value, 700700700, base_dt
    )

    head_unit_serial = 700700700
    pedals_serial = 800800800
    groupset_serial = 900900900
    hr_strap_serial = 111222333

    di_head = DeviceInfoMessage(local_id=LID_DEVICE_INFO)
    di_head.timestamp = unix_ms(base_dt)
    di_head.device_index = 0
    di_head.manufacturer = Manufacturer.GARMIN.value
    di_head.garmin_product = GarminProduct.EDGE_540.value
    di_head.serial_number = head_unit_serial
    di_head.source_type = SourceType.LOCAL.value
    builder.add(di_head)

    di_pedals = DeviceInfoMessage(local_id=LID_DEVICE_INFO)
    di_pedals.timestamp = unix_ms(base_dt)
    di_pedals.device_index = 1
    di_pedals.manufacturer = Manufacturer.FAVERO_ELECTRONICS.value
    di_pedals.favero_product = FaveroProduct.ASSIOMA_DUO.value
    di_pedals.serial_number = pedals_serial
    di_pedals.source_type = SourceType.ANTPLUS.value
    builder.add(di_pedals)

    di_groupset = DeviceInfoMessage(local_id=LID_DEVICE_INFO)
    di_groupset.timestamp = unix_ms(base_dt)
    di_groupset.device_index = 2
    di_groupset.manufacturer = Manufacturer.SHIMANO.value
    di_groupset.product = 12917
    di_groupset.serial_number = groupset_serial
    builder.add(di_groupset)

    di_hr = DeviceInfoMessage(local_id=LID_DEVICE_INFO)
    di_hr.timestamp = unix_ms(base_dt)
    di_hr.device_index = 3
    di_hr.manufacturer = Manufacturer.POLAR_ELECTRO.value
    di_hr.product = 0
    di_hr.serial_number = hr_strap_serial
    di_hr.source_type = SourceType.ANTPLUS.value
    builder.add(di_hr)

    tz_offset = 7200  # UTC+2 (CEST)
    tc = TimestampCorrelationMessage(local_id=LID_TS_CORR)
    tc.timestamp = unix_ms(base_dt)
    tc.system_timestamp = unix_ms(base_dt)
    tc.local_timestamp = local_timestamp_value(base_dt, tz_offset)
    builder.add(tc)

    bike = Segment(
        name="bike",
        sport=Sport.CYCLING,
        sub_sport=None,
        start_offset=0,
        elapsed=3600.0,
        distance=25000.0,
        ascent=310.0,
        waypoint_a=(49.0000, 8.4000),
        waypoint_b=(49.0350, 8.4400),
        hr_range=(120, 168),
        cadence_range=(70, 95),
        power_range=(150, 290),
        altitude_range=(300.0, 310.0),
        temperature_range=(22, 30),
        lap_size=5000.0,
        drop_trailing_lap=False,
        sample_interval=10,
        lap_trigger=LapTrigger.SESSION_END,
    )
    stats = emit_segment(builder, base_dt, bike)
    emit_activity(builder, stats.end_time_recorded, bike.timer, 1)

    fit_bytes = builder.build_bytes()
    meta = {
        "manufacturer": "garmin",
        "product": "edge_540",
        "head_unit_serial": head_unit_serial,
        "pedals_serial": pedals_serial,
        "groupset_serial": groupset_serial,
        "hr_strap_serial": hr_strap_serial,
        "tz_offset": tz_offset,
        "time_created": base_dt,
        "segments": [stats],
    }
    return fit_bytes, meta


# ---------------------------------------------------------------------------
# Fixture 5: early_start_local_date.fit
# ---------------------------------------------------------------------------


def build_early_start_local_date() -> tuple[bytes, dict]:
    # 22:30 UTC on 2026-06-20; at ~49N 8.4E (CEST, UTC+2) local time is 00:30 on
    # 2026-06-21 -- the local date is one day ahead of the UTC date (Trap 11). No
    # timestamp_correlation message: the local date must come from the GPS position.
    base_dt = utc(2026, 6, 20, 22, 30, 0)
    builder = FitFileBuilder(auto_define=True)
    emit_file_id(builder, Manufacturer.GARMIN.value, GarminProduct.FENIX7.value, 200200200, base_dt)

    segments = [
        Segment(
            name="swim",
            sport=Sport.SWIMMING,
            sub_sport=SubSport.OPEN_WATER,
            start_offset=0,
            elapsed=300.0,
            distance=600.0,
            waypoint_a=(49.0000, 8.4000),
            waypoint_b=(49.0010, 8.4008),
            hr_range=(120, 130),
            altitude_range=None,
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="t1",
            sport=Sport.TRANSITION,
            sub_sport=None,
            start_offset=300,
            elapsed=60.0,
            distance=50.0,
            waypoint_a=(49.0010, 8.4008),
            waypoint_b=(49.0011, 8.4009),
            hr_range=(100, 105),
            altitude_range=(360.0, 1.0),
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="bike",
            sport=Sport.CYCLING,
            sub_sport=None,
            start_offset=360,
            elapsed=600.0,
            distance=4000.0,
            ascent=40.0,
            waypoint_a=(49.0011, 8.4009),
            waypoint_b=(49.0200, 8.4200),
            hr_range=(125, 150),
            cadence_range=(80, 90),
            altitude_range=(361.0, 40.0),
            lap_trigger=LapTrigger.SESSION_END,
        ),
        Segment(
            name="t2",
            sport=Sport.TRANSITION,
            sub_sport=None,
            start_offset=960,
            elapsed=60.0,
            distance=40.0,
            waypoint_a=(49.0200, 8.4200),
            waypoint_b=(49.0199, 8.4199),
            hr_range=(105, 110),
            altitude_range=(401.0, -2.0),
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="run",
            sport=Sport.RUNNING,
            sub_sport=None,
            start_offset=1020,
            elapsed=300.0,
            distance=1500.0,
            ascent=15.0,
            waypoint_a=(49.0199, 8.4199),
            waypoint_b=(49.0000, 8.4000),
            hr_range=(130, 145),
            cadence_range=(85, 90),
            altitude_range=(399.0, 15.0),
            lap_trigger=LapTrigger.SESSION_END,
        ),
    ]

    stats = [emit_segment(builder, base_dt, seg) for seg in segments]
    emit_activity(builder, stats[-1].end_time_recorded, sum(s.seg.timer for s in stats), len(stats))

    fit_bytes = builder.build_bytes()
    meta = {
        "manufacturer": "garmin",
        "time_created": base_dt,
        "segments": stats,
        "tz_offset_for_local_date": 7200,
        "expected_utc_date": "2026-06-20",
        "expected_local_date": "2026-06-21",
    }
    return fit_bytes, meta


# ---------------------------------------------------------------------------
# Fixture 6: missing_t2.fit
# ---------------------------------------------------------------------------

# The gap between the bike's end and the run's start where the watch recorded no T2.
MISSING_T2_GAP_S = 146


def build_missing_t2() -> tuple[bytes, dict]:
    # A watch multisport file with only four sessions: swim, T1, bike, run. The athlete
    # went straight from the bike to the run without the watch switching to a
    # transition, so the T2 is only a gap (seen on a real Apple Watch recording).
    base_dt = utc(2026, 8, 23, 8, 0, 0)
    builder = FitFileBuilder(auto_define=True)
    emit_file_id(builder, Manufacturer.DEVELOPMENT.value, 0, 300300300, base_dt)
    run_offset = 300 + 60 + 600 + MISSING_T2_GAP_S
    segments = [
        Segment(
            name="swim",
            sport=Sport.SWIMMING,
            sub_sport=SubSport.OPEN_WATER,
            start_offset=0,
            elapsed=300.0,
            distance=600.0,
            waypoint_a=(48.1500, 7.7300),
            waypoint_b=(48.1510, 7.7308),
            hr_range=(120, 130),
            altitude_range=None,
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="t1",
            sport=Sport.TRANSITION,
            sub_sport=None,
            start_offset=300,
            elapsed=60.0,
            distance=50.0,
            waypoint_a=(48.1510, 7.7308),
            waypoint_b=(48.1511, 7.7309),
            hr_range=(100, 105),
            altitude_range=(180.0, 1.0),
            lap_trigger=LapTrigger.MANUAL,
        ),
        Segment(
            name="bike",
            sport=Sport.CYCLING,
            sub_sport=None,
            start_offset=360,
            elapsed=600.0,
            distance=4000.0,
            ascent=40.0,
            waypoint_a=(48.1511, 7.7309),
            waypoint_b=(48.1700, 7.7500),
            hr_range=(125, 150),
            cadence_range=(80, 90),
            altitude_range=(181.0, 40.0),
            lap_trigger=LapTrigger.SESSION_END,
        ),
        Segment(
            name="run",
            sport=Sport.RUNNING,
            sub_sport=None,
            start_offset=run_offset,
            elapsed=300.0,
            distance=1500.0,
            ascent=15.0,
            waypoint_a=(48.1699, 7.7499),
            waypoint_b=(48.1500, 7.7300),
            hr_range=(130, 145),
            cadence_range=(85, 90),
            altitude_range=(219.0, 15.0),
            lap_trigger=LapTrigger.SESSION_END,
        ),
    ]
    stats = [emit_segment(builder, base_dt, seg) for seg in segments]
    emit_activity(builder, stats[-1].end_time_recorded, sum(s.seg.timer for s in stats), len(stats))
    meta = {"manufacturer": "development", "time_created": base_dt, "segments": stats}
    return builder.build_bytes(), meta


def self_check_missing_t2(path: Path, meta: dict) -> None:
    check_common(path, meta)
    sessions = [f for f in decode_messages(path) if f.name == "session"]
    sports = [field_val(f, "sport") for f in sessions]
    assert sports == ["swimming", "transition", "cycling", "running"], sports
    bike, run = sessions[2], sessions[3]
    bike_end = field_val(bike, "start_time") + timedelta(
        seconds=field_val(bike, "total_elapsed_time")
    )
    assert (field_val(run, "start_time") - bike_end).total_seconds() == MISSING_T2_GAP_S


# ---------------------------------------------------------------------------
# Self-check: re-read every generated file with fitdecode and verify it agrees
# with the values used to build it.
# ---------------------------------------------------------------------------


def decode_messages(path: Path) -> list[fitdecode.records.FitDataMessage]:
    out = []
    with fitdecode.FitReader(str(path)) as fr:
        for frame in fr:
            if frame.frame_type == fitdecode.FIT_FRAME_DATA:
                out.append(frame)
    return out


def field_val(frame, name, default=None):
    try:
        return frame.get_field(name).value
    except KeyError:
        return default


def check_common(path: Path, meta: dict, expect_altitude_absent_names=("swim",)) -> None:
    frames = decode_messages(path)
    sessions = [f for f in frames if f.name == "session"]
    laps = [f for f in frames if f.name == "lap"]
    records = [f for f in frames if f.name == "record"]
    orphan_ts = meta.get("orphan_timestamp")
    if orphan_ts is not None:
        records = [r for r in records if field_val(r, "timestamp") != orphan_ts]

    assert len(sessions) == len(meta["segments"]), (path, len(sessions), len(meta["segments"]))
    assert len(records) == sum(stat.record_count for stat in meta["segments"]), (
        path,
        len(records),
        sum(stat.record_count for stat in meta["segments"]),
    )

    lap_iter = iter(laps)
    for sess_frame, stat in zip(sessions, meta["segments"], strict=True):
        seg = stat.seg
        tag = f"{path.name}/{seg.name}"

        def field_check(fname, expected, tol=None, _frame=sess_frame, _tag=tag):
            got = field_val(_frame, fname)
            ok = abs(got - expected) < tol if tol is not None else got == expected
            assert ok, (_tag, fname, "got", got, "expected", expected)

        assert field_val(sess_frame, "sport") == seg.sport.name.lower(), (tag, "sport")
        if seg.sub_sport is not None:
            field_check("sub_sport", seg.sub_sport.name.lower())
        assert field_val(sess_frame, "start_time") == stat.start_time, (tag, "start_time")
        field_check("total_elapsed_time", seg.elapsed, tol=0.01)
        field_check("total_timer_time", seg.timer, tol=0.01)
        field_check("total_distance", seg.distance, tol=0.02)
        got_ascent = field_val(sess_frame, "total_ascent")
        if seg.ascent is None:
            assert got_ascent is None, (tag, "ascent should be absent", got_ascent)
        else:
            field_check("total_ascent", round(seg.ascent))
        if stat.avg_hr is not None:
            field_check("avg_heart_rate", stat.avg_hr)
            field_check("max_heart_rate", stat.max_hr)
        if stat.avg_power is not None:
            field_check("avg_power", round(stat.avg_power))
        else:
            assert field_val(sess_frame, "avg_power") is None, (tag, "avg_power should be absent")
        if stat.normalized_power is not None:
            field_check("normalized_power", round(stat.normalized_power))
        field_check("num_laps", len(stat.laps))
        seg_laps = [next(lap_iter) for _ in stat.laps]
        for lap_frame, lp in zip(seg_laps, stat.laps, strict=True):
            got_lap_dist = field_val(lap_frame, "total_distance")
            assert abs(got_lap_dist - lp.distance) < 0.02, (tag, "lap distance", got_lap_dist)

    # Records for a swim segment must have no altitude field at all (Trap 9).
    rec_iter = iter(records)
    for stat in meta["segments"]:
        for _ in stat.records:
            r = next(rec_iter)
            has_alt = r.has_field("enhanced_altitude") or r.has_field("altitude")
            if stat.seg.name == "swim":
                assert not has_alt, (path, "swim record unexpectedly has altitude")
            elif stat.seg.altitude_range is not None:
                assert has_alt, (path, stat.seg.name, "record missing altitude")


def bucket_half_open_clamped(t: float, windows: list[tuple[float, float]]) -> list[int]:
    """windows: list of (start, end) already clamped to the next window's start.
    Returns the indices of every window containing t under half-open [start, end)."""
    return [i for i, (s, e) in enumerate(windows) if s <= t < e]


def bucket_inclusive_unclamped(t: float, windows_unclamped: list[tuple[float, float]]) -> list[int]:
    """The naive (wrong) rule: inclusive bounds, no clamping to the next session."""
    return [i for i, (s, e) in enumerate(windows_unclamped) if s <= t <= e]


def self_check_apple(path: Path, meta: dict) -> None:
    check_common(path, meta)
    bike_stat = next(s for s in meta["segments"] if s.seg.name == "bike")
    lap_sum = sum(lp.distance for lp in bike_stat.laps)
    assert len(bike_stat.laps) == 6, len(bike_stat.laps)
    assert lap_sum < bike_stat.seg.distance - 1000, (lap_sum, bike_stat.seg.distance)  # Trap 1
    run_stat = next(s for s in meta["segments"] if s.seg.name == "run")
    assert len(run_stat.laps) == 10, len(run_stat.laps)
    run_lap_sum = sum(lp.distance for lp in run_stat.laps)
    assert abs(run_lap_sum - run_stat.seg.distance) < 0.5  # run laps reconcile
    swim_stat = next(s for s in meta["segments"] if s.seg.name == "swim")
    assert len(swim_stat.laps) == 1

    frames = decode_messages(path)
    fdescs = [f for f in frames if f.name == "field_description"]
    names = {field_val(f, "field_name") for f in fdescs}
    assert names == {"RPE ESTIMATED", "AVG METs"}, names
    sessions = [f for f in frames if f.name == "session"]
    rpe_seen = [field_val(s, "RPE ESTIMATED") for s in sessions]
    assert rpe_seen == meta["rpe_values"], rpe_seen
    for s in sessions:
        assert field_val(s, "AVG METs") is None  # declared, never populated

    bike_frame = sessions[2]
    assert field_val(bike_frame, "avg_power") is None  # Trap 2: no bike power
    run_frame = sessions[4]
    assert field_val(run_frame, "avg_power") is not None  # Trap 2: run power present


def self_check_garmin(path: Path, meta: dict) -> None:
    check_common(path, meta)
    swim_stat = next(s for s in meta["segments"] if s.seg.name == "swim")
    assert len(swim_stat.laps) == 4  # Trap 7
    bike_stat = next(s for s in meta["segments"] if s.seg.name == "bike")
    assert bike_stat.seg.elapsed != bike_stat.seg.timer  # Trap 5

    frames = decode_messages(path)
    sessions = [f for f in frames if f.name == "session"]
    bike_frame = sessions[2]
    assert field_val(bike_frame, "avg_power") == 225
    assert field_val(bike_frame, "normalized_power") == 250
    run_frame = sessions[4]
    assert field_val(run_frame, "avg_power") is None  # Trap 2: no run power

    records = [f for f in frames if f.name == "record"]
    temps = [field_val(r, "temperature") for r in records]
    assert all(t is not None for t in temps), "Trap 8: temperature must be on every record"

    devices = [f for f in frames if f.name == "device_info"]
    assert len(devices) == 2, len(devices)
    for d in devices:
        assert field_val(d, "manufacturer") == "garmin", (path, "device_info manufacturer")
        assert field_val(d, "serial_number") == meta["watch_serial"], (path, "device_info serial")

    # Trap 4: exactly one orphan record between bike and T2.
    windows = []
    for i, stat in enumerate(meta["segments"]):
        s = unix_ms(stat.start_time) / 1000.0
        e = unix_ms(stat.derived_end) / 1000.0
        if i + 1 < len(meta["segments"]):
            next_s = unix_ms(meta["segments"][i + 1].start_time) / 1000.0
            e = min(e, next_s)
        windows.append((s, e))
    all_ts = [unix_ms(r.timestamp) / 1000.0 for stat in meta["segments"] for r in stat.records]
    all_ts.append(unix_ms(meta["orphan_timestamp"]) / 1000.0)
    orphan_count = sum(1 for t in all_ts if not bucket_half_open_clamped(t, windows))
    assert orphan_count == 1, orphan_count


def self_check_overlap(path: Path, meta: dict) -> None:
    check_common(path, meta)
    segs = meta["segments"]
    windows_clamped = []
    windows_unclamped = []
    for i, stat in enumerate(segs):
        s = stat.seg.start_offset
        e = s + stat.seg.elapsed
        windows_unclamped.append((s, e))
        if i + 1 < len(segs):
            e = min(e, segs[i + 1].seg.start_offset)
        windows_clamped.append((s, e))

    boundary_ts = [seg.seg.start_offset for seg in segs[1:]]  # 20, 30, 60, 70
    for t in boundary_ts:
        hits_clamped = bucket_half_open_clamped(t, windows_clamped)
        assert len(hits_clamped) == 1, (t, hits_clamped)
        hits_naive = bucket_inclusive_unclamped(t, windows_unclamped)
        assert len(hits_naive) == 2, (t, hits_naive)  # proves naive bucketing double-counts


def self_check_edge(path: Path, meta: dict) -> None:
    check_common(path, meta)
    frames = decode_messages(path)
    sessions = [f for f in frames if f.name == "session"]
    assert len(sessions) == 1
    devices = [f for f in frames if f.name == "device_info"]
    assert len(devices) == 4
    by_manufacturer = {field_val(d, "manufacturer"): d for d in devices}
    expected_serials = {
        "garmin": meta["head_unit_serial"],
        "favero_electronics": meta["pedals_serial"],
        "shimano": meta["groupset_serial"],
        "polar_electro": meta["hr_strap_serial"],
    }
    for manufacturer, serial in expected_serials.items():
        got = field_val(by_manufacturer[manufacturer], "serial_number")
        assert got == serial, (path, manufacturer, "serial_number", got, serial)
    tcs = [f for f in frames if f.name == "timestamp_correlation"]
    assert len(tcs) == 1
    expected_local = meta["time_created"] + timedelta(seconds=meta["tz_offset"])
    assert field_val(tcs[0], "local_timestamp") == expected_local


def self_check_early_start(path: Path, meta: dict) -> None:
    check_common(path, meta)
    first_session_start = meta["segments"][0].start_time
    assert first_session_start.date().isoformat() == meta["expected_utc_date"]
    local = first_session_start + timedelta(seconds=meta["tz_offset_for_local_date"])
    assert local.date().isoformat() == meta["expected_local_date"]
    frames = decode_messages(path)
    assert not any(f.name == "timestamp_correlation" for f in frames)


# ---------------------------------------------------------------------------
# README generation
# ---------------------------------------------------------------------------


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_num(x, digits=1):
    if x is None:
        return "—"
    if isinstance(x, float) and abs(x - round(x)) < 1e-9:
        x = round(x)
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def session_table_rows(meta: dict) -> list[str]:
    rows = []
    for stat in meta["segments"]:
        seg = stat.seg
        sub = seg.sub_sport.name.lower() if seg.sub_sport else "—"
        rows.append(
            "| {name} | {sport}/{sub} | {start} | {elapsed} | {timer} | {dist} | {asc} | "
            "{avg_hr}/{max_hr} | {avg_pw} | {np} | {laps} | {n_laps} | {recs} |".format(
                name=seg.name,
                sport=seg.sport.name.lower(),
                sub=sub,
                start=fmt_dt(stat.start_time),
                elapsed=fmt_num(seg.elapsed, 3),
                timer=fmt_num(seg.timer, 3),
                dist=fmt_num(seg.distance, 2),
                asc=fmt_num(seg.ascent, 1),
                avg_hr=fmt_num(stat.avg_hr, 0),
                max_hr=fmt_num(stat.max_hr, 0),
                avg_pw=fmt_num(stat.avg_power, 1),
                np=fmt_num(stat.normalized_power, 1),
                laps=", ".join(fmt_num(lp.distance, 1) for lp in stat.laps),
                n_laps=len(stat.laps),
                recs=stat.record_count,
            )
        )
    return rows


def write_readme(all_meta: dict) -> None:
    lines = []
    lines.append("# FIT test fixtures")
    lines.append("")
    lines.append(
        "Synthetic, deterministic FIT files generated by `make_fixtures.py` "
        "(`uv run python tests/fixtures/make_fixtures.py`, from `backend/`). No real "
        "personal data: coordinates are made up, near a lake at ~49.0N 8.4E (CEST, "
        "UTC+2); serial numbers are made up. Every value below is asserted by "
        "`make_fixtures.py`'s own self-check (it re-reads each file with `fitdecode` "
        "after writing it) — this table is generated from the same constants, so it "
        "cannot drift from the files. Regenerate both together after any change."
    )
    lines.append("")
    lines.append(
        "`distance` on `record` messages is a per-session odometer that resets to 0 "
        "at the start of each session (not a single file-wide cumulative value)."
    )
    lines.append("")

    # apple_multisport.fit
    meta = all_meta["apple_multisport.fit"]
    lines.append("## `apple_multisport.fit`")
    lines.append("")
    lines.append(
        "Reproduces: 5 sessions; dropped trailing bike lap (Trap 1); run power, no bike "
        "power (Trap 2); single swim lap (Trap 7); Apple-style developer fields (Trap 10); "
        "swim with no altitude (Trap 9)."
    )
    lines.append("")
    lines.append(
        f"- `file_id.manufacturer` = `development`, `serial_number` = {meta['serial_number']}"
    )
    lines.append(f"- `file_id.time_created` = {fmt_dt(meta['time_created'])}")
    lines.append(
        "- Developer fields (`developer_data_index` 0): `RPE ESTIMATED` (uint8, present on every "
        f"session, values in session order {meta['rpe_values']}) and `AVG METs` (float32, "
        "declared via `field_description` but never given a value on any message)."
    )
    lines.append("")
    lines.append(
        "| Session | sport/sub_sport | start_time (UTC) | elapsed (s) | timer (s) | "
        "distance (m) | ascent (m) | avg/max HR | avg_power (W) | NP (W) | lap distances (m) | "
        "laps | records |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines.extend(session_table_rows(meta))
    lines.append("")
    lines.append(
        "- Bike laps: 6 lap messages of 5000 m each (30000 m); the session `total_distance` "
        "is 33746 m, so the laps undercount the session by 3746 m — the dropped trailing "
        "partial lap (Trap 1). Do not derive segment totals from lap sums."
    )
    lines.append(
        "- Run laps: 10 lap messages (9 × 1000 m + a 172 m trailing partial), summing to the "
        "full 9172 m session distance — Apple keeps the trailing lap on the run."
    )
    lines.append("- Swim: 1 lap for the whole 1500 m swim (Trap 7).")
    lines.append("- `total_elapsed_time == total_timer_time` on every session and lap (no pauses).")
    lines.append(
        "- No `avg_power`/`max_power` field on the bike session, its laps, or its records "
        "(Trap 2). The run session, its laps, and its records all carry `power`."
    )
    lines.append(
        "- Swim records carry neither `altitude` nor `enhanced_altitude`. Every other "
        "session's records carry `enhanced_altitude`."
    )
    lines.append("")

    # garmin_multisport.fit
    meta = all_meta["garmin_multisport.fit"]
    lines.append("## `garmin_multisport.fit`")
    lines.append("")
    lines.append(
        "Reproduces: bike power + normalized power (Trap 2); 4 swim laps (Trap 7); smart "
        "recording (variable sampling); elapsed ≠ timer (Trap 5); device temperature on "
        "every record (Trap 8); one orphan record (Trap 4)."
    )
    lines.append("")
    lines.append(
        f"- `file_id.manufacturer` = `garmin`, product `fenix7`, serial "
        f"{meta['watch_serial']}; `file_id.time_created` = {fmt_dt(meta['time_created'])}"
    )
    lines.append(
        f"- `device_info`: two messages for the watch itself (`device_index` 0, manufacturer "
        f"`garmin`, `garmin_product` `fenix7`, serial {meta['watch_serial']}), at "
        f"{fmt_dt(meta['time_created'])} and "
        f"{fmt_dt(meta['time_created'] + timedelta(seconds=2800))}."
    )
    lines.append("")
    lines.append(
        "| Session | sport/sub_sport | start_time (UTC) | elapsed (s) | timer (s) | "
        "distance (m) | ascent (m) | avg/max HR | avg_power (W) | NP (W) | lap distances (m) | "
        "laps | records |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines.extend(session_table_rows(meta))
    lines.append("")
    lines.append(
        "- Swim: exactly 4 laps of 500 m each (2000 m total) — Trap 7, contrasting with "
        "Apple's single swim lap."
    )
    lines.append(
        f"- Bike: `total_elapsed_time` = {meta['segments'][2].seg.elapsed} s, "
        f"`total_timer_time` = {meta['segments'][2].seg.timer} s — they differ (Trap 5). "
        f"`avg_power` = 225 W, `normalized_power` = 250 W (session-level, authored; lap "
        f"avg_power = 210/220/230/240 W, lap normalized_power = 225/235/245/255 W). Bike "
        f'records sample every 2 s ("smart recording" proxy).'
    )
    lines.append(
        "- Run: no `power` field anywhere (Trap 2). Records sample on a repeating "
        "3 s/5 s/3 s/5 s pattern (~4 s average) instead of a fixed interval."
    )
    lines.append(
        "- Every record in the file (all 5 sessions plus the orphan) carries a `temperature` "
        "field, and every session with records after T1 also carries `avg_temperature` "
        "(Trap 8)."
    )
    bike_stat = meta["segments"][2]
    ms = round(bike_stat.derived_end.microsecond / 1000)
    derived_end_str = bike_stat.derived_end.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"
    lines.append(
        f"- Orphan record (Trap 4): the bike session's derived end is "
        f"start_time + total_elapsed_time = "
        f"{fmt_dt(bike_stat.start_time)} + {bike_stat.seg.elapsed}s = {derived_end_str}. "
        f"T2 starts at {fmt_dt(meta['segments'][3].start_time)}, 1.1 s later. One record "
        f"sits at {fmt_dt(meta['orphan_timestamp'])}, inside that gap, matching no session "
        "window. It must be counted as an orphan, not silently dropped or assigned."
    )
    lines.append("")

    # overlap_boundary.fit
    meta = all_meta["overlap_boundary.fit"]
    lines.append("## `overlap_boundary.fit`")
    lines.append("")
    lines.append("Reproduces: sub-second session overlap (Trap 3). Intentionally tiny.")
    lines.append("")
    lines.append(
        f"- `file_id.manufacturer` = `garmin`; `file_id.time_created` = "
        f"{fmt_dt(meta['time_created'])}"
    )
    lines.append(
        f"- Every session's `total_elapsed_time` (and `total_timer_time`) is its whole-second "
        f"duration plus {meta['overlap_seconds']} s. Its *derived* end "
        f"(`start_time + total_elapsed_time`) therefore lands {meta['overlap_seconds']} s "
        "past the next session's recorded `start_time` — the sub-second overlap."
    )
    lines.append(
        "- Records are placed on a 5 s grid (0, 5, 10, … 90) from the file start, which "
        "lands exactly on every session boundary (20, 30, 60, 70). Correct half-open "
        "`[start, end)` bucketing with each session's end clamped to `min(derived_end, "
        "next_session.start_time)` assigns each boundary record to exactly the *next* "
        "session, and every record matches exactly one session — no orphans, no double "
        "counts. Inclusive or unclamped bucketing double-counts every boundary record "
        "(matched by both the ending and the starting session)."
    )
    lines.append("")
    lines.append(
        "| Session | sport/sub_sport | start_time (UTC) | elapsed (s) | timer (s) | "
        "distance (m) | ascent (m) | avg/max HR | avg_power (W) | NP (W) | lap distances (m) | "
        "laps | records |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines.extend(session_table_rows(meta))
    lines.append("")

    # edge_single_bike.fit
    meta = all_meta["edge_single_bike.fit"]
    lines.append("## `edge_single_bike.fit`")
    lines.append("")
    lines.append(
        "Reproduces: a single-sport upload (no multisport session split); `device_info` "
        "for a head unit plus three paired sensors with made-up serials; "
        "`timestamp_correlation` giving local = UTC+2 (Trap 11 — the correlation-based "
        "path, contrasted with `early_start_local_date.fit`'s GPS-based path)."
    )
    lines.append("")
    lines.append(
        f"- `file_id.manufacturer` = `garmin`, product `edge_540`, serial "
        f"{meta['head_unit_serial']}"
    )
    lines.append(f"- `file_id.time_created` = {fmt_dt(meta['time_created'])}")
    lines.append(
        "- `device_info` messages (all at file start time):\n"
        f"  - head unit: `device_index` 0, manufacturer `garmin`, `garmin_product` "
        f"`edge_540`, serial {meta['head_unit_serial']}\n"
        f"  - power meter pedals: `device_index` 1, manufacturer `favero_electronics`, "
        f"`favero_product` `assioma_duo`, serial {meta['pedals_serial']}\n"
        f"  - groupset: `device_index` 2, manufacturer `shimano`, `product` 12917, "
        f"serial {meta['groupset_serial']}\n"
        f"  - HR strap: `device_index` 3, manufacturer `polar_electro`, `product` 0, "
        f"serial {meta['hr_strap_serial']}"
    )
    lines.append(
        f"- `timestamp_correlation`: `system_timestamp` = {fmt_dt(meta['time_created'])}, "
        f"`local_timestamp` decodes (via `fitdecode`) to "
        f"{fmt_dt(meta['time_created'] + timedelta(seconds=meta['tz_offset']))} "
        f"(UTC+{meta['tz_offset'] // 3600}h)."
    )
    lines.append("")
    lines.append(
        "| Session | sport/sub_sport | start_time (UTC) | elapsed (s) | timer (s) | "
        "distance (m) | ascent (m) | avg/max HR | avg_power (W) | NP (W) | lap distances (m) | "
        "laps | records |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines.extend(session_table_rows(meta))
    lines.append("")

    # early_start_local_date.fit
    meta = all_meta["early_start_local_date.fit"]
    lines.append("## `early_start_local_date.fit`")
    lines.append("")
    lines.append(
        "Reproduces: UTC date ≠ local date at the race start (Trap 11 — the GPS-based "
        "path: no `timestamp_correlation` message is present, so the local date can only "
        "come from the start position's timezone)."
    )
    lines.append("")
    lines.append(
        f"- `file_id.manufacturer` = `garmin`; `file_id.time_created` = "
        f"{fmt_dt(meta['time_created'])}"
    )
    first = meta["segments"][0]
    lines.append(
        f"- First session (`{first.seg.name}`) `start_time` = {fmt_dt(first.start_time)} "
        f"UTC, at position ({first.seg.waypoint_a[0]}, {first.seg.waypoint_a[1]}), which "
        f"is in the CEST timezone (UTC+2 in June). UTC calendar date = "
        f"`{meta['expected_utc_date']}`; local calendar date = `{meta['expected_local_date']}`."
    )
    lines.append("- No `timestamp_correlation` message is present in this file.")
    lines.append("")
    lines.append(
        "| Session | sport/sub_sport | start_time (UTC) | elapsed (s) | timer (s) | "
        "distance (m) | ascent (m) | avg/max HR | avg_power (W) | NP (W) | lap distances (m) | "
        "laps | records |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines.extend(session_table_rows(meta))
    lines.append("")

    # missing_t2.fit
    meta = all_meta["missing_t2.fit"]
    lines.append("## `missing_t2.fit`")
    lines.append("")
    lines.append(
        "Reproduces: a multisport file with no recorded T2 (four sessions: swim, T1, bike, "
        f"run). The run starts {MISSING_T2_GAP_S} s after the bike's end; the importer "
        "derives T2 from that gap."
    )
    lines.append("")
    lines.append(
        "| Session | sport/sub_sport | start_time (UTC) | elapsed (s) | timer (s) | "
        "distance (m) | ascent (m) | avg/max HR | avg_power (W) | NP (W) | lap distances (m) | "
        "laps | records |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines.extend(session_table_rows(meta))
    lines.append("")

    lines.append("## Regenerating")
    lines.append("")
    lines.append("```")
    lines.append("cd backend")
    lines.append("uv run python tests/fixtures/make_fixtures.py")
    lines.append("```")
    lines.append("")
    lines.append(
        "The script writes the six `.fit` files above and this README from the same "
        "constants, then re-reads every file with `fitdecode` and asserts it against "
        "those constants. Only changing a fixture needs `fit-tool`/`fitdecode` "
        "(dev-only dependencies, never installed in the runtime image); the committed "
        "`.fit` files and this README are enough to run the test suite."
    )
    lines.append("")

    (FIXTURES_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    builders = {
        "apple_multisport.fit": build_apple_multisport,
        "garmin_multisport.fit": build_garmin_multisport,
        "overlap_boundary.fit": build_overlap_boundary,
        "edge_single_bike.fit": build_edge_single_bike,
        "early_start_local_date.fit": build_early_start_local_date,
        "missing_t2.fit": build_missing_t2,
    }
    checkers = {
        "apple_multisport.fit": self_check_apple,
        "garmin_multisport.fit": self_check_garmin,
        "overlap_boundary.fit": self_check_overlap,
        "edge_single_bike.fit": self_check_edge,
        "early_start_local_date.fit": self_check_early_start,
        "missing_t2.fit": self_check_missing_t2,
    }

    all_meta = {}
    for filename, build_fn in builders.items():
        fit_bytes, meta = build_fn()
        out_path = FIXTURES_DIR / filename
        out_path.write_bytes(fit_bytes)
        size_kb = len(fit_bytes) / 1024
        print(f"wrote {filename}: {len(fit_bytes):,} bytes ({size_kb:.1f} KiB)")
        checkers[filename](out_path, meta)
        print(f"  self-check OK ({sum(s.record_count for s in meta['segments'])} records)")
        all_meta[filename] = meta

    write_readme(all_meta)
    print("wrote README.md")


if __name__ == "__main__":
    main()
