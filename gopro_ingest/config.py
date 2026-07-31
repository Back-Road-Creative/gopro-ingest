"""Tunable policy for ingest, episode grouping, and GPX correspondence.

Every threshold in this library is editorial, not physical. How long a
publishable episode should be, how far apart two recordings may sit before
they stop being one outing, how close to the start counts as "came back" --
those are choices a particular channel or archive makes, not facts about
GoPro hardware.

So they live in :class:`IngestConfig` rather than as module constants. The
defaults below are a reasonable starting point for long-form driving /
touring footage; change them freely.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_TOLERANCES",
    "CorrespondenceTolerances",
    "IngestConfig",
]


@dataclass(frozen=True)
class IngestConfig:
    """Duration, grouping, and GPS-split policy.

    Attributes:
        min_clip_duration: Clips shorter than this (seconds) are treated as
            accidental record-button presses and filtered out of the plan.
        min_episode_duration: The shortest episode worth publishing. Groups
            below this are dropped, and a GPS split is never taken if it
            would leave a segment under it.
        max_episode_duration: Above this, an episode is a candidate for
            GPS-based splitting, and two recordings are never combined if
            the sum would exceed it.
        target_episode_duration: The length being aimed for. Split points
            are only taken when the resulting leading segment is at least
            ``target_fraction`` of this.
        target_fraction: How close to ``target_episode_duration`` a segment
            must land before a candidate split point is accepted.
        max_gap_between_recordings: Two consecutive recordings closer
            together than this (seconds, end-to-start) are candidates for
            being combined into one episode.
        gps_return_radius_km: A waypoint within this distance of the
            recording's first fix counts as "returned to start" -- a natural
            place to end an episode.
        gps_stop_duration: How long the track must stay effectively
            stationary (seconds) before it counts as a natural stop.
        gps_stationary_speed_kmh: Speed below which consecutive waypoints
            are considered stationary.
        gps_max_interwaypoint_gap_sec: Maximum gap allowed between
            consecutive valid waypoints. A receiver that loses lock and
            re-acquires with a wrong system clock can emit a waypoint
            timestamped days or years away; any gap beyond this bound is
            read as corruption and the track is truncated there. The
            default (2 h) covers a real-world lunch or fuel stop without
            partitioning a clean recording.
        gpmf_dop_max: Maximum dilution-of-precision accepted from the GPMF
            lock filter during extraction.
        gpmf_speed_max_kph: Glitch bound for the GPMF lock filter, *not* a
            cruising cap. Readings above it are discarded as teleports. The
            default suits road vehicles; the underlying library's own
            default is tuned for cycling and will relabel every
            highway-speed fix as unlocked.
        gpx_sample_step_sec: Sampling interval when converting extracted
            telemetry to GPX.
    """

    min_clip_duration: float = 10.0
    min_episode_duration: float = 20 * 60.0
    max_episode_duration: float = 75 * 60.0
    target_episode_duration: float = 60 * 60.0
    target_fraction: float = 0.8

    max_gap_between_recordings: float = 120 * 60.0

    gps_return_radius_km: float = 0.5
    gps_stop_duration: float = 120.0
    gps_stationary_speed_kmh: float = 2.0
    gps_max_interwaypoint_gap_sec: float = 2 * 60 * 60.0

    gpmf_dop_max: float = 10.0
    gpmf_speed_max_kph: float = 200.0
    gpx_sample_step_sec: float = 1.0

    def __post_init__(self) -> None:
        if self.min_episode_duration > self.max_episode_duration:
            raise ValueError(
                "IngestConfig: min_episode_duration "
                f"({self.min_episode_duration}) exceeds max_episode_duration "
                f"({self.max_episode_duration})"
            )
        if not 0 < self.target_fraction <= 1:
            raise ValueError(
                f"IngestConfig: target_fraction must be in (0, 1], got {self.target_fraction}"
            )
        for name in ("min_clip_duration", "gps_return_radius_km", "gps_stop_duration"):
            if getattr(self, name) < 0:
                raise ValueError(f"IngestConfig: {name} must be non-negative")

    def with_(self, **overrides: float) -> IngestConfig:
        """Return a copy with ``overrides`` applied (thin ``dataclasses.replace``)."""
        return replace(self, **overrides)


@dataclass(frozen=True)
class CorrespondenceTolerances:
    """How far a GPX may drift from its video before the pairing is refused.

    Attributes:
        start_time_sec: A corresponding track starts within this many
            seconds of the video's container ``creation_time``. The default
            (1 h) absorbs timezone-offset sloppiness, receiver warm-up, and
            clock drift; beyond it the track almost certainly belongs to a
            different recording.
        span_abs_sec: Absolute floor on the allowed difference between the
            track's span and the video's duration.
        span_rel: Relative slack on that same difference, as a fraction of
            the video duration. The effective tolerance is the larger of
            the two, which keeps short clips from being gated on noise.
        embedded_coord_radius_km: When the container carries an ISO 6709
            ``location`` tag, the track's first fix must be within this
            distance of it.
    """

    start_time_sec: float = 60 * 60.0
    span_abs_sec: float = 120.0
    span_rel: float = 0.25
    embedded_coord_radius_km: float = 25.0


DEFAULT_CONFIG = IngestConfig()
DEFAULT_TOLERANCES = CorrespondenceTolerances()
