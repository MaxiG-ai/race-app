"""Importer tests against the five real fixtures.

Each test names the trap from PLAN.md §3 that it guards. These are the failure modes that
a plausible-looking importer hits on real files, so they are asserted on real files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.base import SEGMENT_ORDER, Segment
from app.services.fit_import import (
    FitParseError,
    local_date,
    parse_fit,
    summarize,
)

FIXTURES = Path(__file__).parent / "fixtures"

APPLE_A = FIXTURES / "multisport_2026-07-11.fit"
APPLE_B = FIXTURES / "multisport_2026-08-09.fit"
GARMIN = FIXTURES / "multisport_2024-08-03_garmin.fit"
EDGE_BIKE = FIXTURES / "single_bike_2026-06-21_edge540.fit"
STRAVA = FIXTURES / "strava_export_2026-06-21_multisport.fit"

MULTISPORT = [APPLE_A, APPLE_B, GARMIN, STRAVA]


@pytest.fixture(scope="module")
def parsed() -> dict[Path, object]:
    return {path: parse_fit(str(path)) for path in [*MULTISPORT, EDGE_BIKE]}


# --- structure ------------------------------------------------------------------


@pytest.mark.parametrize("path", MULTISPORT)
def test_multisport_files_yield_five_segments_in_course_order(parsed, path):
    fit = parsed[path]
    assert fit.is_multisport
    assert [s.segment for s in fit.segments] == list(SEGMENT_ORDER)


def test_single_sport_file_yields_one_bike_segment(parsed):
    fit = parsed[EDGE_BIKE]
    assert not fit.is_multisport
    assert [s.segment for s in fit.segments] == [Segment.BIKE]


def test_segments_are_chronological_and_non_overlapping(parsed):
    for fit in parsed.values():
        for earlier, later in zip(fit.segments, fit.segments[1:], strict=False):
            assert earlier.start_time < later.start_time


# --- trap 1: never sum laps to get a segment total ------------------------------


def test_apple_drops_the_trailing_partial_bike_lap(parsed):
    """The trap that makes lap-summing look safe until it isn't.

    Apple's bike laps sum to 30 000 m against a 33 746 m session. Distance must come from
    the session; the lap sum is exposed only so the discrepancy is visible.
    """
    bike = parsed[APPLE_A].segment(Segment.BIKE)
    assert bike.distance_m == pytest.approx(33746.45, abs=0.1)
    assert bike.lap_distance_sum_m == pytest.approx(30000.0, abs=0.1)
    assert bike.distance_m - bike.lap_distance_sum_m > 3000


def test_garmin_bike_laps_reconcile_exactly(parsed):
    """The other half of the trap: on Garmin the sum matches, so testing only Garmin
    would suggest lap-summing is fine."""
    bike = parsed[GARMIN].segment(Segment.BIKE)
    assert bike.lap_distance_sum_m == pytest.approx(bike.distance_m, abs=0.5)


def test_trailing_partial_laps_are_flagged(parsed):
    garmin_bike = parsed[GARMIN].segment(Segment.BIKE)
    assert garmin_bike.laps[-1].is_partial
    assert garmin_bike.laps[-1].distance_m == pytest.approx(868.11, abs=0.1)
    assert not any(lap.is_partial for lap in garmin_bike.laps[:-1])

    # Apple's bike laps are all full length — the partial one is simply absent.
    apple_bike = parsed[APPLE_A].segment(Segment.BIKE)
    assert not any(lap.is_partial for lap in apple_bike.laps)


# --- trap 2: power is device-dependent, not sport-dependent ---------------------


def test_apple_reports_running_power_and_no_bike_power(parsed):
    fit = parsed[APPLE_A]
    assert fit.segment(Segment.BIKE).avg_power is None
    assert fit.segment(Segment.RUN).avg_power == pytest.approx(295, abs=1)


def test_garmin_reports_bike_power_and_no_running_power(parsed):
    """Exactly inverted from Apple — which is why power lives on every segment."""
    fit = parsed[GARMIN]
    assert fit.segment(Segment.BIKE).avg_power == pytest.approx(225, abs=1)
    assert fit.segment(Segment.BIKE).normalized_power == pytest.approx(250, abs=1)
    assert fit.segment(Segment.RUN).avg_power is None


def test_edge_bike_has_power_where_the_watch_recording_of_it_does_not(parsed):
    """The same bike leg, two devices: the Edge is the better record (§3)."""
    edge = parsed[EDGE_BIKE].segment(Segment.BIKE)
    watch = parsed[STRAVA].segment(Segment.BIKE)
    assert edge.avg_power == pytest.approx(247, abs=1)
    assert watch.avg_power is None
    # Same leg, different measured distance — the reason the gap chart normalises.
    assert edge.distance_m == pytest.approx(19633.63, abs=1)
    assert watch.distance_m == pytest.approx(19991.6, abs=1)


# --- traps 3 & 4: bucketing ------------------------------------------------------


def test_every_record_lands_in_at_most_one_segment(parsed):
    """Sessions overlap sub-second, and on Garmin T2 runs 1.45 s past the run's start.
    Clamping each window to the next session's start makes double-counting impossible."""
    for path, fit in parsed.items():
        assigned = sum(len(seg.gps_track) for seg in fit.segments)
        assert assigned + fit.orphan_record_count == fit.record_count, path


