# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- `check_gpx_video_correspondence` / `check_slice_gpx_correspondence` return a
  `CorrespondenceResult` (`matched`, `mismatched`, `insufficient`, `invalid`,
  plus `checked` / `skipped` checks) so a skipped check is no longer
  indistinguishable from a passed one. `assert_*` and
  `resolve_corresponding_gpx` take `strict=True` to refuse insufficient
  evidence (`InsufficientCorrespondenceEvidence`).

### Fixed

- The `assert_*` gates now raise on a negative, NaN or infinite video
  duration, a reversed or non-finite track, and a non-finite embedded
  coordinate. Previously these were skipped or compared as false and passed.

## 0.1.0

First release.

### Added

- `parse_gopro_filename` / `is_gopro_named` — the `GXccNNNN` / `GHccNNNN`
  chapter-file naming scheme, including the low-res-proxy and thumbnail
  exclusions.
- `scan_video_directory` / `scan_gopro_directory` — regroup chapter files
  into continuous recordings, or treat each file as its own recording for
  sources that do not chapter their output. Mixing the two layouts in one
  directory is refused rather than half-handled.
- `group_recordings_into_episodes` / `create_ingest_plan` — group recordings
  into publishable episodes by inter-recording gap and duration policy, and
  describe a directory without touching it.
- `find_gps_split_points` — natural cut offsets from a GPS track, using
  return-to-start and stationary-dwell signals, with defences against
  null-island dropouts and stale-clock timestamps.
- `detect_gpmf_stream` / `extract_gopro_gpx` — find and extract on-camera
  telemetry to GPX. Only locked fixes are written, and the lock filter's
  speed bound is set for road vehicles rather than cycling.
- `assert_gpx_video_correspondence`, `assert_slice_gpx_correspondence`,
  `resolve_corresponding_gpx`, `gps_coverage_window` — refuse a GPS track
  that does not belong to the video it is being paired with.
- `parse_gpx_file`, `Track`, `TrackPoint`, `haversine_km`, `haversine_m`,
  `first_real_fix_timestamp`, `is_invalid_coord` — a small GPX reader
  hardened against entity-expansion payloads, plus the geometry helpers.
- `IngestConfig` and `CorrespondenceTolerances` — every threshold is a
  constructor argument with a documented default; the library holds no
  policy constants of its own.
- `IngestPlan.to_dict` / `from_dict` — full round-trip, rehydrating nested
  models rather than leaving raw dictionaries behind.
