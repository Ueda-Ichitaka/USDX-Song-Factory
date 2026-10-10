"""Tests for separation.py

About me: checks that the vocal separation is repeatable - demucs shifts
the audio by a random amount (random.randint) before separating, so the
same song gave different vocals, and with them a different lyrics
alignment, on every run. demucs itself is replaced by a stand-in that
records the random draws it would make."""

import random
import unittest
from unittest import mock

from src.modules.Audio import separation


def _draws(*_args, **_kwargs):
    _draws.seen.append([random.randint(0, 22050) for _ in range(3)])


class SeparationRepeatableTest(unittest.TestCase):
    def test_two_separations_draw_the_same_random_shift(self):
        _draws.seen = []
        with mock.patch.object(separation.demucs.separate, "main", _draws):
            separation.separate_audio("/in/song.wav", "/out", separation.DemucsModel.HTDEMUCS)
            random.random()  # anything else using random in between
            separation.separate_audio("/in/song.wav", "/out", separation.DemucsModel.HTDEMUCS)
        self.assertEqual(_draws.seen[0], _draws.seen[1])

    def test_the_random_state_of_the_caller_is_left_alone(self):
        _draws.seen = []
        random.seed(1234)
        expected = random.random()
        random.seed(1234)
        with mock.patch.object(separation.demucs.separate, "main", _draws):
            separation.separate_audio("/in/song.wav", "/out", separation.DemucsModel.HTDEMUCS)
        self.assertEqual(random.random(), expected)


if __name__ == "__main__":
    unittest.main()
