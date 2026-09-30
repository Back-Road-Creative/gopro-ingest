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

"Degrade" still means the ``assert_*`` functions return quietly, which a
caller cannot tell apart from "checked and fine". The ``check_*`` functions
return a :class:`CorrespondenceResult` that keeps the four outcomes apart
(matched, mismatched, insufficient evidence, invalid input) and lists which
checks ran and which were skipped. ``assert_*(..., strict=True)`` raises on
insufficient evidence for callers that must not publish on an unverified
pairing. Non-finite or negative timing is never treated as a skipped check:
NaN makes every comparison false, so it would otherwise pass as a match.
"""

from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .config import DEFAULT_TOLERANCES, CorrespondenceTolerances
from .gpx import first_real_fix_timestamp, haversine_km, parse_gpx_file

__all__ = [
    "CorrespondenceResult",
    "CorrespondenceStatus",
    "GpxCorrespondenceError",
    "InsufficientCorrespondenceEvidence",
    "assert_gpx_video_correspondence",
    "assert_slice_gpx_correspondence",
    "check_gpx_video_correspondence",
    "check_slice_gpx_correspondence",
    "gps_coverage_window",
    "resolve_corresponding_gpx",
]

logger = logging.getLogger(__name__)


class GpxCorrespondenceError(RuntimeError):
    """The track does not correspond to the video it was paired with."""


class InsufficientCorrespondenceEvidence(GpxCorrespondenceError):
    """Raised under ``strict=True`` when too little was available to check."""


class CorrespondenceStatus(StrEnum):
    """The four outcomes of a correspondence check."""

    MATCHED = "matched"
    MISMATCHED = "mismatched"
    #: Nothing contradicted the pairing, but a required check could not run.
    INSUFFICIENT = "insufficient"
    #: The timing inputs themselves are unusable (NaN, infinite, negative, reversed).
    INVALID = "invalid"


@dataclass(frozen=True)
class CorrespondenceResult:
    """What a correspondence check did, not just whether it raised.

    ``checked`` and ``skipped`` name checks (``"start_time"``, ``"span"``,
    ``"position"``); ``problems`` holds the human-readable reasons behind a
    ``MISMATCHED`` or ``INVALID`` status, in check order.

    ``MATCHED`` requires both time checks to have run. The position check is
    extra evidence: it is listed in ``skipped`` when there is no anchor, but
    that alone does not make the result ``INSUFFICIENT``.
    """

    status: CorrespondenceStatus
    checked: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """True only for ``MATCHED``; insufficient evidence is not verification."""
        return self.status is CorrespondenceStatus.MATCHED


_REQUIRED_CHECKS = ("start_time", "span")


def _trackpoints(track) -> list:
    if track is None:
        return []
    return list(getattr(track, "trackpoints", None) or [])


def _finite(value) -> bool:
    try:
        return math.isfinite(value)
    except TypeError:
        return False


def _timing_problems(
    trackpoints: list,
    video_duration: float | None,
    embedded_coord: tuple[float, float] | None,
) -> list[str]:
    """Reasons the timing inputs cannot be compared at all."""
    problems: list[str] = []
    if video_duration is not None and (not _finite(video_duration) or video_duration < 0):
        problems.append(
            f"video duration {video_duration!r} is negative or non-finite -- "
            "refusing to compare against it."
        )
    if trackpoints:
        first_ts = trackpoints[0].timestamp
        last_ts = trackpoints[-1].timestamp
        if not _finite(first_ts) or not _finite(last_ts):
            problems.append("GPX first or last timestamp is missing or non-finite.")
        elif last_ts < first_ts:
            problems.append(
                f"GPX timestamps are reversed (last fix {first_ts - last_ts:.0f}s "
                "before the first) -- the track is not in time order."
            )
    if embedded_coord is not None and not all(_finite(v) for v in embedded_coord):
        problems.append(f"embedded location {embedded_coord!r} is non-finite.")
    return problems


def _evaluate(
    track,
    video_creation_time: datetime.datetime | None,
    video_duration: float | None,
    embedded_coord: tuple[float, float] | None,
    tolerances: CorrespondenceTolerances,
    *,
    is_slice: bool,
) -> CorrespondenceResult:
    all_checks = _REQUIRED_CHECKS if is_slice else (*_REQUIRED_CHECKS, "position")
    trackpoints = _trackpoints(track)

    invalid = _timing_problems(trackpoints, video_duration, None if is_slice else embedded_coord)
    if invalid:
        return CorrespondenceResult(CorrespondenceStatus.INVALID, (), all_checks, tuple(invalid))
    if not trackpoints:
        return CorrespondenceResult(CorrespondenceStatus.INSUFFICIENT, (), all_checks)

    gpx_start = trackpoints[0].timestamp
    gpx_span = trackpoints[-1].timestamp - gpx_start
    duration = video_duration or 0.0
    checked: list[str] = []
    problems: list[str] = []

    if video_creation_time is not None:
        checked.append("start_time")
        start_delta = abs(gpx_start - video_creation_time.timestamp())
        if start_delta > tolerances.start_time_sec:
            subject, verdict = (
                ("Sliced GPX", "the slice looks mis-anchored to its video.")
                if is_slice
                else ("GPX", "the track probably belongs to a different recording.")
            )
            what = "the slice's" if is_slice else "the video's"
            problems.append(
                f"{subject} start-time is off by {start_delta / 60:.0f} min "
                f"(tolerance {tolerances.start_time_sec / 60:.0f} min) from {what} "
                f"creation_time -- {verdict}"
            )

    if duration > 0:
        checked.append("span")
        span_tol = max(tolerances.span_abs_sec, tolerances.span_rel * duration)
        if is_slice:
            over = gpx_span - duration
            if over > span_tol:
                problems.append(
                    f"Sliced GPX span {gpx_span:.0f}s over-covers video duration "
                    f"{duration:.0f}s by {over:.0f}s (tolerance {span_tol:.0f}s) -- the "
                    "slice window reached past its own extent."
                )
        else:
            span_delta = abs(gpx_span - duration)
            if span_delta > span_tol:
                problems.append(
                    f"GPX span {gpx_span:.0f}s diverges from video duration {duration:.0f}s "
                    f"by {span_delta:.0f}s (tolerance {span_tol:.0f}s) -- the track probably "
                    "belongs to a different recording."
                )

    if not is_slice and embedded_coord is not None:
        elat, elon = embedded_coord
        flat = trackpoints[0].lat
        flon = trackpoints[0].lon
        if flat is None or flon is None:
            pass
        elif not (_finite(flat) and _finite(flon)):
            return CorrespondenceResult(
                CorrespondenceStatus.INVALID,
                tuple(checked),
                tuple(c for c in all_checks if c not in checked),
                (*problems, "GPX first fix coordinate is non-finite."),
            )
        else:
            checked.append("position")
            dist_km = haversine_km(flat, flon, elat, elon)
            if dist_km > tolerances.embedded_coord_radius_km:
                problems.append(
                    f"GPX first fix is {dist_km:.0f} km from the video's embedded location "
                    f"tag (tolerance {tolerances.embedded_coord_radius_km:.0f} km) -- the "
                    "track probably belongs to a different recording."
                )

    skipped = tuple(c for c in all_checks if c not in checked)
    if problems:
        status = CorrespondenceStatus.MISMATCHED
    elif all(c in checked for c in _REQUIRED_CHECKS):
        status = CorrespondenceStatus.MATCHED
    else:
        status = CorrespondenceStatus.INSUFFICIENT
    return CorrespondenceResult(status, tuple(checked), skipped, tuple(problems))


def _raise_for(result: CorrespondenceResult, strict: bool) -> None:
    if result.status in (CorrespondenceStatus.MISMATCHED, CorrespondenceStatus.INVALID):
        raise GpxCorrespondenceError(result.problems[0])
    if result.status is CorrespondenceStatus.INSUFFICIENT:
        missing = ", ".join(c for c in _REQUIRED_CHECKS if c in result.skipped)
        if strict:
            raise InsufficientCorrespondenceEvidence(
                f"insufficient evidence to verify correspondence (not checked: {missing})."
            )
        logger.debug("Correspondence: not checked (%s) -- nothing to contradict", missing)


def check_gpx_video_correspondence(
    track,
    video_creation_time: datetime.datetime | None,
    video_duration: float,
    embedded_coord: tuple[float, float] | None = None,
    tolerances: CorrespondenceTolerances = DEFAULT_TOLERANCES,
) -> CorrespondenceResult:
    """Run the full gate and report what happened instead of raising.

    Same inputs and checks as :func:`assert_gpx_video_correspondence`. Never
    raises on a mismatch. ``MATCHED`` means both time checks ran and passed;
    a missing track, video clock, or ``video_duration`` of ``0`` yields
    ``INSUFFICIENT``; a negative, NaN or infinite duration, non-finite track
    time, or a track whose last fix precedes its first yields ``INVALID``.
    """
    return _evaluate(
        track,
        video_creation_time,
        video_duration,
        embedded_coord,
        tolerances,
        is_slice=False,
    )


def check_slice_gpx_correspondence(
    track,
    video_creation_time: datetime.datetime | None,
    video_duration: float,
    tolerances: CorrespondenceTolerances = DEFAULT_TOLERANCES,
) -> CorrespondenceResult:
    """Slice-gate counterpart of :func:`check_gpx_video_correspondence`."""
    return _evaluate(track, video_creation_time, video_duration, None, tolerances, is_slice=True)


def assert_gpx_video_correspondence(
    track,
    video_creation_time: datetime.datetime | None,
    video_duration: float,
    embedded_coord: tuple[float, float] | None = None,
    tolerances: CorrespondenceTolerances = DEFAULT_TOLERANCES,
    *,
    strict: bool = False,
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

    Unusable timing (negative, NaN or infinite ``video_duration``, a
    non-finite or reversed track, a non-finite embedded coordinate) raises
    rather than being skipped.

    By default a missing input (no track, no video clock, ``video_duration``
    of ``0``) skips the affected check and returns quietly. That is a
    compatibility path, not verification. Pass ``strict=True`` to raise
    :class:`InsufficientCorrespondenceEvidence` instead, or call
    :func:`check_gpx_video_correspondence` for the structured result.

    Args:
        track: Anything with a ``trackpoints`` list of objects carrying
            ``lat``, ``lon``, and ``timestamp`` (epoch seconds).
        video_creation_time: Timezone-aware container creation time, or
            ``None``.
        video_duration: Video length in seconds; ``0`` skips the span check.
        embedded_coord: ``(lat, lon)`` from the container tag, if any.
        tolerances: How much drift to accept.
        strict: Raise when the start-time or span check could not run.

    Raises:
        GpxCorrespondenceError: On a genuine mismatch or unusable timing.
        InsufficientCorrespondenceEvidence: Only when ``strict`` and a time
            check could not run.
    """
    result = check_gpx_video_correspondence(
        track, video_creation_time, video_duration, embedded_coord, tolerances
    )
    _raise_for(result, strict)


def assert_slice_gpx_correspondence(
    track,
    video_creation_time: datetime.datetime | None,
    video_duration: float,
    tolerances: CorrespondenceTolerances = DEFAULT_TOLERANCES,
    *,
    strict: bool = False,
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

    ``strict`` and the handling of unusable timing are as for
    :func:`assert_gpx_video_correspondence`.

    Raises:
        GpxCorrespondenceError: On a genuine mismatch or unusable timing.
        InsufficientCorrespondenceEvidence: Only when ``strict`` and a time
            check could not run.
    """
    result = check_slice_gpx_correspondence(track, video_creation_time, video_duration, tolerances)
    _raise_for(result, strict)


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
    *,
    strict: bool = False,
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
       one that passes wins; the rest are refused with a warning. With
       ``strict=True`` a candidate that could not be verified (no video
       clock, or ``video_duration`` of ``0``) is refused too, rather than
       adopted on the strength of the checks that happened to run.

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
                strict=strict,
            )
        except GpxCorrespondenceError as exc:
            logger.warning("  Refusing GPX %s -- no correspondence: %s", candidate.name, exc)
            continue
        return candidate
    return None
