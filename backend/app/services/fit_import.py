"""FIT bytes in, normalised dataclasses out. No database, no I/O.

The vendor behaviour this code handles is documented in docs/reference/fit-files.md;
the trap numbers in comments refer to it.
"""

from __future__ import annotations

import io
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import fitdecode

from app.models.enums import Segment, Sport, UploadKind
from app.services.tz import timezone_at

SEMICIRCLE_TO_DEG = 180 / 2**31
PROGRESS_POINTS = 200

# A multisport race is exactly these five sessions, in this order.
_MULTISPORT: tuple[tuple[Segment, str], ...] = (
    (Segment.SWIM, "swimming"),
    (Segment.T1, "transition"),
    (Segment.BIKE, "cycling"),
    (Segment.T2, "transition"),
    (Segment.RUN, "running"),
)
_FIT_SPORT: dict[str, Sport] = {
    "swimming": Sport.SWIM,
    "cycling": Sport.BIKE,
    "running": Sport.RUN,
}


class FitParseError(Exception):
    """User-facing: says what was found and what was expected."""


@dataclass
class ParsedLap:
    lap_index: int
    start_time: datetime
    total_time: int
    distance_m: float | None
    elevation_gain_m: float | None
    avg_hr: int | None
    max_hr: int | None
    avg_power: int | None
    normalized_power: int | None
    cadence_avg: int | None
    is_partial: bool
    raw_data: dict[str, Any]


@dataclass
class ParsedDevice:
    manufacturer: str
    serial_number: str
    label: str | None

    @property
    def key(self) -> str:
        return f"{self.manufacturer}:{self.serial_number}"


@dataclass
class ParsedSession:
    sport: Sport | None  # None for transitions
    segment: Segment | None  # None for a single-sport file until it is grouped
    start_time: datetime
    elapsed_time: int
    moving_time: int | None
    distance_m: float | None
    elevation_gain_m: float | None
    avg_hr: int | None
    max_hr: int | None
    avg_power: int | None
    normalized_power: int | None
    avg_cadence: int | None
    avg_temp_c: float | None
    rpe_estimated: float | None
    bounds: tuple[float, float, float, float] | None  # n, s, e, w
    gps_track: list[dict[str, Any]]
    progress_series: list[dict[str, Any]]
    laps: list[ParsedLap]
    devices: list[ParsedDevice]
    raw_data: dict[str, Any]

    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(seconds=self.elapsed_time)


@dataclass
class ParsedFile:
    kind: UploadKind
    sessions: list[ParsedSession]
    local_date: date
    orphan_record_count: int
    device_key: str | None  # the recording device itself, from file_id
    warnings: list[str] = field(default_factory=list)

    @property
    def start_time(self) -> datetime:
        return self.sessions[0].start_time

    @property
    def end_time(self) -> datetime:
        return self.sessions[-1].end_time

    @property
    def detected_sport(self) -> Sport | None:
        if self.kind is UploadKind.MULTISPORT:
            return None
        return self.sessions[0].sport or Sport.OTHER

    @property
    def distance_m(self) -> float | None:
        values = [s.distance_m for s in self.sessions if s.distance_m is not None]
        return sum(values) if values else None

    @property
    def location(self) -> tuple[float | None, float | None]:
        """Centroid of every session's bounding box: a draft location the owner can fix."""
        boxes = [s.bounds for s in self.sessions if s.bounds]
        if not boxes:
            return None, None
        n, s = max(b[0] for b in boxes), min(b[1] for b in boxes)
        e, w = max(b[2] for b in boxes), min(b[3] for b in boxes)
        return (n + s) / 2, (e + w) / 2


