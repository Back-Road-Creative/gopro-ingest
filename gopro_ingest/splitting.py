"""Find natural places to cut a long recording, using its GPS track.

A three-hour recording has to become publishable episodes somehow. Cutting
at a fixed offset lands mid-corner, mid-town, mid-sentence. The track knows
better: the points where the vehicle came back to where it started, or sat
still for a few minutes, are the places a viewer would also feel an ending.

Two things have to be defended against first, because both are routine on
real cameras and both produce cut offsets that are not merely wrong but
physically impossible:

* **Lock loss** writes ``(0, 0)``. A "returned to start" test anchored at
  null island then matches every subsequent dropout.
* **Re-acquisition with a bad system clock** writes a fix timestamped days
  or years from its neighbours, which inflates the apparent recording
  length to something absurd and produces cut offsets past the end of the
  file.

:func:`sanitize_waypoints` handles both, and ``max_total_duration`` is a
second, independent bound for anything that survives.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import DEFAULT_CONFIG, IngestConfig
from .gpx import Track, haversine_km, is_invalid_coord, parse_gpx_file

__all__ = ["find_gps_split_points", "sanitize_waypoints"]

logger = logging.getLogger(__name__)


def sanitize_waypoints(waypoints: list, config: IngestConfig = DEFAULT_CONFIG) -> list:
    """Drop impossible fixes and truncate the track at a corrupt time jump.

    Invalid coordinates (see :func:`~gopro_ingest.gpx.is_invalid_coord`) are
    removed outright. Then the track is walked in order and cut at the
    first gap longer than ``config.gps_max_interwaypoint_gap_sec`` -- past
    that point the clock cannot be trusted, so everything after it is
    discarded rather than repaired.

    Accepts any sequence of objects with ``lat`` / ``lon`` / ``timestamp``
    (epoch seconds) and returns the same objects, filtered.
    """
    valid = [wp for wp in waypoints if not is_invalid_coord(wp.lat, wp.lon)]
    if len(valid) < 2:
        return valid

    truncated = [valid[0]]
    for wp in valid[1:]:
        gap = wp.timestamp - truncated[-1].timestamp
        if gap > config.gps_max_interwaypoint_gap_sec:
            logger.warning(
                "  Truncating GPS track: %.1fh gap between fixes; "
                "later points discarded as corrupt.",
                gap / 3600,
            )
            break
        truncated.append(wp)
    return truncated


def find_gps_split_points(
    track: Track | Path | str,
    config: IngestConfig = DEFAULT_CONFIG,
    max_total_duration: float | None = None,
) -> list[float]:
    """Offsets, in seconds from the start, where a recording could be cut.

    Candidates come from two signals:

    1. The track returns within ``config.gps_return_radius_km`` of its own
       first fix.
    2. The track goes effectively stationary -- under
       ``config.gps_stationary_speed_kmh`` -- for at least
       ``config.gps_stop_duration`` seconds.

    A candidate is only accepted if it leaves both the segment before it
    and the remainder at or above ``config.min_episode_duration``, and if
    the segment before it reaches
    ``config.target_episode_duration * config.target_fraction``. That last
    condition is what keeps a drive that passes its own start ten times
    from producing ten fragments.

    Returns an empty list when the track is shorter than
    ``config.max_episode_duration`` -- a recording that is already the
    right length does not need cutting -- or when it is too short or too
    broken to say anything useful, or when the file cannot be read. This
    is advisory output, so being unable to answer is not an error.

    Args:
        track: A parsed :class:`~gopro_ingest.gpx.Track`, or a path to a
            GPX file to parse.
        config: Duration and GPS-split policy.
        max_total_duration: Hard upper bound on elapsed seconds, normally
            the source file's real duration. Candidates beyond it are
            discarded. Without it, one corrupt timestamp can produce a cut
            offset past the end of the file, and the tool that acts on
            these offsets will then read past EOF.

    Returns:
        Ascending offsets in seconds from the first fix.
    """
    if isinstance(track, (str, Path)):
        try:
            track = parse_gpx_file(track)
        except Exception as e:  # noqa: BLE001 -- advisory: no track, no splits
            logger.warning("  GPS split analysis failed: %s", e)
            return []

    waypoints = sanitize_waypoints(list(track.trackpoints), config=config)
    if len(waypoints) < 10:
        return []

    total_duration = waypoints[-1].timestamp - waypoints[0].timestamp
    if total_duration <= config.max_episode_duration:
        return []
    if max_total_duration is not None and total_duration > max_total_duration:
        # Defence in depth: a single corrupt fix can slip past both filters
        # above (closer than the gap threshold to each neighbour, yet far
        # from the start). Clamp to what the caller says the file is.
        total_duration = max_total_duration

    start_lat = waypoints[0].lat
    start_lon = waypoints[0].lon
    start_time = waypoints[0].timestamp

    candidates: list[tuple[float, str]] = []
    stationary_start: float | None = None
    prev_wp = waypoints[0]

    for wp in waypoints[1:]:
        elapsed = wp.timestamp - start_time

        if elapsed > total_duration:
            prev_wp = wp
            continue
        if elapsed < config.min_episode_duration:
            prev_wp = wp
            continue
        if total_duration - elapsed < config.min_episode_duration:
            break

        dist = haversine_km(start_lat, start_lon, wp.lat, wp.lon)
        if dist <= config.gps_return_radius_km:
            candidates.append((elapsed, f"return-to-start ({dist:.2f}km)"))
            prev_wp = wp
            continue

        inter_dist = haversine_km(prev_wp.lat, prev_wp.lon, wp.lat, wp.lon)
        inter_time = wp.timestamp - prev_wp.timestamp
        if inter_time > 0:
            speed_kmh = (inter_dist / inter_time) * 3600
            if speed_kmh < config.gps_stationary_speed_kmh:
                if stationary_start is None:
                    stationary_start = prev_wp.timestamp
                stationary_elapsed = wp.timestamp - stationary_start
                if stationary_elapsed >= config.gps_stop_duration:
                    candidates.append((elapsed, f"extended-stop ({stationary_elapsed:.0f}s)"))
            else:
                stationary_start = None
        else:
            stationary_start = None

        prev_wp = wp

    if not candidates:
        return []

    splits: list[float] = []
    remaining = total_duration
    cursor = 0.0
    min_segment = config.target_episode_duration * config.target_fraction

    for ts, reason in sorted(candidates, key=lambda x: x[0]):
        segment_len = ts - cursor
        if (
            segment_len >= config.min_episode_duration
            and remaining - segment_len >= config.min_episode_duration
            and segment_len >= min_segment
        ):
            splits.append(ts)
            logger.info("  GPS split at %.1fmin: %s", ts / 60, reason)
            cursor = ts
            remaining = total_duration - cursor

    return splits
