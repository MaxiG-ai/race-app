"""Shared `TimezoneFinder` instance.

Two callers need to resolve a timezone from coordinates: the FIT parser, deriving a
race's local date from its first GPS point (§3 — "Timestamps are UTC", and Apple's
filenames encode local time), and weather enrichment, anchoring an entered race date
to a UTC hour for the archive lookup.

`TimezoneFinder` loads a sizeable binary index on construction, so it lives here as a
lazily-built singleton rather than being instantiated per module or per call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from timezonefinder import TimezoneFinder

_finder: TimezoneFinder | None = None


def timezone_finder() -> TimezoneFinder:
    global _finder
    if _finder is None:
        from timezonefinder import TimezoneFinder

        _finder = TimezoneFinder()
    return _finder


def timezone_at(lat: float, lng: float) -> str | None:
    """IANA zone name for a coordinate, or `None` where none resolves (mid-ocean, or
    a lookup failure). Callers decide how to degrade — neither caller should raise
    over a timezone, since both have a usable fallback."""
    try:
        return timezone_finder().timezone_at(lat=lat, lng=lng)
    except Exception:  # pragma: no cover - defensive; the lookup is pure computation
        return None
