"""Tests for UltraSinger.py's CLI argument parsing."""

import unittest

import src.UltraSinger as ultrasinger_module
from src.UltraSinger import init_settings


class TestForceArtistTitleFlags(unittest.TestCase):
    def setUp(self):
        # init_settings() mutates the module-level `settings` singleton, so
        # tests must not leak forced_artist/forced_title between each other
        ultrasinger_module.settings.forced_artist = None
        ultrasinger_module.settings.forced_title = None

    def test_force_artist_and_force_title_are_parsed(self):
        settings = init_settings([
            "-i", "https://example.com/watch?v=abc",
            "-o", "/tmp/out",
            "--force_artist", "Jan Hegenberg",
            "--force_title", "Die Allianz schlägt zurück",
        ])
        self.assertEqual(settings.forced_artist, "Jan Hegenberg")
        self.assertEqual(settings.forced_title, "Die Allianz schlägt zurück")

    def test_force_artist_and_force_title_default_to_none(self):
        settings = init_settings(["-i", "https://example.com/watch?v=abc", "-o", "/tmp/out"])
        self.assertIsNone(settings.forced_artist)
        self.assertIsNone(settings.forced_title)


if __name__ == "__main__":
    unittest.main()
