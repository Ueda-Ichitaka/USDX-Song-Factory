"""Tests for whisper.py"""

import unittest
from src.modules.Speech_Recognition.TranscribedData import TranscribedData
from src.modules.Speech_Recognition.Whisper import (
    convert_to_transcribed_data, drop_trailing_speech_blurb, number_to_words,
)


def _word(word, start, end):
    return TranscribedData(word=word, start=start, end=end)


class DropTrailingSpeechBlurbTest(unittest.TestCase):
    def test_drops_a_short_isolated_blurb_after_a_long_silence(self):
        # a normal song ending around 180s, then a 12s silence gap, then a
        # short 3-word spoken end-card ("thanks for watching")
        data = [
            _word("last ", 175.0, 176.0),
            _word("chorus ", 176.0, 178.0),
            _word("thanks ", 190.0, 191.0),
            _word("for ", 191.0, 191.5),
            _word("watching ", 191.5, 192.5),
        ]
        result = drop_trailing_speech_blurb(data)
        self.assertEqual([w.word for w in result], ["last ", "chorus "])

    def test_keeps_lyrics_with_no_large_trailing_gap(self):
        data = [_word("a ", 0.0, 1.0), _word("b ", 1.0, 2.0), _word("c ", 2.0, 3.0)]
        result = drop_trailing_speech_blurb(data)
        self.assertEqual(result, data)

    def test_keeps_a_long_trailing_section_even_after_a_big_gap(self):
        # a real verse/outro after a long instrumental break must NOT be
        # dropped just because it is isolated - only short blurbs are
        data = [_word("a ", 0.0, 1.0)] + [
            _word(f"w{i} ", 20.0 + i * 2.0, 20.0 + i * 2.0 + 1.5) for i in range(20)
        ]
        result = drop_trailing_speech_blurb(data)
        self.assertEqual(result, data)

    def test_keeps_everything_when_fewer_than_two_words(self):
        self.assertEqual(drop_trailing_speech_blurb([]), [])
        one = [_word("solo ", 0.0, 1.0)]
        self.assertEqual(drop_trailing_speech_blurb(one), one)

    def test_small_gap_does_not_trigger_even_near_the_end(self):
        # a gap below the threshold is normal song phrasing, not an
        # isolated end-card - must never be dropped
        data = [
            _word("last ", 175.0, 176.0),
            _word("chorus ", 176.0, 178.0),
            _word("fade ", 183.5, 184.5),  # 5.5s gap, under the 8s default
            _word("out ", 184.5, 185.0),
        ]
        result = drop_trailing_speech_blurb(data)
        self.assertEqual(result, data)

class ConvertToTranscribedDataTest(unittest.TestCase):
    def test_convert_to_transcribed_data(self):
        # Arrange
        result_aligned = {
            "segments": [
                {
                    "words": [
                        {"word": "UltraSinger", "start": 1.23, "end": 2.34, "confidence": 0.95},
                        {"word": "is", "start": 2.34, "end": 3.45, "confidence": 0.9},
                        {"word": "cool!", "start": 3.45, "end": 4.56, "confidence": 0.85},
                    ]
                },
                {
                    "words": [
                        {"word": "And", "start": 4.56, "end": 5.67, "confidence": 0.95},
                        {"word": "will", "start": 5.67, "end": 6.78, "confidence": 0.9},
                        {"word": "be", "start": 6.78, "end": 7.89, "confidence": 0.85},
                        {"word": "better!", "start": 7.89, "end": 9.01, "confidence": 0.8},
                    ]
                },
            ]
        }

        # Words should have space at the end
        expected_output = [
            TranscribedData(word="UltraSinger ", start=1.23, end=2.34, is_hyphen=False, confidence=0.95),
            TranscribedData(word="is ", start=2.34, end=3.45, is_hyphen=False, confidence=0.9),
            TranscribedData(word="cool! ", start=3.45, end=4.56, is_hyphen=False, confidence=0.85),
            TranscribedData(word="And ", start=4.56, end=5.67, is_hyphen=False, confidence=0.95),
            TranscribedData(word="will ", start=5.67, end=6.78, is_hyphen=False, confidence=0.9),
            TranscribedData(word="be ", start=6.78, end=7.89, is_hyphen=False, confidence=0.85),
            TranscribedData(word="better! ", start=7.89, end=9.01, is_hyphen=False, confidence=0.8),
        ]

        # Act
        transcribed_data = convert_to_transcribed_data(result_aligned)

        # Assert
        self.assertEqual(len(transcribed_data), len(expected_output))
        for i in range(len(transcribed_data)):
            self.assertEqual(transcribed_data[i].word, expected_output[i].word)
            self.assertEqual(transcribed_data[i].end, expected_output[i].end)
            self.assertEqual(transcribed_data[i].start, expected_output[i].start)
            self.assertEqual(transcribed_data[i].is_hyphen, expected_output[i].is_hyphen)

    def test_number_to_words_converts(self):
        #Original, test with no language passed
        self.act_and_assert("I have 1 million dollars and 2 cents.", "I have one million dollars and two cents.")
        self.act_and_assert("1 2 3 4 5", "one two three four five")
        self.act_and_assert("1, 2, 3, 4, 5,", "one, two, three, four, five,")
        self.act_and_assert("Hello world 1, 2!. 3. 4? Test 100#",
                            "Hello world one, two!. three. four? Test one hundred#")
        #Test English
        self.act_and_assert("I have 1 million dollars and 2 cents.", "I have one million dollars and two cents.", "en")
        self.act_and_assert("1 2 3 4 5", "one two three four five", "en")
        self.act_and_assert("1, 2, 3, 4, 5,", "one, two, three, four, five,", "en")
        self.act_and_assert("Hello world 1, 2!. 3. 4? Test 100#",
                            "Hello world one, two!. three. four? Test one hundred#", "en")
        #Test German
        self.act_and_assert("1 2 3 4 5", "eins zwei drei vier fünf" ,"de")
        self.act_and_assert("1, 2, 3, 4, 5","eins, zwei, drei, vier, fünf","de")
        self.act_and_assert("Ich habe 1 Million Dollar und 2 Cent.","Ich habe eins Million Dollar und zwei Cent.","de")
        self.act_and_assert("Hallo Welt 1, 2!. 3. 4? Test 100#","Hallo Welt eins, zwei!. drei. vier? Test einhundert#","de")
        #Test Spanish
        self.act_and_assert("1 2 3 4 5","uno dos tres cuatro cinco","es")
        self.act_and_assert("1, 2, 3, 4, 5","uno, dos, tres, cuatro, cinco","es")
        self.act_and_assert("Tengo un millón de dólares y 2 centavos","Tengo un millón de dólares y dos centavos","es")
        self.act_and_assert("Hola mundo 1, 2!. 3. 4? Prueba 100#","Hola mundo uno, dos!. tres. cuatro? Prueba cien#","es")
        

    def act_and_assert(self, text, expected_output, language="en"):
        # Act
        result = number_to_words(text, language)

        # Assert
        self.assertEqual(result, expected_output)
if __name__ == "__main__":
    unittest.main()
