"""Tests for youtube.py"""

import unittest
from unittest.mock import patch
from src.modules.Audio.youtube import get_youtube_title
from src.modules.Audio.youtube import download_and_convert_thumbnail
from src.modules.Audio.youtube import resolve_song_identity


class TestResolveSongIdentity(unittest.TestCase):
    def test_forced_values_win_over_musicbrainz(self):
        # a MusicBrainz alternate-release match (e.g. "(acoustic)") or a
        # YouTube channel name must never override trusted input-file data
        result = resolve_song_identity(
            mb_artist="Warcraft Movie", mb_title="Die Allianz schlägt zurück",
            forced_artist="Jan Hegenberg", forced_title="Die Allianz schlägt zurück")
        self.assertEqual(result, ("Jan Hegenberg", "Die Allianz schlägt zurück"))

    def test_falls_back_to_musicbrainz_when_nothing_forced(self):
        result = resolve_song_identity(
            mb_artist="Some Artist", mb_title="Some Title",
            forced_artist=None, forced_title=None)
        self.assertEqual(result, ("Some Artist", "Some Title"))

    def test_partial_force_only_overrides_the_given_field(self):
        result = resolve_song_identity(
            mb_artist="MB Artist", mb_title="MB Title",
            forced_artist="Trusted Artist", forced_title=None)
        self.assertEqual(result, ("Trusted Artist", "MB Title"))

    def test_blank_forced_values_do_not_override(self):
        result = resolve_song_identity(
            mb_artist="MB Artist", mb_title="MB Title",
            forced_artist="  ", forced_title="")
        self.assertEqual(result, ("MB Artist", "MB Title"))

class TestGetYoutubeTitle(unittest.TestCase):
    @patch("yt_dlp.YoutubeDL")
    def test_get_youtube_title(self, mock_youtube_dl):
        # Arrange
        # Also test leading and trailing whitespaces
        mock_youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "artist": "   Test Artist   ",
            "track": "   Test Track   ",
            "title": "   Test Artist - Test Track   ",
            "channel": "   Test Channel   "
        }
        url = "   https://fakeUrl   "

        # Act
        result = get_youtube_title(url)

        # Assert
        self.assertEqual(result, ("Test Artist", "Test Track"))
        mock_youtube_dl.assert_called_once()

    @patch("yt_dlp.YoutubeDL")
    def test_get_youtube_title_no_dash_no_channel_returns_none_artist(self, mock_youtube_dl):
        # Arrange: no artist/track metadata, no " - " in the title, no channel
        # -> there is no artist to report at all, must be None (not "")
        # so callers route to the title-only musicbrainz search instead of
        # the doomed multi_line_search("", title).
        mock_youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "title": "Just A Song Title",
            "channel": "",
        }
        url = "https://fakeUrl"

        # Act
        result = get_youtube_title(url)

        # Assert
        self.assertEqual(result, (None, "Just A Song Title"))

    @patch("src.modules.Audio.youtube.yt_dlp.YoutubeDL")
    @patch("src.modules.Audio.youtube.save_image")
    def test_download_and_convert_thumbnail(self, mock_save_image, mock_youtube_dl):
        # Arrange
        mock_youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {"thumbnail": "test_thumbnail_url"}
        mock_youtube_dl.return_value.__enter__.return_value.urlopen.return_value.read.return_value = b"test_image_data"

        mock_save_image.return_value = None

        ydl_opts = {}
        url = "https://fakeUrl"
        clear_filename = "test"
        output_path = "/path/to/output"

        # Act
        download_and_convert_thumbnail(ydl_opts, url, clear_filename, output_path)

        # Assert
        mock_youtube_dl.assert_called_once_with(ydl_opts)
        mock_youtube_dl.return_value.__enter__.return_value.extract_info.assert_called_once_with(url, download=False)
        mock_youtube_dl.return_value.__enter__.return_value.urlopen.assert_called_once_with("test_thumbnail_url")

if __name__ == "__main__":
    unittest.main()