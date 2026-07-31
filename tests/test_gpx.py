"""GPX reading, distance helpers, and cold-start anchor recovery."""

import math

import pytest
from conftest import LAT_A, LAT_B, LON_A, LON_B, T0, gpx_text

from gopro_ingest import (
    GpxParseError,
    Track,
    TrackPoint,
    first_real_fix_timestamp,
    haversine_km,
    haversine_m,
    is_invalid_coord,
    parse_gpx_file,
)


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(LAT_A, LON_A, LAT_A, LON_A) == 0.0

    def test_one_degree_of_latitude(self):
        """A degree of latitude is ~111.19 km anywhere on a sphere."""
        assert haversine_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.19, abs=0.1)

    def test_quarter_meridian(self):
        """Equator to pole is a quarter of the great circle."""
        expected = 2 * math.pi * 6371.0 / 4
        assert haversine_km(0.0, 0.0, 90.0, 0.0) == pytest.approx(expected, rel=1e-6)

    def test_short_distance(self):
        """~500 m north."""
        assert 0.3 < haversine_km(LAT_A, LON_A, LAT_A + 0.0045, LON_A) < 0.7

    def test_metres_helper_agrees(self):
        km = haversine_km(LAT_A, LON_A, LAT_B, LON_B)
        assert haversine_m(LAT_A, LON_A, LAT_B, LON_B) == pytest.approx(km * 1000.0)


class TestIsInvalidCoord:
    def test_null_island_rejected(self):
        assert is_invalid_coord(0.0, 0.0)
        assert is_invalid_coord(0.0005, -0.0005)

    def test_out_of_range_rejected(self):
        assert is_invalid_coord(91.0, 0.0)
        assert is_invalid_coord(0.0, 181.0)
        assert is_invalid_coord(-90.5, 0.0)

    def test_none_rejected(self):
        assert is_invalid_coord(None, 0.0)
        assert is_invalid_coord(0.0, None)

    def test_real_coords_accepted(self):
        assert not is_invalid_coord(LAT_A, LON_A)
        assert not is_invalid_coord(LAT_B, LON_B)


class TestTrack:
    def test_span_and_bounds(self):
        track = Track(
            [TrackPoint(LAT_A, LON_A, None, 100.0), TrackPoint(LAT_A, LON_A, None, 400.0)]
        )
        assert track.start_time == 100.0
        assert track.end_time == 400.0
        assert track.span == 300.0
        assert len(track) == 2

    def test_empty_track_degrades(self):
        track = Track()
        assert track.start_time is None
        assert track.end_time is None
        assert track.span == 0.0

    def test_points_in_range_is_inclusive(self):
        track = Track([TrackPoint(LAT_A, LON_A, None, float(t)) for t in (0, 10, 20, 30)])
        assert [p.timestamp for p in track.points_in_range(10, 20)] == [10.0, 20.0]


