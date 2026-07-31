"""Directory scanning: chapter grouping, generic mode, and mode refusal."""

import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from gopro_ingest import IngestConfig, scan_gopro_directory, scan_video_directory

CT = datetime(2026, 2, 20, 16, 0, tzinfo=UTC)


class TestGoProMode:
    @patch("gopro_ingest.scanner.get_clip_duration", return_value=1025.0)
    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    def test_groups_by_recording_number(self, _date, _dur, tmp_path):
        for name in ("GX010046.MP4", "GX020046.MP4", "GX010047.MP4"):
            (tmp_path / name).touch()

        recs, _, _ = scan_gopro_directory(tmp_path)

        assert set(recs) == {46, 47}
        assert len(recs[46].chapters) == 2
        assert len(recs[47].chapters) == 1

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=1025.0)
    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    def test_chapters_sorted_by_number(self, _date, _dur, tmp_path):
        for name in ("GX030046.MP4", "GX010046.MP4", "GX020046.MP4"):
            (tmp_path / name).touch()

        recs, _, _ = scan_gopro_directory(tmp_path)
        assert [ch.chapter_num for ch in recs[46].chapters] == [1, 2, 3]

    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    @patch("gopro_ingest.scanner.get_clip_duration")
    def test_filters_short_clips(self, mock_dur, _date, tmp_path):
        (tmp_path / "GX010046.MP4").touch()
        (tmp_path / "GX010048.MP4").touch()
        mock_dur.side_effect = lambda p: 1025.0 if "0046" in str(p) else 4.0

        recs, filtered, _ = scan_gopro_directory(tmp_path)

        assert 46 in recs
        assert 48 not in recs
        assert [c.recording_num for c in filtered] == [48]

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=1025.0)
    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    def test_non_gopro_names_collected_not_raised(self, _date, _dur, tmp_path):
        """The lenient wrapper collects strays instead of refusing the card."""
        (tmp_path / "GX010046.MP4").touch()
        (tmp_path / "random_video.MP4").touch()

        recs, _, unrec = scan_gopro_directory(tmp_path)

        assert len(recs) == 1
        assert [p.name for p in unrec] == ["random_video.MP4"]

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=1025.0)
    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    def test_strict_mode_groups_the_same_way(self, _date, _dur, tmp_path):
        for name in ("GX010046.MP4", "GX020046.MP4", "GX010047.MP4"):
            (tmp_path / name).touch()

        recs, _, _ = scan_video_directory(tmp_path, extensions=[".mp4"], mode="gopro")

        assert len(recs[46].chapters) == 2
        assert len(recs[47].chapters) == 1


class TestGoProModeEdgeCases:
    @patch("gopro_ingest.scanner.get_clip_duration", return_value=1025.0)
    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    def test_missing_chapter_mid_sequence(self, _date, _dur, tmp_path):
        """Chapter 2 absent: 1 and 3 still group, gap is the caller's problem."""
        (tmp_path / "GX010046.MP4").touch()
        (tmp_path / "GX030046.MP4").touch()

        recs, _, _ = scan_gopro_directory(tmp_path)
        assert [ch.chapter_num for ch in recs[46].chapters] == [1, 3]

    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    @patch("gopro_ingest.scanner.get_clip_duration")
    def test_zero_duration_file_is_filtered(self, mock_dur, _date, tmp_path):
        """A truncated file probes as 0s and falls under the minimum."""
        (tmp_path / "GX010046.MP4").touch()
        (tmp_path / "GX010047.MP4").touch()
        mock_dur.side_effect = lambda p: 0.0 if "0047" in str(p) else 1800.0

        recs, filtered, _ = scan_gopro_directory(tmp_path)
        assert 46 in recs
        assert [c.recording_num for c in filtered] == [47]

    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    @patch("gopro_ingest.scanner.get_clip_duration")
    def test_probe_failure_skips_only_that_file(self, mock_dur, _date, tmp_path):
        (tmp_path / "GX010046.MP4").touch()
        (tmp_path / "GX010047.MP4").touch()

        def dur(path):
            if "0047" in str(path):
                raise RuntimeError("ffprobe failed: invalid data")
            return 1800.0

        mock_dur.side_effect = dur

        recs, _, unrec = scan_gopro_directory(tmp_path)
        assert 46 in recs
        assert 47 not in recs
        assert [p.name for p in unrec] == ["GX010047.MP4"]

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=1800.0)
    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    def test_same_chapter_number_across_sessions(self, _date, _dur, tmp_path):
        (tmp_path / "GX010046.MP4").touch()
        (tmp_path / "GX010047.MP4").touch()

        recs, _, _ = scan_gopro_directory(tmp_path)
        assert len(recs[46].chapters) == 1
        assert len(recs[47].chapters) == 1

    def test_empty_directory(self, tmp_path):
        assert scan_gopro_directory(tmp_path) == ({}, [], [])


