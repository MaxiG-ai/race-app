"""Coordinate → IANA timezone, offline (timezonefinder; see docs/architecture.md)."""

from functools import lru_cache

from timezonefinder import TimezoneFinder


@lru_cache(maxsize=1)
def _finder() -> TimezoneFinder:
    return TimezoneFinder()  # loads a sizeable index, so build it once


def timezone_at(lat: float, lng: float) -> str | None:
    return _finder().timezone_at(lat=lat, lng=lng)
