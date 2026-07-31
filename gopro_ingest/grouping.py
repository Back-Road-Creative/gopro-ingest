"""Group recordings into episodes, and turn a directory into a plan."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_CONFIG, IngestConfig
from .models import Episode, GoProRecording, IngestPlan
from .scanner import scan_video_directory

__all__ = ["create_ingest_plan", "group_recordings_into_episodes"]

logger = logging.getLogger(__name__)


def group_recordings_into_episodes(
    recordings: dict[int, GoProRecording],
    config: IngestConfig = DEFAULT_CONFIG,
) -> list[Episode]:
    """Combine recordings that belong to the same outing.

    Two consecutive recordings are merged when the gap between the end of
    one and the start of the next is under
    ``config.max_gap_between_recordings`` *and* the combined length would
    stay under ``config.max_episode_duration``. Either condition failing
    starts a new episode. A recording that is on its own longer than the
    maximum stays whole -- see :func:`~gopro_ingest.splitting.find_gps_split_points`
    for cutting those at natural stops instead of arbitrary offsets.

    Anything left under ``config.min_episode_duration`` is dropped, and the
    survivors are renumbered from 1.

    Recordings whose ``creation_time`` is unknown sort first and never
    combine with anything, since there is no way to tell whether they are
    adjacent in time.
    """
    if not recordings:
        return []

    # The boolean in the sort key segregates the unknown-time recordings so
    # a timezone-aware datetime is never compared against a naive sentinel.
    sorted_recs = sorted(
        recordings.values(),
        key=lambda r: (r.creation_time is not None, r.creation_time or datetime.min),
    )

    episodes: list[Episode] = []
    current_group: list[GoProRecording] = [sorted_recs[0]]

    for rec in sorted_recs[1:]:
        prev_end = current_group[-1].end_time
        curr_start = rec.creation_time

        can_combine = prev_end is not None and curr_start is not None
        if can_combine:
            gap = (curr_start - prev_end).total_seconds()
            if gap > config.max_gap_between_recordings:
                can_combine = False

        group_duration = sum(r.total_duration for r in current_group)
        if group_duration + rec.total_duration > config.max_episode_duration:
            can_combine = False

        if can_combine:
            current_group.append(rec)
        else:
            episodes.append(_make_episode(len(episodes) + 1, current_group))
            current_group = [rec]

    if current_group:
        episodes.append(_make_episode(len(episodes) + 1, current_group))

    kept = []
    for ep in episodes:
        if ep.total_duration < config.min_episode_duration:
            logger.info(
                "  Dropping episode %d (%.0fs) -- under the %.0fs minimum",
                ep.index,
                ep.total_duration,
                config.min_episode_duration,
            )
        else:
            kept.append(ep)

    for i, ep in enumerate(kept, 1):
        ep.index = i

    return kept


def _make_episode(index: int, recordings: list[GoProRecording]) -> Episode:
    ct = recordings[0].creation_time
    return Episode(
        index=index,
        recordings=list(recordings),
        date=ct.strftime("%Y-%m-%d") if ct else "",
    )


def create_ingest_plan(
    source_dir: Path | str,
    config: IngestConfig = DEFAULT_CONFIG,
    mode: str = "gopro",
    extensions: Sequence[str] | None = None,
) -> IngestPlan:
    """Scan a directory and describe what it would become.

    Performs no file operations at all -- nothing is concatenated,
    extracted, or moved. Print :meth:`~gopro_ingest.models.IngestPlan.summary`
    and look at it before committing to anything expensive.

    In generic mode each accepted file becomes its own episode, and a
    ``<stem>.gpx`` sidecar sitting beside a file is attached to that
    episode and marked ``"external"`` -- a non-GoPro source has no
    on-camera telemetry, so the sidecar is its only track, and marking it
    external is what puts it through the correspondence gate.

    Raises:
        ValueError: ``source_dir`` is not a directory, or the scan refuses
            the directory (see :func:`~gopro_ingest.scanner.scan_video_directory`).
    """
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise ValueError(f"Not a directory: {source_dir}")

    recordings, filtered, unrecognized = scan_video_directory(
        source_dir,
        extensions=list(extensions) if extensions is not None else [".mp4"],
        mode=mode,
        config=config,
    )

    if mode == "generic":
        episodes: list[Episode] = []
        for idx, rec_num in enumerate(sorted(recordings.keys()), start=1):
            rec = recordings[rec_num]
            ep = Episode(index=idx, recordings=[rec])
            ct = rec.creation_time
            if ct is not None:
                ep.date = ct.strftime("%Y-%m-%d")
            if rec.chapters:
                sidecar_gpx = rec.chapters[0].path.with_suffix(".gpx")
                if sidecar_gpx.exists() and sidecar_gpx.stat().st_size > 0:
                    ep.gpx_path = sidecar_gpx
                    ep.gpx_source = "external"
            episodes.append(ep)
    else:
        episodes = group_recordings_into_episodes(recordings, config=config)

    return IngestPlan(
        source_dir=source_dir,
        episodes=episodes,
        filtered_clips=filtered,
        unrecognized_files=unrecognized,
    )
