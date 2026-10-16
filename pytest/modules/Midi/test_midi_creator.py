"""Tests for midi_creator.py"""

import unittest

from src.modules.Midi.MidiSegment import MidiSegment
from src.modules.Midi.midi_creator import create_midi_segments_from_transcribed_data
from src.modules.Midi.midi_creator import normalise_midi_segments, sure_pitch_fraction
from src.modules.Pitcher.pitched_data import PitchedData


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


def _pitched(confidence):
    """A pitch track of 0.1 s frames at 440 Hz with the given confidences."""
    return PitchedData([i * 0.1 for i in range(len(confidence))], [440.0] * len(confidence), confidence)


class TestSurePitchFraction(unittest.TestCase):
    def test_share_of_frames_above_the_shared_threshold(self):
        # frames 0.0 / 0.1 / 0.2 of [0.0, 0.3]: confidences 0.9, 0.1, 0.5
        self.assertAlmostEqual(sure_pitch_fraction(0.0, 0.3, _pitched([0.9, 0.1, 0.5, 0.9])), 2 / 3)

    def test_a_note_shorter_than_a_frame_uses_its_frame(self):
        self.assertEqual(sure_pitch_fraction(0.1, 0.12, _pitched([0.9, 0.1, 0.5])), 0.0)


class TestNormaliseMidiSegments(unittest.TestCase):
    """UltraSinger's own notes are octave-folded like the lyrics step's
    (pitch_normalise.normalise_for_traits()), on the finished note list."""

    MELODY = ["E4", "F4", "G4", "F4", "E4", "D4", "E4", "F4", "G4", "A4", "G4", "F4"]

    def segments(self, notes):
        return [MidiSegment(n, i * 0.5, i * 0.5 + 0.4, "la ") for i, n in enumerate(notes)]

    def test_a_note_two_octaves_too_high_comes_back(self):
        notes = list(self.MELODY)
        notes[4] = "E6"
        result = normalise_midi_segments(self.segments(notes), _pitched([0.9] * 70), traits=())
        self.assertEqual([s.note for s in result], self.MELODY)

    def test_timing_and_words_stay(self):
        segments = self.segments(self.MELODY)
        result = normalise_midi_segments(segments, _pitched([0.9] * 70), traits=())
        self.assertEqual([(s.start, s.end, s.word) for s in result],
                         [(s.start, s.end, s.word) for s in self.segments(self.MELODY)])

    def test_harsh_vocals_pulls_an_unsure_note_towards_the_melody(self):
        notes = ["F4"] * 11
        notes[5] = "B4"  # 6 semitones above (inside the octave fold), its frames unsure
        segments = [MidiSegment(n, i * 1.0, i * 1.0 + 0.5, "la ") for i, n in enumerate(notes)]
        confidence = [0.9] * 120
        for frame in range(50, 56):
            confidence[frame] = 0.05
        default = normalise_midi_segments(segments, _pitched(confidence), traits=())
        self.assertEqual(default[5].note, "B4")
        harsh = normalise_midi_segments(segments, _pitched(confidence), traits=["harsh_vocals"])
        self.assertEqual(harsh[5].note, "G♯4")  # halfway, 3 above the melody

    def test_empty(self):
        self.assertEqual(normalise_midi_segments([], _pitched([0.9]), traits=()), [])


if __name__ == "__main__":
    unittest.main()
