"""Telemetry extraction.

The parsing library is an optional dependency, so most of this runs against
a stand-in injected into ``sys.modules``. The tests that need the real
library to prove a filtering decision skip themselves when it is absent.
"""

import datetime
import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from conftest import LAT_A, LON_A

from gopro_ingest import IngestConfig, extract_gopro_gpx, parse_gpx_file


def _stream_json() -> str:
    """A minimal ``ffprobe -show_streams`` payload with a telemetry track."""
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "index": 0,
                    "disposition": {"default": 1},
                    "width": 1920,
                    "height": 1080,
                    "duration": "30.0",
                    "nb_frames": "900",
                    "avg_frame_rate": "30/1",
                },
                {"codec_type": "audio", "index": 1, "disposition": {"default": 1}},
                {
                    "codec_type": "data",
                    "codec_tag_string": "gpmd",
                    "index": 2,
                    "time_base": "1/600",
                },
            ]
        }
    )


def _mock_library(gpx_xml='<?xml version="1.0"?><gpx></gpx>'):
    """A ``sys.modules`` patch dict standing in for the whole library."""
    ffmpeg_inst = MagicMock()
    ffmpeg_inst.ffprobe.return_value.invoke.return_value.stdout = _stream_json()

    ffmpeg_mod = MagicMock()
    ffmpeg_mod.FFMPEG.return_value = ffmpeg_inst

    ffmpeg_gopro_mod = MagicMock()
    ffmpeg_gopro_mod.FFMPEGGoPro.return_value.find_frame_duration.return_value = MagicMock()

    gpx_obj = MagicMock()
    gpx_obj.to_xml.return_value = gpx_xml
    framemeta_gpx_mod = MagicMock()
    framemeta_gpx_mod.framemeta_to_gpx.return_value = gpx_obj

    return {
        "gopro_overlay": MagicMock(),
        "gopro_overlay.ffmpeg": ffmpeg_mod,
        "gopro_overlay.ffmpeg_gopro": ffmpeg_gopro_mod,
        "gopro_overlay.framemeta_gpmd": MagicMock(),
        "gopro_overlay.framemeta_gpx": framemeta_gpx_mod,
        "gopro_overlay.gpmf": MagicMock(),
        "gopro_overlay.gpmd_filters": MagicMock(),
        "gopro_overlay.counter": MagicMock(),
        "gopro_overlay.dimensions": MagicMock(),
        "gopro_overlay.timeunits": MagicMock(),
        "gopro_overlay.units": MagicMock(),
    }


class TestExtractionWithStandIn:
    def test_writes_a_gpx_and_returns_its_path(self, tmp_path):
        video = tmp_path / "gopro.mp4"
        video.touch()

        with patch.dict("sys.modules", _mock_library()):
            result = extract_gopro_gpx(video, tmp_path / "out")

        assert result is not None
        assert result.suffix == ".gpx"
        assert result.name == "gopro_gopro.gpx"
        assert result.exists()

    def test_returns_none_rather_than_raising_on_failure(self, tmp_path):
        video = tmp_path / "bad.mp4"
        video.touch()

        modules = _mock_library()
        modules["gopro_overlay.ffmpeg"].FFMPEG.side_effect = RuntimeError("probe blew up")

        with patch.dict("sys.modules", modules):
            assert extract_gopro_gpx(video, tmp_path / "out") is None

    def test_returns_none_when_the_library_is_missing(self, tmp_path, caplog):
        video = tmp_path / "gopro.mp4"
        video.touch()

        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else None

        def _blocked(name, *args, **kwargs):
            if name.startswith("gopro_overlay"):
                raise ImportError("no module named gopro_overlay")
            return (real_import or __import__)(name, *args, **kwargs)

        with caplog.at_level(logging.ERROR), patch("builtins.__import__", side_effect=_blocked):
            result = extract_gopro_gpx(video, tmp_path / "out")

        assert result is None
        assert "gopro-ingest[gopro]" in caplog.text

    def test_empty_output_is_reported_as_failure(self, tmp_path):
        video = tmp_path / "gopro.mp4"
        video.touch()

        with patch.dict("sys.modules", _mock_library(gpx_xml="")):
            assert extract_gopro_gpx(video, tmp_path / "out") is None


