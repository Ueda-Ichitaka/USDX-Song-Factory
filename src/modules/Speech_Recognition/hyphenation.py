"""Hyphenation module

About me: splits the words of a song into syllables, so every syllable
becomes its own note. Uses pyphen, whose dictionaries are installed with
the package (no download while a song is made), plus the Finnish
dictionary shipped in dictionaries/ (pyphen has none). Song-list and
whisper language codes are mapped to a dictionary by language_check();
a language without a dictionary gives no hyphenation, never an error.
"""

import os
import string

import pyphen
from tqdm import tqdm

from modules.Speech_Recognition.TranscribedData import TranscribedData
from modules.console_colors import (
    ULTRASINGER_HEAD,
    blue_highlighted, red_highlighted,
)

# dictionaries shipped next to this module, by language code
SHIPPED_DICTIONARIES = {
    "fi": os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "dictionaries", "hyph_fi.dic"),
}
# language codes whose pyphen dictionary has another name ("no" is
# Norwegian in whisper and the song list, pyphen has Bokmaal "nb")
LANGUAGE_ALIASES = {"no": "nb"}


def language_check(language="en") -> str | None:
    """The dictionary for a language code ("de", "fr_FR", "no", ...), or
    None when there is none."""
    if not language:
        return None
    language = LANGUAGE_ALIASES.get(language, language)
    if language in SHIPPED_DICTIONARIES:
        key = language
    else:
        key = pyphen.language_fallback(language)
    if key is None:
        return None

    print(
        f"{ULTRASINGER_HEAD} Hyphenate using language code: {blue_highlighted(key)}"
    )
    return key


def contains_punctuation(word: str) -> bool:
    """Check if word contains punctuation"""

    return any(elem in word for elem in string.punctuation)


def __clean_word(word: str):
    """Remove punctuation from word"""
    cleaned_string = ""
    removed_indices = []
    removed_symbols = []
    for i, char in enumerate(word):
        if char not in string.punctuation and char not in " ":
            cleaned_string += char
        else:
            removed_indices.append(i)
            removed_symbols.append(char)
    return cleaned_string, removed_indices, removed_symbols


def __insert_removed_symbols(separated_array, removed_indices, symbols):
    """Insert symbols into the syllables"""
    result = []
    symbol_index = 0
    i = 0

    # Add removed symbols to the syllables
    for syllable in separated_array:
        tmp = ""
        for char in syllable:
            # while, not if - CONSECUTIVE removed positions (e.g. "hi!!bye",
            # "wait... what") must all land together before the next real
            # character, not scatter one-per-character-processed
            while i in removed_indices:
                tmp += symbols[symbol_index]
                symbol_index += 1
                i += 1
            tmp += char
            i += 1
        result.append(tmp)

    # Add remaining symbols to the last syllable
    if symbol_index < len(symbols):
        tmp = result[-1]
        for i in range(symbol_index, len(symbols)):
            tmp += symbols[i]
        result[-1] = tmp

    return result


def __create_hyphenator(lang_region: str) -> pyphen.Pyphen:
    """Create hyphenator for a key from language_check()"""
    if lang_region in SHIPPED_DICTIONARIES:
        return pyphen.Pyphen(filename=SHIPPED_DICTIONARIES[lang_region])
    return pyphen.Pyphen(lang=lang_region)


def hyphenation(word: str, hyphenator: pyphen.Pyphen) -> list[str] | None:
    """Hyphenate word"""

    cleaned_string, removed_indices, removed_symbols = __clean_word(word)

    # Hyphenation of word longer than 100 characters throws exception
    if len(cleaned_string) > 100:
        return None

    cuts = [0] + list(hyphenator.positions(cleaned_string)) + [len(cleaned_string)]
    syllabus = [cleaned_string[a:b] for a, b in zip(cuts, cuts[1:])]

    length = len(syllabus)
    if length > 1:
        hyphen = []
        for i in range(length):
            hyphen.append(syllabus[i])
        hyphen = __insert_removed_symbols(hyphen, removed_indices, removed_symbols)
    else:
        hyphen = None

    return hyphen


def hyphenate_each_word(
        language: str, transcribed_data: list[TranscribedData]
) -> list[list[str]] | None:
    """Hyphenate each word in the transcribed data."""
    lang_region = language_check(language)
    if lang_region is None:
        print(
            f"{ULTRASINGER_HEAD} {red_highlighted('Error in hyphenation for language ')} {blue_highlighted(language)}{red_highlighted(', maybe you want to disable it?')}"
        )
        return None

    hyphenated_word = []
    try:
        hyphenator = __create_hyphenator(lang_region)
        for i in tqdm(enumerate(transcribed_data)):
            pos = i[0]
            hyphenated_word.append(
                hyphenation(transcribed_data[pos].word, hyphenator)
            )
    except Exception as e:
        print(
            f"{ULTRASINGER_HEAD} {red_highlighted('Error in hyphenation for language ')} {blue_highlighted(language)}{red_highlighted(', maybe you want to disable it?')}")
        print(f"\t{red_highlighted(f'->{e}')}")
        return None

    return hyphenated_word
