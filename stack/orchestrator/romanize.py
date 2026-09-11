#!/usr/bin/env python3
"""Romanize non-Latin lyrics in an UltraStar txt, in place.

About me: after a song is created or repaired, its lyrics may be in
Cyrillic (Russian/Ukrainian), Korean (Hangul) or Japanese (kana/kanji) -
whatever whisper transcribed. This rewrites every note's word field to its
romanized (Latin-script) form, word by word, so the txt that ends up in the
UltraStar library always has romanized lyrics. Script detection is per
WORD (not per song), so a song mixing scripts (loanwords, an already-latin
chorus, ...) is handled correctly - each word is only touched if it
actually contains a script we know how to romanize; everything else
(already-Latin words, digits, punctuation) passes through unchanged.

Only the word TEXT changes. Beat/dur/pitch and every other byte of the
line (including the separating whitespace) are kept exactly as in the
original file - reuses repair.py's Note/Txt parser and NOTE_LINE_RE so the
verbatim-word-field handling (see 05-LESSONS.md) is not duplicated.

Usage
-----
  romanize.py --txt SONG.txt        romanize one file
  romanize.py --song-dir DIR        romanize every ultrastar txt in DIR

For every processed txt a `ROMANIZE_DONE ...` or `ROMANIZE_SKIPPED ...`
line is printed for the orchestrator to parse.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repair import NOTE_LINE_RE, Txt, find_ultrastar_txts  # noqa: E402

CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ]")
JAPANESE_RE = re.compile(r"[぀-ヿ一-鿿]")
UKRAINIAN_MARKERS = set("іїєґІЇЄҐ")


def detect_script(text: str):
    """Return 'cyrillic' | 'korean' | 'japanese' | None for one word/text."""
    if CYRILLIC_RE.search(text):
        return "cyrillic"
    if HANGUL_RE.search(text):
        return "korean"
    if JAPANESE_RE.search(text):
        return "japanese"
    return None


def detect_cyrillic_lang(all_text: str) -> str:
    """'ua' when Ukrainian-only letters appear anywhere in the song, else
    'ru' - cyrtranslit needs a concrete language code for best results.
    NOTE: cyrtranslit's Ukrainian code is 'ua', NOT the ISO 639-1 'uk' -
    passing 'uk' silently no-ops (verified empirically, see test_romanize.py)."""
    return "ua" if any(ch in UKRAINIAN_MARKERS for ch in all_text) else "ru"


_kakasi_converter = None


def _romanize_japanese(text: str) -> str:
    global _kakasi_converter
    if _kakasi_converter is None:
        import pykakasi
        _kakasi_converter = pykakasi.kakasi()
    return "".join(item["hepburn"] for item in _kakasi_converter.convert(text))


def _romanize_korean(text: str) -> str:
    from korean_romanizer.romanizer import Romanizer
    return Romanizer(text).romanize()


def _romanize_cyrillic(text: str, lang: str) -> str:
    import cyrtranslit
    return cyrtranslit.to_latin(text, lang)


def romanize_text(text: str, script: str, cyrillic_lang: str = "ru") -> str:
    if script == "cyrillic":
        return _romanize_cyrillic(text, cyrillic_lang)
    if script == "korean":
        return _romanize_korean(text)
    if script == "japanese":
        return _romanize_japanese(text)
    return text


def romanize_word_field(word: str, cyrillic_lang: str = "ru"):
    """Romanize the CORE text of one note's word field.

    Leading/trailing whitespace and '~' hold markers are word-boundary
    carriers (see 05-LESSONS.md) and are kept byte-for-byte; only the
    actual text is romanized, and only when it contains a script we
    recognize. Returns (new_word, script_used|None) - script_used is None
    when nothing changed (already Latin, a hold marker, or empty).
    """
    core = word.strip()
    if not core or core.strip("~") == "":
        return word, None
    script = detect_script(core)
    if script is None:
        return word, None
    romanized = romanize_text(core, script, cyrillic_lang)
    if romanized == core:
        return word, None
    leading = word[:len(word) - len(word.lstrip())]
    trailing = word[len(word.rstrip()):]
    return f"{leading}{romanized}{trailing}", script


def romanize_line(raw: str, cyrillic_lang: str = "ru"):
    """Rewrite one raw note line's word field only. Returns
    (new_raw, script_used|None)."""
    m = NOTE_LINE_RE.match(raw)
    if not m:
        return raw, None
    word = m.group(5) or ""
    new_word, script = romanize_word_field(word, cyrillic_lang)
    if script is None:
        return raw, None
    return raw[:m.start(5)] + new_word, script


def romanize_txt_file(txt: Txt):
    """Romanize every note line of `txt` in place (file is only rewritten
    if something actually changed). Returns (changed, scripts_used, n_words)."""
    all_text = "".join(n.syllable for n in txt.notes)
    cyrillic_lang = detect_cyrillic_lang(all_text)

    scripts = set()
    n_words = 0
    for line in txt.lines:
        if "note" not in line:
            continue
        new_raw, script = romanize_line(line["raw"], cyrillic_lang)
        if script is not None:
            line["raw"] = new_raw
            scripts.add(script)
            n_words += 1

    if n_words == 0:
        return False, scripts, 0

    with open(txt.path, "w", encoding="utf-8", newline="\n") as f:
        for line in txt.lines:
            f.write(line["raw"].rstrip("\r\n") + "\n")
    return True, scripts, n_words


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--txt", help="a single ultrastar txt file to romanize")
    parser.add_argument("--song-dir", help="romanize every ultrastar txt in this folder")
    args = parser.parse_args()

    if not args.txt and not args.song_dir:
        parser.error("either --txt or --song-dir is required")

    if args.txt:
        paths = [args.txt]
    else:
        paths = [t.path for t in find_ultrastar_txts(args.song_dir)]

    overall_ok = True
    for path in paths:
        try:
            txt = Txt(path)
            changed, scripts, n_words = romanize_txt_file(txt)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"ROMANIZE_FAILED path={path} error={exc}")
            overall_ok = False
            continue
        if changed:
            print(f"ROMANIZE_DONE path={path} "
                  f"scripts={','.join(sorted(scripts))} words={n_words}")
        else:
            print(f"ROMANIZE_SKIPPED path={path} reason=already-latin")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
