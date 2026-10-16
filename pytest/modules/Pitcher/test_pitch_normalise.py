"""Tests for pitch_normalise.py (octave folding of generated note pitches).

About me: unit tests for the folding steps and the per-profile strength
(default songs: song-wide limit 18 semitones; trait harsh_vocals: 12 plus
the unsure-note pull). Pure standard library, no audio.
"""

import unittest

import src.modules.Pitcher.pitch_normalise as pn

MELODY = [4, 5, 7, 5, 4, 2, 4, 5, 7, 9, 7, 5]
# real pitches of a screamed passage (Cypecore - The Hills Have Eyes)
SCATTERED = [3, 47, 3, -5, -4, 48, -6, 48, 20, 47, 22, 36, 37, -5, 8, 5, 6, 2]


def whole_octaves(a, b):
    return all((x - y) % 12 == 0 for x, y in zip(a, b))


class RollingMedianTest(unittest.TestCase):
    def test_window_of_k_neighbours_shrinking_at_the_edges(self):
        self.assertEqual(pn.rolling_median([1, 5, 2, 8, 3], 1), [3.0, 2.0, 5.0, 3.0, 5.5])


class FoldOctavesTest(unittest.TestCase):
    def test_a_melody_within_the_tolerance_stays(self):
        self.assertEqual(pn.fold_octaves(MELODY), MELODY)

    def test_a_note_two_octaves_too_high_comes_back_down(self):
        self.assertEqual(pn.fold_octaves([4, 5, 7, 5, 28, 2, 4, 5, 7, 9, 7, 5])[4], 4)

    def test_a_note_an_octave_too_low_comes_back_up(self):
        self.assertEqual(pn.fold_octaves([4, 5, 7, 5, -8, 2, 4, 5, 7, 9, 7, 5])[4], 4)

    def test_a_real_leap_of_up_to_7_semitones_is_kept(self):
        leap = [4, 5, 7, 5, 11, 12, 11, 9, 7, 5, 4, 5]
        self.assertEqual(pn.fold_octaves(leap), leap)

    def test_only_whole_octaves(self):
        wrong = [4, 5, 7, 5, 28, 2, 4, 5, 7, 9, 7, 5]
        self.assertTrue(whole_octaves(pn.fold_octaves(wrong), wrong))

    def test_a_scattered_sequence_loses_its_multi_octave_jumps(self):
        folded = pn.fold_octaves(SCATTERED)
        self.assertLess(max(abs(a - b) for a, b in zip(folded, folded[1:])), 24)

    def test_empty(self):
        self.assertEqual(pn.fold_octaves([]), [])


class FoldToSongRangeTest(unittest.TestCase):
    DRIFTED = [3, 11, 3, 7, 8, 24, 18, 24, 20, 23, 22, 24, 25, 19, 20, 17, 18, 14]

    def test_every_note_within_the_tolerance_of_the_song_median(self):
        bounded = pn.fold_to_song_range(self.DRIFTED, 12)
        self.assertTrue(all(abs(p - 19) <= 12 for p in bounded))
        self.assertEqual(bounded[5:], self.DRIFTED[5:])
        self.assertTrue(whole_octaves(bounded, self.DRIFTED))

    def test_empty(self):
        self.assertEqual(pn.fold_to_song_range([]), [])


class NormalisePitchesTest(unittest.TestCase):
    def test_octave_errors_folded_to_whole_numbers(self):
        raw = [4, 5, 7, 5, 28, 2, 4, 5, 40, 9, 7, 5]
        result = pn.normalise_pitches(raw)
        self.assertEqual((result[4], result[8]), (4, 4))
        self.assertTrue(all(isinstance(p, int) for p in result))
        self.assertTrue(whole_octaves(result, raw))

    def test_a_line_of_different_pitches_is_not_flattened(self):
        growl = [3, 9, 2, 10, 4, 8, 1, 11, 5, 7]
        self.assertEqual(pn.normalise_pitches(growl), growl)

    def test_default_song_limit_is_18_semitones(self):
        self.assertEqual(pn.PITCH_SONG_TOLERANCE, 18)

    def test_default_keeps_a_real_wide_melody_section(self):
        # a verse around 0 and a chorus 15 semitones higher: the chorus is a
        # real part of the melody, more than an octave above the song's middle
        song = [0, 2, 0, 3, 2, 0] * 4 + [15, 17, 15, 16, 17, 15] * 2
        self.assertEqual(pn.normalise_pitches(song), song)

    def test_a_tighter_song_limit_folds_that_section(self):
        song = [0, 2, 0, 3, 2, 0] * 4 + [15, 17, 15, 16, 17, 15] * 2
        self.assertNotEqual(pn.normalise_pitches(song, song_tolerance=12), song)