class TestParseGpxFile:
    def test_parses_points_and_elevation(self, tmp_path):
        path = tmp_path / "t.gpx"
        path.write_text(gpx_text(T0, 600))

        track = parse_gpx_file(path)

        assert len(track) == 2
        assert track.trackpoints[0].lat == pytest.approx(LAT_A)
        assert track.trackpoints[0].elevation == pytest.approx(120.0)
        assert track.trackpoints[1].elevation is None
        assert track.span == pytest.approx(600.0)

    def test_parses_without_a_namespace(self, tmp_path):
        path = tmp_path / "t.gpx"
        path.write_text(
            "<gpx><trk><trkseg>"
            '<trkpt lat="45.0" lon="-120.0"><time>2026-05-01T12:00:00Z</time></trkpt>'
            "</trkseg></trk></gpx>"
        )
        assert len(parse_gpx_file(path)) == 1

    def test_points_are_sorted_by_time(self, tmp_path):
        path = tmp_path / "t.gpx"
        path.write_text(
            "<gpx><trk><trkseg>"
            '<trkpt lat="45.0" lon="-120.0"><time>2026-05-01T12:10:00Z</time></trkpt>'
            '<trkpt lat="45.1" lon="-120.1"><time>2026-05-01T12:00:00Z</time></trkpt>'
            "</trkseg></trk></gpx>"
        )
        stamps = [p.timestamp for p in parse_gpx_file(path).trackpoints]
        assert stamps == sorted(stamps)

    def test_timeless_point_is_dropped_not_zeroed(self, tmp_path):
        """A zero timestamp sorts to the front and scrambles every offset."""
        path = tmp_path / "t.gpx"
        path.write_text(
            "<gpx><trk><trkseg>"
            '<trkpt lat="45.0" lon="-120.0"></trkpt>'
            '<trkpt lat="45.1" lon="-120.1"><time>2026-05-01T12:00:00Z</time></trkpt>'
            "</trkseg></trk></gpx>"
        )
        track = parse_gpx_file(path)
        assert len(track) == 1
        assert track.trackpoints[0].timestamp > 0

    def test_malformed_coords_dropped(self, tmp_path):
        path = tmp_path / "t.gpx"
        path.write_text(
            "<gpx><trk><trkseg>"
            '<trkpt lat="north" lon="-120.0"><time>2026-05-01T12:00:00Z</time></trkpt>'
            '<trkpt lat="45.1" lon="-120.1"><time>2026-05-01T12:01:00Z</time></trkpt>'
            "</trkseg></trk></gpx>"
        )
        assert len(parse_gpx_file(path)) == 1

    def test_zoneless_time_is_read_as_utc(self, tmp_path):
        """Left naive, .timestamp() reads host-local and skews by whole hours."""
        path = tmp_path / "t.gpx"
        path.write_text(
            "<gpx><trk><trkseg>"
            '<trkpt lat="45.0" lon="-120.0"><time>2026-05-01T12:00:00</time></trkpt>'
            "</trkseg></trk></gpx>"
        )
        import datetime

        expected = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=datetime.UTC).timestamp()
        assert parse_gpx_file(path).trackpoints[0].timestamp == pytest.approx(expected)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(GpxParseError, match="not found"):
            parse_gpx_file(tmp_path / "nope.gpx")

    def test_malformed_xml_raises(self, tmp_path):
        path = tmp_path / "t.gpx"
        path.write_text("<gpx><trk>")
        with pytest.raises(GpxParseError, match="Invalid GPX XML"):
            parse_gpx_file(path)

    def test_entity_expansion_is_refused(self, tmp_path):
        """GPX is untrusted input; a billion-laughs payload must not expand."""
        path = tmp_path / "bomb.gpx"
        path.write_text(
            '<?xml version="1.0"?>\n'
            "<!DOCTYPE gpx [\n"
            '  <!ENTITY a "aaaaaaaaaa">\n'
            '  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
            "]>\n"
            "<gpx><trk><trkseg><trkpt lat='45.0' lon='-120.0'>"
            "<time>&b;</time></trkpt></trkseg></trk></gpx>"
        )
        with pytest.raises(GpxParseError):
            parse_gpx_file(path)


class TestFirstRealFixTimestamp:
    def test_clean_track_keeps_its_own_start(self):
        track = Track([TrackPoint(LAT_A, LON_A, None, T0 + i) for i in range(3)])
        assert first_real_fix_timestamp(track, T0) == T0

    def test_leading_null_island_is_skipped(self):
        track = Track(
            [
                TrackPoint(0.0, 0.0, None, T0),
                TrackPoint(0.0, 0.0, None, T0 + 30),
                TrackPoint(LAT_A, LON_A, None, T0 + 300),
            ]
        )
        assert first_real_fix_timestamp(track, T0) == T0 + 300

    def test_leading_stale_lock_is_skipped(self):
        """A cached coordinate from a previous session is non-zero and far away."""
        track = Track(
            [
                TrackPoint(LAT_B, LON_B, None, T0),
                TrackPoint(LAT_B, LON_B, None, T0 + 60),
                TrackPoint(LAT_A, LON_A, None, T0 + 300),
            ]
        )
        assert first_real_fix_timestamp(track, T0) == T0 + 300

    def test_parked_start_keeps_its_anchor(self):
        """A vehicle sitting still also repeats a coordinate -- and is correct."""
        track = Track(
            [
                TrackPoint(LAT_A, LON_A, None, T0),
                TrackPoint(LAT_A, LON_A, None, T0 + 60),
                TrackPoint(LAT_A + 0.0005, LON_A, None, T0 + 300),
            ]
        )
        assert first_real_fix_timestamp(track, T0) == T0

    def test_null_inside_a_stale_run_is_never_returned(self):
        track = Track(
            [
                TrackPoint(LAT_B, LON_B, None, T0),
                TrackPoint(LAT_B, LON_B, None, T0 + 30),
                TrackPoint(0.0, 0.0, None, T0 + 60),
                TrackPoint(LAT_A, LON_A, None, T0 + 300),
            ]
        )
        assert first_real_fix_timestamp(track, T0) == T0 + 300

    def test_degrades_to_fallback(self):
        assert first_real_fix_timestamp(None, 42.0) == 42.0
        assert first_real_fix_timestamp(Track(), 42.0) == 42.0

    def test_accepts_a_plain_dict(self):
        data = {
            "trackpoints": [
                {"lat": 0.0, "lon": 0.0, "timestamp": T0},
                {"lat": LAT_A, "lon": LON_A, "timestamp": T0 + 90},
            ]
        }
        assert first_real_fix_timestamp(data, T0) == T0 + 90