# ---------------------------------------------------------------------------
# The two filtering decisions, exercised against the real library.
# ---------------------------------------------------------------------------

BASE = datetime.datetime(2026, 5, 1, 11, 0, 0, tzinfo=datetime.UTC)


def _framemeta_with_mixed_fixes():
    """Points spanning pre-lock and locked fixes.

    Pre-lock rows carry coordinates the receiver cached from a previous
    session -- non-zero, so no null-island check sees them. The lock filter
    only relabels a reading; it never drops it. Without a locked-only
    filter on the way out, every one of these lands in the GPX.
    """
    from gopro_overlay.entry import Entry
    from gopro_overlay.framemeta import FrameMeta
    from gopro_overlay.point import Point
    from gopro_overlay.timeunits import timeunits
    from gopro_overlay.units import units

    # (fix quality, lat): 0=none, 1=unknown (both pre-lock); 2=2D, 3=3D.
    rows = [(0, LAT_B := 30.0), (1, LAT_B + 0.1), (2, LAT_A), (3, LAT_A + 0.1)]
    fm = FrameMeta()
    for i, (fix, lat) in enumerate(rows):
        fm.add(
            timeunits(seconds=i),
            Entry(
                dt=BASE + datetime.timedelta(seconds=i),
                point=Point(lat, LON_A),
                alt=units.Quantity(100, "m"),
                gpsfix=fix,
            ),
        )
    return fm


class TestLockedFixesOnly:
    """GPX carries no fix-quality field, so a stale point that slips in can
    never be told apart from a real one downstream."""

    def test_prelock_points_are_absent_from_the_output(self, tmp_path):
        pytest.importorskip("gopro_overlay")

        video = tmp_path / "gopro.mp4"
        video.touch()

        ffmpeg_inst = MagicMock()
        ffmpeg_inst.ffprobe.return_value.invoke.return_value.stdout = _stream_json()

        with (
            patch("gopro_overlay.ffmpeg.FFMPEG", return_value=ffmpeg_inst),
            patch("gopro_overlay.ffmpeg_gopro.FFMPEGGoPro"),
            patch("gopro_overlay.ffmpeg_gopro.GoproRecording"),
            patch("gopro_overlay.ffmpeg_gopro.filestat"),
            patch(
                "gopro_overlay.framemeta_gpmd.parse_gopro",
                return_value=_framemeta_with_mixed_fixes(),
            ),
        ):
            result = extract_gopro_gpx(video, tmp_path / "out")

        assert result is not None
        lats = [p.lat for p in parse_gpx_file(result).trackpoints]
        assert 30.0 not in lats
        assert 30.1 not in lats
        assert pytest.approx(LAT_A) in lats
        assert len(lats) == 2

    def test_rejection_reasons_are_logged(self, tmp_path, caplog):
        pytest.importorskip("gopro_overlay")

        video = tmp_path / "gopro.mp4"
        video.touch()

        ffmpeg_inst = MagicMock()
        ffmpeg_inst.ffprobe.return_value.invoke.return_value.stdout = _stream_json()
        fm = _framemeta_with_mixed_fixes()

        def _parse_and_reject(*_args, **kwargs):
            from gopro_overlay.gpmd_filters import GPSLockComponents
            from gopro_overlay.gpmf import GPSFix
            from gopro_overlay.point import Point

            kwargs["gps_lock_filter"].submit(
                GPSLockComponents(fix=GPSFix.LOCK_3D, point=Point(LAT_A, LON_A), speed=0.0, dop=999)
            )
            return fm

        with (
            patch("gopro_overlay.ffmpeg.FFMPEG", return_value=ffmpeg_inst),
            patch("gopro_overlay.ffmpeg_gopro.FFMPEGGoPro"),
            patch("gopro_overlay.ffmpeg_gopro.GoproRecording"),
            patch("gopro_overlay.ffmpeg_gopro.filestat"),
            patch("gopro_overlay.framemeta_gpmd.parse_gopro", side_effect=_parse_and_reject),
            caplog.at_level(logging.INFO, logger="gopro_ingest.telemetry"),
        ):
            result = extract_gopro_gpx(video, tmp_path / "out")

        assert result is not None
        assert "DOP" in caplog.text


