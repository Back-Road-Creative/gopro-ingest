"""Container metadata read through ``ffprobe``.

Every function here shells out to ``ffprobe`` and degrades to ``None`` or a
zero value rather than raising, because a card full of footage routinely
contains one truncated file and the scan should survive it.

``ffprobe`` must be on ``PATH``. It ships with FFmpeg.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import subprocess
from pathlib import Path

__all__ = [
    "detect_gpmf_stream",
    "extract_location_coords",
    "extract_recording_date",
    "ffprobe_creation_time",
    "get_clip_duration",
]

logger = logging.getLogger(__name__)

#: Seconds to wait on any single ``ffprobe`` invocation.
FFPROBE_TIMEOUT_SEC = 30


def _ffprobe(args: list[str], timeout: int = FFPROBE_TIMEOUT_SEC) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ffprobe", "-v", "error", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def get_clip_duration(video_path: Path) -> float:
    """Duration of ``video_path`` in seconds, or ``0.0`` if it cannot be read.

    A zero here is meaningful downstream: a truncated or corrupt file
    reports zero and is then filtered out by the minimum-clip-duration
    rule, instead of crashing the scan.
    """
    try:
        result = _ffprobe(
            ["-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:  # noqa: BLE001 -- a bad file must not stop the scan
        logger.warning("Failed to get duration for %s: %s", video_path.name, e)
    return 0.0


def ffprobe_creation_time(video_path: Path) -> datetime.datetime | None:
    """Container ``creation_time`` as a timezone-aware datetime, or ``None``.

    FFmpeg writes this tag in ISO 8601 with a trailing ``Z`` for UTC. The
    ``Z`` is normalised to an explicit ``+00:00`` offset rather than
    stripped, so the result stays timezone-aware: a naive value silently
    re-reads as host-local time, and an evening recording in UTC then keys
    into the following calendar day. Strings that already carry an offset
    pass through unchanged.
    """
    try:
        result = _ffprobe(
            ["-show_entries", "format_tags=creation_time", "-of", "csv=p=0", str(video_path)],
        )
        raw = (result.stdout or "").strip()
        if result.returncode != 0 or not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(raw)
    except Exception as e:  # noqa: BLE001
        logger.debug("ffprobe creation_time failed for %s: %s", video_path.name, e)
        return None


def extract_recording_date(video_path: Path) -> datetime.datetime | None:
    """Recording start time from container metadata, as UTC, or ``None``.

    Accepts the handful of ``creation_time`` spellings seen in the wild.
    All of them denote UTC -- the trailing ``Z`` is matched as a literal
    and the zone-less form is UTC too -- so UTC is attached explicitly.
    Without that, ``.timestamp()`` would reinterpret the wall clock as
    host-local and skew every downstream comparison by the host's offset,
    which is exactly the kind of error that makes a correct track look
    like a mismatched one.

    Works with GoPro and with most other cameras that write the standard
    MP4 container tag.
    """
    try:
        result = _ffprobe(
            ["-show_entries", "format_tags=creation_time", "-of", "json", str(video_path)],
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        creation_time = data.get("format", {}).get("tags", {}).get("creation_time")
        if not creation_time:
            return None

        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.datetime.strptime(creation_time, fmt)
            except ValueError:
                continue
            return parsed.replace(tzinfo=datetime.UTC)

        return None
    except Exception as e:  # noqa: BLE001
        logger.debug("Recording date extraction failed for %s: %s", video_path, e)
        return None


def extract_location_coords(video_path: Path) -> tuple[float, float] | None:
    """Approximate ``(latitude, longitude)`` from the container tag, or ``None``.

    GoPro (and various phones) stamp an ISO 6709 ``location`` tag into the
    container -- ``"+45.0000-120.0000/"`` and similar. Reading it costs one
    ``ffprobe`` call, versus parsing the whole telemetry stream, which
    makes it a cheap anchor for the correspondence gate.
    """
    try:
        result = _ffprobe(
            [
                "-show_entries",
                "format_tags=location,location-eng",
                "-of",
                "json",
                str(video_path),
            ],
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        tags = data.get("format", {}).get("tags", {})
        loc_str = tags.get("location") or tags.get("location-eng")
        if not loc_str:
            return None

        match = re.match(r"([+-]\d+\.?\d*?)([+-]\d+\.?\d*)", loc_str)
        if match:
            return (float(match.group(1)), float(match.group(2)))
        return None
    except Exception as e:  # noqa: BLE001
        logger.debug("Location extraction failed for %s: %s", video_path, e)
        return None


def detect_gpmf_stream(video_path: Path) -> bool:
    """Return ``True`` when the file carries a GoPro telemetry stream.

    GoPro stores GPS, accelerometer, and gyro samples in a private data
    track tagged ``gpmd``. Its presence is what separates a file whose
    track can be recovered from the video itself from one that needs an
    external track.
    """
    try:
        result = _ffprobe(["-show_streams", "-of", "json", str(video_path)])
        if result.returncode != 0:
            return False

        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            if stream.get("codec_tag_string") == "gpmd" or stream.get("codec_name") == "gpmd":
                return True
        return False
    except Exception as e:  # noqa: BLE001
        logger.debug("Telemetry detection failed for %s: %s", video_path, e)
        return False
