"""The gates that refuse a track belonging to a different recording."""

from datetime import UTC, datetime

import pytest
from conftest import CREATION, LAT_A, LAT_B, LON_A, LON_B, T0, gpx_text, make_track

from gopro_ingest import (
    CorrespondenceTolerances,
    GpxCorrespondenceError,
    Track,
    TrackPoint,
    assert_gpx_video_correspondence,
    assert_slice_gpx_correspondence,
    gps_coverage_window,
    resolve_corresponding_gpx,
)
from gopro_ingest import correspondence as corr


class TestFullGate:
    def test_corresponding_track_passes(self):
        assert_gpx_video_correspondence(make_track(T0 + 5, 600), CREATION, video_duration=600.0)

    def test_wrong_start_time_raises(self):
        with pytest.raises(GpxCorrespondenceError, match="start-time"):
            assert_gpx_video_correspondence(
                make_track(T0 + 2 * 3600, 600), CREATION, video_duration=600.0
            )

    def test_wrong_span_raises(self):
        """Start lines up but the track covers 30 min of a 10 min video."""
        with pytest.raises(GpxCorrespondenceError, match="span"):
            assert_gpx_video_correspondence(make_track(T0, 1800), CREATION, video_duration=600.0)

    def test_span_within_tolerance_passes(self):
        assert_gpx_video_correspondence(make_track(T0, 615), CREATION, video_duration=600.0)

    def test_first_fix_near_embedded_coord_passes(self):
        assert_gpx_video_correspondence(
            make_track(T0, 600, LAT_A, LON_A),
            CREATION,
            video_duration=600.0,
            embedded_coord=(LAT_A + 0.01, LON_A - 0.01),
        )

    def test_first_fix_far_from_embedded_coord_raises(self):
        with pytest.raises(GpxCorrespondenceError, match="embedded"):
            assert_gpx_video_correspondence(
                make_track(T0, 600, LAT_B, LON_B),
                CREATION,
                video_duration=600.0,
                embedded_coord=(LAT_A, LON_A),
            )

    def test_no_embedded_coord_still_gates_on_time(self):
        """A missing anchor is exactly how a swapped track arrives. It is not
        a free pass."""
        with pytest.raises(GpxCorrespondenceError, match="start-time"):
            assert_gpx_video_correspondence(
                make_track(T0 + 2 * 3600, 600),
                CREATION,
                video_duration=600.0,
                embedded_coord=None,
            )

    def test_no_embedded_coord_corresponding_track_passes(self):
        assert_gpx_video_correspondence(
            make_track(T0, 600), CREATION, video_duration=600.0, embedded_coord=None
        )

    def test_missing_inputs_degrade_without_raising(self):
        assert_gpx_video_correspondence(Track(), CREATION, video_duration=600.0)
        assert_gpx_video_correspondence(make_track(0, 600), None, video_duration=600.0)
        assert_gpx_video_correspondence(None, CREATION, video_duration=600.0)
        assert_gpx_video_correspondence(make_track(T0, 600), CREATION, video_duration=0.0)

    def test_tolerances_are_configurable(self):
        tight = CorrespondenceTolerances(start_time_sec=60.0)
        with pytest.raises(GpxCorrespondenceError, match="start-time"):
            assert_gpx_video_correspondence(
                make_track(T0 + 300, 600), CREATION, video_duration=600.0, tolerances=tight
            )
        # Same track passes under the default hour tolerance.
        assert_gpx_video_correspondence(make_track(T0 + 300, 600), CREATION, video_duration=600.0)


class TestSliceGate:
    def test_well_corresponded_slice_passes(self):
        assert_slice_gpx_correspondence(make_track(T0, 600), CREATION, video_duration=600.0)

    def test_lock_delay_prefix_passes(self):
        """A late lock leaves the window opening late but well inside an hour."""
        assert_slice_gpx_correspondence(
            make_track(T0 + 34 * 60, 1560), CREATION, video_duration=3600.0
        )

    def test_under_coverage_passes(self):
        """A mid-recording dropout is expected, not a mis-anchored slice."""
        assert_slice_gpx_correspondence(make_track(T0, 300), CREATION, video_duration=600.0)

    def test_wrong_start_time_raises(self):
        with pytest.raises(GpxCorrespondenceError, match="start-time"):
            assert_slice_gpx_correspondence(
                make_track(T0 + 2 * 3600, 600), CREATION, video_duration=600.0
            )

    def test_over_coverage_raises(self):
        """More track than video means the window reached past its own extent."""
        with pytest.raises(GpxCorrespondenceError, match="over-covers"):
            assert_slice_gpx_correspondence(make_track(T0, 1800), CREATION, video_duration=600.0)

    def test_missing_inputs_degrade_without_raising(self):
        assert_slice_gpx_correspondence(Track(), CREATION, video_duration=600.0)
        assert_slice_gpx_correspondence(make_track(T0, 600), None, video_duration=600.0)
        assert_slice_gpx_correspondence(None, CREATION, video_duration=600.0)


