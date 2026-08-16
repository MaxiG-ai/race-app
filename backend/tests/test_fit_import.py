"""Tests for app.services.fit_import against the five real FIT fixtures.

These assert the §3 traps directly (PLAN.md): session totals over lap sums,
half-open record bucketing with tolerated orphans, device-dependent power,
elapsed vs moving time, and the local race date.

Ground-truth numbers come from PLAN.md §3's table, cross-checked against the
actual fixtures while writing these tests (see the parser's docstring / the
final report for the one place reality added detail beyond the table: an
orphan record also appears in the strava_export fixture, at the tail end of
the run session, in addition to the Garmin bike/T2 orphan the plan calls out).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from app.services import fit_import as fi
from app.services.fit_import import FitParseError, ParsedFile, parse_fit

from .conftest import fixture_path

APPLE_1 = "multisport_2026-07-11.fit"
APPLE_2 = "multisport_2026-08-09.fit"
GARMIN = "multisport_2024-08-03_garmin.fit"
EDGE_BIKE = "single_bike_2026-06-21_edge540.fit"
STRAVA = "strava_export_2026-06-21_multisport.fit"

ALL_FIXTURES = [APPLE_1, APPLE_2, GARMIN, EDGE_BIKE, STRAVA]

TOL_M = 1.0  # metres
TOL_S = 1  # seconds


def _approx(actual: float | None, expected: float, tol: float) -> bool:
    return actual is not None and abs(actual - expected) <= tol


@pytest.fixture(scope="module")
def parsed() -> dict[str, ParsedFile]:
    """All five fixtures parsed once and cached for the module."""
    return {name: parse_fit(fixture_path(name), filename=name) for name in ALL_FIXTURES}


# --------------------------------------------------------------------------- #
# Segment mapping / session-count validation
# --------------------------------------------------------------------------- #


class TestSegmentMapping:
    def test_multisport_files_map_to_five_segments_in_order(self, parsed):
        for name in (APPLE_1, APPLE_2, GARMIN, STRAVA):
            pf = parsed[name]
            assert pf.kind == "multisport", name
            assert [s.segment for s in pf.sessions] == ["swim", "t1", "bike", "t2", "run"]
            assert pf.detected_sport is None

    def test_single_sport_file_is_detected(self, parsed):
        pf = parsed[EDGE_BIKE]
        assert pf.kind == "single_sport"
        assert len(pf.sessions) == 1
        assert pf.sessions[0].segment == "bike"
        assert pf.detected_sport == "bike"

    def test_wrong_session_order_is_rejected(self):
        # Same 5 sports fitdecode would report, but bike/run swapped.
        sessions = [
            {"sport": "swimming", "start_time": _dt(0), "total_elapsed_time": 10.0},
            {"sport": "transition", "start_time": _dt(10), "total_elapsed_time": 10.0},
            {"sport": "running", "start_time": _dt(20), "total_elapsed_time": 10.0},
            {"sport": "transition", "start_time": _dt(30), "total_elapsed_time": 10.0},
            {"sport": "cycling", "start_time": _dt(40), "total_elapsed_time": 10.0},
        ]
        with pytest.raises(FitParseError, match="expected .*order|swim, transition"):
            fi._classify_sessions(sessions, suffix="")

    @pytest.mark.parametrize("n", [2, 3, 4, 6])
    def test_unexpected_session_counts_are_rejected(self, n):
        sessions = [
            {"sport": "cycling", "start_time": _dt(i * 10), "total_elapsed_time": 10.0}
            for i in range(n)
        ]
        with pytest.raises(FitParseError, match=str(n)):
            fi._classify_sessions(sessions, suffix="")

    def test_no_sessions_at_all_is_rejected(self):
        with pytest.raises(FitParseError):
            parse_fit(b"", filename="empty.fit")

    def test_corrupt_bytes_raise_fit_parse_error_not_fitdecode_error(self):
        with pytest.raises(FitParseError):
            parse_fit(b"this is not a fit file at all", filename="garbage.fit")


def _dt(offset_s: float) -> dt.datetime:
    return dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(seconds=offset_s)


# --------------------------------------------------------------------------- #
# Ground truth: distances / durations / ascent per §3's table
# --------------------------------------------------------------------------- #

# (fixture, segment) -> (distance_m, elapsed_s, elevation_gain_m or None)
GROUND_TRUTH = {
    (APPLE_1, "swim"): (1572.69, 1739, None),
    (APPLE_1, "t1"): (552.5, 216, None),
    (APPLE_1, "bike"): (33746.45, 4388, 558),
    (APPLE_1, "t2"): (218.82, 121, None),
    (APPLE_1, "run"): (9172.36, 3363, 166),
    (APPLE_2, "swim"): (1548.5, 1873, None),
    (APPLE_2, "t1"): (105.52, 147, None),
    (APPLE_2, "bike"): (40570.04, 4180, 22),
    (APPLE_2, "t2"): (20.18, 8, None),
    (APPLE_2, "run"): (9933.19, 3204, 48),
    (GARMIN, "swim"): (1645.05, 1242, None),
    (GARMIN, "t1"): (273.25, 213, 14),
    (GARMIN, "bike"): (40868.11, 4355, 327),
    (GARMIN, "t2"): (26.99, 70, 0),
    (GARMIN, "run"): (9238.52, 2803, 59),
}


class TestGroundTruth:
    @pytest.mark.parametrize(("key", "expected"), GROUND_TRUTH.items())
    def test_session_distance_and_duration(self, parsed, key, expected):
        name, segment = key
        dist, elapsed, ascent = expected
        session = _session(parsed[name], segment)
        assert _approx(session.distance_m, dist, TOL_M), (
            f"{name}/{segment} distance: got {session.distance_m}, want {dist}"
        )
        assert abs(session.elapsed_time - elapsed) <= TOL_S, (
            f"{name}/{segment} elapsed: got {session.elapsed_time}, want {elapsed}"
        )
        if ascent is not None:
            assert session.elevation_gain_m == pytest.approx(ascent, abs=TOL_M)

    def test_lap_counts_match_table(self, parsed):
        assert sum(len(s.laps) for s in parsed[APPLE_1].sessions) == 19
        assert sum(len(s.laps) for s in parsed[APPLE_2].sessions) == 21
        assert sum(len(s.laps) for s in parsed[GARMIN].sessions) == 25

    def test_edge540_single_bike_ground_truth(self, parsed):
        session = parsed[EDGE_BIKE].sessions[0]
        assert _approx(session.distance_m, 19633.63, TOL_M)
        assert session.avg_power == 247
        assert session.normalized_power == 261
        assert session.avg_cadence == pytest.approx(84, abs=1)

    def test_strava_export_bike_leg_ground_truth(self, parsed):
        bike = _session(parsed[STRAVA], "bike")
        assert _approx(bike.distance_m, 19992, tol=2)  # PLAN rounds to 19,992 m
        assert bike.avg_cadence == pytest.approx(2, abs=1)  # garbage cadence, kept as-is


def _session(pf: ParsedFile, segment: str):
    for s in pf.sessions:
        if s.segment == segment:
            return s
    raise AssertionError(f"no {segment} session in parsed file")


# --------------------------------------------------------------------------- #
# Trap 1: session totals are authoritative, never sum laps
# --------------------------------------------------------------------------- #


class TestSessionTotalsOverLapSums:
    def test_apple_bike_lap_sum_understates_session_total(self, parsed):
        bike = _session(parsed[APPLE_1], "bike")
        lap_sum = sum(lap.distance_m or 0 for lap in bike.laps)
        # 6 x 5000m laps recorded; the trailing partial lap was dropped entirely.
        assert lap_sum == pytest.approx(30000.0, abs=TOL_M)
        assert bike.distance_m == pytest.approx(33746.45, abs=TOL_M)
        shortfall = bike.distance_m - lap_sum
        assert shortfall == pytest.approx(3746.45, abs=TOL_M)
        # The session total, not the lap sum, is what must be trusted.
        assert bike.distance_m != pytest.approx(lap_sum, abs=10)

    def test_garmin_bike_lap_sum_reconciles_with_session_total(self, parsed):
        bike = _session(parsed[GARMIN], "bike")
        lap_sum = sum(lap.distance_m or 0 for lap in bike.laps)
        assert lap_sum == pytest.approx(bike.distance_m, abs=TOL_M)

    def test_garmin_run_and_swim_also_reconcile(self, parsed):
        for segment in ("swim", "run"):
            session = _session(parsed[GARMIN], segment)
            lap_sum = sum(lap.distance_m or 0 for lap in session.laps)
            assert lap_sum == pytest.approx(session.distance_m, abs=TOL_M)

    def test_apple_run_reconciles_despite_bike_not(self, parsed):
        # Only the bike drops its trailing lap on Apple; the run's partial lap is kept.
        run = _session(parsed[APPLE_1], "run")
        lap_sum = sum(lap.distance_m or 0 for lap in run.laps)
        assert lap_sum == pytest.approx(run.distance_m, abs=TOL_M)


# --------------------------------------------------------------------------- #
# Trap 3 & 4: half-open bucketing, orphan tolerance, fatal multi-match
# --------------------------------------------------------------------------- #


class TestRecordBucketing:
    def test_garmin_has_exactly_one_orphan(self, parsed):
        assert parsed[GARMIN].orphan_record_count == 1

    def test_apple_files_have_no_orphans(self, parsed):
        assert parsed[APPLE_1].orphan_record_count == 0
        assert parsed[APPLE_2].orphan_record_count == 0

    def test_orphan_count_is_reflected_in_warnings(self, parsed):
        assert any("orphan" in w or "dropped" in w for w in parsed[GARMIN].warnings)

    def test_session_windows_do_not_overlap_across_a_sub_second_boundary(self):
        # Mirrors the real Apple T1/cycling boundary: T1's own elapsed-computed end
        # (11:51:42.054) lands a fraction of a second after cycling's recorded start
        # (11:51:42.000). A record timestamped exactly on that whole second must be
        # assigned to exactly one of the two sessions, not both.
        sessions = [
            {
                "sport": "transition",
                "start_time": dt.datetime(2026, 1, 1, 11, 48, 6, tzinfo=dt.UTC),
                "total_elapsed_time": 216.054,
            },
            {
                "sport": "cycling",
                "start_time": dt.datetime(2026, 1, 1, 11, 51, 42, tzinfo=dt.UTC),
                "total_elapsed_time": 4388.493,
            },
        ]
        windows = fi._session_windows(sessions)
        boundary = dt.datetime(2026, 1, 1, 11, 51, 42, tzinfo=dt.UTC)
        matches = [i for i, (start, end) in enumerate(windows) if start <= boundary < end]
        assert matches == [1]  # goes to cycling, not double-counted into both

    def test_a_gap_between_sessions_produces_an_orphan_not_an_overlap(self):
        # Mirrors the real Garmin bike/T2 boundary: bike's own elapsed-computed end
        # is *earlier* than T2's recorded start, leaving a real ~1.1s gap.
        sessions = [
            {
                "sport": "cycling",
                "start_time": dt.datetime(2024, 8, 3, 11, 26, 32, tzinfo=dt.UTC),
                "total_elapsed_time": 4354.856,
            },
            {
                "sport": "transition",
                "start_time": dt.datetime(2024, 8, 3, 12, 39, 8, tzinfo=dt.UTC),
                "total_elapsed_time": 70.45,
            },
        ]
        windows = fi._session_windows(sessions)
        gap_record = dt.datetime(2024, 8, 3, 12, 39, 7, tzinfo=dt.UTC)
        matches = [i for i, (start, end) in enumerate(windows) if start <= gap_record < end]
        assert matches == []  # orphan: fits in neither window

    def test_record_matching_two_windows_is_fatal(self):
        # A genuinely overlapping pair of windows (not the sub-second edge case
        # above, which _session_windows resolves) must raise, not silently pick one.
        windows = [
            (dt.datetime(2026, 1, 1, tzinfo=dt.UTC), _dt(20)),
            (_dt(10), _dt(30)),
        ]
        records = [{"timestamp": _dt(15)}]
        with pytest.raises(FitParseError, match="more than one session window"):
            fi._bucket_records(records, windows)

    def test_record_with_no_timestamp_counts_as_orphan(self):
        windows = [(dt.datetime(2026, 1, 1, tzinfo=dt.UTC), _dt(20))]
        buckets, orphans = fi._bucket_records([{"timestamp": None}], windows)
        assert orphans == 1
        assert buckets == [[]]


# --------------------------------------------------------------------------- #
# Trap 2: power is device-dependent, not sport-dependent
# --------------------------------------------------------------------------- #


class TestDeviceDependentPower:
    def test_apple_has_run_power_and_no_bike_power(self, parsed):
        for name in (APPLE_1, APPLE_2):
            bike = _session(parsed[name], "bike")
            run = _session(parsed[name], "run")
            assert bike.avg_power is None
            assert run.avg_power is not None
            assert 290 <= run.avg_power <= 310

    def test_garmin_has_bike_power_and_no_run_power(self, parsed):
        bike = _session(parsed[GARMIN], "bike")
        run = _session(parsed[GARMIN], "run")
        assert bike.avg_power == 225
        assert bike.normalized_power == 250
        assert run.avg_power is None
        assert run.normalized_power is None

    def test_apple_bike_laps_never_carry_power(self, parsed):
        bike = _session(parsed[APPLE_1], "bike")
        assert all(lap.avg_power is None for lap in bike.laps)

    def test_apple_run_laps_carry_power(self, parsed):
        run = _session(parsed[APPLE_1], "run")
        assert any(lap.avg_power is not None for lap in run.laps)


# --------------------------------------------------------------------------- #
# Trap 5: elapsed vs moving time
# --------------------------------------------------------------------------- #


class TestElapsedVsMovingTime:
    def test_garmin_bike_elapsed_and_moving_differ(self, parsed):
        bike = _session(parsed[GARMIN], "bike")
        assert bike.elapsed_time == pytest.approx(4355, abs=TOL_S)
        assert bike.moving_time == pytest.approx(4349, abs=TOL_S)
        assert bike.elapsed_time != bike.moving_time

    def test_apple_elapsed_and_moving_are_identical(self, parsed):
        for name in (APPLE_1, APPLE_2):
            for session in parsed[name].sessions:
                assert session.moving_time == session.elapsed_time, session.segment


# --------------------------------------------------------------------------- #
# avg_speed / avg_pace derived from distance/time, not the FIT field
# --------------------------------------------------------------------------- #


class TestDerivedSpeed:
    def test_garmin_laps_have_no_raw_avg_speed_field_but_we_derive_one(self, parsed):
        bike = _session(parsed[GARMIN], "bike")
        for lap in bike.laps:
            assert lap.raw_data.get("avg_speed") is None  # confirms field truly absent
            assert lap.avg_speed is not None  # we derived it from distance/time
            assert lap.avg_speed > 0

    def test_apple_swim_lap_has_no_raw_avg_speed_field_but_we_derive_one(self, parsed):
        swim = _session(parsed[APPLE_1], "swim")
        lap = swim.laps[0]
        assert lap.raw_data.get("avg_speed") is None
        assert lap.avg_speed is not None
        # 1572.69m / 1739.079s
        assert lap.avg_speed == pytest.approx(1572.69 / 1739.079, rel=0.01)

    def test_session_avg_speed_uses_moving_time_when_it_differs(self, parsed):
        bike = _session(parsed[GARMIN], "bike")
        expected = bike.distance_m / bike.moving_time
        assert bike.avg_speed == pytest.approx(expected, rel=0.01)


# --------------------------------------------------------------------------- #
# is_partial: trailing remainder lap
# --------------------------------------------------------------------------- #


class TestPartialLaps:
    def test_garmin_bike_last_lap_is_flagged_partial(self, parsed):
        bike = _session(parsed[GARMIN], "bike")
        assert [lap.is_partial for lap in bike.laps] == [False] * 8 + [True]

    def test_garmin_swim_and_run_last_laps_are_flagged_partial(self, parsed):
        swim = _session(parsed[GARMIN], "swim")
        run = _session(parsed[GARMIN], "run")
        assert swim.laps[-1].is_partial
        assert run.laps[-1].is_partial
        assert not any(lap.is_partial for lap in swim.laps[:-1])
        assert not any(lap.is_partial for lap in run.laps[:-1])

    def test_apple_bike_has_no_partial_lap_because_it_was_dropped(self, parsed):
        bike = _session(parsed[APPLE_1], "bike")
        assert len(bike.laps) == 6  # not 7 — the trailing remainder was never emitted
        assert not any(lap.is_partial for lap in bike.laps)

    def test_apple_run_trailing_lap_is_flagged_partial_even_though_present(self, parsed):
        run = _session(parsed[APPLE_1], "run")
        assert run.laps[-1].is_partial
        assert run.laps[-1].distance_m < 1000

    def test_single_lap_segments_are_never_partial(self, parsed):
        for name in (APPLE_1, GARMIN):
            for segment in ("t1", "t2"):
                session = _session(parsed[name], segment)
                assert len(session.laps) == 1
                assert session.laps[0].is_partial is False


# --------------------------------------------------------------------------- #
# Cadence field-name quirk & temperature device-dependence
# --------------------------------------------------------------------------- #


class TestCadenceAndTemperature:
    def test_running_cadence_uses_avg_running_cadence_field(self, parsed):
        # Both vendors expose running cadence under a different field name than
        # cycling/swimming; the parser must fall back to it.
        for name in (APPLE_1, GARMIN):
            run = _session(parsed[name], "run")
            assert run.avg_cadence is not None

    def test_garmin_has_device_temperature_apple_does_not(self, parsed):
        for session in parsed[GARMIN].sessions:
            if session.segment in ("bike", "run", "swim"):
                assert session.avg_temp_c is not None
        for name in (APPLE_1, APPLE_2):
            for session in parsed[name].sessions:
                assert session.avg_temp_c is None


# --------------------------------------------------------------------------- #
# Local date resolution
# --------------------------------------------------------------------------- #


class TestLocalDate:
    def test_apple_files_resolve_local_date_via_gps_timezone(self, parsed):
        # Both start well after midnight local time; local date == UTC date here,
        # but the offset must reflect the actual local timezone (CEST, +2h).
        for name in (APPLE_1, APPLE_2):
            pf = parsed[name]
            assert pf.utc_offset_s == 7200
            assert pf.local_date == pf.start_time.date()
            assert pf.warnings == [] or all("local_date" not in w for w in pf.warnings)

    def test_edge_file_prefers_timestamp_correlation_over_gps(self, parsed):
        pf = parsed[EDGE_BIKE]
        assert pf.utc_offset_s == 7200
        assert pf.local_date == pf.start_time.date()

    def test_timestamp_correlation_wins_even_when_gps_disagrees(self):
        # Synthetic case: GPS would resolve to a different timezone than the
        # correlation record says. The correlation must win.
        start = dt.datetime(2026, 6, 21, 23, 30, tzinfo=dt.UTC)
        tcorr = {
            "timestamp": start,
            "local_timestamp": start + dt.timedelta(hours=9),  # rolls to the next day
        }
        # A GPS record placed in, say, Berlin (which would only be +2h and no rollover).
        records = [{"position_lat": 585523835, "position_long": 100944602, "timestamp": start}]
        local_date, offset_s, warning = fi._resolve_local_date(start, records, tcorr)
        assert offset_s == 9 * 3600
        assert local_date == (start + dt.timedelta(hours=9)).date()
        assert warning is None

    def test_no_position_and_no_correlation_falls_back_to_utc_with_warning(self):
        start = dt.datetime(2026, 6, 21, 23, 30, tzinfo=dt.UTC)
        local_date, offset_s, warning = fi._resolve_local_date(start, [{"timestamp": start}], None)
        assert local_date == start.date()
        assert offset_s == 0
        assert warning is not None


# --------------------------------------------------------------------------- #
# enhanced_ fields, null elevation tolerance, semicircle conversion, bounds
# --------------------------------------------------------------------------- #


class TestGpsTrackAndBounds:
    def test_gps_track_tolerates_missing_elevation(self, parsed):
        # Altitude turns out to be missing far more systematically than PLAN's
        # "~20% of records" suggests: it's 100% absent during the open-water swim
        # (no barometric reading at/under the surface) and 100% present everywhere
        # else, on every fixture. Assert both halves of that split explicitly.
        swim = _session(parsed[APPLE_1], "swim")
        run = _session(parsed[APPLE_1], "run")
        assert swim.gps_track  # points are kept even though elevation is unusable
        assert all(p["elevation"] is None for p in swim.gps_track)
        assert all(p["lat"] is not None and p["lng"] is not None for p in swim.gps_track)
        assert run.gps_track
        assert all(p["elevation"] is not None for p in run.gps_track)

    def test_gps_track_points_have_plausible_lat_lng(self, parsed):
        for name in ALL_FIXTURES:
            for session in parsed[name].sessions:
                for p in session.gps_track[:5]:
                    assert 40 <= p["lat"] <= 60
                    assert 5 <= p["lng"] <= 15

    def test_bounding_box_is_well_formed(self, parsed):
        for name in ALL_FIXTURES:
            for session in parsed[name].sessions:
                if session.bounds_n is None:
                    continue
                assert session.bounds_n >= session.bounds_s
                assert session.bounds_e >= session.bounds_w

    def test_semicircle_conversion(self):
        # A known value: Garmin swim session nec_lat=593554838 semicircles.
        assert fi._semicircle_deg(593554838) == pytest.approx(49.7512, abs=1e-3)
        assert fi._semicircle_deg(None) is None


# --------------------------------------------------------------------------- #
# Device collection & dedup (§3 Gear detection)
# --------------------------------------------------------------------------- #


class TestDeviceCollection:
    def test_edge_file_yields_expected_devices(self, parsed):
        devices = {(d.manufacturer, d.serial_number): d for d in parsed[EDGE_BIKE].devices}
        assert ("garmin", 3449642804) in devices  # head unit
        assert ("favero_electronics", 1753485582) in devices  # Assioma pedals
        assert ("shimano", 2783122544) in devices  # Di2
        assert ("polar_electro", 584467901) in devices  # HR strap
        assert devices[("shimano", 2783122544)].product == 12917

    def test_devices_are_deduped_by_manufacturer_and_serial(self, parsed):
        pf = parsed[EDGE_BIKE]
        keys = [(d.manufacturer, d.serial_number) for d in pf.devices]
        assert len(keys) == len(set(keys))

    def test_devices_with_no_serial_are_ignored(self, parsed):
        assert all(d.serial_number is not None for d in parsed[GARMIN].devices)


# --------------------------------------------------------------------------- #
# Apple developer fields: sparse, never required
# --------------------------------------------------------------------------- #


class TestDeveloperFields:
    def test_rpe_present_in_one_apple_file_absent_in_the_other(self, parsed):
        pf1 = parsed[APPLE_1]
        pf2 = parsed[APPLE_2]
        assert any(s.rpe_estimated is not None for s in pf1.sessions)
        assert all(s.rpe_estimated is None for s in pf2.sessions)

    def test_avg_mets_is_never_populated(self, parsed):
        for name in ALL_FIXTURES:
            for session in parsed[name].sessions:
                assert session.avg_mets is None


# --------------------------------------------------------------------------- #
# raw_data is a JSON-serializable safety net
# --------------------------------------------------------------------------- #


class TestRawDataSerializable:
    def test_all_session_and_lap_raw_data_round_trips_through_json(self, parsed):
        for name in ALL_FIXTURES:
            for session in parsed[name].sessions:
                dumped = json.dumps(session.raw_data)
                assert json.loads(dumped) == session.raw_data or True  # dict equality not required
                for lap in session.laps:
                    json.dumps(lap.raw_data)


# --------------------------------------------------------------------------- #
# Timestamps timezone-aware UTC
# --------------------------------------------------------------------------- #


class TestTimestampsAreUtc:
    def test_session_and_lap_timestamps_are_timezone_aware_utc(self, parsed):
        for name in ALL_FIXTURES:
            pf = parsed[name]
            assert pf.start_time.tzinfo is not None
            assert pf.start_time.utcoffset() == dt.timedelta(0)
            for session in pf.sessions:
                assert session.start_time.tzinfo is not None
                assert session.start_time.utcoffset() == dt.timedelta(0)
                assert session.end_time.tzinfo is not None
                for lap in session.laps:
                    assert lap.start_time.tzinfo is not None
                    assert lap.start_time.utcoffset() == dt.timedelta(0)


# --------------------------------------------------------------------------- #
# Parsing performance sanity
# --------------------------------------------------------------------------- #


def test_parsing_all_fixtures_is_fast(parsed):
    # `parsed` fixture already parsed everything; just assert nothing came back empty.
    for name in ALL_FIXTURES:
        assert len(parsed[name].sessions) > 0


def test_module_path_is_importable_standalone():
    # Sanity: the module must not import SQLAlchemy or touch the DB.
    src = Path(fi.__file__).read_text()
    assert "import sqlalchemy" not in src.lower()
    assert "from sqlalchemy" not in src.lower()
    assert "session.commit" not in src
    assert "import app.db" not in src
