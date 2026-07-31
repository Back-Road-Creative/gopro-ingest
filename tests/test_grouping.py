"""Episode grouping and end-to-end plan creation."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from gopro_ingest import (
    GoProChapter,
    GoProRecording,
    IngestConfig,
    create_ingest_plan,
    group_recordings_into_episodes,
)

CT = datetime(2026, 2, 20, 16, 0, tzinfo=UTC)
SEVENTY_FIVE_MIN = IngestConfig(max_episode_duration=75 * 60)


def _rec(rec_num, duration_min, ct):
    return GoProRecording(
        recording_num=rec_num,
        chapters=[
            GoProChapter(
                path=Path(f"/fake/GX01{rec_num:04d}.MP4"),
                chapter_num=1,
                recording_num=rec_num,
                duration=duration_min * 60,
                creation_time=ct,
            )
        ],
    )


def _at(hour, minute=0, day=20):
    return datetime(2026, 2, day, hour, minute, tzinfo=UTC)


class TestGroupRecordings:
    def test_close_recordings_combine(self):
        recs = {46: _rec(46, 27, _at(16)), 47: _rec(47, 40, _at(17, 48))}
        episodes = group_recordings_into_episodes(recs, config=SEVENTY_FIVE_MIN)
        assert len(episodes) == 1
        assert len(episodes[0].recordings) == 2

    def test_split_when_combined_exceeds_max(self):
        recs = {46: _rec(46, 50, _at(16)), 47: _rec(47, 50, _at(17))}
        episodes = group_recordings_into_episodes(recs, config=SEVENTY_FIVE_MIN)
        assert len(episodes) == 2

    def test_split_when_gap_exceeds_max(self):
        recs = {46: _rec(46, 27, _at(10)), 47: _rec(47, 49, _at(16))}
        episodes = group_recordings_into_episodes(
            recs, config=IngestConfig(max_gap_between_recordings=120 * 60)
        )
        assert len(episodes) == 2

    def test_different_days_stay_separate(self):
        recs = {46: _rec(46, 27, _at(16)), 50: _rec(50, 96, _at(17, day=21))}
        assert len(group_recordings_into_episodes(recs)) == 2

    def test_three_close_recordings_chain(self):
        recs = {
            51: _rec(51, 30, _at(18, 48, day=21)),
            52: _rec(52, 26, _at(19, 20, day=21)),
            53: _rec(53, 12, _at(19, 50, day=21)),
        }
        episodes = group_recordings_into_episodes(recs, config=SEVENTY_FIVE_MIN)
        assert len(episodes) == 1
        assert len(episodes[0].recordings) == 3

    def test_empty_input(self):
        assert group_recordings_into_episodes({}) == []

    def test_single_over_length_recording_stays_whole(self):
        """Splitting a long recording is the GPS splitter's job, not this one's."""
        recs = {58: _rec(58, 117, _at(15, day=24))}
        episodes = group_recordings_into_episodes(recs)
        assert len(episodes) == 1
        assert episodes[0].total_duration_min == pytest.approx(117.0)

    def test_episodes_are_numbered_from_one(self):
        recs = {
            46: _rec(46, 27, _at(10)),
            47: _rec(47, 49, _at(16)),
            50: _rec(50, 96, _at(17, day=21)),
        }
        assert [ep.index for ep in group_recordings_into_episodes(recs)] == [1, 2, 3]

    def test_short_episodes_dropped_and_renumbered(self):
        recs = {
            57: _rec(57, 0.2, _at(14, day=24)),
            58: _rec(58, 117, _at(15, day=24)),
        }
        episodes = group_recordings_into_episodes(recs)
        assert len(episodes) == 1
        assert episodes[0].recordings[0].recording_num == 58
        assert episodes[0].index == 1

    def test_date_comes_from_first_recording(self):
        recs = {46: _rec(46, 27, datetime(2026, 2, 20, 16, 26, 7, tzinfo=UTC))}
        assert group_recordings_into_episodes(recs)[0].date == "2026-02-20"

    def test_mixed_aware_and_missing_creation_time_does_not_crash(self):
        """An unknown clock must not be compared against an aware datetime."""
        recs = {46: _rec(46, 27, _at(16)), 47: _rec(47, 40, None)}
        assert len(group_recordings_into_episodes(recs, config=SEVENTY_FIVE_MIN)) >= 1

    def test_minimum_is_configurable(self):
        recs = {46: _rec(46, 5, _at(16))}
        assert group_recordings_into_episodes(recs) == []
        kept = group_recordings_into_episodes(recs, config=IngestConfig(min_episode_duration=60.0))
        assert len(kept) == 1


class TestCreateIngestPlanGoPro:
    @patch("gopro_ingest.scanner.get_clip_duration", return_value=1800.0)
    @patch("gopro_ingest.scanner.extract_recording_date")
    def test_two_close_recordings_become_one_episode(self, mock_date, _dur, tmp_path):
        mock_date.side_effect = lambda p: _at(16) if "0046" in str(p) else _at(17)
        for name in ("GX010046.MP4", "GX010047.MP4"):
            (tmp_path / name).touch()

        plan = create_ingest_plan(tmp_path)

        assert len(plan.episodes) == 1
        assert plan.total_source_files == 2

    def test_empty_directory_yields_empty_plan(self, tmp_path):
        assert create_ingest_plan(tmp_path).episodes == []

    def test_non_directory_raises(self, tmp_path):
        f = tmp_path / "not_a_dir.mp4"
        f.touch()
        with pytest.raises(ValueError, match="Not a directory"):
            create_ingest_plan(f)


class TestCreateIngestPlanGeneric:
    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=CT)
    def test_each_file_becomes_an_episode(self, _ct, _dur, tmp_path):
        for name in ("a.mp4", "b.mp4"):
            (tmp_path / name).touch()

        plan = create_ingest_plan(tmp_path, mode="generic", extensions=[".mp4"])

        assert len(plan.episodes) == 2
        assert [ep.index for ep in plan.episodes] == [1, 2]
        assert all(ep.date == "2026-02-20" for ep in plan.episodes)

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=CT)
    def test_sidecar_gpx_is_attached_and_marked_external(self, _ct, _dur, tmp_path):
        """A non-GoPro source has no on-camera telemetry, so the sidecar is
        the only track -- and being external is what puts it through the gate."""
        (tmp_path / "a.mp4").touch()
        (tmp_path / "a.gpx").write_text("<gpx></gpx>")

        ep = create_ingest_plan(tmp_path, mode="generic", extensions=[".mp4"]).episodes[0]

        assert ep.gpx_path == tmp_path / "a.gpx"
        assert ep.gpx_source == "external"

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=CT)
    def test_no_sidecar_leaves_gpx_unset(self, _ct, _dur, tmp_path):
        (tmp_path / "a.mp4").touch()
        ep = create_ingest_plan(tmp_path, mode="generic", extensions=[".mp4"]).episodes[0]
        assert ep.gpx_path is None

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=None)
    def test_undateable_file_refuses_rather_than_using_copy_time(self, _ct, _dur, tmp_path):
        (tmp_path / "drive.mp4").touch()
        with pytest.raises(ValueError, match="drive.mp4"):
            create_ingest_plan(tmp_path, mode="generic", extensions=[".mp4"])
