"""GoPro chapter-filename parsing."""

from gopro_ingest import is_gopro_named, parse_gopro_filename


class TestParseGoProFilename:
    def test_standard_name(self):
        assert parse_gopro_filename("GX010046.MP4") == (1, 46)

    def test_multi_chapter(self):
        assert parse_gopro_filename("GX070058.MP4") == (7, 58)

    def test_hevc_prefix(self):
        """GH is the HEVC prefix on some models."""
        assert parse_gopro_filename("GH010046.MP4") == (1, 46)

    def test_lowercase_extension(self):
        assert parse_gopro_filename("GX010046.mp4") == (1, 46)

    def test_non_gopro_file(self):
        assert parse_gopro_filename("video.mp4") is None

    def test_low_res_proxy_rejected(self):
        assert parse_gopro_filename("GL010046.LRV") is None

    def test_thumbnail_rejected(self):
        assert parse_gopro_filename("GX010046.THM") is None

    def test_empty_string(self):
        assert parse_gopro_filename("") is None

    def test_chapter_zero_is_valid(self):
        assert parse_gopro_filename("GX000001.MP4") == (0, 1)

    def test_chapter_is_the_middle_two_digits(self):
        """The session number trails; a lexical sort would interleave sessions."""
        assert parse_gopro_filename("GX020046.MP4") == (2, 46)
        assert parse_gopro_filename("GX010047.MP4") == (1, 47)


class TestIsGoProNamed:
    def test_true_for_chapter_file(self):
        assert is_gopro_named("GX010046.MP4")

    def test_false_for_other_video(self):
        assert not is_gopro_named("drone_clip.mp4")
