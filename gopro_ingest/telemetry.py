"""Pull the GPS track out of a GoPro's own telemetry stream.

GoPro cameras record more than picture and sound. A private data track,
tagged ``gpmd``, carries GPS fixes, accelerometer and gyro samples, and
various camera state. A track recovered from there is frame-aligned with
the footage by construction, which makes it strictly better than any
external log: there is no clock to reconcile and no possibility of pairing
it with the wrong video.

Parsing the binary format is done by the third-party ``gopro-overlay``
library, which is an **optional** dependency:

    pip install "gopro-ingest[gopro]"

The import is deliberately deferred to call time. Detection, scanning,
grouping, splitting, and the correspondence gates all work without it, and
most users of this package never extract telemetry at all.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from .config import DEFAULT_CONFIG, IngestConfig
from .probe import detect_gpmf_stream

__all__ = ["detect_gpmf_stream", "extract_gopro_gpx"]

logger = logging.getLogger(__name__)


def extract_gopro_gpx(
    video_path: Path,
    output_dir: Path,
    config: IngestConfig = DEFAULT_CONFIG,
) -> Path | None:
    """Write ``<stem>_gopro.gpx`` from the video's telemetry stream.

    Two filtering decisions are worth knowing about, because both were
    silently wrong with the library's defaults:

    **Only locked fixes are written.** The lock filter *relabels* a bad
    reading's fix quality; it never drops the point. Without an explicit
    locked-only filter on the way out, coordinates the receiver had cached
    from a previous session -- non-zero, so invisible to any null-island
    check -- end up in the file. GPX has no fix-quality field, so once they
    are written no downstream consumer can tell them apart from real ones.

    **The speed cap is a glitch bound, not a cruising limit.** It exists to
    discard readings that teleport. The library's own default is tuned for
    cycling, and at that setting every highway-speed fix is relabelled
    unlocked and disappears -- taking whole stretches of a drive out of the
    resulting track. ``config.gpmf_speed_max_kph`` defaults to a
    road-vehicle bound instead.

    Args:
        video_path: A GoPro MP4 carrying a ``gpmd`` stream.
        output_dir: Created if needed; receives ``<stem>_gopro.gpx``.
        config: Supplies the lock-filter bounds and the sampling step.

    Returns:
        Path to the written GPX, or ``None`` if the library is missing,
        the file has no telemetry, or extraction fails. Never raises --
        a card with one unreadable file should still ingest.
    """
    try:
        from gopro_overlay import gpmd_filters
        from gopro_overlay.counter import ReasonCounter
        from gopro_overlay.dimensions import Dimension
        from gopro_overlay.ffmpeg import FFMPEG
        from gopro_overlay.ffmpeg_gopro import (
            AudioStream,
            DataStream,
            FFMPEGGoPro,
            GoproRecording,
            VideoStream,
            filestat,
        )
        from gopro_overlay.framemeta_gpmd import parse_gopro
        from gopro_overlay.framemeta_gpx import framemeta_to_gpx
        from gopro_overlay.gpmf import GPS_FIXED_VALUES
        from gopro_overlay.timeunits import timeunits
        from gopro_overlay.units import units
    except ImportError:
        logger.error(
            "  Telemetry extraction needs the optional 'gopro' extra: "
            'pip install "gopro-ingest[gopro]"'
        )
        return None

    try:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        gpx_path = output_dir / f"{video_path.stem}_gopro.gpx"

        ffmpeg = FFMPEG(None)
        output = (
            ffmpeg.ffprobe()
            .invoke(["-hide_banner", "-print_format", "json", "-show_streams", str(video_path)])
            .stdout
        )
        streams = json.loads(str(output))["streams"]

        # GoPro files carry two audio streams; take the one marked default.
        video_streams = [
            s for s in streams if s["codec_type"] == "video" and s["disposition"]["default"] == 1
        ]
        audio_streams = [
            s for s in streams if s["codec_type"] == "audio" and s["disposition"]["default"] == 1
        ]
        data_streams = [s for s in streams if s.get("codec_tag_string") == "gpmd"]

        if not video_streams or not data_streams:
            logger.warning("  No video or telemetry stream in %s", video_path.name)
            return None

        if not audio_streams:
            audio_streams = [s for s in streams if s["codec_type"] == "audio"]

        video = video_streams[0]
        audio = audio_streams[0] if audio_streams else None
        data = data_streams[0]

        avg_fr = video["avg_frame_rate"].split("/")
        video_stream = VideoStream(
            stream=int(video["index"]),
            dimension=Dimension(video["width"], video["height"]),
            duration=timeunits(seconds=float(video["duration"])),
            frame_count=int(video["nb_frames"]),
            frame_rate_numerator=int(avg_fr[0]),
            frame_rate_denominator=int(avg_fr[1]),
        )

        ffmpeg_gopro = FFMPEGGoPro(ffmpeg)
        data_stream = DataStream(
            stream=int(data["index"]),
            frame_duration=ffmpeg_gopro.find_frame_duration(video_path, int(data["index"])),
            frame_count=int(video["nb_frames"]),
            timebase=int(data.get("time_base", "1/1000").split("/")[1]),
        )

        audio_stream = AudioStream(stream=int(audio["index"])) if audio else AudioStream(stream=1)

        recording = GoproRecording(
            ffmpeg=ffmpeg,
            location=video_path,
            file=filestat(video_path),
            video=video_stream,
            audio=audio_stream,
            data=data_stream,
        )

        counter = ReasonCounter()
        frame_meta = parse_gopro(
            recording.load_data(),
            units,
            recording.data,
            gps_lock_filter=gpmd_filters.standard(
                dop_max=config.gpmf_dop_max,
                speed_max=units.Quantity(config.gpmf_speed_max_kph, "kph"),
                report=counter.because,
            ),
        )
        gpx = framemeta_to_gpx(
            frame_meta,
            step=datetime.timedelta(seconds=config.gpx_sample_step_sec),
            filter_fn=lambda e: e.gpsfix in GPS_FIXED_VALUES,
        )

        # Report what the lock filter threw away. The counter is otherwise
        # write-only, and a silently emptied track is hard to diagnose.
        if counter:
            dropped = sum(counter.values())
            reasons = "; ".join(f"{reason}: {n}" for reason, n in sorted(counter.items()))
            logger.info(
                "  Lock filter rejected %d unlocked reading(s), excluded from GPX (%s)",
                dropped,
                reasons,
            )

        gpx_path.write_text(gpx.to_xml())

        if gpx_path.exists() and gpx_path.stat().st_size > 0:
            logger.info("  Extracted telemetry: %s", gpx_path.name)
            return gpx_path

        logger.warning("  No GPS data extracted from %s", video_path.name)
        return None

    except Exception as e:  # noqa: BLE001 -- one bad file must not stop a card
        logger.warning("  Telemetry extraction failed for %s: %s", video_path.name, e)
        return None