def test_garmin_orphan_record_is_tolerated_not_fatal(parsed):
    """One record sits in the 1.1 s gap between bike end and T2 start. Rejecting the file
    over it would reject a valid file."""
    assert parsed[GARMIN].orphan_record_count == 1
    assert parsed[APPLE_A].orphan_record_count == 0


def test_record_counts_match_the_files(parsed):
    assert parsed[APPLE_A].record_count == 9829
    assert parsed[APPLE_B].record_count == 9413
    assert parsed[GARMIN].record_count == 6389
    assert parsed[EDGE_BIKE].record_count == 2001


# --- trap 5: elapsed vs moving ---------------------------------------------------


def test_garmin_elapsed_and_moving_time_differ(parsed):
    bike = parsed[GARMIN].segment(Segment.BIKE)
    assert bike.elapsed_time == pytest.approx(4354.856, abs=0.01)
    assert bike.moving_time == pytest.approx(4348.949, abs=0.01)
    assert bike.elapsed_time > bike.moving_time


def test_apple_elapsed_equals_moving(parsed):
    for seg in parsed[APPLE_A].segments:
        assert seg.elapsed_time == pytest.approx(seg.moving_time, abs=0.001)


# --- trap 6: transitions are real but poorly recorded ----------------------------


def test_transitions_carry_real_distance(parsed):
    t1 = parsed[APPLE_A].segment(Segment.T1)
    assert t1.distance_m == pytest.approx(552.5, abs=1)


def test_barely_recorded_transition_still_parses(parsed):
    """Apple's T2 in the August race is 20 m / 7.7 s — the watch nearly missed it."""
    t2 = parsed[APPLE_B].segment(Segment.T2)
    assert t2.elapsed_time == pytest.approx(7.718, abs=0.01)
    assert t2.distance_m == pytest.approx(20.18, abs=0.5)


# --- trap 7: lap granularity varies by vendor ------------------------------------


def test_swim_lap_count_differs_between_vendors(parsed):
    """An Apple swim is one lap — it cannot render as a line chart. A Garmin swim is four."""
    assert len(parsed[APPLE_A].segment(Segment.SWIM).laps) == 1
    assert len(parsed[GARMIN].segment(Segment.SWIM).laps) == 4
    assert len(parsed[APPLE_A].segment(Segment.BIKE).laps) == 6
    assert len(parsed[APPLE_A].segment(Segment.RUN).laps) == 10


# --- trap 8: device temperature ---------------------------------------------------


def test_garmin_carries_device_temperature_and_apple_does_not(parsed):
    assert parsed[GARMIN].segment(Segment.BIKE).avg_temp_c is not None
    assert parsed[APPLE_A].segment(Segment.BIKE).avg_temp_c is None


# --- trap 10: developer fields are Apple-only and sparse --------------------------


def test_declared_humidity_is_never_populated(parsed):
    """The field is declared in every Apple file and filled in none, which is why weather
    is API-only in practice."""
    for path in (APPLE_A, APPLE_B, STRAVA):
        assert all(seg.humidity_pct is None for seg in parsed[path].segments), path


