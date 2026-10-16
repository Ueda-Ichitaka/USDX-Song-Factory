"""Tests for UltraSinger.py's CLI argument parsing."""

import os
import tempfile
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


class TestQuantizeToKeyFlags(unittest.TestCase):
    """Key snapping: on by default, --quantize_to_key switches it on,
    --disable_quantize_to_key off (the --disable_* convention of the other
    on/off options); the value is a real bool, never a string."""

    def setUp(self):
        ultrasinger_module.settings.quantize_to_key = True

    def test_on_by_default(self):
        settings = init_settings(["-i", "https://example.com/watch?v=abc", "-o", "/tmp/out"])
        self.assertIs(settings.quantize_to_key, True)

    def test_quantize_to_key_switches_it_on(self):
        ultrasinger_module.settings.quantize_to_key = False
        settings = init_settings(["-i", "https://example.com/watch?v=abc", "-o", "/tmp/out",
                                  "--quantize_to_key"])
        self.assertIs(settings.quantize_to_key, True)

    def test_disable_quantize_to_key_switches_it_off(self):
        settings = init_settings(["-i", "https://example.com/watch?v=abc", "-o", "/tmp/out",
                                  "--disable_quantize_to_key", "--force_title", "X"])
        self.assertIs(settings.quantize_to_key, False)
        self.assertEqual(settings.forced_title, "X")


class TestTraitsFlag(unittest.TestCase):
    """--traits a,b: the song's profile (karaoke-dashboard trait codes),
    which sets how strongly the note pitches are folded."""

    def setUp(self):
        ultrasinger_module.settings.traits = []

    def test_no_traits_by_default(self):
        settings = init_settings(["-i", "https://example.com/watch?v=abc", "-o", "/tmp/out"])
        self.assertEqual(settings.traits, [])

    def test_comma_joined_traits_are_parsed(self):
        settings = init_settings(["-i", "https://example.com/watch?v=abc", "-o", "/tmp/out",
                                  "--traits", "harsh_vocals, choir"])
        self.assertEqual(settings.traits, ["harsh_vocals", "choir"])


class TestLanguageFilePersistence(unittest.TestCase):
    """UltraSinger.py's own new-song whisper path applies the identical
    language.txt precedence as repair.py's realignment path (see
    modules.language_file.resolve_language_with_file) - both paths were
    explicitly requested, not just the repair one."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        ultrasinger_module.settings.output_folder_path = self._tmpdir.name
        self._orig_transcribe_audio = ultrasinger_module.transcribe_audio
        self._orig_remove_silence = ultrasinger_module.remove_silence_from_transcription_data
        self._orig_hyphenation = ultrasinger_module.settings.hyphenation
        ultrasinger_module.settings.hyphenation = False
        ultrasinger_module.remove_silence_from_transcription_data = lambda *a, **kw: []

    def tearDown(self):
        self._tmpdir.cleanup()
        ultrasinger_module.transcribe_audio = self._orig_transcribe_audio
        ultrasinger_module.remove_silence_from_transcription_data = self._orig_remove_silence
        ultrasinger_module.settings.hyphenation = self._orig_hyphenation

    def _language_file_path(self):
        return os.path.join(self._tmpdir.name, "language.txt")

    def test_resolve_transcription_language_prefers_saved_file_over_forced(self):
        with open(self._language_file_path(), "w", encoding="utf-8") as f:
            f.write("de\n")
        process_data = ultrasinger_module.ProcessData(
            media_info=ultrasinger_module.MediaInfo(title="t", artist="a", language="en"))
        result = ultrasinger_module.resolve_transcription_language(process_data)
        self.assertEqual(result, "de")

    def test_resolve_transcription_language_returns_none_when_nothing_known(self):
        process_data = ultrasinger_module.ProcessData(
            media_info=ultrasinger_module.MediaInfo(title="t", artist="a", language=None))
        result = ultrasinger_module.resolve_transcription_language(process_data)
        self.assertIsNone(result)

    def test_transcribe_audio_persists_the_auto_detected_language(self):
        ultrasinger_module.transcribe_audio = lambda *a, **kw: ultrasinger_module.TranscriptionResult(
            transcribed_data=[], detected_language="ja")
        process_data = ultrasinger_module.ProcessData(
            media_info=ultrasinger_module.MediaInfo(title="t", artist="a", language=None))
        ultrasinger_module.TranscribeAudio(process_data)
        self.assertEqual(process_data.media_info.language, "ja")
        with open(self._language_file_path(), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "ja")

    def test_transcribe_audio_does_not_overwrite_an_already_known_language(self):
        ultrasinger_module.transcribe_audio = lambda *a, **kw: ultrasinger_module.TranscriptionResult(
            transcribed_data=[], detected_language="ja")
        process_data = ultrasinger_module.ProcessData(
            media_info=ultrasinger_module.MediaInfo(title="t", artist="a", language="de"))
        ultrasinger_module.TranscribeAudio(process_data)
        self.assertEqual(process_data.media_info.language, "de")
        self.assertFalse(os.path.isfile(self._language_file_path()))


if __name__ == "__main__":
    unittest.main()
