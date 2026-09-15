"""Tests for ultrastar_parser.py"""

import unittest
import os
import tempfile
from unittest.mock import patch, MagicMock
from modules.Ultrastar.ultrastar_parser import parse, parse_ultrastar_txt


class TestParseColonInValue(unittest.TestCase):
    """Regression for a real bug found live 2026-09-15: every tag line
    (#ARTIST, #TITLE, #MP3, #AUDIO, #VIDEO, #GAP, #BPM, #VIDEOGAP, #COVER,
    #BACKGROUND) was parsed with `line.split(":")[1]` - splitting on
    EVERY colon and keeping only the piece between the first and second
    one. A value that itself contains a colon (a song title with a
    subtitle, e.g. "Title: A Subtitle" - a common, ordinary shape, not a
    rare edge case) was silently truncated at the second colon, losing
    everything after it."""

    def _parse_content(self, content: str):
        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            return parse(path)
        finally:
            os.unlink(path)

    def test_title_with_a_colon_is_not_truncated(self):
        result = self._parse_content(
            "#ARTIST:Some Artist\n"
            "#TITLE:Some Title: A Subtitle\n"
            "#MP3:song.mp3\n"
            "#BPM:100\n"
            "#GAP:0\n"
            ": 0 4 60 Hi\n"
        )
        self.assertEqual(result.title, "Some Title: A Subtitle")

    def test_artist_with_a_colon_is_not_truncated(self):
        result = self._parse_content(
            "#ARTIST:Some Artist: Featuring Someone\n"
            "#TITLE:Some Title\n"
            "#MP3:song.mp3\n"
            "#BPM:100\n"
            "#GAP:0\n"
            ": 0 4 60 Hi\n"
        )
        self.assertEqual(result.artist, "Some Artist: Featuring Someone")


class TestUltraStarParser(unittest.TestCase):
    @patch("modules.Ultrastar.ultrastar_parser.parse")
    @patch("modules.Ultrastar.ultrastar_parser.os.path.dirname")
    @patch("modules.os_helper.create_folder")
    def test_parse_ultrastar_txt(self, mock_create_folder, mock_dirname, mock_parse):
        # Arrange
        mock_parse.return_value = MagicMock(mp3="test.mp3",
                                            artist="  Test Artist  ",
                                            # Also test leading and trailing whitespaces
                                            title="  Test Title  ")  # Also test leading and trailing whitespaces

        mock_dirname.return_value = os.path.join("path", "to", "input")
        output_file_path = os.path.join("path", "to", "output")

        mock_create_folder.return_value = None

        # Act
        result = parse_ultrastar_txt(mock_dirname.return_value, output_file_path)

        # Assert
        self.assertEqual(result, ("Test Artist - Test Title",
                                  os.path.join("path", "to", "output", "Test Artist - Test Title"),
                                  os.path.join("path", "to", "input", "test.mp3"),
                                  mock_parse.return_value,
                                  "mp3"))
        #
        mock_parse.assert_called_once()
        mock_dirname.assert_called_once()
        mock_create_folder.assert_called_once()
