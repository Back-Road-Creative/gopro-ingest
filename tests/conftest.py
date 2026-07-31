"""Shared synthetic fixtures.

Every coordinate in this suite is fabricated. The anchor is a round-number
grid intersection and every other point is a fixed offset from it, so no
fixture traces a real route or names a real place. Do not "improve" these
by substituting somewhere recognisable -- a test suite is a permanent
public record of wherever its fixtures point.
"""

from __future__ import annotations

import datetime

import pytest

from gopro_ingest import Track, TrackPoint

# Synthetic anchor and a second, far-away synthetic anchor. The pair is
# ~2900 km apart, which is comfortably outside any correspondence radius.
LAT_A = 45.0
LON_A = -120.0
LAT_B = 30.0
LON_B = -95.0

#: A fixed, arbitrary instant used as every fixture's video creation time.
CREATION = datetime.datetime(2026, 5, 1, 12, 0, 0, tzinfo=datetime.UTC)
T0 = CREATION.timestamp()


def make_track(
    start_unix: float,
    span_sec: float,
    lat: float = LAT_A,
    lon: float = LON_A,
) -> Track:
    """A two-point track spanning ``span_sec`` from ``start_unix``."""
    return Track(
        trackpoints=[
            TrackPoint(lat, lon, None, start_unix),
            TrackPoint(lat + 0.01, lon + 0.01, None, start_unix + span_sec),
        ]
    )


def gpx_text(start_unix: float, span_sec: float, lat: float = LAT_A, lon: float = LON_A) -> str:
    """Serialise a two-point track as GPX 1.1."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    t0 = datetime.datetime.fromtimestamp(start_unix, tz=datetime.UTC).strftime(fmt)
    t1 = datetime.datetime.fromtimestamp(start_unix + span_sec, tz=datetime.UTC).strftime(fmt)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">\n'
        "  <trk><trkseg>\n"
        f'    <trkpt lat="{lat}" lon="{lon}"><ele>120.0</ele><time>{t0}</time></trkpt>\n'
        f'    <trkpt lat="{lat + 0.01}" lon="{lon + 0.01}"><time>{t1}</time></trkpt>\n'
        "  </trkseg></trk>\n"
        "</gpx>\n"
    )


@pytest.fixture
def creation() -> datetime.datetime:
    return CREATION
