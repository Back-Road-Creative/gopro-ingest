"""The gates that refuse a track belonging to a different recording."""

import math
from datetime import UTC, datetime

import pytest
from conftest import CREATION, LAT_A, LAT_B, LON_A, LON_B, T0, gpx_text, make_track

from gopro_ingest import (
    CorrespondenceStatus,
    CorrespondenceTolerances,
    GpxCorrespondenceError,
    InsufficientCorrespondenceEvidence,
    Track,
    TrackPoint,
    assert_gpx_video_correspondence,
    assert_slice_gpx_correspondence,
    check_gpx_video_correspondence,
    check_slice_gpx_correspondence,
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


class TestCheckResult:
    """The structured result keeps "verified" and "could not check" apart."""

    def test_valid_pair_is_matched_with_both_time_checks(self):
        result = check_gpx_video_correspondence(
            make_track(T0 + 5, 600), CREATION, video_duration=600.0
        )
        assert result.status is CorrespondenceStatus.MATCHED
        assert result.verified
        assert result.checked == ("start_time", "span")
        assert result.skipped == ("position",)
        assert result.problems == ()

    def test_position_check_is_recorded_when_run(self):
        result = check_gpx_video_correspondence(
            make_track(T0, 600), CREATION, 600.0, embedded_coord=(LAT_A, LON_A)
        )
        assert result.status is CorrespondenceStatus.MATCHED
        assert result.checked == ("start_time", "span", "position")
        assert result.skipped == ()

    def test_mismatch_carries_the_reason(self):
        result = check_gpx_video_correspondence(
            make_track(T0 + 2 * 3600, 600), CREATION, video_duration=600.0
        )
        assert result.status is CorrespondenceStatus.MISMATCHED
        assert not result.verified
        assert "start-time" in result.problems[0]

    @pytest.mark.parametrize(
        ("track", "creation", "duration", "skipped"),
        [
            (Track(), CREATION, 600.0, {"start_time", "span", "position"}),
            (None, CREATION, 600.0, {"start_time", "span", "position"}),
            (make_track(T0, 600), None, 600.0, {"start_time", "position"}),
            (make_track(T0, 600), CREATION, 0.0, {"span", "position"}),
        ],
        ids=["empty-track", "no-track", "no-clock", "no-duration"],
    )
    def test_missing_input_is_insufficient_not_matched(self, track, creation, duration, skipped):
        result = check_gpx_video_correspondence(track, creation, duration)
        assert result.status is CorrespondenceStatus.INSUFFICIENT
        assert not result.verified
        assert set(result.skipped) == skipped

    @pytest.mark.parametrize("duration", [-1.0, math.nan, math.inf, -math.inf])
    def test_bad_duration_is_invalid(self, duration):
        result = check_gpx_video_correspondence(make_track(T0, 600), CREATION, duration)
        assert result.status is CorrespondenceStatus.INVALID
        assert not result.verified
        assert "duration" in result.problems[0]

    def test_reversed_timestamps_are_invalid(self):
        result = check_gpx_video_correspondence(
            make_track(T0 + 600, -600), CREATION, video_duration=600.0
        )
        assert result.status is CorrespondenceStatus.INVALID
        assert "reversed" in result.problems[0]

    @pytest.mark.parametrize("bad", [math.nan, math.inf])
    def test_nonfinite_track_timestamp_is_invalid(self, bad):
        track = Track([TrackPoint(LAT_A, LON_A, None, bad), TrackPoint(LAT_A, LON_A, None, T0)])
        result = check_gpx_video_correspondence(track, CREATION, video_duration=600.0)
        assert result.status is CorrespondenceStatus.INVALID

    def test_nonfinite_embedded_coord_is_invalid(self):
        result = check_gpx_video_correspondence(
            make_track(T0, 600), CREATION, 600.0, embedded_coord=(math.nan, LON_A)
        )
        assert result.status is CorrespondenceStatus.INVALID

    def test_invalid_outranks_insufficient(self):
        """No clock AND a NaN duration: the bad input is the headline."""
        result = check_gpx_video_correspondence(make_track(T0, 600), None, math.nan)
        assert result.status is CorrespondenceStatus.INVALID

    def test_slice_result_has_no_position_check(self):
        result = check_slice_gpx_correspondence(make_track(T0, 600), CREATION, 600.0)
        assert result.status is CorrespondenceStatus.MATCHED
        assert result.checked == ("start_time", "span")
        assert result.skipped == ()

    def test_slice_over_coverage_is_a_mismatch(self):
        result = check_slice_gpx_correspondence(make_track(T0, 1800), CREATION, 600.0)
        assert result.status is CorrespondenceStatus.MISMATCHED
        assert "over-covers" in result.problems[0]

    def test_slice_insufficient_and_invalid(self):
        assert (
            check_slice_gpx_correspondence(Track(), CREATION, 600.0).status
            is CorrespondenceStatus.INSUFFICIENT
        )
        assert (
            check_slice_gpx_correspondence(make_track(T0, 600), CREATION, math.nan).status
            is CorrespondenceStatus.INVALID
        )


class TestAssertCompatibilityAndStrict:
    """Legacy asserts keep their degrade-on-missing path; strict closes it."""

    @pytest.mark.parametrize("duration", [-1.0, math.nan, math.inf])
    def test_nonfinite_or_negative_duration_raises_even_when_not_strict(self, duration):
        """These used to be silently skipped; NaN made every comparison False."""
        with pytest.raises(GpxCorrespondenceError, match="duration"):
            assert_gpx_video_correspondence(make_track(T0, 600), CREATION, duration)
        with pytest.raises(GpxCorrespondenceError, match="duration"):
            assert_slice_gpx_correspondence(make_track(T0, 600), CREATION, duration)

    def test_reversed_track_raises_even_when_not_strict(self):
        with pytest.raises(GpxCorrespondenceError, match="reversed"):
            assert_gpx_video_correspondence(make_track(T0 + 600, -600), CREATION, 600.0)

    def test_nan_track_timestamp_no_longer_passes_silently(self):
        track = Track(
            [TrackPoint(LAT_A, LON_A, None, math.nan), TrackPoint(LAT_A, LON_A, None, T0)]
        )
        with pytest.raises(GpxCorrespondenceError):
            assert_gpx_video_correspondence(track, CREATION, 600.0)

    @pytest.mark.parametrize(
        ("track", "creation", "duration"),
        [
            (Track(), CREATION, 600.0),
            (None, CREATION, 600.0),
            (make_track(T0, 600), None, 600.0),
            (make_track(T0, 600), CREATION, 0.0),
        ],
        ids=["empty-track", "no-track", "no-clock", "no-duration"],
    )
    def test_strict_refuses_insufficient_evidence(self, track, creation, duration):
        with pytest.raises(InsufficientCorrespondenceEvidence, match="insufficient"):
            assert_gpx_video_correspondence(track, creation, duration, strict=True)
        with pytest.raises(InsufficientCorrespondenceEvidence):
            assert_slice_gpx_correspondence(track, creation, duration, strict=True)

    def test_insufficient_error_is_a_correspondence_error(self):
        assert issubclass(InsufficientCorrespondenceEvidence, GpxCorrespondenceError)

    def test_strict_accepts_a_fully_checked_pair(self):
        assert_gpx_video_correspondence(make_track(T0, 600), CREATION, 600.0, strict=True)
        assert_slice_gpx_correspondence(make_track(T0, 600), CREATION, 600.0, strict=True)

    def test_strict_still_raises_the_mismatch_message(self):
        with pytest.raises(GpxCorrespondenceError, match="start-time"):
            assert_gpx_video_correspondence(
                make_track(T0 + 2 * 3600, 600), CREATION, 600.0, strict=True
            )


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

    def test_strict_refuses_a_candidate_it_cannot_verify(self, tmp_path, monkeypatch):
        """No video clock: a lenient glob adopts on span alone; strict does not."""
        monkeypatch.setattr("gopro_ingest.probe.extract_recording_date", lambda _p: None)
        monkeypatch.setattr("gopro_ingest.probe.extract_location_coords", lambda _p: None)
        video = tmp_path / "myclip.mp4"
        video.touch()
        candidate = tmp_path / "randomname.gpx"
        candidate.write_text(gpx_text(T0 + 5, 600))

        assert resolve_corresponding_gpx(video, video_duration=600.0) == candidate
        assert resolve_corresponding_gpx(video, video_duration=600.0, strict=True) is None

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
