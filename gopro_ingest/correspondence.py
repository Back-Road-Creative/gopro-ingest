"""Refuse a GPS track that does not belong to the video it is being paired with.

Pairing the wrong track to a video is a quiet failure. Nothing crashes.
The video is labelled with a place it never visited, the chapter marks land
at meaningless times, the distance is wrong, and every one of those errors
looks exactly like a correct result until someone who knows the road
watches it.

The risk is not evenly spread:

* Telemetry pulled out of the video's own file cannot be swapped. It came
  from those bytes. Nothing here needs to check it.
* A window cut out of that telemetry cannot be swapped either, but a bad
  window boundary can hand a segment more track than it has video.
  :func:`assert_slice_gpx_correspondence` is tuned for that.
* Anything else -- a handheld logger, a phone export, a sidecar file
  sitting in the folder, a pairing restored from a saved plan -- can be
  from a completely different day. :func:`assert_gpx_video_correspondence`
  is the gate for that case.

All the gates degrade rather than fail when they are missing an input:
no track, no video clock, no embedded coordinate. What they will not do is
treat a *missing* signal as a *passing* one. In particular, a track with no
location anchor still gets both time checks, because "no embedded tag" is
precisely the situation a swapped track arrives in.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

from .config import DEFAULT_TOLERANCES, CorrespondenceTolerances
from .gpx import first_real_fix_timestamp, haversine_km, parse_gpx_file

__all__ = [
    "GpxCorrespondenceError",
    "assert_gpx_video_correspondence",
    "assert_slice_gpx_correspondence",
    "gps_coverage_window",
    "resolve_corresponding_gpx",
]

logger = logging.getLogger(__name__)


class GpxCorrespondenceError(RuntimeError):
    """The track does not correspond to the video it was paired with."""


def _trackpoints(track) -> list:
    if track is None:
        return []
    return list(getattr(track, "trackpoints", None) or [])


def assert_gpx_video_correspondence(
    track,
    video_creation_time: datetime.datetime | None,
    video_duration: float,
    embedded_coord: tuple[float, float] | None = None,
    tolerances: CorrespondenceTolerances = DEFAULT_TOLERANCES,
) -> None:
    """Raise unless ``track`` plausibly belongs to the video described.

    Three checks, in order:

    1. **Start time.** The first fix must be within
       ``tolerances.start_time_sec`` of the video's container
       ``creation_time``. Skipped when either clock is unavailable.
    2. **Span.** The elapsed time covered by the track must be within
       either ``tolerances.span_abs_sec`` or ``tolerances.span_rel`` of
       the video's duration, whichever is larger. A half-hour track on a
       ten-minute video is a different recording.
    3. **Position.** When the container carries an ISO 6709 location tag,
       the first fix must be within
       ``tolerances.embedded_coord_radius_km`` of it.

    Args:
        track: Anything with a ``trackpoints`` list of objects carrying
            ``lat``, ``lon``, and ``timestamp`` (epoch seconds).
        video_creation_time: Timezone-aware container creation time, or
            ``None``.
        video_duration: Video length in seconds; ``0`` skips the span check.
        embedded_coord: ``(lat, lon)`` from the container tag, if any.
        tolerances: How much drift to accept.

    Raises:
        GpxCorrespondenceError: On a genuine mismatch.
    """
    trackpoints = _trackpoints(track)
    if not trackpoints:
        logger.debug("Correspondence: no trackpoints -- nothing to check")
        return

    gpx_start = trackpoints[0].timestamp
    gpx_span = trackpoints[-1].timestamp - gpx_start

    if video_creation_time is not None:
        start_delta = abs(gpx_start - video_creation_time.timestamp())
        if start_delta > tolerances.start_time_sec:
            raise GpxCorrespondenceError(
                f"GPX start-time is off by {start_delta / 60:.0f} min "
                f"(tolerance {tolerances.start_time_sec / 60:.0f} min) from the video's "
                "creation_time -- the track probably belongs to a different recording."
            )

    if video_duration and video_duration > 0:
        span_delta = abs(gpx_span - video_duration)
        span_tol = max(tolerances.span_abs_sec, tolerances.span_rel * video_duration)
        if span_delta > span_tol:
            raise GpxCorrespondenceError(
                f"GPX span {gpx_span:.0f}s diverges from video duration {video_duration:.0f}s "
                f"by {span_delta:.0f}s (tolerance {span_tol:.0f}s) -- the track probably "
                "belongs to a different recording."
            )

    if embedded_coord is None:
        logger.debug("Correspondence: no embedded coordinate -- time checks only")
        return

    elat, elon = embedded_coord
    flat = trackpoints[0].lat
    flon = trackpoints[0].lon
    if flat is None or flon is None:
        return
    dist_km = haversine_km(flat, flon, elat, elon)
    if dist_km > tolerances.embedded_coord_radius_km:
        raise GpxCorrespondenceError(
            f"GPX first fix is {dist_km:.0f} km from the video's embedded location tag "
            f"(tolerance {tolerances.embedded_coord_radius_km:.0f} km) -- the track "
            "probably belongs to a different recording."
        )


def assert_slice_gpx_correspondence(
    track,
    video_creation_time: datetime.datetime | None,
    video_duration: float,
    tolerances: CorrespondenceTolerances = DEFAULT_TOLERANCES,
) -> None:
    """Raise unless a *window* of a parent track matches the segment cut from it.

    A slice's track came out of the parent's own telemetry, so it cannot be
    a different recording. What it can be is badly anchored. The gate is
    therefore narrower than the full one:

    * **Start time** -- same rule. A receiver that took a while to lock
      leaves the window opening late, but well inside the hour tolerance;
      an anchoring bug does not.
    * **Span, one direction only** -- covering *less* video than the slice
      has is normal (a lock delay at the front, a dropout in the middle),
      so under-coverage never raises. Covering *more* means the window
      reached past the slice's own boundaries, which is a real bug.
    * **No position check** -- a stream-copied slice inherits the parent
      container's location tag, which points at where the whole recording
      started. Checking it would reject every slice of every recording
      that went anywhere.

    Raises:
        GpxCorrespondenceError: On a genuine mismatch.
    """
    trackpoints = _trackpoints(track)
    if not trackpoints:
        logger.debug("Slice correspondence: no trackpoints -- nothing to check")
        return

    gpx_start = trackpoints[0].timestamp
    gpx_span = trackpoints[-1].timestamp - gpx_start

    if video_creation_time is not None:
        start_delta = abs(gpx_start - video_creation_time.timestamp())
        if start_delta > tolerances.start_time_sec:
            raise GpxCorrespondenceError(
                f"Sliced GPX start-time is off by {start_delta / 60:.0f} min "
                f"(tolerance {tolerances.start_time_sec / 60:.0f} min) from the slice's "
                "creation_time -- the slice looks mis-anchored to its video."
            )

    if video_duration and video_duration > 0:
        over = gpx_span - video_duration
        span_tol = max(tolerances.span_abs_sec, tolerances.span_rel * video_duration)
        if over > span_tol:
            raise GpxCorrespondenceError(
                f"Sliced GPX span {gpx_span:.0f}s over-covers video duration "
                f"{video_duration:.0f}s by {over:.0f}s (tolerance {span_tol:.0f}s) -- the "
                "slice window reached past its own extent."
            )


def gps_coverage_window(
    track,
    video_creation_time: datetime.datetime | None,
) -> tuple[float, float] | None:
    """Where in the *video* the GPS coverage actually starts and ends.

    Returns ``(first_fix_offset_s, last_fix_offset_s)`` measured from the
    container ``creation_time`` -- video ``t=0`` -- not from the track's
    own first fix. That choice is the whole point: a non-zero
    ``first_fix_offset_s`` is a receiver that had not locked yet when
    recording started, which means any footage-to-GPS mapping anchored on
    the first fix is shifted by exactly that much.

    Leading cold-start artefacts are skipped via
    :func:`~gopro_ingest.gpx.first_real_fix_timestamp`, so the window opens
    at the first believable fix.

    Purely diagnostic -- it derives nothing and changes nothing. Returns
    ``None`` when either the anchor or the fixes are unavailable.
    """
    if track is None or video_creation_time is None:
        return None
    trackpoints = _trackpoints(track)
    if not trackpoints:
        return None

    anchor = video_creation_time.timestamp()
    first_valid_ts = first_real_fix_timestamp(track, trackpoints[0].timestamp)
    return (first_valid_ts - anchor, trackpoints[-1].timestamp - anchor)


def resolve_corresponding_gpx(
    video_path: Path,
    explicit_gpx: Path | None = None,
    video_duration: float = 0.0,
    tolerances: CorrespondenceTolerances = DEFAULT_TOLERANCES,
) -> Path | None:
    """Pick a GPX for ``video_path``, or return ``None`` rather than guess.

    The obvious implementation -- ``glob("*.gpx")[0]`` -- adopts whichever
    file the filesystem happens to list first. One stale track left in a
    working folder then silently drives the labels, the chapter marks, and
    the recorded date of an unrelated video.

    Precedence here:

    1. ``explicit_gpx`` if given. The caller said so; it is authoritative.
       If it does not exist, the answer is ``None`` -- never a quiet fall
       back to a glob.
    2. An exact ``<video-stem>.gpx`` beside the video. Trusted by name.
    3. Any other ``*.gpx`` in the same folder, but only if it passes
       :func:`assert_gpx_video_correspondence` against this video. First
       one that passes wins; the rest are refused with a warning.

    Returns:
        A path that exists and is trustworthy, or ``None``.
    """
    from .probe import extract_location_coords, extract_recording_date

    if explicit_gpx is not None:
        explicit_gpx = Path(explicit_gpx)
        return explicit_gpx if explicit_gpx.exists() else None

    sidecar = video_path.with_suffix(".gpx")
    if sidecar.exists():
        return sidecar

    candidates = sorted(video_path.parent.glob("*.gpx"))
    if not candidates:
        return None

    creation_time = extract_recording_date(video_path)
    embedded = extract_location_coords(video_path)
    for candidate in candidates:
        try:
            track = parse_gpx_file(candidate)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  Skipping unparseable GPX %s: %s", candidate.name, exc)
            continue
        try:
            assert_gpx_video_correspondence(
                track,
                creation_time,
                video_duration,
                embedded_coord=embedded,
                tolerances=tolerances,
            )
        except GpxCorrespondenceError as exc:
            logger.warning("  Refusing GPX %s -- no correspondence: %s", candidate.name, exc)
            continue
        return candidate
    return None
