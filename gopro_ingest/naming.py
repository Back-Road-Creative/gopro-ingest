"""GoPro chapter-file naming.

GoPro Hero cameras cap each file at roughly 4 GB and roll over into a new
"chapter" mid-recording. A single continuous press of the record button can
therefore land on disk as several files:

    GX010046.MP4   chapter 01 of recording 0046
    GX020046.MP4   chapter 02 of recording 0046
    GX030046.MP4   chapter 03 of recording 0046

The prefix encodes the codec -- ``GX`` for AVC, ``GH`` for HEVC. Note the
counter-intuitive layout: the *chapter* is the middle two digits and the
*recording session* is the trailing four, so a naive lexical sort of a card
interleaves unrelated recordings.

Low-resolution proxies (``GL……LRV``) and thumbnails (``……THM``) deliberately
do not match.
"""

from __future__ import annotations

import re

__all__ = ["GOPRO_RE", "is_gopro_named", "parse_gopro_filename"]

#: ``G[XH]ccNNNN.MP4`` -- ``cc`` = chapter, ``NNNN`` = recording session.
GOPRO_RE = re.compile(r"^G[XH](\d{2})(\d{4})\.(MP4|mp4)$")


def parse_gopro_filename(filename: str) -> tuple[int, int] | None:
    """Split a GoPro chapter filename into ``(chapter_num, recording_num)``.

    Args:
        filename: A bare filename, e.g. ``"GX010046.MP4"``. Directory
            components are not accepted -- pass ``path.name``.

    Returns:
        ``(chapter_num, recording_num)``, or ``None`` when the name is not
        a GoPro chapter file.

    Example:
        >>> parse_gopro_filename("GX010046.MP4")
        (1, 46)
        >>> parse_gopro_filename("GH070058.MP4")
        (7, 58)
        >>> parse_gopro_filename("holiday.mp4") is None
        True
    """
    m = GOPRO_RE.match(filename)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def is_gopro_named(name: str) -> bool:
    """Return ``True`` when ``name`` matches the GoPro chapter pattern."""
    return GOPRO_RE.match(name) is not None
