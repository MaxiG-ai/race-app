"""Vendor-agnostic FIT file parser.

Pure function: bytes/path in, normalized dataclasses out. No SQLAlchemy, no session,
no I/O beyond reading the given file. See PLAN.md §3 Ingestion for the vendor quirks
this code exists to paper over.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import fitdecode

Segment = Literal["swim", "t1", "bike", "t2", "run"]
Sport = Literal["swim", "bike", "run", "transition", "other"]

# FIT lat/lng fields are "semicircles": a signed 32-bit int spanning -180..180 degrees.
SEMICIRCLE_TO_DEG = 180 / 2**31

# The 5-session order a multisport triathlon file is expected to follow, and the
# (segment, expected FIT `sport` value) pair at each position.
_EXPECTED_MULTISPORT: tuple[tuple[Segment, str], ...] = (
    ("swim", "swimming"),
    ("t1", "transition"),
    ("bike", "cycling"),
    ("t2", "transition"),
    ("run", "running"),
)

_FIT_SPORT_TO_SEGMENT: dict[str, Sport] = {
    "swimming": "swim",
    "cycling": "bike",
    "running": "run",
    "transition": "transition",
}


class FitParseError(Exception):
    """Raised when a FIT file cannot be parsed into a normalized ParsedFile.

    Messages are written to be user-facing: they name what was found and what was
    expected, since these surface directly in the upload UI.
    """


@dataclass
class ParsedLap:
    sport: Segment
    lap_index: int
    start_time: datetime
    total_time: int
    distance_m: float | None
    avg_hr: int | None
    max_hr: int | None
    avg_power: int | None
    normalized_power: int | None
    avg_speed: float | None
    elevation_gain_m: float | None
    cadence_avg: float | None
    is_partial: bool
    raw_data: dict[str, Any]


@dataclass
class ParsedSession:
    segment: Segment
    sport: Sport
    start_time: datetime
    end_time: datetime
    elapsed_time: int
    moving_time: int | None
    distance_m: float
    elevation_gain_m: float | None
    avg_hr: int | None
    max_hr: int | None
    avg_power: int | None
    normalized_power: int | None
    avg_speed: float | None
    avg_cadence: float | None
    avg_temp_c: float | None
    rpe_estimated: float | None
    avg_mets: float | None
    bounds_n: float | None
    bounds_s: float | None
    bounds_e: float | None
    bounds_w: float | None
    gps_track: list[dict[str, Any]]
    laps: list[ParsedLap]
    device_keys: list[str]
    raw_data: dict[str, Any]


@dataclass
class ParsedDevice:
    manufacturer: str | None
    serial_number: int | None
    product: int | str | None
    label: str | None


@dataclass
class ParsedFile:
    kind: Literal["multisport", "single_sport"]
    sessions: list[ParsedSession]
    devices: list[ParsedDevice]
    orphan_record_count: int
    start_time: datetime
    end_time: datetime
    local_date: date
    utc_offset_s: int
    detected_sport: Sport | None
    total_distance_m: float
    warnings: list[str]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def parse_fit(data: bytes | Path, *, filename: str | None = None) -> ParsedFile:
    """Parse a FIT file's bytes (or a path to one) into a normalized ParsedFile.

    Raises FitParseError for anything that would otherwise result in a half-imported
    file: corrupt frames, an unexpected session count/order, or a record matching
    more than one session window.
    """
    raw_bytes = data.read_bytes() if isinstance(data, Path) else data
    suffix = f" ({filename})" if filename else ""

    sessions_raw: list[dict[str, Any]] = []
    laps_raw: list[dict[str, Any]] = []
    records_raw: list[dict[str, Any]] = []
    device_infos_raw: list[dict[str, Any]] = []
    tcorr: dict[str, Any] | None = None

    try:
        with fitdecode.FitReader(_as_stream(raw_bytes)) as fit:
            for frame in fit:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                name = frame.name
                fields = _frame_fields(frame)
                if name == "record":
                    records_raw.append(fields)
                elif name == "session":
                    sessions_raw.append(fields)
                elif name == "lap":
                    laps_raw.append(fields)
                elif name == "device_info":
                    device_infos_raw.append(fields)
                elif name == "timestamp_correlation" and tcorr is None:
                    tcorr = fields
    except fitdecode.FitError as exc:
        raise FitParseError(f"Could not parse FIT file{suffix}: {exc}") from exc
    except FitParseError:
        raise
    except Exception as exc:  # pragma: no cover - defensive catch-all
        raise FitParseError(f"Unexpected error parsing FIT file{suffix}: {exc}") from exc

    if not sessions_raw:
        raise FitParseError(f"FIT file{suffix} contains no session messages.")

    kind, segments = _classify_sessions(sessions_raw, suffix)
    windows = _session_windows(sessions_raw)
    record_buckets, orphan_count = _bucket_records(records_raw, windows)
    lap_buckets = _bucket_leniently(laps_raw, windows, ts_key="start_time")
    device_buckets = _bucket_leniently(device_infos_raw, windows, ts_key="timestamp")

    warnings: list[str] = []
    if orphan_count:
        warnings.append(
            f"{orphan_count} record(s) fell outside every session window and were dropped."
        )

    sports = [_normalize_sport(s.get("sport")) for s in sessions_raw]
    parsed_sessions = [
        _build_session(
            segments[i],
            sports[i],
            sessions_raw[i],
            lap_buckets[i],
            record_buckets[i],
            device_buckets[i],
        )
        for i in range(len(sessions_raw))
    ]

    devices = _dedup_devices(device_infos_raw)

    start_time = parsed_sessions[0].start_time
    end_time = parsed_sessions[-1].end_time
    total_distance = sum(s.distance_m or 0.0 for s in parsed_sessions)

    local_date, utc_offset_s, tz_warning = _resolve_local_date(start_time, records_raw, tcorr)
    if tz_warning:
        warnings.append(tz_warning)

    detected_sport = sports[0] if kind == "single_sport" else None

    return ParsedFile(
        kind=kind,
        sessions=parsed_sessions,
        devices=devices,
        orphan_record_count=orphan_count,
        start_time=start_time,
        end_time=end_time,
        local_date=local_date,
        utc_offset_s=utc_offset_s,
        detected_sport=detected_sport,
        total_distance_m=total_distance,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# Frame reading helpers
# --------------------------------------------------------------------------- #


def _as_stream(raw_bytes: bytes):
    import io

    return io.BytesIO(raw_bytes)


def _frame_fields(frame: fitdecode.FitDataMessage) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for f in frame.fields:
        key = f.name if f.name else f"field_{f.def_num}"
        fields[key] = f.value
    return fields


# --------------------------------------------------------------------------- #
# Session classification & windowing
# --------------------------------------------------------------------------- #


def _classify_sessions(
    sessions_raw: list[dict[str, Any]], suffix: str
) -> tuple[Literal["multisport", "single_sport"], list[Segment]]:
    n = len(sessions_raw)
    if n == 5:
        found_sports = [s.get("sport") for s in sessions_raw]
        expected_sports = [expected for _, expected in _EXPECTED_MULTISPORT]
        if found_sports != expected_sports:
            raise FitParseError(
                f"FIT file{suffix} has 5 sessions but not in the expected "
                "swim, transition, cycling, transition, running order "
                f"(found: {found_sports!r})."
            )
        segments = [segment for segment, _ in _EXPECTED_MULTISPORT]
        return "multisport", segments
    if n == 1:
        segment = _FIT_SPORT_TO_SEGMENT.get(sessions_raw[0].get("sport"), "other")
        return "single_sport", [segment]
    found_sports = [s.get("sport") for s in sessions_raw]
    raise FitParseError(
        f"FIT file{suffix} has an unsupported shape: found {n} session message(s) "
        f"(sports={found_sports!r}). Expected 1 (single-sport upload) or 5 "
        "(swim, t1, bike, t2, run multisport race)."
    )


def _normalize_sport(fit_sport: str | None) -> Sport:
    return _FIT_SPORT_TO_SEGMENT.get(fit_sport, "other")


def _session_windows(sessions_raw: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    """Half-open [start, end) windows for each session, in file order.

    A session's own end (start + total_elapsed_time) can run a fraction of a second
    past the next session's recorded start (Trap 3) — clamping to the next start
    avoids the resulting double-match without swallowing genuine gaps between
    sessions (Trap 4), since a gap only exists when the own end is *earlier* than
    the next start, in which case the clamp is a no-op.
    """
    windows: list[tuple[datetime, datetime]] = []
    n = len(sessions_raw)
    for i, s in enumerate(sessions_raw):
        start = s["start_time"]
        elapsed = s.get("total_elapsed_time") or 0.0
        own_end = start + timedelta(seconds=elapsed)
        if i + 1 < n:
            next_start = sessions_raw[i + 1]["start_time"]
            end = min(own_end, next_start)
        else:
            end = own_end
        windows.append((start, end))
    return windows


def _bucket_records(
    records_raw: list[dict[str, Any]], windows: list[tuple[datetime, datetime]]
) -> tuple[list[list[dict[str, Any]]], int]:
    buckets: list[list[dict[str, Any]]] = [[] for _ in windows]
    orphans = 0
    for r in records_raw:
        ts = r.get("timestamp")
        if ts is None:
            orphans += 1
            continue
        matches = [i for i, (start, end) in enumerate(windows) if start <= ts < end]
        if not matches:
            orphans += 1
            continue
        if len(matches) > 1:
            raise FitParseError(
                f"Record at {ts.isoformat()} falls inside more than one session window "
                f"(sessions {matches}); this indicates corrupt or overlapping session "
                "boundaries and the file was not imported."
            )
        buckets[matches[0]].append(r)
    return buckets, orphans


def _bucket_leniently(
    items: list[dict[str, Any]], windows: list[tuple[datetime, datetime]], *, ts_key: str
) -> list[list[dict[str, Any]]]:
    """Assign items (laps, device_info) to the first session window they fall in.

    Unlike record bucketing, a miss or an ambiguous boundary here isn't fatal — these
    are supplementary (pacing shape, gear hints), not the segment totals that matter.
    """
    buckets: list[list[dict[str, Any]]] = [[] for _ in windows]
    for item in items:
        ts = item.get(ts_key)
        if ts is None:
            continue
        for i, (start, end) in enumerate(windows):
            if start <= ts < end:
                buckets[i].append(item)
                break
    return buckets


# --------------------------------------------------------------------------- #
# Session / lap construction
# --------------------------------------------------------------------------- #


def _semicircle_deg(value: int | None) -> float | None:
    if value is None:
        return None
    return value * SEMICIRCLE_TO_DEG


def _derive_speed(
    distance_m: float | None, moving_s: float | None, elapsed_s: float | None
) -> float | None:
    """distance / time, never the FIT avg_speed field (unreliable per PLAN §3)."""
    t = moving_s if moving_s else elapsed_s
    if not t or distance_m is None:
        return None
    return distance_m / t


def _cadence(d: dict[str, Any]) -> float | None:
    """Running sessions/laps use `avg_running_cadence`, not `avg_cadence`."""
    running = d.get("avg_running_cadence")
    return running if running is not None else d.get("avg_cadence")


def _partial_flags(laps_raw: list[dict[str, Any]]) -> list[bool]:
    """Flag the trailing remainder lap of an auto-lap sequence.

    Only the last lap can be partial, and only when there's more than one lap (a
    lone lap covering the whole segment — e.g. an Apple swim, a transition — is
    whole, not a remainder). Garmin marks it via `lap_trigger == "session_end"`;
    Apple's own trailing run lap is *not* tagged that way, so distance well under
    the auto-lap length (established from the preceding laps) is the fallback.
    """
    n = len(laps_raw)
    flags = [False] * n
    if n < 2:
        return flags
    reference_dists = [d for lap in laps_raw[:-1] if (d := lap.get("total_distance")) is not None]
    reference = statistics.median(reference_dists) if reference_dists else None
    last = laps_raw[-1]
    last_dist = last.get("total_distance")
    is_session_end = last.get("lap_trigger") == "session_end"
    is_short = reference is not None and last_dist is not None and last_dist < 0.9 * reference
    flags[-1] = is_session_end or is_short
    return flags


def _build_laps(laps_raw: list[dict[str, Any]], segment: Segment) -> list[ParsedLap]:
    flags = _partial_flags(laps_raw)
    out: list[ParsedLap] = []
    for i, lap in enumerate(laps_raw):
        elapsed = lap.get("total_elapsed_time") or 0.0
        moving = lap.get("total_timer_time")
        distance = lap.get("total_distance")
        out.append(
            ParsedLap(
                sport=segment,
                lap_index=i,
                start_time=lap["start_time"],
                total_time=round(elapsed),
                distance_m=distance,
                avg_hr=lap.get("avg_heart_rate"),
                max_hr=lap.get("max_heart_rate"),
                avg_power=lap.get("avg_power"),
                normalized_power=lap.get("normalized_power"),
                avg_speed=_derive_speed(distance, moving, elapsed),
                elevation_gain_m=lap.get("total_ascent"),
                cadence_avg=_cadence(lap),
                is_partial=flags[i],
                raw_data=_json_safe(lap),
            )
        )
    return out


def _build_session(
    segment: Segment,
    sport: Sport,
    s: dict[str, Any],
    laps_raw: list[dict[str, Any]],
    records_raw: list[dict[str, Any]],
    device_infos_raw: list[dict[str, Any]],
) -> ParsedSession:
    start = s["start_time"]
    elapsed = s.get("total_elapsed_time") or 0.0
    end = start + timedelta(seconds=elapsed)
    moving = s.get("total_timer_time")
    distance = s.get("total_distance") or 0.0

    gps_track: list[dict[str, Any]] = []
    for r in records_raw:
        lat, lng = r.get("position_lat"), r.get("position_long")
        if lat is None or lng is None:
            continue
        elevation = r.get("enhanced_altitude")
        if elevation is None:
            elevation = r.get("altitude")
        gps_track.append(
            {
                "lat": _semicircle_deg(lat),
                "lng": _semicircle_deg(lng),
                "elevation": elevation,
                "timestamp": r.get("timestamp"),
            }
        )

    # nec/swc are nominally the north-east/south-west corners, but on at least one
    # real fixture (an Apple open-water swim covering a small loop) the two ends of
    # a lat/lng pair come back the "wrong" way round. Take min/max of the pair
    # rather than trusting which field is which, so the box is always well-formed.
    nec_lat = _semicircle_deg(s.get("nec_lat"))
    nec_long = _semicircle_deg(s.get("nec_long"))
    swc_lat = _semicircle_deg(s.get("swc_lat"))
    swc_long = _semicircle_deg(s.get("swc_long"))
    bounds_n = max(nec_lat, swc_lat) if nec_lat is not None and swc_lat is not None else nec_lat
    bounds_s = min(nec_lat, swc_lat) if nec_lat is not None and swc_lat is not None else swc_lat
    bounds_e = (
        max(nec_long, swc_long) if nec_long is not None and swc_long is not None else nec_long
    )
    bounds_w = (
        min(nec_long, swc_long) if nec_long is not None and swc_long is not None else swc_long
    )
    if gps_track and None in (bounds_n, bounds_s, bounds_e, bounds_w):
        lats = [p["lat"] for p in gps_track]
        lngs = [p["lng"] for p in gps_track]
        bounds_n = bounds_n if bounds_n is not None else max(lats)
        bounds_s = bounds_s if bounds_s is not None else min(lats)
        bounds_e = bounds_e if bounds_e is not None else max(lngs)
        bounds_w = bounds_w if bounds_w is not None else min(lngs)

    device_keys = sorted(
        {
            f"{d.get('manufacturer') or 'unknown'}:{d['serial_number']}"
            for d in device_infos_raw
            if d.get("serial_number") is not None
        }
    )

    rpe_raw = s.get("workout_rpe")
    rpe_estimated = round(rpe_raw / 10, 1) if rpe_raw is not None else None
    avg_mets = s.get("AVG METs")
    if avg_mets is None:
        avg_mets = s.get("avg_mets")

    return ParsedSession(
        segment=segment,
        sport=sport,
        start_time=start,
        end_time=end,
        elapsed_time=round(elapsed),
        moving_time=round(moving) if moving is not None else None,
        distance_m=distance,
        elevation_gain_m=s.get("total_ascent"),
        avg_hr=s.get("avg_heart_rate"),
        max_hr=s.get("max_heart_rate"),
        avg_power=s.get("avg_power"),
        normalized_power=s.get("normalized_power"),
        avg_speed=_derive_speed(distance, moving, elapsed),
        avg_cadence=_cadence(s),
        avg_temp_c=s.get("avg_temperature"),
        rpe_estimated=rpe_estimated,
        avg_mets=avg_mets,
        bounds_n=bounds_n,
        bounds_s=bounds_s,
        bounds_e=bounds_e,
        bounds_w=bounds_w,
        gps_track=gps_track,
        laps=_build_laps(laps_raw, segment),
        device_keys=device_keys,
        raw_data=_json_safe(s),
    )


def _dedup_devices(device_infos_raw: list[dict[str, Any]]) -> list[ParsedDevice]:
    seen: dict[tuple[str | None, int], ParsedDevice] = {}
    for d in device_infos_raw:
        serial = d.get("serial_number")
        if serial is None:
            continue
        key = (d.get("manufacturer"), serial)
        if key not in seen:
            seen[key] = ParsedDevice(
                manufacturer=d.get("manufacturer"),
                serial_number=serial,
                product=d.get("product"),
                label=d.get("product_name"),
            )
    return list(seen.values())


# --------------------------------------------------------------------------- #
# Local date resolution (§3: Race.date is the local date, not the UTC date)
# --------------------------------------------------------------------------- #

_tf_singleton = None


def _timezone_finder():
    global _tf_singleton
    if _tf_singleton is None:
        from timezonefinder import TimezoneFinder

        _tf_singleton = TimezoneFinder()
    return _tf_singleton


def _resolve_local_date(
    start_time: datetime,
    records_raw: list[dict[str, Any]],
    tcorr: dict[str, Any] | None,
) -> tuple[date, int, str | None]:
    """Prefer timestamp_correlation (Edge 540); else GPS position + timezonefinder;
    else fall back to the UTC date with a warning."""
    if tcorr is not None:
        utc_ts = tcorr.get("timestamp")
        local_ts = tcorr.get("local_timestamp")
        if utc_ts is not None and local_ts is not None:
            offset_s = round((local_ts - utc_ts).total_seconds())
            local_dt = start_time + timedelta(seconds=offset_s)
            return local_dt.date(), offset_s, None

    for r in records_raw:
        lat, lng = r.get("position_lat"), r.get("position_long")
        if lat is None or lng is None:
            continue
        tzname = _timezone_finder().timezone_at(lat=_semicircle_deg(lat), lng=_semicircle_deg(lng))
        if not tzname:
            break
        local_dt = start_time.astimezone(ZoneInfo(tzname))
        offset = local_dt.utcoffset()
        offset_s = round(offset.total_seconds()) if offset else 0
        return local_dt.date(), offset_s, None

    return (
        start_time.date(),
        0,
        "No GPS position or timestamp_correlation available; local_date falls back "
        "to the UTC date.",
    )


# --------------------------------------------------------------------------- #
# JSON-safety for raw_data
# --------------------------------------------------------------------------- #


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, bytes):
        return list(value)
    return value