class TestGenericMode:
    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=CT)
    def test_each_file_is_its_own_recording(self, _ct, _dur, tmp_path):
        for name in ("clip_a.mp4", "drone_clip.mov", "phone.mkv"):
            (tmp_path / name).touch()

        recs, filtered, unrec = scan_video_directory(
            tmp_path, extensions=[".mp4", ".mov", ".mkv"], mode="generic"
        )

        assert len(recs) == 3
        assert not filtered and not unrec
        assert all(len(r.chapters) == 1 and r.chapters[0].chapter_num == 1 for r in recs.values())

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time")
    def test_creation_time_honored_when_present(self, mock_ct, _dur, tmp_path):
        expected = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
        mock_ct.return_value = expected
        (tmp_path / "x.mp4").touch()

        recs, _, _ = scan_video_directory(tmp_path, extensions=[".mp4"], mode="generic")
        assert next(iter(recs.values())).chapters[0].creation_time == expected

    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=CT)
    @patch("gopro_ingest.scanner.get_clip_duration", return_value=1234.5)
    def test_duration_honored(self, _dur, _ct, tmp_path):
        (tmp_path / "x.mp4").touch()
        recs, _, _ = scan_video_directory(tmp_path, extensions=[".mp4"], mode="generic")
        assert next(iter(recs.values())).chapters[0].duration == 1234.5

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=None)
    def test_undateable_file_hard_fails(self, _ct, _dur, tmp_path):
        """mtime is copy time. Stamping it would date the clip to the offload day."""
        vid = tmp_path / "x.mp4"
        vid.touch()
        offload = datetime(2026, 3, 15, 10, 0, tzinfo=UTC).timestamp()
        os.utime(vid, (offload, offload))

        with pytest.raises(ValueError, match="x.mp4"):
            scan_video_directory(tmp_path, extensions=[".mp4"], mode="generic")

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=None)
    def test_gpx_sidecar_satisfies_and_mtime_is_never_stamped(self, _ct, _dur, tmp_path):
        vid = tmp_path / "x.mp4"
        vid.touch()
        offload = datetime(2026, 3, 15, 10, 0, tzinfo=UTC).timestamp()
        os.utime(vid, (offload, offload))
        (tmp_path / "x.gpx").write_text("<gpx></gpx>")

        recs, _, _ = scan_video_directory(tmp_path, extensions=[".mp4"], mode="generic")

        # Left None on purpose: the caller dates it from the track's first fix.
        assert next(iter(recs.values())).chapters[0].creation_time is None

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=None)
    def test_empty_gpx_sidecar_does_not_satisfy(self, _ct, _dur, tmp_path):
        vid = tmp_path / "x.mp4"
        vid.touch()
        (tmp_path / "x.gpx").touch()  # zero bytes

        with pytest.raises(ValueError, match="x.mp4"):
            scan_video_directory(tmp_path, extensions=[".mp4"], mode="generic")


class TestModeRefusal:
    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    def test_generic_mode_refuses_gopro_names(self, _dur, tmp_path):
        (tmp_path / "GX010046.MP4").touch()
        (tmp_path / "other_clip.mp4").touch()

        with pytest.raises(ValueError, match="GX010046"):
            scan_video_directory(tmp_path, extensions=[".mp4"], mode="generic")

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    def test_gopro_mode_refuses_non_gopro_names(self, _dur, tmp_path):
        (tmp_path / "GX010046.MP4").touch()
        (tmp_path / "random.mp4").touch()

        with pytest.raises(ValueError, match="random.mp4"):
            scan_video_directory(tmp_path, extensions=[".mp4"], mode="gopro")

    def test_unknown_mode_raises(self, tmp_path):
        with pytest.raises(ValueError, match="must be 'gopro' or 'generic'"):
            scan_video_directory(tmp_path, mode="sideways")


class TestExtensionFiltering:
    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=CT)
    def test_files_outside_the_list_are_ignored(self, _ct, _dur, tmp_path):
        (tmp_path / "ok.mp4").touch()
        (tmp_path / "skipme.txt").touch()
        (tmp_path / "skipme.avi").touch()

        recs, _, _ = scan_video_directory(tmp_path, extensions=[".mp4"], mode="generic")

        assert len(recs) == 1
        assert next(iter(recs.values())).chapters[0].path.name == "ok.mp4"

    @patch("gopro_ingest.scanner.get_clip_duration", return_value=600.0)
    @patch("gopro_ingest.scanner.ffprobe_creation_time", return_value=CT)
    def test_extension_match_is_case_insensitive(self, _ct, _dur, tmp_path):
        (tmp_path / "a.MP4").touch()
        (tmp_path / "b.Mov").touch()

        recs, _, _ = scan_video_directory(tmp_path, extensions=[".mp4", ".mov"], mode="generic")
        assert len(recs) == 2


class TestConfigDrivesFiltering:
    @patch("gopro_ingest.scanner.extract_recording_date", return_value=CT)
    @patch("gopro_ingest.scanner.get_clip_duration", return_value=30.0)
    def test_raising_min_clip_duration_filters_more(self, _dur, _date, tmp_path):
        (tmp_path / "GX010046.MP4").touch()

        recs, filtered, _ = scan_gopro_directory(tmp_path)
        assert 46 in recs and not filtered

        recs, filtered, _ = scan_gopro_directory(
            tmp_path, config=IngestConfig(min_clip_duration=60.0)
        )
        assert not recs and len(filtered) == 1
