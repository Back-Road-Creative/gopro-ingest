"""Reassemble GoPro footage into episodes, and pair it with GPS you can trust.

A card of GoPro footage does not arrive as recordings. It arrives as
chapter files with a counter-intuitive naming scheme, split at 4 GB
boundaries that have nothing to do with when you pressed record. This
package puts them back together, groups the results into publishable
episodes, finds sensible places to cut the long ones using the GPS track,
recovers that track from the camera's own telemetry stream, and refuses to
attach a track that belongs to a different recording.

Typical use::

    from gopro_ingest import create_ingest_plan

    plan = create_ingest_plan("/media/card/DCIM/100GOPRO")
    print(plan.summary())

Every threshold is in :class:`IngestConfig`; nothing is hard-coded to one
publishing schedule.
"""

from __future__ import annotations

from .config import (
    DEFAULT_CONFIG,
    DEFAULT_TOLERANCES,
    CorrespondenceTolerances,
    IngestConfig,
)
from .correspondence import (
    CorrespondenceResult,
    CorrespondenceStatus,
    GpxCorrespondenceError,
    InsufficientCorrespondenceEvidence,
    assert_gpx_video_correspondence,
    assert_slice_gpx_correspondence,
    check_gpx_video_correspondence,
    check_slice_gpx_correspondence,
    gps_coverage_window,
    resolve_corresponding_gpx,
)
from .gpx import (
    GpxParseError,
    Track,
    TrackPoint,
    first_real_fix_timestamp,
    haversine_km,
    haversine_m,
    is_invalid_coord,
    parse_gpx_file,
)
from .grouping import create_ingest_plan, group_recordings_into_episodes
from .models import Episode, GoProChapter, GoProRecording, IngestPlan
from .naming import GOPRO_RE, is_gopro_named, parse_gopro_filename
from .probe import (
    detect_gpmf_stream,
    extract_location_coords,
    extract_recording_date,
    ffprobe_creation_time,
    get_clip_duration,
)
from .scanner import scan_gopro_directory, scan_video_directory
from .splitting import find_gps_split_points, sanitize_waypoints
from .telemetry import extract_gopro_gpx

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_TOLERANCES",
    "GOPRO_RE",
    "CorrespondenceResult",
    "CorrespondenceStatus",
    "CorrespondenceTolerances",
    "Episode",
    "GoProChapter",
    "GoProRecording",
    "GpxCorrespondenceError",
    "GpxParseError",
    "IngestConfig",
    "IngestPlan",
    "InsufficientCorrespondenceEvidence",
    "Track",
    "TrackPoint",
    "__version__",
    "assert_gpx_video_correspondence",
    "assert_slice_gpx_correspondence",
    "check_gpx_video_correspondence",
    "check_slice_gpx_correspondence",
    "create_ingest_plan",
    "detect_gpmf_stream",
    "extract_gopro_gpx",
    "extract_location_coords",
    "extract_recording_date",
    "ffprobe_creation_time",
    "find_gps_split_points",
    "first_real_fix_timestamp",
    "get_clip_duration",
    "gps_coverage_window",
    "group_recordings_into_episodes",
    "haversine_km",
    "haversine_m",
    "is_gopro_named",
    "is_invalid_coord",
    "parse_gopro_filename",
    "parse_gpx_file",
    "resolve_corresponding_gpx",
    "sanitize_waypoints",
    "scan_gopro_directory",
    "scan_video_directory",
]