def test_rpe_is_present_on_one_apple_file_and_absent_on_the_other(parsed):
    assert parsed[APPLE_A].segment(Segment.SWIM).rpe_estimated is not None
    assert parsed[APPLE_B].segment(Segment.SWIM).rpe_estimated is None


# --- trap 11: local date ----------------------------------------------------------


def test_garmin_supplies_its_utc_offset(parsed):
    """timestamp_correlation removes the timezone guesswork for Garmin files."""
    assert parsed[EDGE_BIKE].utc_offset_seconds == 7200


def test_apple_supplies_no_offset_and_falls_back_to_utc_date(parsed):
    fit = parsed[APPLE_A]
    assert fit.utc_offset_seconds is None
    assert local_date(fit.start_time, fit.utc_offset_seconds).isoformat() == "2026-07-11"


def test_local_date_can_differ_from_the_utc_date():
    """The reason the offset matters: a late-evening UTC start is the next day locally."""
    from datetime import UTC, datetime

    late = datetime(2026, 7, 11, 22, 30, tzinfo=UTC)
    assert local_date(late, None).isoformat() == "2026-07-11"
    assert local_date(late, 7200).isoformat() == "2026-07-12"


# --- gear detection ----------------------------------------------------------------


def test_edge_file_exposes_paired_sensor_serials(parsed):
    """These serials are what gear resolution keys on (§3 Gear detection)."""
    keys = {d.device_key for d in parsed[EDGE_BIKE].devices}
    assert "garmin:3449642804" in keys  # the head unit
    assert "favero_electronics:1753485582" in keys  # power meter pedals
    assert "shimano:2783122544" in keys  # Di2 groupset
    assert "polar_electro:584467901" in keys  # HR strap
    assert parsed[EDGE_BIKE].creator_device_key == "garmin:3449642804"


def test_apple_file_exposes_only_the_watch(parsed):
    """No paired sensors at all — which is why run shoes can never be auto-detected."""
    devices = parsed[APPLE_A].devices
    assert len(devices) == 1
    assert devices[0].is_creator


# --- streams ------------------------------------------------------------------------


def test_gps_points_carry_the_devices_own_distance(parsed):
    bike = parsed[APPLE_A].segment(Segment.BIKE)
    assert bike.gps_track
    assert all("distance_m" in p for p in bike.gps_track)
    with_distance = [p for p in bike.gps_track if p["distance_m"] is not None]
    assert len(with_distance) == len(bike.gps_track)


def test_progress_series_is_resampled_and_monotonic(parsed):
    for path, fit in parsed.items():
        for seg in fit.segments:
            series = seg.progress_series
            assert len(series) == 200, (path, seg.segment)
            assert series[0]["progress"] == 0.0
            assert series[-1]["progress"] == 1.0
            elapsed = [p["elapsed_s"] for p in series]
            assert elapsed == sorted(elapsed), (path, seg.segment)


def test_progress_series_end_matches_segment_duration(parsed):
    """The last sample should land at roughly the segment's elapsed time — the gap chart
    stacks these, so a drift here would misplace every later checkpoint."""
    bike = parsed[GARMIN].segment(Segment.BIKE)
    assert bike.progress_series[-1]["elapsed_s"] == pytest.approx(bike.elapsed_time, rel=0.02)


# --- deduplication -------------------------------------------------------------------


def test_strava_export_is_byte_identical_to_the_apple_multisport_shape(parsed):
    """Strava's 'export original' returns the uploaded file, so downloading the swim and
    the run yields the same bytes. Content hashing is what stops three downloads becoming
    three copies."""
    import hashlib

    digest = hashlib.sha256(STRAVA.read_bytes()).hexdigest()
    assert digest == "ff74871146f5abb2a0ac59f48361ef6210127ea5da338e7dc5228b8c579abde1"


# --- failure modes ---------------------------------------------------------------------


def test_non_fit_input_raises_a_clear_error(tmp_path):
    junk = tmp_path / "notafit.fit"
    junk.write_bytes(b"this is not a FIT file at all")
    with pytest.raises((FitParseError, Exception)):
        parse_fit(str(junk))


def test_summary_is_json_shaped(parsed):
    summary = summarize(parsed[APPLE_A])
    assert summary["is_multisport"] is True
    assert len(summary["segments"]) == 5
    assert summary["segments"][2]["segment"] == "bike"
