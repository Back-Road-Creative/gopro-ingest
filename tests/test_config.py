"""Configuration: documented defaults, validation, and immutability."""

import dataclasses

import pytest

from gopro_ingest import DEFAULT_CONFIG, DEFAULT_TOLERANCES, CorrespondenceTolerances, IngestConfig


class TestDocumentedDefaults:
    """The README quotes these. If one changes, the README changes with it."""

    def test_episode_durations(self):
        assert DEFAULT_CONFIG.min_episode_duration == 20 * 60
        assert DEFAULT_CONFIG.max_episode_duration == 75 * 60
        assert DEFAULT_CONFIG.target_episode_duration == 60 * 60

    def test_grouping(self):
        assert DEFAULT_CONFIG.min_clip_duration == 10.0
        assert DEFAULT_CONFIG.max_gap_between_recordings == 2 * 60 * 60

    def test_gps(self):
        assert DEFAULT_CONFIG.gps_return_radius_km == 0.5
        assert DEFAULT_CONFIG.gps_stop_duration == 120.0
        assert DEFAULT_CONFIG.gps_max_interwaypoint_gap_sec == 2 * 60 * 60

    def test_tolerances(self):
        assert DEFAULT_TOLERANCES.start_time_sec == 60 * 60
        assert DEFAULT_TOLERANCES.span_abs_sec == 120.0
        assert DEFAULT_TOLERANCES.span_rel == 0.25
        assert DEFAULT_TOLERANCES.embedded_coord_radius_km == 25.0


class TestValidation:
    def test_min_above_max_is_refused(self):
        with pytest.raises(ValueError, match="exceeds max_episode_duration"):
            IngestConfig(min_episode_duration=90 * 60, max_episode_duration=60 * 60)

    def test_target_fraction_must_be_in_range(self):
        with pytest.raises(ValueError, match="target_fraction"):
            IngestConfig(target_fraction=0.0)
        with pytest.raises(ValueError, match="target_fraction"):
            IngestConfig(target_fraction=1.5)

    def test_negative_durations_refused(self):
        with pytest.raises(ValueError, match="min_clip_duration"):
            IngestConfig(min_clip_duration=-1.0)


class TestImmutability:
    def test_config_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            DEFAULT_CONFIG.min_clip_duration = 99.0

    def test_tolerances_are_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            DEFAULT_TOLERANCES.start_time_sec = 1.0

    def test_with_returns_a_copy(self):
        tweaked = DEFAULT_CONFIG.with_(min_clip_duration=30.0)
        assert tweaked.min_clip_duration == 30.0
        assert DEFAULT_CONFIG.min_clip_duration == 10.0
        assert tweaked.max_episode_duration == DEFAULT_CONFIG.max_episode_duration

    def test_tolerances_construct_freely(self):
        assert CorrespondenceTolerances(start_time_sec=30.0).start_time_sec == 30.0
