"""Tests for hyphenation.py"""

import unittest
from unittest.mock import patch
from src.modules.Speech_Recognition.hyphenation import hyphenation, language_check
from src.modules.Speech_Recognition import hyphenation as _hyphenation_module
from hyphen import Hyphenator, dictools

# __insert_removed_symbols/__clean_word are module-private (leading
# double underscore) - fetched via getattr (a string lookup, never
# mangled) since this file's test methods live inside TestCase class
# bodies, where a literal "module.__name" reference WOULD be mangled.
_clean_word = getattr(_hyphenation_module, "__clean_word")
_insert_removed_symbols = getattr(_hyphenation_module, "__insert_removed_symbols")


class TestInsertRemovedSymbols(unittest.TestCase):
    """Regression for a real bug found live 2026-09-15: consecutive
    removed characters (punctuation/spaces stripped by __clean_word, e.g.
    "hi!!bye" or "wait... what") were not kept together when reinserted
    into the hyphenated syllables - __insert_removed_symbols only checked
    `if i in removed_indices` (once) instead of `while` (consume ALL
    consecutive removed positions at the current spot) before appending
    the next real character, scattering the second+ symbol to the very
    end of the word instead of staying adjacent to the first. Lyrics
    (whisper transcription, fetched online lyrics) routinely contain
    exactly this shape of input (contractions, ellipses, stylized
    punctuation like "Wow!!" or "?!") - not a rare edge case."""

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


class TestHypenation(unittest.TestCase):

    def test_hypenation(self):
        """Test case for hyphenation function."""

        # prepare test
        installed = dictools.list_installed()
        for lang in installed:
            dictools.uninstall(lang)

        assert hyphenation("darkness", Hyphenator("en")) == ["dark", "ness"]
        assert hyphenation("Hombre", Hyphenator("es")) == ["Hom", "bre"]
        assert hyphenation("begegnen", Hyphenator("de_DE")) == ["be", "geg", "nen"]
        assert hyphenation(".b,e~g'eg*nen, ", Hyphenator("de_DE")) == [".b,e", "~g'eg", "*nen, "]
        assert hyphenation("Abend, ", Hyphenator("de_AT")) == None
        assert hyphenation("Abend.", Hyphenator("de_AT")) == None

    @patch('hyphen.dictools.list_installed')
    def test_language_check_has_installed_language(self, mock_list_installed):
        mock_list_installed.return_value = ['de', 'de_DE', 'en', 'en_US', 'en_GB']
        assert language_check("en") == "en_US"
        assert language_check("de") == "de_DE"

    @patch('hyphen.dictools.list_installed')
    def test_language_check_not_installed_language(self, mock_list_installed):
        mock_list_installed.return_value = []
        assert language_check("fr") == "fr_FR"
        assert language_check("de") == "de"
        assert language_check("none") == None


if __name__ == "__main__":
    unittest.main()
