"""Chapter / recording / episode / plan data models."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from gopro_ingest import Episode, GoProChapter, GoProRecording, IngestPlan


def _chapter(chapter_num, rec_num, duration, ct=None):
    return GoProChapter(
        path=Path(f"/fake/GX{chapter_num:02d}{rec_num:04d}.MP4"),
        chapter_num=chapter_num,
        recording_num=rec_num,
        duration=duration,
        creation_time=ct,
    )


def _recording(rec_num, duration, ct):
    return GoProRecording(recording_num=rec_num, chapters=[_chapter(1, rec_num, duration, ct)])


class TestGoProRecording:
    def test_total_duration_sums_chapters(self):
        rec = GoProRecording(recording_num=50)
        rec.chapters = [
            _chapter(1, 50, 1025.0),
            _chapter(2, 50, 1025.0),
            _chapter(3, 50, 617.7),
        ]
        assert abs(rec.total_duration - 2667.7) < 0.01

    def test_creation_time_comes_from_first_chapter(self):
        ct = datetime(2026, 2, 21, 17, 11, 14, tzinfo=UTC)
        rec = GoProRecording(recording_num=50, chapters=[_chapter(1, 50, 1025.0, ct)])
        assert rec.creation_time == ct

    def test_end_time_is_start_plus_duration(self):
        ct = datetime(2026, 2, 21, 17, 11, 14, tzinfo=UTC)
        rec = GoProRecording(
            recording_num=50,
            chapters=[_chapter(1, 50, 1025.0, ct), _chapter(2, 50, 600.0, ct)],
        )
        assert rec.end_time == ct + timedelta(seconds=1625.0)

    def test_end_time_none_without_clock(self):
        rec = GoProRecording(recording_num=50, chapters=[_chapter(1, 50, 10.0, None)])
        assert rec.end_time is None


class TestEpisode:
    def test_total_duration_sums_recordings(self):
        ep = Episode(
            index=1,
            recordings=[
                _recording(46, 1620.0, datetime(2026, 2, 20, 16, 0, tzinfo=UTC)),
                _recording(47, 2913.0, datetime(2026, 2, 20, 17, 48, tzinfo=UTC)),
            ],
        )
        assert abs(ep.total_duration - 4533.0) < 0.01

    def test_total_duration_min(self):
        ep = Episode(
            index=1,
            recordings=[_recording(46, 3600.0, datetime(2026, 2, 20, 16, 0, tzinfo=UTC))],
        )
        assert abs(ep.total_duration_min - 60.0) < 0.01

    def test_chapter_files_sorted_by_chapter_not_insertion(self):
        rec = GoProRecording(recording_num=46)
        rec.chapters = [_chapter(2, 46, 595.0), _chapter(1, 46, 1025.0)]
        files = Episode(index=1, recordings=[rec]).chapter_files()
        assert [f.name for f in files] == ["GX010046.MP4", "GX020046.MP4"]

    def test_gpx_source_defaults_to_camera_telemetry(self):
        """Telemetry from the video's own bytes cannot be a different recording."""
        assert Episode(index=1).gpx_source == "gpmf"

    def test_gpx_source_settable_external(self):
        assert Episode(index=1, gpx_source="external").gpx_source == "external"


class TestSliceDerivedEpisode:
    """A slice's own extent wins over the recordings it no longer describes."""

    def test_explicit_duration_overrides_recordings(self):
        rec = _recording(46, 3600.0, datetime(2026, 2, 20, 16, 0, tzinfo=UTC))
        ep = Episode(index=1, recordings=[rec], explicit_total_duration=900.0)
        assert ep.total_duration == 900.0

    def test_explicit_creation_time_overrides_recordings(self):
        parent_ct = datetime(2026, 2, 20, 16, 0, tzinfo=UTC)
        slice_ct = datetime(2026, 2, 20, 17, 0, tzinfo=UTC)
        rec = _recording(46, 3600.0, parent_ct)
        ep = Episode(index=1, recordings=[rec], explicit_creation_time=slice_ct)
        assert ep.creation_time == slice_ct

    def test_slice_with_no_recordings_reports_its_own_extent(self):
        ep = Episode(index=1, recordings=[], explicit_total_duration=2880.0)
        assert ep.total_duration == 2880.0
        assert ep.chapter_files() == []


class TestIngestPlan:
    def test_summary_format(self):
        ep = Episode(
            index=1,
            recordings=[
                GoProRecording(
                    recording_num=46,
                    chapters=[
                        GoProChapter(
                            Path("/f/GX010046.MP4"),
                            1,
                            46,
                            1620.0,
                            datetime(2026, 2, 20, 16, 0, tzinfo=UTC),
                        )
                    ],
                )
            ],
            date="2026-02-20",
            location="Test Region",
        )
        summary = IngestPlan(source_dir=Path("/test"), episodes=[ep]).summary()
        assert "Episode 1" in summary
        assert "Test Region" in summary
        assert "1 episodes" in summary

    def test_totals(self):
        ep = Episode(index=1, recordings=[], explicit_total_duration=1800.0)
        plan = IngestPlan(source_dir=Path("/test"), episodes=[ep])
        assert abs(plan.total_duration_min - 30.0) < 0.01
        assert plan.total_source_files == 0


class TestRoundTrip:
    """to_dict / from_dict must return real dataclasses, not raw dicts."""

    def _plan(self):
        ct = datetime(2026, 2, 20, 16, 0, tzinfo=UTC)
        return IngestPlan(
            source_dir=Path("/card"),
            episodes=[
                Episode(
                    index=1,
                    recordings=[_recording(46, 1800.0, ct)],
                    date="2026-02-20",
                    location="Test Region",
                    concat_path=Path("/out/ep01.mp4"),
                    gpx_path=Path("/out/ep01.gpx"),
                    gpx_source="external",
                )
            ],
            filtered_clips=[_chapter(1, 99, 4.0, ct)],
            unrecognized_files=[Path("/card/readme.txt")],
        )

    def test_round_trip_preserves_values(self):
        restored = IngestPlan.from_dict(self._plan().to_dict())
        assert restored.source_dir == Path("/card")
        ep = restored.episodes[0]
        assert ep.date == "2026-02-20"
        assert ep.location == "Test Region"
        assert ep.gpx_source == "external"
        assert ep.concat_path == Path("/out/ep01.mp4")
        assert abs(ep.total_duration - 1800.0) < 0.01
        assert restored.filtered_clips[0].recording_num == 99
        assert restored.unrecognized_files == [Path("/card/readme.txt")]

    def test_children_are_dataclasses_not_dicts(self):
        """A shallow rehydrate leaves dicts, and getattr on a dict returns
        the default instead of raising -- every episode then silently
        reports a blank date and zero duration."""
        restored = IngestPlan.from_dict(self._plan().to_dict())
        assert isinstance(restored.episodes[0], Episode)
        assert isinstance(restored.episodes[0].recordings[0], GoProRecording)
        assert isinstance(restored.episodes[0].recordings[0].chapters[0], GoProChapter)
        assert isinstance(restored.filtered_clips[0], GoProChapter)

    def test_creation_time_survives_as_aware_datetime(self):
        restored = IngestPlan.from_dict(self._plan().to_dict())
        ct = restored.episodes[0].creation_time
        assert ct is not None
        assert ct.tzinfo is not None
        assert ct.utcoffset() == timedelta(0)

    def test_from_dict_tolerates_a_minimal_payload(self):
        plan = IngestPlan.from_dict({"source_dir": "/card"})
        assert plan.source_dir == Path("/card")
        assert plan.episodes == []
