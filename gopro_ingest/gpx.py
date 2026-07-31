"""A small, self-contained GPX reader and the geometry helpers around it.

Only what this library needs: track points with a time, a position, and an
optional elevation. Routes, waypoints, extensions, and the various vendor
namespaces are ignored rather than half-supported.

Timestamps are held as Unix epoch seconds (``float``) rather than
``datetime``. Every comparison in this package is against a video clock
that also reduces to an epoch, and keeping one representation removes a
whole class of naive-versus-aware bugs.

GPX arriving from a camera, a phone app, or an operator's folder is
untrusted input, so parsing goes through :mod:`defusedxml`, which refuses
entity expansion and external-entity resolution.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "GpxParseError",
    "Track",
    "TrackPoint",
    "first_real_fix_timestamp",
    "haversine_km",
    "haversine_m",
    "is_invalid_coord",
    "parse_gpx_file",
]

logger = logging.getLogger(__name__)

_GPX_NS = "http://www.topografix.com/GPX/1/1"

#: A leading run of one repeated coordinate is only read as a stale lock
#: when the first fix that breaks the run is at least this far away. A car
#: genuinely parked at the start of a recording pulls away by tens of
#: metres; a receiver holding a coordinate cached from a previous session
#: resolves hundreds of kilometres away.
STALE_LOCK_MIN_JUMP_M = 1000.0


class GpxParseError(ValueError):
    """The GPX file is missing, malformed, or unsafe to parse."""


@dataclass
class TrackPoint:
    """One fix: position, time, and optionally elevation."""

    lat: float
    lon: float
    elevation: float | None = None
    timestamp: float = 0.0  # Unix epoch seconds

    @property
    def has_elevation(self) -> bool:
        return self.elevation is not None


@dataclass
class Track:
    """An ordered series of fixes.

    The attribute is named ``trackpoints`` so that any object exposing a
    ``trackpoints`` list of things with ``lat`` / ``lon`` / ``timestamp``
    can be handed to this package's gates without adapting.
    """

    trackpoints: list[TrackPoint] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.trackpoints)

    @property
    def start_time(self) -> float | None:
        return self.trackpoints[0].timestamp if self.trackpoints else None

    @property
    def end_time(self) -> float | None:
        return self.trackpoints[-1].timestamp if self.trackpoints else None

    @property
    def span(self) -> float:
        """Elapsed seconds between the first and last fix (``0.0`` if empty)."""
        if len(self.trackpoints) < 2:
            return 0.0
        return self.trackpoints[-1].timestamp - self.trackpoints[0].timestamp

    def points_in_range(self, start_time: float, end_time: float) -> list[TrackPoint]:
        """Fixes whose timestamp falls within ``[start_time, end_time]``."""
        return [tp for tp in self.trackpoints if start_time <= tp.timestamp <= end_time]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two coordinates, in kilometres."""
    earth_radius_km = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two coordinates, in metres."""
    return haversine_km(lat1, lon1, lat2, lon2) * 1000.0


def is_invalid_coord(lat: float | None, lon: float | None) -> bool:
    """Return ``True`` for coordinates that cannot be a real fix.

    Catches the two things receivers actually emit when they have nothing:
    null island -- ``(0, 0)`` and its immediate neighbourhood -- and values
    outside the valid range. Null island has to be treated as a sentinel
    rather than a location: a "returned to start" check anchored there
    matches every other dropout in the track.
    """
    if lat is None or lon is None:
        return True
    if abs(lat) > 90 or abs(lon) > 180:
        return True
    if abs(lat) < 0.001 and abs(lon) < 0.001:
        return True
    return False


def parse_gpx_file(gpx_path: Path | str) -> Track:
    """Read a GPX file into a :class:`Track`, sorted by time.

    Handles GPX 1.1 with or without a declared namespace. Points that
    cannot be placed in time are dropped rather than defaulted: a
    zero-valued timestamp sorts to the front and scrambles both the
    ordering and every offset computed from it. Points with unparseable
    coordinates are dropped the same way. Both cases log a warning, so a
    systematically broken file is still visible.

    Raises:
        GpxParseError: The file is missing, is not well-formed XML, or
            trips the hardened parser's entity limits.
    """
    import defusedxml.ElementTree as ET  # noqa: N817 -- drop-in for stdlib ET
    from defusedxml.common import DefusedXmlException

    gpx_path = Path(gpx_path)
    if not gpx_path.exists():
        raise GpxParseError(f"GPX file not found: {gpx_path}")

    try:
        root = ET.parse(str(gpx_path)).getroot()
    except ET.ParseError as e:
        raise GpxParseError(f"Invalid GPX XML: {e}") from e
    except DefusedXmlException as e:
        raise GpxParseError(f"Unsafe GPX XML (entity expansion blocked): {e}") from e

    ns = {"gpx": _GPX_NS}
    trkpts = (
        root.findall(".//gpx:trkpt", ns)
        or root.findall(f".//{{{_GPX_NS}}}trkpt")
        or root.findall(".//trkpt")
    )

    def _find(elem, tag):
        """First match for ``tag``, trying prefixed, expanded, then bare form."""
        for found in (
            elem.find(f"gpx:{tag}", ns),
            elem.find(f"{{{_GPX_NS}}}{tag}"),
            elem.find(tag),
        ):
            if found is not None:
                return found
        return None

    points: list[TrackPoint] = []
    for trkpt in trkpts:
        try:
            lat = float(trkpt.get("lat"))
            lon = float(trkpt.get("lon"))
        except (TypeError, ValueError):
            logger.warning(
                "Dropping malformed trackpoint: lat=%s lon=%s", trkpt.get("lat"), trkpt.get("lon")
            )
            continue

        ele_elem = _find(trkpt, "ele")
        elevation = None
        if ele_elem is not None and ele_elem.text:
            try:
                elevation = float(ele_elem.text)
            except ValueError:
                elevation = None

        time_elem = _find(trkpt, "time")
        if time_elem is None or not time_elem.text:
            logger.warning("Dropping timeless trackpoint: lat=%s lon=%s", lat, lon)
            continue

        time_str = time_elem.text.strip()
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(time_str)
        except ValueError:
            logger.warning("Dropping trackpoint with unparseable time %r", time_elem.text)
            continue
        if dt.tzinfo is None:
            # A zone-less <time> is UTC by the GPX spec. Left naive,
            # .timestamp() would read it as host-local and skew the epoch
            # by whole hours.
            dt = dt.replace(tzinfo=UTC)

        points.append(TrackPoint(lat, lon, elevation, dt.timestamp()))

    points.sort(key=lambda tp: tp.timestamp)
    logger.debug("Parsed %d trackpoints from %s", len(points), gpx_path.name)
    return Track(trackpoints=points)


def first_real_fix_timestamp(track, fallback: float = 0.0) -> float:
    """Timestamp of the first fix that is not a cold-start artefact.

    A receiver waking up can open a track two ways, and both make the
    first stored timestamp the wrong anchor:

    * a run of null-island ``(0, 0)`` readings while it searches, or
    * a run of the *same non-zero* coordinate cached from wherever it was
      last switched off -- invisible to a null-island check, and hundreds
      of kilometres from where the recording actually starts.

    Only the second kind needs care, because a vehicle parked at the true
    start also produces a run of identical coordinates and that run is
    legitimate. The two are told apart by distance: the fix that breaks
    the run is metres away for a parked start and
    :data:`STALE_LOCK_MIN_JUMP_M` or more away for a stale lock.

    Accepts anything with a ``trackpoints`` sequence -- :class:`Track`, a
    compatible object, or a plain dict -- and falls back to ``fallback``
    when there is nothing usable.
    """
    base = float(fallback or 0.0)
    if track is None:
        return base

    tps = getattr(track, "trackpoints", None)
    if tps is None and isinstance(track, dict):
        tps = track.get("trackpoints") or []
    tps = list(tps or [])
    if not tps:
        return base

    def _ll(tp) -> tuple[float, float]:
        if isinstance(tp, dict):
            return float(tp.get("lat", 0.0) or 0.0), float(tp.get("lon", 0.0) or 0.0)
        return float(getattr(tp, "lat", 0.0) or 0.0), float(getattr(tp, "lon", 0.0) or 0.0)

    def _ts(tp) -> float:
        return float(tp["timestamp"]) if isinstance(tp, dict) else float(tp.timestamp)

    first_lat, first_lon = _ll(tps[0])
    leading_null = first_lat == 0.0 and first_lon == 0.0
    leading_repeat = not leading_null and len(tps) > 1 and _ll(tps[1]) == (first_lat, first_lon)

    leading_stale = False
    if leading_repeat:
        for tp in tps:
            lat, lon = _ll(tp)
            if lat == 0.0 and lon == 0.0:
                continue  # null island can't settle the question -- keep scanning
            if (lat, lon) == (first_lat, first_lon):
                continue  # still inside the leading run
            leading_stale = haversine_m(first_lat, first_lon, lat, lon) >= STALE_LOCK_MIN_JUMP_M
            break

    if not (leading_null or leading_stale):
        return base

    # The two skip reasons stay separate so a null-island reading buried
    # inside a stale-lock run can never be returned as the anchor.
    stuck = (first_lat, first_lon) if leading_stale else None
    for tp in tps:
        lat, lon = _ll(tp)
        if lat == 0.0 and lon == 0.0:
            continue
        if stuck is not None and (lat, lon) == stuck:
            continue
        return _ts(tp)
    return base
