"""Container-metadata reads, all with ffprobe stubbed out."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gopro_ingest import (
    detect_gpmf_stream,
    extract_location_coords,
    extract_recording_date,
    ffprobe_creation_time,
    get_clip_duration,
)

FAKE = Path("/fake/video.mp4")


def _run(stdout="", returncode=0):
    return MagicMock(returncode=returncode, stdout=stdout, stderr="")


class TestGetClipDuration:
    def test_reads_duration(self):
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run("1025.5\n")):
            assert get_clip_duration(FAKE) == pytest.approx(1025.5)

    def test_zero_on_probe_failure(self):
        """A truncated file reports zero and is filtered, never crashes the scan."""
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run("", 1)):
            assert get_clip_duration(FAKE) == 0.0

    def test_zero_when_ffprobe_is_absent(self):
        with patch("gopro_ingest.probe.subprocess.run", side_effect=FileNotFoundError):
            assert get_clip_duration(FAKE) == 0.0


class TestFfprobeCreationTime:
    def test_z_suffix_becomes_utc_aware(self):
        with patch(
            "gopro_ingest.probe.subprocess.run", return_value=_run("2026-05-01T18:00:00.000000Z\n")
        ):
            result = ffprobe_creation_time(FAKE)
        assert result is not None
        assert result.tzinfo is not None
        assert result.timestamp() == datetime(2026, 5, 1, 18, tzinfo=UTC).timestamp()

    def test_existing_offset_passes_through(self):
        with patch(
            "gopro_ingest.probe.subprocess.run", return_value=_run("2026-05-01T13:00:00-05:00\n")
        ):
            result = ffprobe_creation_time(FAKE)
        assert result.timestamp() == datetime(2026, 5, 1, 18, tzinfo=UTC).timestamp()

    def test_none_when_absent(self):
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run("")):
            assert ffprobe_creation_time(FAKE) is None


class TestExtractRecordingDate:
    def test_parses_iso_creation_time(self):
        out = json.dumps({"format": {"tags": {"creation_time": "2026-05-01T11:12:19.000000Z"}}})
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run(out)):
            result = extract_recording_date(FAKE)
        assert (result.year, result.month, result.day) == (2026, 5, 1)

    def test_none_when_tag_missing(self):
        with patch(
            "gopro_ingest.probe.subprocess.run", return_value=_run(json.dumps({"format": {}}))
        ):
            assert extract_recording_date(FAKE) is None

    def test_none_on_probe_failure(self):
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run("", 1)):
            assert extract_recording_date(FAKE) is None

    @pytest.mark.parametrize(
        "creation_time",
        [
            "2026-04-01T18:00:00.000000Z",
            "2026-04-01T18:00:00Z",
            "2026-04-01 18:00:00",
        ],
    )
    def test_result_is_utc_aware_and_host_tz_invariant(self, creation_time):
        """The tag denotes UTC in all three spellings. Parsed naive,
        .timestamp() would re-read it as host-local and shift the epoch by
        the host's offset -- enough to make a correct track fail the gate."""
        import os
        import time

        expected = datetime(2026, 4, 1, 18, tzinfo=UTC).timestamp()
        out = json.dumps({"format": {"tags": {"creation_time": creation_time}}})

        old_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/New_York"
            time.tzset()
            with patch("gopro_ingest.probe.subprocess.run", return_value=_run(out)):
                result = extract_recording_date(FAKE)
            assert result is not None
            assert result.tzinfo is not None
            assert result.utcoffset() == UTC.utcoffset(None)
            assert result.timestamp() == expected
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()


class TestExtractLocationCoords:
    def test_parses_iso_6709_tag(self):
        out = json.dumps({"format": {"tags": {"location": "+45.0000-120.0000/"}}})
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run(out)):
            assert extract_location_coords(FAKE) == (45.0, -120.0)

    def test_falls_back_to_the_eng_variant(self):
        out = json.dumps({"format": {"tags": {"location-eng": "+45.0000-120.0000/"}}})
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run(out)):
            assert extract_location_coords(FAKE) == (45.0, -120.0)

    def test_none_when_absent(self):
        out = json.dumps({"format": {"tags": {}}})
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run(out)):
            assert extract_location_coords(FAKE) is None

    def test_none_on_unparseable_tag(self):
        out = json.dumps({"format": {"tags": {"location": "somewhere nice"}}})
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run(out)):
            assert extract_location_coords(FAKE) is None


class TestDetectGpmfStream:
    def test_true_for_a_gpmd_stream(self):
        out = json.dumps(
            {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "codec_tag_string": "avc1"},
                    {"codec_type": "data", "codec_name": "gpmd", "codec_tag_string": "gpmd"},
                ]
            }
        )
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run(out)):
            assert detect_gpmf_stream(FAKE) is True

    def test_false_without_telemetry(self):
        out = json.dumps(
            {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "codec_tag_string": "avc1"},
                    {"codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
                ]
            }
        )
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run(out)):
            assert detect_gpmf_stream(FAKE) is False

    def test_false_on_probe_error(self):
        with patch("gopro_ingest.probe.subprocess.run", return_value=_run("", 1)):
            assert detect_gpmf_stream(FAKE) is False

    def test_false_when_ffprobe_is_absent(self):
        with patch("gopro_ingest.probe.subprocess.run", side_effect=OSError("no ffprobe")):
            assert detect_gpmf_stream(FAKE) is False