def _parse_through_lock_filter(*_args, **kwargs):
    """Drive two clean fixes through the real lock filter and stamp the
    resulting quality onto each entry, exactly as the library's own visitor
    does. Both have good DOP, so only the speed bound can relabel them.
    """
    from gopro_overlay.entry import Entry
    from gopro_overlay.framemeta import FrameMeta
    from gopro_overlay.gpmd_filters import GPSLockComponents
    from gopro_overlay.gpmf import GPSFix
    from gopro_overlay.point import Point
    from gopro_overlay.timeunits import timeunits
    from gopro_overlay.units import units

    lock = kwargs["gps_lock_filter"]
    rows = [(LAT_A, 11.1), (LAT_A + 0.5, 30.56)]  # ~40 kph, ~110 kph
    fm = FrameMeta()
    for i, (lat, speed_mps) in enumerate(rows):
        point = Point(lat, LON_A)
        calculated = lock.submit(
            GPSLockComponents(fix=GPSFix.LOCK_3D, point=point, speed=speed_mps, dop=1.0)
        )
        fm.add(
            timeunits(seconds=i),
            Entry(
                dt=BASE + datetime.timedelta(seconds=i),
                point=point,
                alt=units.Quantity(100, "m"),
                gpsfix=calculated.value,
            ),
        )
    return fm


class TestSpeedBoundIsAGlitchBound:
    """The library's own default is tuned for cycling. At that setting every
    highway-speed fix is relabelled unlocked and whole stretches of a drive
    vanish from the track."""

    def test_highway_speed_fix_survives(self, tmp_path):
        pytest.importorskip("gopro_overlay")

        video = tmp_path / "gopro.mp4"
        video.touch()

        ffmpeg_inst = MagicMock()
        ffmpeg_inst.ffprobe.return_value.invoke.return_value.stdout = _stream_json()

        with (
            patch("gopro_overlay.ffmpeg.FFMPEG", return_value=ffmpeg_inst),
            patch("gopro_overlay.ffmpeg_gopro.FFMPEGGoPro"),
            patch("gopro_overlay.ffmpeg_gopro.GoproRecording"),
            patch("gopro_overlay.ffmpeg_gopro.filestat"),
            patch(
                "gopro_overlay.framemeta_gpmd.parse_gopro",
                side_effect=_parse_through_lock_filter,
            ),
        ):
            result = extract_gopro_gpx(video, tmp_path / "out")

        assert result is not None
        lats = [p.lat for p in parse_gpx_file(result).trackpoints]
        assert pytest.approx(LAT_A + 0.5) in lats
        assert pytest.approx(LAT_A) in lats

    def test_a_cycling_bound_would_drop_it(self, tmp_path):
        pytest.importorskip("gopro_overlay")

        video = tmp_path / "gopro.mp4"
        video.touch()

        ffmpeg_inst = MagicMock()
        ffmpeg_inst.ffprobe.return_value.invoke.return_value.stdout = _stream_json()

        with (
            patch("gopro_overlay.ffmpeg.FFMPEG", return_value=ffmpeg_inst),
            patch("gopro_overlay.ffmpeg_gopro.FFMPEGGoPro"),
            patch("gopro_overlay.ffmpeg_gopro.GoproRecording"),
            patch("gopro_overlay.ffmpeg_gopro.filestat"),
            patch(
                "gopro_overlay.framemeta_gpmd.parse_gopro",
                side_effect=_parse_through_lock_filter,
            ),
        ):
            result = extract_gopro_gpx(
                video, tmp_path / "out", config=IngestConfig(gpmf_speed_max_kph=60.0)
            )

        assert result is not None
        lats = [p.lat for p in parse_gpx_file(result).trackpoints]
        assert pytest.approx(LAT_A + 0.5) not in lats
