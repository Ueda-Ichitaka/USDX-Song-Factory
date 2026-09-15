"""Tests for language_file.py"""

import os
import tempfile
import unittest

from src.modules.language_file import resolve_language_with_file, LANGUAGE_FILE_NAME


class TestResolveLanguageWithFile(unittest.TestCase):
    """Precedence: (1) file + forced -> prefer the FILE (warn on
    mismatch); (2) no file + forced -> write file from forced; (3) file,
    no forced -> use the file; (4) no file, no forced -> auto-detect and
    write the result. Mirrors the same admin-editable-persisted-file
    pattern already used for lyrics.txt (see repair.py's
    find_lyrics_file()) - shared between repair.py's realignment path and
    UltraSinger.py's own new-song transcription so both use identical
    rules."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.song_dir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_file(self, language):
        with open(os.path.join(self.song_dir, LANGUAGE_FILE_NAME), "w",
                  encoding="utf-8") as f:
            f.write(language + "\n")

    def _read_file(self):
        with open(os.path.join(self.song_dir, LANGUAGE_FILE_NAME),
                  encoding="utf-8") as f:
            return f.read().strip()

    def test_file_and_forced_agree(self):
        self._write_file("de")
        result = resolve_language_with_file(self.song_dir, forced="de")
        self.assertEqual(result, "de")

    def test_file_present_prefers_file_over_conflicting_forced(self):
        self._write_file("de")
        result = resolve_language_with_file(self.song_dir, forced="en")
        self.assertEqual(result, "de")

    def test_no_file_with_forced_writes_the_file(self):
        result = resolve_language_with_file(self.song_dir, forced="fr")
        self.assertEqual(result, "fr")
        self.assertEqual(self._read_file(), "fr")

    def test_file_present_no_forced_uses_the_file(self):
        self._write_file("ru")
        result = resolve_language_with_file(self.song_dir, forced=None)
        self.assertEqual(result, "ru")

    def test_no_file_no_forced_auto_detects_and_writes_result(self):
        detect_calls = []

        def fake_detect():
            detect_calls.append(1)
            return "ja"

        result = resolve_language_with_file(
            self.song_dir, forced=None, detect_fn=fake_detect)
        self.assertEqual(result, "ja")
        self.assertEqual(self._read_file(), "ja")
        self.assertEqual(len(detect_calls), 1)

    def test_never_auto_detects_when_a_file_or_forced_value_exists(self):
        detect_calls = []

        def fake_detect():
            detect_calls.append(1)
            return "ja"

        self._write_file("de")
        resolve_language_with_file(self.song_dir, forced=None, detect_fn=fake_detect)
        self.assertEqual(detect_calls, [])

        resolve_language_with_file(self.song_dir, forced="en", detect_fn=fake_detect)
        self.assertEqual(detect_calls, [])

    def test_blank_forced_value_is_treated_as_not_given(self):
        # a blank/whitespace-only forced value must fall through to
        # auto-detection, not be treated as a real language code
        result = resolve_language_with_file(
            self.song_dir, forced="   ", detect_fn=lambda: "es")
        self.assertEqual(result, "es")


if __name__ == "__main__":
    unittest.main()
