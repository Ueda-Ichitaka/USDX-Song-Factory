"""Tests for os_helper.replace_song_output_dir().

About me: checks that a new song replaces an existing output folder of the
same name (no "<name> (1)" next to it) and that the folder is never deleted
when it holds the run's own input file.
"""

import os
import tempfile
import unittest

from modules.os_helper import replace_song_output_dir


class TestReplaceSongOutputDir(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.song = os.path.join(self.root.name, "Band - Song")

    def tearDown(self):
        self.root.cleanup()

    def _file(self, name):
        path = os.path.join(self.song, name)
        os.makedirs(self.song, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("old")
        return path

    def test_a_missing_folder_is_returned_unchanged(self):
        self.assertEqual(replace_song_output_dir(self.song), self.song)
        self.assertFalse(os.path.exists(self.song))

    def test_an_existing_folder_is_removed_and_its_path_returned(self):
        self._file("Band - Song.webm")
        self.assertEqual(replace_song_output_dir(self.song), self.song)
        self.assertFalse(os.path.exists(self.song))

    def test_a_folder_holding_the_input_file_is_kept(self):
        input_file = self._file("Band - Song.txt")
        other = self._file("Band - Song.mp3")
        self.assertEqual(replace_song_output_dir(self.song, keep=[input_file]), self.song)
        self.assertTrue(os.path.isfile(input_file))
        self.assertTrue(os.path.isfile(other))

    def test_an_input_file_elsewhere_does_not_keep_the_folder(self):
        self._file("Band - Song.txt")
        elsewhere = os.path.join(self.root.name, "input.mp4")
        self.assertEqual(replace_song_output_dir(self.song, keep=[elsewhere, None]), self.song)
        self.assertFalse(os.path.exists(self.song))


if __name__ == "__main__":
    unittest.main()
