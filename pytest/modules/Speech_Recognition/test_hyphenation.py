"""Tests for hyphenation.py

About me: checks that words are split into syllables offline for every
language the song factory sees (pyphen's bundled dictionaries, plus the
Finnish dictionary shipped next to hyphenation.py), that unknown languages
give no hyphenation instead of an error, and that punctuation stays in
place when a word is split.
"""

import unittest
from src.modules.Speech_Recognition.hyphenation import (
    hyphenation, language_check, hyphenate_each_word)
from src.modules.Speech_Recognition import hyphenation as _hyphenation_module
from src.modules.Speech_Recognition.TranscribedData import TranscribedData

# __insert_removed_symbols/__clean_word/__create_hyphenator are
# module-private (leading double underscore) - fetched via getattr (a
# string lookup, never mangled) since this file's test methods live inside
# TestCase class bodies, where a literal "module.__name" reference WOULD be
# mangled.
_clean_word = getattr(_hyphenation_module, "__clean_word")
_insert_removed_symbols = getattr(_hyphenation_module, "__insert_removed_symbols")
_create_hyphenator = getattr(_hyphenation_module, "__create_hyphenator")


def _words(*words):
    data = []
    for word in words:
        item = TranscribedData()
        item.word = word
        item.start = 0.0
        item.end = 1.0
        data.append(item)
    return data


class TestInsertRemovedSymbols(unittest.TestCase):
    """Consecutive removed characters (punctuation/spaces stripped by
    __clean_word, e.g. "hi!!bye" or "wait... what") stay together when they
    are put back into the hyphenated syllables."""

    def test_consecutive_punctuation_stays_together(self):
        cleaned, removed_idx, removed_sym = _clean_word("hi!!bye")
        self.assertEqual(cleaned, "hibye")
        result = _insert_removed_symbols(["hi", "bye"], removed_idx, removed_sym)
        self.assertEqual(result, ["hi", "!!bye"])

    def test_single_punctuation_still_works(self):
        cleaned, removed_idx, removed_sym = _clean_word("hi!bye")
        result = _insert_removed_symbols(["hi", "bye"], removed_idx, removed_sym)
        self.assertEqual(result, ["hi", "!bye"])

    def test_trailing_punctuation_still_works(self):
        cleaned, removed_idx, removed_sym = _clean_word("hi!!")
        result = _insert_removed_symbols(["hi"], removed_idx, removed_sym)
        self.assertEqual(result, ["hi!!"])


class TestHyphenation(unittest.TestCase):

    def test_hyphenation(self):
        self.assertEqual(hyphenation("darkness", _create_hyphenator("en")), ["dark", "ness"])
        self.assertEqual(hyphenation("Hombre", _create_hyphenator("es")), ["Hom", "bre"])
        self.assertEqual(hyphenation("begegnen", _create_hyphenator("de")), ["be", "geg", "nen"])
        self.assertEqual(hyphenation(".b,e~g'eg*nen, ", _create_hyphenator("de")),
                         [".b,e", "~g'eg", "*nen, "])
        self.assertIsNone(hyphenation("Abend, ", _create_hyphenator("de")))
        self.assertIsNone(hyphenation("Abend.", _create_hyphenator("de")))

    def test_french(self):
        self.assertEqual(hyphenation("endormi", _create_hyphenator("fr")), ["en", "dor", "mi"])

    def test_norwegian(self):
        self.assertEqual(hyphenation("helvegen", _create_hyphenator("nb")), ["hel", "ve", "gen"])

    def test_finnish(self):
        hyphenator = _create_hyphenator("fi")
        self.assertEqual(hyphenation("lemmelleni", hyphenator), ["lem", "mel", "le", "ni"])
        self.assertEqual(hyphenation("Taivaankannen", hyphenator), ["Tai", "vaan", "kan", "nen"])


class TestLanguageCheck(unittest.TestCase):

    def test_languages_of_the_song_list(self):
        for language in ("en", "de", "ru", "fr", "es", "it"):
            self.assertEqual(language_check(language), language)

    def test_norwegian_uses_bokmaal(self):
        self.assertEqual(language_check("no"), "nb")

    def test_finnish_uses_the_shipped_dictionary(self):
        self.assertEqual(language_check("fi"), "fi")

    def test_region_code(self):
        self.assertEqual(language_check("fr_FR"), "fr")

    def test_unknown_language(self):
        self.assertIsNone(language_check("none"))
        self.assertIsNone(language_check(""))
        self.assertIsNone(language_check(None))


class TestHyphenateEachWord(unittest.TestCase):

    def test_french_words_are_split(self):
        result = hyphenate_each_word("fr", _words("endormi ", "nouveau "))
        self.assertEqual(result, [["en", "dor", "mi "], ["nou", "veau "]])

    def test_finnish_words_are_split(self):
        result = hyphenate_each_word("fi", _words("lemmelleni "))
        self.assertEqual(result, [["lem", "mel", "le", "ni "]])

    def test_unknown_language_gives_none(self):
        self.assertIsNone(hyphenate_each_word("xx", _words("word ")))


if __name__ == "__main__":
    unittest.main()