class TestCoverageWindow:
    def test_healthy_track_opens_at_zero(self):
        first, last = gps_coverage_window(make_track(T0, 600), CREATION)
        assert abs(first) < 0.01
        assert abs(last - 600.0) < 0.01

    def test_unlocked_prefix_surfaces_as_an_offset(self):
        """A non-zero opening offset is a receiver that had not locked yet --
        every first-fix-anchored mapping is shifted by exactly that much."""
        first, _ = gps_coverage_window(make_track(T0 + 34 * 60, 1560), CREATION)
        assert abs(first - 34 * 60) < 0.01

    def test_leading_null_island_is_skipped(self):
        track = Track(
            [
                TrackPoint(0.0, 0.0, None, T0),
                TrackPoint(0.0, 0.0, None, T0 + 30),
                TrackPoint(LAT_A, LON_A, None, T0 + 300),
                TrackPoint(LAT_A + 0.01, LON_A + 0.01, None, T0 + 900),
            ]
        )
        first, last = gps_coverage_window(track, CREATION)
        assert abs(first - 300.0) < 0.01
        assert abs(last - 900.0) < 0.01

    def test_degrades_to_none(self):
        assert gps_coverage_window(None, CREATION) is None
        assert gps_coverage_window(make_track(T0, 600), None) is None
        assert gps_coverage_window(Track(), CREATION) is None


@pytest.fixture
def stub_probe(monkeypatch):
    """Stand in for the ffprobe-backed readers so no real MP4 is needed."""
    monkeypatch.setattr(corr, "extract_recording_date", lambda _p: CREATION, raising=False)
    monkeypatch.setattr(corr, "extract_location_coords", lambda _p: None, raising=False)
    monkeypatch.setattr("gopro_ingest.probe.extract_recording_date", lambda _p: CREATION)
    monkeypatch.setattr("gopro_ingest.probe.extract_location_coords", lambda _p: None)


class TestResolveCorrespondingGpx:
    def test_arbitrary_non_matching_track_is_refused(self, tmp_path, stub_probe):
        """One stale track left in a folder must not silently drive the labels."""
        video = tmp_path / "myclip.mp4"
        video.touch()
        (tmp_path / "someotherdrive.gpx").write_text(gpx_text(T0 + 2 * 3600, 600))

        assert resolve_corresponding_gpx(video, video_duration=600.0) is None

    def test_arbitrary_matching_track_is_adopted(self, tmp_path, stub_probe):
        video = tmp_path / "myclip.mp4"
        video.touch()
        match = tmp_path / "randomname.gpx"
        match.write_text(gpx_text(T0 + 5, 600))

        assert resolve_corresponding_gpx(video, video_duration=600.0) == match

    def test_exact_stem_sidecar_trusted_by_name(self, tmp_path, stub_probe):
        video = tmp_path / "myclip.mp4"
        video.touch()
        sidecar = tmp_path / "myclip.gpx"
        sidecar.write_text(gpx_text(T0 + 2 * 3600, 600))  # would fail the gate

        assert resolve_corresponding_gpx(video, video_duration=600.0) == sidecar

    def test_explicit_path_is_authoritative(self, tmp_path, stub_probe):
        video = tmp_path / "myclip.mp4"
        video.touch()
        explicit = tmp_path / "operator_supplied.gpx"
        explicit.write_text(gpx_text(T0 + 2 * 3600, 600))

        resolved = resolve_corresponding_gpx(video, explicit_gpx=explicit, video_duration=600.0)
        assert resolved == explicit

    def test_missing_explicit_path_never_falls_back_to_a_glob(self, tmp_path, stub_probe):
        video = tmp_path / "myclip.mp4"
        video.touch()
        (tmp_path / "tempting.gpx").write_text(gpx_text(T0 + 5, 600))

        assert (
            resolve_corresponding_gpx(
                video, explicit_gpx=tmp_path / "gone.gpx", video_duration=600.0
            )
            is None
        )

    def test_no_candidates_returns_none(self, tmp_path, stub_probe):
        video = tmp_path / "myclip.mp4"
        video.touch()
        assert resolve_corresponding_gpx(video, video_duration=600.0) is None

    def test_unparseable_candidate_is_skipped_not_fatal(self, tmp_path, stub_probe):
        video = tmp_path / "myclip.mp4"
        video.touch()
        (tmp_path / "aaa_broken.gpx").write_text("<gpx><trk>")
        good = tmp_path / "zzz_good.gpx"
        good.write_text(gpx_text(T0 + 5, 600))

        assert resolve_corresponding_gpx(video, video_duration=600.0) == good


def test_creation_time_is_compared_as_a_true_epoch():
    """A naive video clock would skew the gate by the host's UTC offset and
    reject a perfectly good track."""
    aware = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    assert_gpx_video_correspondence(make_track(aware.timestamp(), 600), aware, 600.0)
