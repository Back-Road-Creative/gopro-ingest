"""GPS-derived split points, and the defences that keep them physical.

Two failure modes on real cameras produce cut offsets that are not merely
wrong but impossible -- past the end of the file. Both are exercised here:
lock loss writing null island, and re-acquisition with a bad clock writing
a fix days or years from its neighbours.
"""

from conftest import LAT_A, LON_A, T0

from gopro_ingest import (
    DEFAULT_CONFIG,
    IngestConfig,
    Track,
    TrackPoint,
    find_gps_split_points,
    sanitize_waypoints,
)

HOUR = 3600.0
DAY = 24 * HOUR


def _wp(lat, lon, t):
    return TrackPoint(lat, lon, None, t)


class TestSanitizeWaypoints:
    def test_drops_null_island(self):
        wps = [
            _wp(LAT_A, LON_A, T0),
            _wp(0.0, 0.0, T0 + 10),
            _wp(LAT_A + 0.01, LON_A - 0.01, T0 + 20),
        ]
        out = sanitize_waypoints(wps)
        assert len(out) == 2
        assert all(wp.lat != 0.0 for wp in out)

    def test_truncates_at_a_large_time_gap(self):
        wps = [_wp(LAT_A, LON_A, T0 + i * 60) for i in range(5)]
        # A stale-clock fix five years in the future, plus its neighbour.
        wps.append(_wp(LAT_A + 0.1, LON_A - 0.1, T0 + 365 * 5 * DAY))
        wps.append(_wp(LAT_A + 0.2, LON_A - 0.2, T0 + 365 * 5 * DAY + 60))

        out = sanitize_waypoints(wps)

        assert len(out) == 5
        assert out[-1].timestamp - out[0].timestamp < 600

    def test_clean_track_is_untouched(self):
        wps = [_wp(LAT_A, LON_A, T0 + i * 30) for i in range(20)]
        assert sanitize_waypoints(wps) == wps

    def test_default_gap_bound_would_not_split_a_lunch_stop(self):
        assert DEFAULT_CONFIG.gps_max_interwaypoint_gap_sec <= 3 * HOUR

    def test_gap_bound_is_configurable(self):
        wps = [_wp(LAT_A, LON_A, T0), _wp(LAT_A, LON_A, T0 + 30 * 60)]
        assert len(sanitize_waypoints(wps)) == 2
        tight = IngestConfig(gps_max_interwaypoint_gap_sec=60.0)
        assert len(sanitize_waypoints(wps, config=tight)) == 1

    def test_fewer_than_two_valid_points_returns_what_is_left(self):
        assert sanitize_waypoints([_wp(0.0, 0.0, T0)]) == []


def _loop_track(n=120, step=60.0, radius=0.05):
    """A track that leaves the start, comes back to it midway, and leaves again.

    ``n`` fixes ``step`` seconds apart, walking two circuits of ``radius``
    degrees (~5.5 km). Only the midpoint is inside the return radius of the
    first fix, so there is exactly one obvious place to cut.
    """
    import math

    points = []
    for i in range(n):
        angle = 4 * math.pi * i / (n - 1)
        points.append(
            _wp(
                LAT_A + radius * math.sin(angle),
                LON_A + radius * (1 - math.cos(angle)),
                T0 + i * step,
            )
        )
    return Track(points)


class TestFindGpsSplitPoints:
    def test_short_track_yields_nothing(self):
        assert find_gps_split_points(Track([_wp(LAT_A, LON_A, T0 + i) for i in range(5)])) == []

    def test_track_under_the_max_is_never_split(self):
        """A recording already the right length does not need cutting."""
        track = Track([_wp(LAT_A, LON_A, T0 + i * 60) for i in range(30)])
        assert find_gps_split_points(track) == []

    def test_return_to_start_produces_a_split(self):
        # 120 fixes a minute apart = ~119 min, over the 75-min default max.
        splits = find_gps_split_points(_loop_track())
        assert splits
        assert all(0 < s < 119 * 60 for s in splits)

    def test_split_leaves_both_sides_publishable(self):
        config = DEFAULT_CONFIG
        splits = find_gps_split_points(_loop_track())
        total = 119 * 60
        for s in splits:
            assert s >= config.min_episode_duration
            assert total - s >= config.min_episode_duration

    def test_extended_stop_produces_a_split(self):
        """Sit still for long enough in the middle and that becomes the cut."""
        points = []
        t = T0
        # 40 min moving out, away from the start.
        for i in range(40):
            points.append(_wp(LAT_A + 0.01 * i, LON_A, t))
            t += 60
        # 10 min parked.
        for _ in range(10):
            points.append(_wp(LAT_A + 0.40, LON_A, t))
            t += 60
        # 40 more min moving further out.
        for i in range(40):
            points.append(_wp(LAT_A + 0.40 + 0.01 * i, LON_A, t))
            t += 60

        splits = find_gps_split_points(Track(points))

        assert splits
        assert any(abs(s - 48 * 60) < 12 * 60 for s in splits), splits

    def test_corrupt_track_never_yields_a_split_past_the_source_duration(self):
        """Lock loss plus a stale clock once produced cut offsets of 92 h and
        5 years on a ~111 min source. Both defences run here."""
        points = [_wp(LAT_A + i * 0.001, LON_A + i * 0.001, T0 + i * 200) for i in range(30)]
        points.append(_wp(0.0, 0.0, T0 + 5527 * 60))
        points.append(_wp(0.0, 0.0, T0 + 2_626_327 * 60))

        splits = find_gps_split_points(Track(points), max_total_duration=6650.0)

        assert all(0 <= s <= 6650.0 for s in splits), splits

    def test_max_total_duration_bounds_survivors(self):
        points = [_wp(LAT_A, LON_A, T0 + i * 60) for i in range(80)]
        points.append(_wp(LAT_A + 0.1, LON_A - 0.1, T0 + 10 * HOUR))

        splits = find_gps_split_points(Track(points), max_total_duration=4800.0)

        assert all(s <= 4800.0 for s in splits)

    def test_unreadable_path_degrades_to_no_splits(self, tmp_path):
        """Advisory output: being unable to answer is not an error."""
        assert find_gps_split_points(tmp_path / "missing.gpx") == []

    def test_accepts_a_gpx_path(self, tmp_path):
        from conftest import gpx_text

        path = tmp_path / "t.gpx"
        path.write_text(gpx_text(T0, 600))
        assert find_gps_split_points(path) == []  # only two fixes

    def test_target_fraction_gates_short_leading_segments(self):
        """Loosening the target lets an earlier return-to-start be taken."""
        strict = find_gps_split_points(_loop_track(), config=DEFAULT_CONFIG)
        loose = find_gps_split_points(
            _loop_track(), config=IngestConfig(target_fraction=0.1, min_episode_duration=60.0)
        )
        assert len(loose) >= len(strict)
