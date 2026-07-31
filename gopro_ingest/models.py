"""Data models for chapters, recordings, episodes, and an ingest plan.

The hierarchy mirrors how footage actually comes off a card:

    IngestPlan          one source directory
      └─ Episode        one publishable video
           └─ GoProRecording   one press of the record button
                └─ GoProChapter    one file on the card

Everything is a plain dataclass and everything round-trips through
``to_dict`` / ``from_dict``, so a plan can be checkpointed to JSON between
runs without a serialisation framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

__all__ = ["Episode", "GoProChapter", "GoProRecording", "IngestPlan"]

#: Provenance values for :attr:`Episode.gpx_source`.
#:
#: ``"gpmf"``
#:     Extracted from this episode's own on-camera telemetry stream. Frame
#:     aligned by construction -- it cannot belong to another recording, so
#:     the correspondence gate has nothing to check.
#: ``"gpmf_slice"``
#:     A time window cut out of a parent episode's own telemetry. Also
#:     un-swappable, but a bad slice boundary can still hand a segment a
#:     window that overshoots its video, so the slice-tuned gate applies.
#: ``"external"``
#:     A track supplied from outside the video -- a handheld logger, a phone
#:     app, a sidecar left in the folder, or a pairing restored from a saved
#:     plan. Alignment is not guaranteed. This is the case the full
#:     correspondence gate exists for.
GPX_SOURCES = ("gpmf", "gpmf_slice", "external")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _un_iso(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _path(value: Any) -> Path | None:
    if value is None or isinstance(value, Path):
        return value
    return Path(value)


@dataclass
class GoProChapter:
    """A single file on the card.

    Attributes:
        path: Location of the file.
        chapter_num: 1-based chapter index within the recording. Cameras
            emit ``00`` occasionally; it is accepted rather than rejected.
        recording_num: The 4-digit recording-session number shared by every
            chapter of one continuous recording.
        duration: Length in seconds, normally from ``ffprobe``.
        creation_time: Container ``creation_time``, timezone-aware when
            known and ``None`` when the container carries no usable clock.
    """

    path: Path
    chapter_num: int
    recording_num: int
    duration: float
    creation_time: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "chapter_num": self.chapter_num,
            "recording_num": self.recording_num,
            "duration": self.duration,
            "creation_time": _iso(self.creation_time),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GoProChapter:
        return cls(
            path=_path(d.get("path")) or Path(""),
            chapter_num=int(d.get("chapter_num", 0)),
            recording_num=int(d.get("recording_num", 0)),
            duration=float(d.get("duration", 0.0)),
            creation_time=_un_iso(d.get("creation_time")),
        )


@dataclass
class GoProRecording:
    """One continuous recording, reassembled from its chapter files."""

    recording_num: int
    chapters: list[GoProChapter] = field(default_factory=list)

    @property
    def total_duration(self) -> float:
        """Summed duration of every chapter, in seconds."""
        return sum(ch.duration for ch in self.chapters)

    @property
    def creation_time(self) -> datetime | None:
        """Start time, taken from the first chapter."""
        if self.chapters:
            return self.chapters[0].creation_time
        return None

    @property
    def end_time(self) -> datetime | None:
        """Estimated end: ``creation_time`` plus total duration."""
        ct = self.creation_time
        if ct:
            return ct + timedelta(seconds=self.total_duration)
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording_num": self.recording_num,
            "chapters": [ch.to_dict() for ch in self.chapters],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GoProRecording:
        return cls(
            recording_num=int(d.get("recording_num", 0)),
            chapters=[GoProChapter.from_dict(c) for c in d.get("chapters") or []],
        )


@dataclass
class Episode:
    """A group of recordings destined to become one publishable video.

    There are two shapes, and the properties below hide the difference from
    every consumer:

    1. **Recording-derived** -- the ordinary case. ``recordings`` is
       populated and duration/start time are summed from the chapters.

    2. **Slice-derived** -- produced when a long episode is cut into
       smaller ones at GPS split points. The slice file is the new source
       of truth (the original chapters no longer describe its bytes), so
       ``recordings`` is empty and ``explicit_total_duration`` /
       ``explicit_creation_time`` carry the slice's real extent.

    ``total_duration`` and ``creation_time`` prefer the explicit fields and
    fall back to the recordings, so downstream code never has to ask which
    shape it is holding.

    Attributes:
        index: 1-based episode number within the plan.
        recordings: Source recordings, in playback order.
        location: Free-form label, filled in by the caller if it wants one.
        date: ``YYYY-MM-DD`` for the episode's start.
        concat_path: Where the assembled video lives, once assembled.
        gpx_path: Where this episode's track lives, once extracted.
        gpx_source: Provenance of ``gpx_path``; see :data:`GPX_SOURCES`.
        explicit_total_duration: Slice-derived duration override.
        explicit_creation_time: Slice-derived start-time override.
    """

    index: int
    recordings: list[GoProRecording] = field(default_factory=list)
    location: str = ""
    date: str = ""
    concat_path: Path | None = None
    gpx_path: Path | None = None
    gpx_source: str = "gpmf"
    explicit_total_duration: float | None = None
    explicit_creation_time: datetime | None = None

    @property
    def total_duration(self) -> float:
        if self.explicit_total_duration is not None:
            return self.explicit_total_duration
        return sum(r.total_duration for r in self.recordings)

    @property
    def total_duration_min(self) -> float:
        return self.total_duration / 60

    @property
    def num_chapters(self) -> int:
        return sum(len(r.chapters) for r in self.recordings)

    @property
    def creation_time(self) -> datetime | None:
        if self.explicit_creation_time is not None:
            return self.explicit_creation_time
        if self.recordings:
            return self.recordings[0].creation_time
        return None

    def chapter_files(self) -> list[Path]:
        """Every chapter file, in playback order.

        Empty on a slice-derived episode: there the file at
        ``concat_path`` *is* the bytes, and re-assembling from chapters
        would produce the wrong video.
        """
        files: list[Path] = []
        for rec in self.recordings:
            for ch in sorted(rec.chapters, key=lambda c: c.chapter_num):
                files.append(ch.path)
        return files

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "recordings": [r.to_dict() for r in self.recordings],
            "location": self.location,
            "date": self.date,
            "concat_path": str(self.concat_path) if self.concat_path else None,
            "gpx_path": str(self.gpx_path) if self.gpx_path else None,
            "gpx_source": self.gpx_source,
            "explicit_total_duration": self.explicit_total_duration,
            "explicit_creation_time": _iso(self.explicit_creation_time),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Episode:
        return cls(
            index=int(d.get("index", 0)),
            recordings=[GoProRecording.from_dict(r) for r in d.get("recordings") or []],
            location=d.get("location") or "",
            date=d.get("date") or "",
            concat_path=_path(d.get("concat_path")),
            gpx_path=_path(d.get("gpx_path")),
            gpx_source=d.get("gpx_source") or "gpmf",
            explicit_total_duration=d.get("explicit_total_duration"),
            explicit_creation_time=_un_iso(d.get("explicit_creation_time")),
        )


@dataclass
class IngestPlan:
    """What a directory of clips would become, before anything is written.

    A plan is a dry run: it names the episodes, the clips that were too
    short to keep, and the files that were not recognised. Nothing has been
    concatenated, extracted, or moved.
    """

    source_dir: Path
    episodes: list[Episode] = field(default_factory=list)
    filtered_clips: list[GoProChapter] = field(default_factory=list)
    unrecognized_files: list[Path] = field(default_factory=list)

    @property
    def total_duration_min(self) -> float:
        return sum(ep.total_duration_min for ep in self.episodes)

    @property
    def total_source_files(self) -> int:
        return sum(ep.num_chapters for ep in self.episodes)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable snapshot of the whole plan."""
        return {
            "source_dir": str(self.source_dir),
            "episodes": [ep.to_dict() for ep in self.episodes],
            "filtered_clips": [c.to_dict() for c in self.filtered_clips],
            "unrecognized_files": [str(p) for p in self.unrecognized_files],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IngestPlan:
        """Rebuild a plan from :meth:`to_dict` output.

        Nested collections are rehydrated all the way down to real
        dataclasses. A shallow version of this -- leaving ``episodes`` as
        raw dicts -- looks like it works, because ``getattr`` on a dict
        returns the default instead of raising: every episode then reports
        an empty date and a zero duration, and a caller silently processes
        nothing while reporting success. Deep rehydration removes that
        failure mode rather than documenting it.
        """
        source_dir = _path(d.get("source_dir")) or Path(".")
        return cls(
            source_dir=source_dir,
            episodes=[
                ep if isinstance(ep, Episode) else Episode.from_dict(ep)
                for ep in d.get("episodes") or []
            ],
            filtered_clips=[
                c if isinstance(c, GoProChapter) else GoProChapter.from_dict(c)
                for c in d.get("filtered_clips") or []
            ],
            unrecognized_files=[Path(p) for p in d.get("unrecognized_files") or []],
        )

    def summary(self) -> str:
        """A human-readable report, suitable for printing before committing."""
        lines = [
            f"Ingest plan: {self.source_dir}",
            f"  {self.total_source_files} source files -> {len(self.episodes)} episodes "
            f"({self.total_duration_min:.0f} min total)",
        ]
        if self.filtered_clips:
            lines.append(f"  {len(self.filtered_clips)} clips filtered (too short)")
        if self.unrecognized_files:
            lines.append(f"  {len(self.unrecognized_files)} unrecognized files skipped")

        lines.append("")
        for ep in self.episodes:
            rec_nums = [f"{r.recording_num:04d}" for r in ep.recordings]
            lines.append(
                f"  Episode {ep.index}: {ep.total_duration_min:.0f}min "
                f"({ep.num_chapters} files, recordings: {'+'.join(rec_nums)})"
            )
            lines.append(f"    Date: {ep.date}  Location: {ep.location or '(none)'}")
            for rec in ep.recordings:
                lines.append(
                    f"    Rec {rec.recording_num:04d}: "
                    f"{len(rec.chapters)} chapters, {rec.total_duration / 60:.1f}min"
                )

        return "\n".join(lines)