class HarshProfileTest(unittest.TestCase):
    LINE = [5, 5, 5, 5, 5, 13, 5, 5, 5, 5, 5]
    SURE = [1.0] * 11
    UNSURE = [1.0] * 5 + [0.1] + [1.0] * 5

    def test_confident_notes_stay(self):
        self.assertEqual(pn.pull_unsure_notes(self.LINE, self.SURE), self.LINE)

    def test_an_unsure_note_8_above_ends_3_above(self):
        pulled = pn.pull_unsure_notes(self.LINE, self.UNSURE)
        self.assertEqual(pulled[5], 8)
        self.assertEqual(pulled[:5] + pulled[6:], self.LINE[:5] + self.LINE[6:])

    def test_an_unsure_note_2_above_moves_halfway(self):
        self.assertEqual(pn.pull_unsure_notes([5, 5, 5, 5, 5, 7, 5, 5, 5, 5, 5], self.UNSURE)[5], 6)

    def test_below_the_melody_too(self):
        self.assertEqual(pn.pull_unsure_notes([5, 5, 5, 5, 5, -1, 5, 5, 5, 5, 5], self.UNSURE)[5], 2)

    def test_30_percent_confident_frames_count_as_sure(self):
        self.assertEqual(pn.pull_unsure_notes(self.LINE, [1.0] * 5 + [0.3] + [1.0] * 5), self.LINE)

    def test_octaves_are_folded_before_pulling(self):
        self.assertEqual(pn.normalise_harsh_pitches([5, 5, 5, 5, 5, 29, 5, 5, 5, 5, 5], self.UNSURE)[5], 5)

    def test_pulls_after_folding(self):
        self.assertEqual(pn.normalise_harsh_pitches([5, 5, 5, 5, 5, 11, 5, 5, 5, 5, 5], self.UNSURE)[5], 8)

    def test_harsh_song_limit_is_12_semitones(self):
        song = [0, 2, 0, 3, 2, 0] * 4 + [15, 17, 15, 16, 17, 15] * 2
        result = pn.normalise_harsh_pitches(song, [1.0] * len(song))
        self.assertTrue(all(abs(p - 2) <= 12 for p in result))

    def test_the_screamed_passage_ends_within_an_octave_of_its_median(self):
        result = pn.normalise_harsh_pitches(SCATTERED, [1.0] * len(SCATTERED))
        median = sorted(result)[len(result) // 2]
        self.assertTrue(all(abs(p - median) <= 12 for p in result))


class NormaliseForTraitsTest(unittest.TestCase):
    SONG = [0, 2, 0, 3, 2, 0] * 4 + [15, 17, 15, 16, 17, 15] * 2
    SURE = [1.0] * 36

    def test_no_traits_is_the_default_profile(self):
        self.assertEqual(pn.normalise_for_traits(self.SONG, self.SURE, ()),
                         pn.normalise_pitches(self.SONG))
        self.assertEqual(pn.normalise_for_traits(self.SONG, self.SURE, None),
                         pn.normalise_pitches(self.SONG))

    def test_harsh_vocals_is_the_harsh_profile(self):
        self.assertEqual(pn.normalise_for_traits(self.SONG, self.SURE, ["choir", "harsh_vocals"]),
                         pn.normalise_harsh_pitches(self.SONG, self.SURE))

    def test_other_traits_are_the_default_profile(self):
        self.assertEqual(pn.normalise_for_traits(self.SONG, self.SURE, ["operatic_vocals"]),
                         pn.normalise_pitches(self.SONG))


if __name__ == "__main__":
    unittest.main()
