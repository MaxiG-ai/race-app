"""FIT parsing: bytes in, normalized dataclasses out.

Deliberately free of database and framework concerns so it can be tested directly against
real files. Everything here is driven by what the five fixtures actually contain (§3 of
PLAN.md); the vendor differences are the reason most of this code exists.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO

import fitdecode

from app.models.base import SEGMENT_ORDER, Segment

#: FIT semicircle → degrees.
_SEMICIRCLE = 180.0 / 2**31

#: Sport values that map onto a segment. Transitions are positional, not named.
_SPORT_TO_SEGMENT = {
    "swimming": Segment.SWIM,
    "cycling": Segment.BIKE,
    "running": Segment.RUN,
}

#: Developer fields, Apple-only and sparse — every one is optional.
_DEV_RPE = "WORKOUT RPE ESTIMATED"
_DEV_METS = "AVG METs"
_DEV_HUMIDITY = "SESSION WEATHER HUMIDITY"

#: A trailing lap counts as partial below this fraction of the segment's longest lap.
_PARTIAL_LAP_RATIO = 0.95


class FitParseError(ValueError):
    """The file could not be interpreted as a race recording."""


def _sc(value: Any) -> float | None:
    return value * _SEMICIRCLE if isinstance(value, (int, float)) else None


def _as_dict(frame: Any) -> dict[str, Any]:
    return {f.name: f.value for f in frame.fields}


@dataclass(slots=True)
class LapData:
    lap_index: int
    start_time: datetime
    total_time: float
    distance_m: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_power: int | None = None
    normalized_power: int | None = None
    avg_speed: float | None = None
    elevation_gain_m: float | None = None
    cadence_avg: float | None = None
    is_partial: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SegmentData:
    """One session: one leg of a race as recorded by one device."""

    segment: Segment
    start_time: datetime
    end_time: datetime
    elapsed_time: float
    moving_time: float | None = None
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_power: int | None = None
    normalized_power: int | None = None
    avg_speed: float | None = None
    avg_cadence: float | None = None
    avg_temp_c: float | None = None
    rpe_estimated: float | None = None
    avg_mets: float | None = None
    humidity_pct: float | None = None
    bounds: dict[str, float] | None = None
    gps_track: list[dict[str, Any]] = field(default_factory=list)
    progress_series: list[dict[str, float]] = field(default_factory=list)
    laps: list[LapData] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def lap_distance_sum_m(self) -> float:
        """Summed lap distance.

        Exposed so callers can *see* the Apple discrepancy rather than trip over it:
        segment totals always come from `distance_m` (the session), never from this.
        """
        return sum(lap.distance_m or 0.0 for lap in self.laps)


@dataclass(slots=True)
class DeviceInfo:
    device_key: str
    manufacturer: str | None
    serial_number: str | None
    product: str | None
    is_creator: bool


@dataclass(slots=True)
class ParsedFit:
    is_multisport: bool
    segments: list[SegmentData]
    devices: list[DeviceInfo]
    manufacturer: str | None
    creator_device_key: str | None
    #: UTC offset from `timestamp_correlation` (Garmin only); None when the file
    #: doesn't say and the caller must fall back to a lookup from coordinates.
    utc_offset_seconds: int | None
    #: Records falling in no session window. Tolerated, not fatal — the Garmin fixture
    #: has one in the 1.1 s gap between bike end and T2 start.
    orphan_record_count: int
    record_count: int

    @property
    def start_time(self) -> datetime:
        return self.segments[0].start_time

    @property
    def end_time(self) -> datetime:
        return self.segments[-1].end_time

    def segment(self, segment: Segment) -> SegmentData | None:
        return next((s for s in self.segments if s.segment == segment), None)


def parse_fit(source: str | BinaryIO, *, progress_points: int = 200) -> ParsedFit:
    """Parse a FIT file into segments, laps and per-segment streams."""
    sessions: list[dict[str, Any]] = []
    laps: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    devices: list[DeviceInfo] = []
    file_id: dict[str, Any] = {}
    utc_offset: int | None = None
    seen_device_keys: set[str] = set()

    try:
        with fitdecode.FitReader(source) as reader:
            for frame in reader:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                name = frame.name
                if name == "session":
                    sessions.append(_as_dict(frame))
                elif name == "lap":
                    laps.append(_as_dict(frame))
                elif name == "record":
                    records.append(_as_dict(frame))
                elif name == "file_id":
                    file_id = _as_dict(frame)
                elif name == "device_info":
                    _collect_device(_as_dict(frame), devices, seen_device_keys)
                elif name == "timestamp_correlation":
                    utc_offset = _utc_offset(_as_dict(frame))
    except fitdecode.FitError as exc:  # pragma: no cover - depends on corrupt input
        raise FitParseError(f"could not decode FIT file: {exc}") from exc

    if not sessions:
        raise FitParseError("file contains no session messages")

    segments = _map_segments(sessions)
    _assign_records(segments, sessions, records)
    orphans = _orphan_count(sessions, records)

    lap_buckets = _bucket_laps(segments, laps)
    for seg, seg_laps in zip(segments, lap_buckets, strict=True):
        seg.laps = seg_laps
        seg.progress_series = _build_progress_series(seg, progress_points)

    creator = next((d for d in devices if d.is_creator), None)
    return ParsedFit(
        is_multisport=len(sessions) == 5,
        segments=segments,
        devices=devices,
        manufacturer=file_id.get("manufacturer"),
        creator_device_key=creator.device_key if creator else None,
        utc_offset_seconds=utc_offset,
        orphan_record_count=orphans,
        record_count=len(records),
    )


def _utc_offset(correlation: dict[str, Any]) -> int | None:
    system = correlation.get("system_timestamp")
    local = correlation.get("local_timestamp")
    if isinstance(system, datetime) and isinstance(local, datetime):
        return int((local - system).total_seconds())
    return None


def _collect_device(
    info: dict[str, Any], devices: list[DeviceInfo], seen: set[str]
) -> None:
    """Record each distinct sensor once.

    Serials are stable across rides, which is what lets a bike resolve itself from its
    power meter after being named a single time (§3 Gear detection).
    """
    serial = info.get("serial_number")
    manufacturer = info.get("manufacturer")
    if serial is None or manufacturer is None:
        return
    key = f"{manufacturer}:{serial}"
    if key in seen:
        return
    seen.add(key)
    product = info.get("garmin_product") or info.get("product_name") or info.get("product")
    devices.append(
        DeviceInfo(
            device_key=key,
            manufacturer=str(manufacturer),
            serial_number=str(serial),
            product=str(product) if product is not None else None,
            is_creator=info.get("device_index") == "creator",
        )
    )


def _map_segments(sessions: list[dict[str, Any]]) -> list[SegmentData]:
    """Map sessions onto segments, validating the sequence rather than trusting it."""
    if len(sessions) == 5:
        sports = [s.get("sport") for s in sessions]
        expected = ["swimming", "transition", "cycling", "transition", "running"]
        if sports != expected:
            raise FitParseError(
                f"5-session file is not a triathlon: expected {expected}, got {sports}"
            )
        return [_session_to_segment(s, seg) for s, seg in zip(sessions, SEGMENT_ORDER, strict=True)]

    if len(sessions) == 1:
        sport = sessions[0].get("sport")
        segment = _SPORT_TO_SEGMENT.get(str(sport))
        if segment is None:
            raise FitParseError(f"unsupported single-sport activity: {sport!r}")
        return [_session_to_segment(sessions[0], segment)]

    raise FitParseError(
        f"expected 1 or 5 sessions, found {len(sessions)} — not a recognised race recording"
    )


def _session_to_segment(session: dict[str, Any], segment: Segment) -> SegmentData:
    start = session["start_time"]
    elapsed = float(session.get("total_elapsed_time") or 0.0)
    moving = session.get("total_timer_time")
    bounds = None
    if session.get("nec_lat") is not None and session.get("swc_lat") is not None:
        bounds = {
            "north": _sc(session["nec_lat"]),
            "east": _sc(session["nec_long"]),
            "south": _sc(session["swc_lat"]),
            "west": _sc(session["swc_long"]),
        }
    return SegmentData(
        segment=segment,
        start_time=start,
        end_time=start + timedelta(seconds=elapsed),
        elapsed_time=elapsed,
        moving_time=float(moving) if moving is not None else None,
        distance_m=session.get("total_distance"),
        elevation_gain_m=session.get("total_ascent"),
        avg_hr=session.get("avg_heart_rate"),
        max_hr=session.get("max_heart_rate"),
        avg_power=session.get("avg_power"),
        normalized_power=session.get("normalized_power"),
        avg_speed=session.get("enhanced_avg_speed") or session.get("avg_speed"),
        avg_cadence=session.get("avg_cadence"),
        avg_temp_c=session.get("avg_temperature"),
        rpe_estimated=session.get(_DEV_RPE),
        avg_mets=session.get(_DEV_METS),
        humidity_pct=session.get(_DEV_HUMIDITY),
        bounds=bounds,
        raw={k: v for k, v in session.items() if _jsonable(v)},
    )


def _effective_windows(sessions: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    """Half-open, non-overlapping windows, one per session.

    Two separate problems, one fix. Consecutive sessions overlap sub-second (a session
    ends 11:51:42.054 while the next starts 11:51:42), and on Garmin T2's window extends
    1.45 s past the run's start — so half-open intervals alone still double-count. Each
    window is therefore clamped to the next session's start: the next leg beginning is
    what actually ended the previous one.
    """
    windows: list[tuple[datetime, datetime]] = []
    for i, session in enumerate(sessions):
        start = session["start_time"]
        end = start + timedelta(seconds=float(session.get("total_elapsed_time") or 0.0))
        if i + 1 < len(sessions):
            end = min(end, sessions[i + 1]["start_time"])
        windows.append((start, end))
    return windows


def _assign_records(
    segments: list[SegmentData], sessions: list[dict[str, Any]], records: list[dict[str, Any]]
) -> None:
    windows = _effective_windows(sessions)
    for seg, (start, end) in zip(segments, windows, strict=True):
        in_window = (r for r in records if start <= r["timestamp"] < end)
        points = (_record_to_point(r, seg.start_time) for r in in_window)
        seg.gps_track = [p for p in points if p is not None]


def _orphan_count(sessions: list[dict[str, Any]], records: list[dict[str, Any]]) -> int:
    windows = _effective_windows(sessions)
    count = 0
    for record in records:
        ts = record.get("timestamp")
        if ts is None or not any(start <= ts < end for start, end in windows):
            count += 1
    return count


def _record_to_point(record: dict[str, Any], origin: datetime) -> dict[str, Any] | None:
    lat, lng = record.get("position_lat"), record.get("position_long")
    timestamp = record.get("timestamp")
    if timestamp is None:
        return None
    return {
        "lat": _sc(lat),
        "lng": _sc(lng),
        "elevation": record.get("enhanced_altitude") or record.get("altitude"),
        "t": round((timestamp - origin).total_seconds(), 3),
        # From the device's own odometer. Recomputing it from coordinates is noisy and
        # disagrees with what the watch reported (§11).
        "distance_m": record.get("distance"),
    }


def _bucket_laps(
    segments: list[SegmentData], laps: list[dict[str, Any]]
) -> list[list[LapData]]:
    buckets: list[list[dict[str, Any]]] = [[] for _ in segments]
    for lap in laps:
        start = lap.get("start_time")
        if start is None:
            continue
        idx = _lap_segment_index(segments, start)
        if idx is not None:
            buckets[idx].append(lap)

    out: list[list[LapData]] = []
    for bucket in buckets:
        bucket.sort(key=lambda entry: entry["start_time"])
        out.append(_to_lap_data(bucket))
    return out


def _lap_segment_index(segments: list[SegmentData], start: datetime) -> int | None:
    for i, seg in enumerate(segments):
        upper = segments[i + 1].start_time if i + 1 < len(segments) else seg.end_time
        if seg.start_time <= start < max(upper, seg.start_time + timedelta(microseconds=1)):
            return i
    # A lap starting exactly at the final segment's end still belongs to it.
    return len(segments) - 1 if segments and start >= segments[-1].start_time else None


def _to_lap_data(laps: list[dict[str, Any]]) -> list[LapData]:
    distances = [lap.get("total_distance") or 0.0 for lap in laps]
    longest = max(distances, default=0.0)
    out: list[LapData] = []
    for index, lap in enumerate(laps):
        distance = lap.get("total_distance")
        is_last = index == len(laps) - 1
        partial = bool(
            is_last and longest > 0 and (distance or 0.0) < longest * _PARTIAL_LAP_RATIO
        )
        out.append(
            LapData(
                lap_index=index,
                start_time=lap["start_time"],
                total_time=float(lap.get("total_elapsed_time") or 0.0),
                distance_m=distance,
                avg_hr=lap.get("avg_heart_rate"),
                max_hr=lap.get("max_heart_rate"),
                avg_power=lap.get("avg_power"),
                normalized_power=lap.get("normalized_power"),
                avg_speed=lap.get("enhanced_avg_speed") or lap.get("avg_speed"),
                elevation_gain_m=lap.get("total_ascent"),
                cadence_avg=lap.get("avg_cadence"),
                is_partial=partial,
                raw={k: v for k, v in lap.items() if _jsonable(v)},
            )
        )
    return out


def _build_progress_series(seg: SegmentData, points: int) -> list[dict[str, float]]:
    """Resample a segment onto an even progress grid.

    Progress runs on distance where there is distance to run on, and on time otherwise —
    transitions cover 20 m in one fixture, where a distance grid would be meaningless.
    """
    if points < 2 or not seg.gps_track:
        return []

    samples = [
        (p["t"], p["distance_m"])
        for p in seg.gps_track
        if p.get("t") is not None and p.get("distance_m") is not None
    ]
    if len(samples) < 2 or (seg.distance_m or 0) <= 1.0:
        return _time_grid(seg, points)

    # Device odometers are monotonic in principle; enforce it so interpolation is stable.
    times: list[float] = []
    dists: list[float] = []
    running = float("-inf")
    base = samples[0][1]
    for t, d in samples:
        d = max(d - base, running if running > float("-inf") else 0.0)
        if d > running:
            running = d
            times.append(t)
            dists.append(d)
    if len(dists) < 2:
        # Too few distinct odometer readings to interpolate on distance — a 7.7 s
        # transition covering 20 m has almost none. Time is the honest axis there.
        return _time_grid(seg, points)

    total = dists[-1]
    series: list[dict[str, float]] = []
    cursor = 0
    for i in range(points):
        progress = i / (points - 1)
        target = total * progress
        while cursor + 1 < len(dists) - 1 and dists[cursor + 1] < target:
            cursor += 1
        d0, d1 = dists[cursor], dists[cursor + 1]
        t0, t1 = times[cursor], times[cursor + 1]
        ratio = 0.0 if d1 == d0 else (target - d0) / (d1 - d0)
        series.append(
            {
                "progress": round(progress, 6),
                "elapsed_s": round(t0 + (t1 - t0) * max(0.0, min(1.0, ratio)), 3),
                "distance_m": round(target, 2),
            }
        )
    return series


def _time_grid(seg: SegmentData, points: int) -> list[dict[str, float]]:
    """Even grid on elapsed time, for segments with no usable distance signal."""
    span = points - 1
    return [
        {
            "progress": round(i / span, 6),
            "elapsed_s": round(seg.elapsed_time * i / span, 3),
            "distance_m": round((seg.distance_m or 0.0) * i / span, 2),
        }
        for i in range(points)
    ]


def _jsonable(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def local_date(start: datetime, utc_offset_seconds: int | None) -> Any:
    """The race's local calendar date.

    FIT timestamps are UTC while a race belongs to the local day it was run on; an early
    start would otherwise land on the wrong date (§3).
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if utc_offset_seconds is None:
        return start.date()
    return (start + timedelta(seconds=utc_offset_seconds)).date()


def summarize(parsed: ParsedFit) -> dict[str, Any]:
    """Compact preview for the grouping tray, so it renders without re-reading files."""
    return {
        "is_multisport": parsed.is_multisport,
        "manufacturer": parsed.manufacturer,
        "device_key": parsed.creator_device_key,
        "record_count": parsed.record_count,
        "orphan_record_count": parsed.orphan_record_count,
        "segments": [
            {
                "segment": seg.segment.value,
                "start_time": seg.start_time.isoformat(),
                "elapsed_time": seg.elapsed_time,
                "distance_m": seg.distance_m,
                "lap_count": len(seg.laps),
                "has_power": seg.avg_power is not None,
            }
            for seg in parsed.segments
        ],
    }


def median_lap_distance(seg: SegmentData) -> float | None:
    full = [lap.distance_m for lap in seg.laps if lap.distance_m and not lap.is_partial]
    return statistics.median(full) if full else None
