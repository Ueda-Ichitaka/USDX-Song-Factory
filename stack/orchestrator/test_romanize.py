#!/usr/bin/env python3
"""Tests for romanize.py.

About me: plain assert-based regression checks (no pytest, matching
test_repair.py). Needs cyrtranslit/pykakasi/korean-romanizer, which only
the docker image installs - run inside the container:

    docker compose run --rm ultrasinger python /app/orchestrator/test_romanize.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import romanize as rz  # noqa: E402

failures = []


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# script detection
# --------------------------------------------------------------------------

check("detects cyrillic", rz.detect_script("привет") == "cyrillic")
check("detects korean (hangul)", rz.detect_script("안녕") == "korean")
check("detects japanese (hiragana)", rz.detect_script("こんにちは") == "japanese")
check("detects japanese (kanji)", rz.detect_script("東京") == "japanese")
check("latin text has no script", rz.detect_script("hello") is None)
check("digits/punctuation have no script", rz.detect_script("123!?") is None)

check("detects ukrainian via distinctive letters (cyrtranslit code 'ua')",
      rz.detect_cyrillic_lang("привіт це українська") == "ua")
check("defaults cyrillic to russian",
      rz.detect_cyrillic_lang("привет мир") == "ru")

# guard against the real bug this caught: passing the ISO code 'uk' to
# cyrtranslit silently no-ops instead of romanizing - so the LANGUAGE CODE
# alone isn't a strong enough check; assert the actual text changes too
check("ukrainian text is actually romanized end-to-end (not a silent no-op)",
      rz.romanize_text("Привіт, як справи?", "cyrillic", "ua") != "Привіт, як справи?")

# --------------------------------------------------------------------------
# word-field romanization: text changes, spacing/hold markers are verbatim
# --------------------------------------------------------------------------

w1, s1 = rz.romanize_word_field("привет ", "ru")
check("romanizes cyrillic core", w1.strip() != "привет" and s1 == "cyrillic")
check("keeps the TRAILING space verbatim", w1.endswith(" "))

w2, s2 = rz.romanize_word_field(" мир", "ru")
check("keeps the LEADING space verbatim", w2.startswith(" ") and s2 == "cyrillic")

w3, s3 = rz.romanize_word_field("~", "ru")
check("hold marker is left untouched", w3 == "~" and s3 is None)

w4, s4 = rz.romanize_word_field("~ ", "ru")
check("hold marker with trailing space is left untouched", w4 == "~ " and s4 is None)

w5, s5 = rz.romanize_word_field("hello ", "ru")
check("already-latin word is left untouched", w5 == "hello " and s5 is None)

w6, s6 = rz.romanize_word_field("안녕", "ru")
check("romanizes korean", w6 != "안녕" and s6 == "korean")

w7, s7 = rz.romanize_word_field("こんにちは", "ru")
check("romanizes japanese", w7 != "こんにちは" and s7 == "japanese")

# --------------------------------------------------------------------------
# romanize_line: only the word field changes, byte-for-byte prefix kept
# --------------------------------------------------------------------------

raw = ": 10 4 5 привет"
new_raw, script = rz.romanize_line(raw, "ru")
check("romanize_line keeps type/beat/dur/pitch byte-for-byte",
      new_raw.startswith(": 10 4 5 "))
check("romanize_line actually romanized the word",
      new_raw != raw and script == "cyrillic")

raw_latin = ": 10 4 5 hello"
new_raw_latin, script_latin = rz.romanize_line(raw_latin, "ru")
check("romanize_line is a no-op for an already-latin line",
      new_raw_latin == raw_latin and script_latin is None)

# --------------------------------------------------------------------------
# full txt round-trip + idempotency
# --------------------------------------------------------------------------

sample = """#BPM:200
#GAP:0
: 0 4 0 при
: 4 4 0 вет
- 8
: 8 4 0 мир
E
"""

tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
tmp.write(sample)
tmp.close()

try:
    txt = rz.Txt(tmp.name)
    changed, scripts, n_words = rz.romanize_txt_file(txt)
    check("romanize_txt_file reports a change", changed is True)
    check("romanize_txt_file detected cyrillic", scripts == {"cyrillic"})
    check(f"romanize_txt_file romanized all 3 words (got {n_words})", n_words == 3)

    with open(tmp.name, encoding="utf-8") as f:
        result_text = f.read()
    check("output has no cyrillic characters left",
          rz.CYRILLIC_RE.search(result_text) is None)
    check("output kept the header lines untouched",
          "#BPM:200" in result_text and "#GAP:0" in result_text)
    check("output kept the 'E' end marker untouched",
          result_text.rstrip().splitlines()[-1] == "E")

    # idempotency: romanizing an already-romanized file must be a no-op
    txt2 = rz.Txt(tmp.name)
    changed2, scripts2, n_words2 = rz.romanize_txt_file(txt2)
    check("romanizing an already-romanized file is idempotent",
          changed2 is False and n_words2 == 0)
finally:
    os.unlink(tmp.name)

# a pure-Latin song must be left alone entirely
sample_latin = """#BPM:200
#GAP:0
: 0 4 0 hello
: 4 4 0 world
E
"""
tmp2 = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
tmp2.write(sample_latin)
tmp2.close()
try:
    txt3 = rz.Txt(tmp2.name)
    changed3, scripts3, n_words3 = rz.romanize_txt_file(txt3)
    check("a pure-latin song is reported unchanged", changed3 is False)
finally:
    os.unlink(tmp2.name)

# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# ROMANIZE_STYLE: "scientific" (default, GOST 7.79 B via cyrtranslit) or
# "readable" (karaoke-friendly Russian: kh, ts, shch, y, yo, no soft/hard sign)
# --------------------------------------------------------------------------

check("style default is scientific (unchanged output)",
      rz.romanize_text("щука", "cyrillic", "ru") == "shhuka")
check("readable: shch/ts/kh/y instead of shh/cz/h/y'",
      rz.romanize_text("щука", "cyrillic", "ru", style="readable") == "shchuka" and
      rz.romanize_text("седмицу", "cyrillic", "ru", style="readable") == "sedmitsu" and
      rz.romanize_text("лесных", "cyrillic", "ru", style="readable") == "lesnykh")
check("readable: soft and hard signs are left out, yo/ya/yu/zh/ch",
      rz.romanize_text("объём", "cyrillic", "ru", style="readable") == "obyom" and
      rz.romanize_text("вольная", "cyrillic", "ru", style="readable") == "volnaya" and
      rz.romanize_text("жую чай", "cyrillic", "ru", style="readable") == "zhuyu chay")
check("readable: capitals keep their case, all-caps stays all-caps",
      rz.romanize_text("Ярило", "cyrillic", "ru", style="readable") == "Yarilo" and
      rz.romanize_text("Щит", "cyrillic", "ru", style="readable") == "Shchit" and
      rz.romanize_text("ЩИТ", "cyrillic", "ru", style="readable") == "SHCHIT")
check("readable: non-Cyrillic characters pass through",
      rz.romanize_text("да!", "cyrillic", "ru", style="readable") == "da!")
check("readable: Ukrainian stays scientific (its readable standard depends on the "
      "position in the word, which a syllable does not know)",
      rz.romanize_text("їжак", "cyrillic", "ua", style="readable") ==
      rz.romanize_text("їжак", "cyrillic", "ua"))
check("an unknown style is rejected",
      _raises(lambda: rz.romanize_text("да", "cyrillic", "ru", style="fancy")))
check("romanize_word_field passes the style on",
      rz.romanize_word_field("щу", style="readable")[0] == "shchu")

_saved_style, _saved_argv = rz.ROMANIZE_STYLE, sys.argv
_exit = None
try:
    rz.ROMANIZE_STYLE = "fancy"
    sys.argv = ["romanize.py", "--txt", "/nonexistent.txt"]
    try:
        rz.main()
    except SystemExit as exc:
        _exit = exc.code
finally:
    rz.ROMANIZE_STYLE, sys.argv = _saved_style, _saved_argv
check(f"main: an unknown ROMANIZE_STYLE stops with exit code 2 before any file (got {_exit})",
      _exit == 2)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
