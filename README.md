# gopro-ingest

Reassemble GoPro footage into episodes, and refuse to pair it with a GPS
track from a different drive.

A memory card full of GoPro footage does not contain recordings. It contains
*chapter files*: the camera caps each file around 4 GB and rolls over mid-take,
so one press of the record button can land on disk as five files whose names
say nothing useful in sort order. Then there is the second problem — the GPS
track. Getting it out of the camera's telemetry stream is fiddly, and pairing
the wrong track with a video fails completely silently.

This library does the boring, error-prone parts:

- **Parses the naming scheme.** `GX010046.MP4` is chapter 01 of recording 0046.
  The chapter is the *middle* two digits and the session is the trailing four,
  which is why a lexical sort interleaves unrelated recordings.
- **Regroups chapters into recordings, and recordings into episodes** by
  elapsed time and duration policy.
- **Finds natural cut points** in a too-long recording from its GPS track —
  where the route came back to where it started, or sat still for a while —
  instead of cutting at an arbitrary offset.
- **Detects and extracts on-camera telemetry** to GPX.
- **Gates video-to-GPX pairing** with explicit start-skew, span-skew, and
  first-fix-versus-container-tag assertions, so a track from another day
  cannot be quietly attached.

## Why the pairing gate exists

Attaching the wrong GPS track is the failure mode that makes this library
worth having. Nothing crashes. Nothing logs an error. The video is simply
labelled with a place it never went, its chapter marks land at meaningless
times, and its distance is wrong — and all of that looks like a correct
result until somebody who knows the road watches it.

The risk is not evenly spread, so neither is the checking:

| Track came from | Can it be the wrong recording? | What is checked |
| --- | --- | --- |
| The video's own telemetry stream | No — it came from those bytes | Nothing needed |
| A window cut from that telemetry | No, but the window can overshoot | Start time, and over-coverage only |
| Anything external | **Yes** | Start time, span, and position |

All three gates degrade rather than fail when an input is missing. What they
will not do is treat a *missing* signal as a *passing* one: a track with no
position anchor still gets both time checks, because "no anchor" is precisely
the situation a swapped track shows up in.

## Install

```bash
pip install gopro-ingest
```

Requires Python 3.11+ and **`ffprobe` on your `PATH`** (it ships with FFmpeg).
Everything except telemetry extraction works with just that.

Extracting telemetry needs a binary GPMF parser, which is an optional extra
because it pulls in a fair amount:

```bash
pip install "gopro-ingest[gopro]"
```

## Usage

Plan first. Nothing is written, moved, or re-encoded:

```python
from gopro_ingest import create_ingest_plan

plan = create_ingest_plan("/media/card/DCIM/100GOPRO")
print(plan.summary())
```

```
Ingest plan: /media/card/DCIM/100GOPRO
  7 source files -> 2 episodes (94 min total)

  Episode 1: 67min (5 files, recordings: 0046+0047)
    Date: 2026-05-01  Location: (none)
  Episode 2: 27min (2 files, recordings: 0050)
    Date: 2026-05-02  Location: (none)
```

Change the policy — every threshold is an argument, not a constant:

```python
from gopro_ingest import IngestConfig, create_ingest_plan

config = IngestConfig(
    min_episode_duration=10 * 60,  # keep shorter pieces
    max_episode_duration=45 * 60,  # split sooner
    max_gap_between_recordings=30 * 60,  # a 30-min break ends the outing
)
plan = create_ingest_plan("/media/card/DCIM/100GOPRO", config=config)
```

Get the track out of a video and find places to cut it:

```python
from pathlib import Path
from gopro_ingest import detect_gpmf_stream, extract_gopro_gpx, find_gps_split_points

video = Path("/media/card/DCIM/100GOPRO/GX010046.MP4")

if detect_gpmf_stream(video):
    gpx = extract_gopro_gpx(video, Path("./out"))  # needs the [gopro] extra
    for offset in find_gps_split_points(gpx, max_total_duration=6650.0):
        print(f"natural cut at {offset / 60:.1f} min")
```

Check a track before you trust it:

```python
from gopro_ingest import (
    GpxCorrespondenceError,
    assert_gpx_video_correspondence,
    extract_location_coords,
    extract_recording_date,
    get_clip_duration,
    parse_gpx_file,
)

track = parse_gpx_file("./someones-logger-export.gpx")
try:
    assert_gpx_video_correspondence(
        track,
        extract_recording_date(video),
        get_clip_duration(video),
        embedded_coord=extract_location_coords(video),
    )
except GpxCorrespondenceError as e:
    print(f"refusing this track: {e}")
```