def parse_fit(data: bytes) -> ParsedFile:
    """Raises FitParseError for anything that would otherwise half-import a file."""
    sessions: list[dict[str, Any]] = []
    laps: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    device_infos: list[dict[str, Any]] = []
    tcorr: dict[str, Any] | None = None
    file_id: dict[str, Any] = {}
    try:
        with fitdecode.FitReader(io.BytesIO(data)) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                fields = {f.name or f"field_{f.def_num}": f.value for f in frame.fields}
                match frame.name:
                    case "record":
                        records.append(fields)
                    case "session":
                        sessions.append(fields)
                    case "lap":
                        laps.append(fields)
                    case "device_info":
                        device_infos.append(fields)
                    case "timestamp_correlation" if tcorr is None:
                        tcorr = fields
                    case "file_id" if not file_id:
                        file_id = fields
    except Exception as exc:  # fitdecode raises several unrelated types on corrupt input
        raise FitParseError(f"Could not read the FIT file: {exc}") from exc

    if not sessions:
        raise FitParseError("The FIT file contains no sessions.")
    sessions.sort(key=lambda s: s["start_time"])
    kind, segments = _check_shape(sessions)

    windows = _windows(sessions)
    record_buckets, orphans = _bucket_records(records, windows)
    lap_buckets = _bucket_loosely(laps, windows, "start_time")
    device_buckets = _bucket_loosely(device_infos, windows, "timestamp")
    # Devices listed before the first session starts (common on Garmin) belong to it.
    device_buckets[0].extend(d for d in device_infos if _before(d.get("timestamp"), windows))

    parsed = [
        _session(segments[i], sessions[i], lap_buckets[i], record_buckets[i], device_buckets[i])
        for i in range(len(sessions))
    ]
    local_date, tz_warning = _local_date(parsed[0].start_time, records, tcorr)
    warnings = [tz_warning] if tz_warning else []
    if orphans:
        warnings.append(f"{orphans} record(s) fell outside every session and were dropped.")
    device_key = None
    if file_id.get("serial_number") is not None:
        device_key = f"{file_id.get('manufacturer') or 'unknown'}:{file_id['serial_number']}"
    return ParsedFile(kind, parsed, local_date, orphans, device_key, warnings)


# --- shape and bucketing ----------------------------------------------------------------


def _check_shape(sessions: list[dict[str, Any]]) -> tuple[UploadKind, list[Segment | None]]:
    found = [s.get("sport") for s in sessions]
    if len(sessions) == 1:
        return UploadKind.SINGLE_SPORT, [None]
    if found == [sport for _, sport in _MULTISPORT]:
        return UploadKind.MULTISPORT, [segment for segment, _ in _MULTISPORT]
    raise FitParseError(
        f"Expected one session, or five in the order swim, transition, bike, transition, "
        f"run; found {len(sessions)}: {', '.join(str(s) for s in found)}."
    )


