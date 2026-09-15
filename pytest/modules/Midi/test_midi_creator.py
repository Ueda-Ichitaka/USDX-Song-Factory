"""Tests for midi_creator.py"""

import unittest

from src.modules.Midi.midi_creator import create_midi_segments_from_transcribed_data


class TestCreateMidiSegmentsFromTranscribedData(unittest.TestCase):
    """Regression for a real bug found live 2026-09-15: an empty/falsy
    transcribed_data (a fully-instrumental or silent song - whisper
    genuinely can transcribe zero segments) made the function fall off
    its own end with no return statement, implicitly returning None
    instead of an empty list. UltraSinger.py assigns the result straight
    to process_data.midi_segments and passes it into
    merge_syllable_segments() right after - a None there (instead of the
    empty list every other empty-input path already produces) would
    crash the whole song's generation over what should just be "no vocal
    notes to create"."""

    def test_empty_transcribed_data_returns_empty_list_not_none(self):
        result = create_midi_segments_from_transcribed_data([], pitched_data=None)
        self.assertEqual(result, [])

    def test_none_transcribed_data_returns_empty_list_not_none(self):
        result = create_midi_segments_from_transcribed_data(None, pitched_data=None)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