`assert_*` returns quietly when an input is missing (no track, no video clock,
`video_duration=0`), which looks the same as "checked and fine". To tell them
apart, ask for the structured result, or make the assert strict:

```python
from gopro_ingest import CorrespondenceStatus, check_gpx_video_correspondence

result = check_gpx_video_correspondence(track, creation_time, duration)
result.status  # matched | mismatched | insufficient | invalid
result.checked  # e.g. ("start_time", "span")
result.skipped  # e.g. ("position",)
result.verified  # True only for matched

assert_gpx_video_correspondence(track, creation_time, duration, strict=True)
# raises InsufficientCorrespondenceEvidence instead of passing on missing input
```

`matched` needs both time checks to have run; the position check is extra
evidence. Negative, NaN or infinite durations and reversed or non-finite track
times are `invalid` (and raise from the `assert_*` functions even without
`strict`) because they would otherwise compare as "not different" and pass.

Or let it choose for you, and return nothing rather than guess
(`strict=True` also refuses a candidate that could not be verified):

```python
from gopro_ingest import resolve_corresponding_gpx

gpx = resolve_corresponding_gpx(video, video_duration=get_clip_duration(video))
```

That last one exists because the obvious implementation — `glob("*.gpx")[0]` —
adopts whichever file the filesystem lists first. One stale track left in a
working folder then silently drives the labels of an unrelated video.

## Defaults

The duration and grouping numbers below are *editorial*, not physical. They
suit long-form driving and touring footage. Nothing in the library reads them
from anywhere but `IngestConfig`, so change whatever does not suit you.

| Setting | Default | What it decides |
| --- | --- | --- |
| `min_clip_duration` | 10 s | Below this, it was an accidental button press |
| `min_episode_duration` | 20 min | The shortest episode worth keeping |
| `target_episode_duration` | 60 min | The length being aimed for |
| `max_episode_duration` | 75 min | Above this, look for a place to cut |
| `max_gap_between_recordings` | 2 h | A longer break means a new outing |
| `gps_return_radius_km` | 0.5 km | Close enough to the start to count as back |
| `gps_stop_duration` | 2 min | Stationary this long is a natural stop |
| `gps_max_interwaypoint_gap_sec` | 2 h | A longer jump means the clock is corrupt |
| `gpmf_speed_max_kph` | 200 | Glitch bound for the lock filter, not a speed cap |

Correspondence tolerances live in a separate `CorrespondenceTolerances`:
1 h of start skew, span within 120 s or 25%, first fix within 25 km of the
container's location tag.

## Two things real cameras do

Both of these produce not merely wrong output but *impossible* output, and
both are defended against here, because neither is rare.

**Lock loss writes null island.** A receiver with no fix emits `(0, 0)`.
A "did we come back to the start?" test anchored there matches every
subsequent dropout in the track, so a clean recording shatters into
fragments. Coordinates at or near `(0, 0)` are treated as a sentinel, not a
place.

**Re-acquisition with a bad clock writes a fix from another year.** One such
point inflates the apparent length of the recording to something absurd, and
the cut offsets that fall out of it land past the end of the file — after
which whatever acts on those offsets reads past EOF. The track is truncated
at the first implausible time jump, and `find_gps_split_points` takes a
`max_total_duration` bound as an independent second check.

There is a third, quieter one worth knowing about if you extract telemetry:
a receiver that has not locked yet reports the coordinates it cached the
last time it was switched off. Those are non-zero, so no null-island check
sees them, and they can be hundreds of kilometres away. Only locked fixes
are written to the GPX, because GPX has no fix-quality field — once a stale
point is in the file, nothing downstream can tell it from a real one.

## Honest limits

- **It does not cut video.** `find_gps_split_points` returns offsets in
  seconds. Feed them to FFmpeg yourself. Nothing here shells out to an
  encoder.
- **It does not geocode.** `Episode.location` is a free-form string this
  library never fills in.
- **It does not concatenate.** `Episode.chapter_files()` gives you the files
  in playback order; assembling them is your business.
- **Scanning is not recursive.** One directory at a time.
- **Split points are advisory.** When the track is too short, too broken, or
  unreadable, you get an empty list, not an exception.
- **`gopro_ingest` reads the GPS channel only.** The telemetry stream also
  carries accelerometer, gyro, and camera state; none of that is exposed.
- **Generic mode refuses to guess a date.** A file with no container clock and
  no GPX sidecar raises, rather than falling back to the file's modification
  time — that is when the file was *copied*, and using it would date a whole
  card to the day you offloaded it.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Tests that need the optional GPMF parser skip themselves when it is absent.

## License

MIT. See [LICENSE](LICENSE).