def _windows(sessions: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    """Half-open [start, end) windows, each end clamped to the next start (Trap 3).

    The clamp only bites where a session's derived end runs past the next recorded
    start; where there is a real gap it does nothing, so orphans survive (Trap 4).
    """
    windows = []
    for i, s in enumerate(sessions):
        start = s["start_time"]
        end = start + timedelta(seconds=s.get("total_elapsed_time") or 0)
        if i + 1 < len(sessions):
            end = min(end, sessions[i + 1]["start_time"])
        windows.append((start, end))
    return windows


def _bucket_records(
    records: list[dict[str, Any]], windows: list[tuple[datetime, datetime]]
) -> tuple[list[list[dict[str, Any]]], int]:
    buckets: list[list[dict[str, Any]]] = [[] for _ in windows]
    orphans = 0
    for r in records:
        ts = r.get("timestamp")
        hits = [i for i, (start, end) in enumerate(windows) if ts and start <= ts < end]
        if len(hits) > 1:
            raise FitParseError(
                f"A record at {ts.isoformat()} falls inside more than one session; "
                "the session boundaries are corrupt and nothing was imported."
            )
        if hits:
            buckets[hits[0]].append(r)
        else:
            orphans += 1  # Trap 4: ordinary, dropped and counted
    return buckets, orphans


def _bucket_loosely(
    items: list[dict[str, Any]], windows: list[tuple[datetime, datetime]], key: str
) -> list[list[dict[str, Any]]]:
    """Laps and device_info: first matching window wins; a miss is not an error."""
    buckets: list[list[dict[str, Any]]] = [[] for _ in windows]
    for item in items:
        ts = item.get(key)
        for i, (start, end) in enumerate(windows):
            if ts and start <= ts < end:
                buckets[i].append(item)
                break
    return buckets


def _before(ts: datetime | None, windows: list[tuple[datetime, datetime]]) -> bool:
    return ts is not None and ts < windows[0][0]


# --- sessions and laps ------------------------------------------------------------------


def _deg(value: int | None) -> float | None:
    return value * SEMICIRCLE_TO_DEG if value is not None else None


def _int(value: float | None) -> int | None:
    return round(value) if value is not None else None


def _cadence(d: dict[str, Any]) -> int | None:
    return _int(d.get("avg_running_cadence") or d.get("avg_cadence"))


def _session(
    segment: Segment | None,
    s: dict[str, Any],
    laps: list[dict[str, Any]],
    records: list[dict[str, Any]],
    device_infos: list[dict[str, Any]],
) -> ParsedSession:
    start = s["start_time"]
    distance = s.get("total_distance")
    track = _track(start, records)
    rpe = s.get("RPE ESTIMATED")
    if rpe is None and s.get("workout_rpe") is not None:
        rpe = s["workout_rpe"] / 10
    return ParsedSession(
        sport=_FIT_SPORT.get(s.get("sport") or "", Sport.OTHER)
        if s.get("sport") != "transition"
        else None,
        segment=segment,
        start_time=start,
        elapsed_time=round(s.get("total_elapsed_time") or 0),
        moving_time=_int(s.get("total_timer_time")),
        distance_m=distance,
        elevation_gain_m=s.get("total_ascent"),
        avg_hr=s.get("avg_heart_rate"),
        max_hr=s.get("max_heart_rate"),
        avg_power=s.get("avg_power"),
        normalized_power=s.get("normalized_power"),
        avg_cadence=_cadence(s),
        avg_temp_c=s.get("avg_temperature"),
        rpe_estimated=rpe,
        bounds=_bounds(s, track),
        gps_track=track,
        progress_series=progress_series(track, s.get("total_elapsed_time") or 0, distance),
        laps=_laps(laps),
        devices=_devices(device_infos),
        raw_data=_json_safe(s),
    )


def _track(start: datetime, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{lat, lng, elevation, t, distance_m}]; t is seconds since the session start and
    distance_m the device's own odometer, made relative to the session start."""
    points = []
    first_distance = next((r["distance"] for r in records if r.get("distance") is not None), 0)
    for r in records:
        lat, lng = _deg(r.get("position_lat")), _deg(r.get("position_long"))
        if lat is None or lng is None:
            continue
        elevation = r.get("enhanced_altitude", r.get("altitude"))  # Trap 9
        distance = r.get("distance")
        points.append(
            {
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "elevation": round(elevation, 1) if elevation is not None else None,
                "t": round((r["timestamp"] - start).total_seconds()),
                "distance_m": round(distance - first_distance, 1) if distance is not None else None,
            }
        )
    return points


def progress_series(
    track: list[dict[str, Any]], elapsed_s: float, distance_m: float | None
) -> list[dict[str, Any]]:
    """~200 points of {progress, elapsed_s, distance_m}, evenly spaced in progress.

    Progress is the fraction of the segment's distance where the device measured one,
    otherwise the fraction of its time. Comparison interpolates on this series, so the
    browser never aligns two 9 000-point streams.
    """
    by_distance = bool(distance_m) and any(p["distance_m"] for p in track)
    samples = [
        (p["t"], p["distance_m"]) for p in track if not by_distance or p["distance_m"] is not None
    ]
    if not samples or not elapsed_s:
        return []
    samples = [(0.0, 0.0)] + samples + [(float(elapsed_s), distance_m or samples[-1][1])]
    total = distance_m if by_distance else elapsed_s
    axis = [(d if by_distance else t) or 0 for t, d in samples]
    out = []
    j = 0
    for k in range(PROGRESS_POINTS + 1):
        target = total * k / PROGRESS_POINTS
        while j < len(samples) - 2 and axis[j + 1] < target:
            j += 1
        (t0, d0), (t1, d1) = samples[j], samples[j + 1]
        a0, a1 = axis[j], axis[j + 1]
        f = (target - a0) / (a1 - a0) if a1 > a0 else 0.0
        f = min(max(f, 0.0), 1.0)
        out.append(
            {
                "progress": round(k / PROGRESS_POINTS, 4),
                "elapsed_s": round(t0 + f * (t1 - t0), 1),
                "distance_m": round((d0 or 0) + f * ((d1 or 0) - (d0 or 0)), 1)
                if by_distance
                else None,
            }
        )
    return out


def _bounds(
    s: dict[str, Any], track: list[dict[str, Any]]
) -> tuple[float, float, float, float] | None:
    """nec/swc from the session; the pair can come back swapped, so take min/max."""
    lats = [v for v in (_deg(s.get("nec_lat")), _deg(s.get("swc_lat"))) if v is not None]
    lngs = [v for v in (_deg(s.get("nec_long")), _deg(s.get("swc_long"))) if v is not None]
    if len(lats) < 2 or len(lngs) < 2:
        if not track:
            return None
        lats = [p["lat"] for p in track]
        lngs = [p["lng"] for p in track]
    return max(lats), min(lats), max(lngs), min(lngs)


def _laps(laps: list[dict[str, Any]]) -> list[ParsedLap]:
    """Laps carry pacing shape only; segment totals come from the session (Trap 1)."""
    partial = _partial_flags(laps)
    return [
        ParsedLap(
            lap_index=i,
            start_time=lap["start_time"],
            total_time=round(lap.get("total_elapsed_time") or 0),
            distance_m=lap.get("total_distance"),
            elevation_gain_m=lap.get("total_ascent"),
            avg_hr=lap.get("avg_heart_rate"),
            max_hr=lap.get("max_heart_rate"),
            avg_power=lap.get("avg_power"),
            normalized_power=lap.get("normalized_power"),
            cadence_avg=_cadence(lap),
            is_partial=partial[i],
            raw_data=_json_safe(lap),
        )
        for i, lap in enumerate(laps)
    ]


def _partial_flags(laps: list[dict[str, Any]]) -> list[bool]:
    """Only the last of several laps can be a remainder. Garmin tags it `session_end`;
    Apple doesn't, so a last lap well short of the auto-lap length also counts."""
    flags = [False] * len(laps)
    if len(laps) < 2:
        return flags
    earlier = [d for lap in laps[:-1] if (d := lap.get("total_distance")) is not None]
    last = laps[-1]
    short = bool(earlier) and (last.get("total_distance") or 0) < 0.9 * statistics.median(earlier)
    flags[-1] = last.get("lap_trigger") == "session_end" or short
    return flags


def _devices(device_infos: list[dict[str, Any]]) -> list[ParsedDevice]:
    """Paired sensors with a serial. The `creator` entry is the recording device itself,
    which is `ParsedFile.device_key`, not a sensor."""
    seen: dict[str, ParsedDevice] = {}
    for d in device_infos:
        if d.get("serial_number") is None or d.get("device_index") == "creator":
            continue
        device = ParsedDevice(
            manufacturer=str(d.get("manufacturer") or "unknown"),
            serial_number=str(d["serial_number"]),
            label=d.get("product_name"),
        )
        seen.setdefault(device.key, device)
    return sorted(seen.values(), key=lambda d: d.key)


# --- local date (Trap 11) ---------------------------------------------------------------


def _local_date(
    start: datetime, records: list[dict[str, Any]], tcorr: dict[str, Any] | None
) -> tuple[date, str | None]:
    """timestamp_correlation first, then the first GPS fix's timezone, then UTC."""
    if tcorr and tcorr.get("timestamp") and tcorr.get("local_timestamp"):
        offset = tcorr["local_timestamp"] - tcorr["timestamp"]
        if isinstance(offset, timedelta):
            return (start + offset).date(), None
    for r in records:
        lat, lng = _deg(r.get("position_lat")), _deg(r.get("position_long"))
        if lat is None or lng is None:
            continue
        zone = timezone_at(lat, lng)
        if zone:
            return start.astimezone(ZoneInfo(zone)).date(), None
        break
    return start.date(), "No position or timezone in the file; the race date is the UTC date."


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float) and value != value:  # NaN is not valid JSON
        return None
    return value
