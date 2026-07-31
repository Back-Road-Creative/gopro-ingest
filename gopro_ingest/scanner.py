"""Scan a directory of video files and group them into recordings."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from .config import DEFAULT_CONFIG, IngestConfig
from .models import GoProChapter, GoProRecording
from .naming import is_gopro_named, parse_gopro_filename
from .probe import extract_recording_date, ffprobe_creation_time, get_clip_duration

__all__ = ["scan_gopro_directory", "scan_video_directory"]

logger = logging.getLogger(__name__)

ScanResult = tuple[dict[int, GoProRecording], list[GoProChapter], list[Path]]


def scan_video_directory(
    directory: Path | str,
    extensions: Sequence[str] | None = None,
    mode: str = "gopro",
    config: IngestConfig = DEFAULT_CONFIG,
    strict_mode: bool = True,
) -> ScanResult:
    """Find video files in ``directory`` and group them into recordings.

    Two grouping strategies, chosen by ``mode``:

    ``"gopro"``
        Chapter files are grouped by their recording-session number, and
        the chapters within each recording are sorted by chapter number.
        This is what reassembles a rolled-over recording.

    ``"generic"``
        Every accepted file becomes its own single-chapter recording.
        Use this for sources that do not chapter their output -- 360
        cameras, drones, phones. Recording numbers are assigned in
        discovery order.

    The mode is total: by default a GoPro-named file under ``"generic"``,
    or a non-GoPro-named video under ``"gopro"``, raises ``ValueError``
    naming the offending files. Mixing the two layouts in one directory is
    almost always a mistake, and a silent partial grouping is worse than a
    refusal. Set ``strict_mode=False`` to route unrecognised names into
    the third return value instead.

    In generic mode, a file with no container ``creation_time`` must have a
    ``<stem>.gpx`` sidecar beside it, or the scan raises. The tempting
    fallback -- the file's modification time -- is its *copy* time, not
    when it was recorded, so a card offloaded a week later would date every
    clip to the offload day. Refusing is the only honest option. When the
    sidecar is present, ``creation_time`` is deliberately left ``None`` so
    the caller dates the recording from the track's first fix.

    Args:
        directory: Directory to scan. Not recursive.
        extensions: Accepted suffixes, matched case-insensitively.
            Defaults to ``[".mp4"]``.
        mode: ``"gopro"`` or ``"generic"``.
        config: Supplies ``min_clip_duration``.
        strict_mode: Whether to refuse a mixed-layout directory.

    Returns:
        ``(recordings_by_number, filtered_clips, unrecognized_files)``.

    Raises:
        ValueError: Unknown ``mode``; a mixed-layout directory under
            ``strict_mode``; or an undateable file in generic mode.
    """
    if mode not in ("gopro", "generic"):
        raise ValueError(f"scan_video_directory: mode must be 'gopro' or 'generic', got {mode!r}")

    directory = Path(directory)
    recordings: dict[int, GoProRecording] = {}
    filtered: list[GoProChapter] = []
    unrecognized: list[Path] = []

    exts_lower = {".mp4"} if extensions is None else {e.lower() for e in extensions}

    video_files = sorted(
        (f for f in directory.iterdir() if f.is_file() and f.suffix.lower() in exts_lower),
        key=lambda f: f.name,
    )

    if strict_mode:
        if mode == "generic":
            bad = [f for f in video_files if is_gopro_named(f.name)]
            if bad:
                raise ValueError(
                    "scan_video_directory(mode='generic') refuses GoPro-named file(s): "
                    f"{', '.join(b.name for b in bad)}. Use mode='gopro' or remove them."
                )
        else:
            bad = [f for f in video_files if not is_gopro_named(f.name)]
            if bad:
                raise ValueError(
                    "scan_video_directory(mode='gopro') refuses non-GoPro-named file(s): "
                    f"{', '.join(b.name for b in bad)}. Use mode='generic' or remove them."
                )

    if not video_files:
        logger.warning("No video files found in %s", directory)
        return {}, [], []

    logger.info("Found %d video files in %s (mode=%s)", len(video_files), directory, mode)

    if mode == "generic":
        return _scan_generic(video_files, recordings, filtered, unrecognized, config)
    return _scan_gopro(video_files, recordings, filtered, unrecognized, config)


def _scan_generic(
    video_files: list[Path],
    recordings: dict[int, GoProRecording],
    filtered: list[GoProChapter],
    unrecognized: list[Path],
    config: IngestConfig,
) -> ScanResult:
    for idx, vid in enumerate(video_files, start=1):
        try:
            duration = get_clip_duration(vid)
        except Exception as e:  # noqa: BLE001
            logger.warning("Skipping %s: failed to get duration: %s", vid.name, e)
            unrecognized.append(vid)
            continue

        creation_time = ffprobe_creation_time(vid)
        if creation_time is None:
            sidecar_gpx = vid.with_suffix(".gpx")
            if not (sidecar_gpx.exists() and sidecar_gpx.stat().st_size > 0):
                raise ValueError(
                    f"generic-mode ingest: {vid.name} has no container creation_time and "
                    f"no GPX sidecar ({sidecar_gpx.name}) to date it. File mtime is copy "
                    "time, not recording time -- refusing to stamp it. Supply a GPX "
                    "sidecar next to the file, or repair the file's creation_time."
                )
            # Sidecar present: leave creation_time None so the caller dates
            # the recording from the track's first fix, never from mtime.

        chapter = GoProChapter(
            path=vid,
            chapter_num=1,
            recording_num=idx,
            duration=duration,
            creation_time=creation_time,
        )

        if duration < config.min_clip_duration:
            filtered.append(chapter)
            logger.debug("Filtered %s: %.1fs < %.1fs", vid.name, duration, config.min_clip_duration)
            continue

        recordings[idx] = GoProRecording(recording_num=idx, chapters=[chapter])

    logger.info(
        "  generic mode: %d recordings, %d filtered, %d unrecognized",
        len(recordings),
        len(filtered),
        len(unrecognized),
    )
    return recordings, filtered, unrecognized


def _scan_gopro(
    video_files: list[Path],
    recordings: dict[int, GoProRecording],
    filtered: list[GoProChapter],
    unrecognized: list[Path],
    config: IngestConfig,
) -> ScanResult:
    for video in video_files:
        parsed = parse_gopro_filename(video.name)
        if parsed is None:
            unrecognized.append(video)
            continue

        chapter_num, rec_num = parsed
        try:
            duration = get_clip_duration(video)
        except Exception as e:  # noqa: BLE001
            logger.warning("Skipping %s: failed to get duration: %s", video.name, e)
            unrecognized.append(video)
            continue

        chapter = GoProChapter(
            path=video,
            chapter_num=chapter_num,
            recording_num=rec_num,
            duration=duration,
            creation_time=extract_recording_date(video),
        )

        if duration < config.min_clip_duration:
            filtered.append(chapter)
            logger.debug(
                "Filtered %s: %.1fs < %.1fs", video.name, duration, config.min_clip_duration
            )
            continue

        recordings.setdefault(rec_num, GoProRecording(recording_num=rec_num))
        recordings[rec_num].chapters.append(chapter)

    for rec in recordings.values():
        rec.chapters.sort(key=lambda c: c.chapter_num)

    logger.info(
        "  %d recordings, %d clips, %d filtered, %d unrecognized",
        len(recordings),
        sum(len(r.chapters) for r in recordings.values()),
        len(filtered),
        len(unrecognized),
    )
    return recordings, filtered, unrecognized


def scan_gopro_directory(
    directory: Path | str,
    config: IngestConfig = DEFAULT_CONFIG,
) -> ScanResult:
    """Scan a card of GoPro ``.mp4`` chapter files, tolerating stray names.

    A convenience wrapper: GoPro mode, ``.mp4`` only, and non-GoPro names
    collected into ``unrecognized_files`` instead of raising. Reach for
    :func:`scan_video_directory` when you want the strict contract or a
    different extension list.
    """
    return scan_video_directory(
        directory=directory,
        extensions=[".mp4"],
        mode="gopro",
        config=config,
        strict_mode=False,
    )
