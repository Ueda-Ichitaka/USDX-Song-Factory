#!/usr/bin/env python3
"""Tests for lyrics_fill.py.

About me: plain assert-based checks (no pytest) for finding lyrics that are
sung but missing from the lyrics text, from whisper's transcript. Pure
standard library, runs on the host:

    python3 test_lyrics_fill.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lyrics_fill  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


def words(text, start=0.0, step=0.5):
    return [(start + k * step, start + k * step + 0.4, w) for k, w in enumerate(text.split())]


# matching keeps the unmatched words in one block
_a = "technik chemie atomkraft physik technik chemie atomkraft mikro".split()
_b = ("technik chemie atomkraft physik technik chemie atomkraft physik "
      "technik chemie atomkraft mikro").split()
_pairs = lyrics_fill.affine_pairs(_a, _b)
_unmatched = [j for j in range(len(_b)) if j not in {p[1] for p in _pairs}]
check(f"affine_pairs: all lyric words matched, the extra repeat stays one block (got {_unmatched})",
      len(_pairs) == len(_a) and _unmatched == list(range(_unmatched[0], _unmatched[0] + 4)))
check("affine_pairs: empty input", lyrics_fill.affine_pairs([], ["a"]) == [])

# a chorus line sung three times, written twice: the missing repeat is filled
_lines = [["Technik,", "Chemie,", "Atomkraft,", "Physik"],
          ["Technik,", "Chemie,", "Atomkraft,", "Physik"],
          ["und", "dann", "kommt", "das", "Ende"]]
_heard = words("Technik Chemie Atomkraft Physik Technik Chemie Atomkraft Physik "
               "Technik Chemie Atomkraft Physik und dann kommt das Ende")
_new, _origin, _review = lyrics_fill.candidate_lines(_lines, _heard)
check(f"candidate_lines: a missing repeat comes back as a copy of the line (got {_origin})",
      len(_new) == 4 and sum(1 for o in _origin if o == ("repeat", 0) or o == ("repeat", 1)) == 1
      and _new[_origin.index(next(o for o in _origin if not isinstance(o, int)))] == _lines[0]
      and _review == [])

# singing the lyrics do not have at all: whisper's own words
_lines2 = [["eins", "zwei", "drei"], ["sieben", "acht", "neun"]]
_heard2 = words("eins zwei drei vier fuenf sechs sieben acht neun")
_new2, _origin2, _ = lyrics_fill.candidate_lines(_lines2, _heard2)
check(f"candidate_lines: unknown singing becomes a whisper line in its place (got {_new2}, {_origin2})",
      _new2 == [["eins", "zwei", "drei"], ["vier", "fuenf", "sechs"], ["sieben", "acht", "neun"]]
      and _origin2 == [0, ("whisper", "vier fuenf sechs"), 1])

# fewer than min_whisper_words unknown words: nothing to fill, marked for review
_heard3 = words("eins zwei drei vier fuenf sieben acht neun")
_new3, _origin3, _review3 = lyrics_fill.candidate_lines(_lines2, _heard3, min_run=2)
check(f"candidate_lines: too few words to fill -> review span (got {_origin3}, {_review3})",
      _origin3 == [0, 1] and len(_review3) == 1 and _review3[0][0] == 1.5)

# the lyrics match what is sung: nothing changes
_new4, _origin4, _review4 = lyrics_fill.candidate_lines(_lines2, words("eins zwei drei sieben acht neun"))
check("candidate_lines: complete lyrics stay as they are",
      _new4 == _lines2 and _origin4 == [0, 1] and _review4 == [])

# mishearing inside one line is not missing text
_new5, _origin5, _ = lyrics_fill.candidate_lines(
    [["eins", "zwei", "drei", "vier", "fuenf", "sechs"]],
    words("eins zwei hallo welt wie drei vier fuenf sechs"))
check(f"candidate_lines: unknown words inside one line are not filled (got {_origin5})",
      _origin5 == [0])

# whisper's own words before the first / after the last lyric line are a
# video's spoken intro or outro: marked for review, not filled
_new6, _origin6, _review6 = lyrics_fill.candidate_lines(
    _lines2, words("hallo und willkommen zu meinem video eins zwei drei sieben acht neun danke fuers zuschauen"))
check(f"candidate_lines: spoken intro/outro words are not filled but reviewed (got {_origin6}, {_review6})",
      _origin6 == [0, 1] and len(_review6) == 2)
# a repeat of a lyric line before the first one is still filled
_new7, _origin7, _ = lyrics_fill.candidate_lines(
    [["eins", "zwei", "drei"], ["sieben", "acht", "neun"]],
    words("sieben acht neun eins zwei drei sieben acht neun"))
check(f"candidate_lines: a repeat before the first line is filled (got {_origin7})",
      _origin7 == [("repeat", 1), 0, 1])

# whisper looping on one word ("hello hello hello ...") is not a repeat of
# a line that has that word several times: a repeat needs enough DIFFERENT
# words of the line (Nirvana - Smells Like Teen Spirit, measured)
_seg = lyrics_fill.segment_run("hello hello hello hello hello hello hello hello hello".split(),
                               [["Hello,", "hello,", "hello,", "how", "low"]])
check(f"segment_run: one word repeated is no copy of a line (got {_seg})",
      all(kind != "repeat" for kind, _ in _seg))
_seg2 = lyrics_fill.segment_run("hello hello hello how low".split(),
                                [["Hello,", "hello,", "hello,", "how", "low"]])
check(f"segment_run: the whole line heard is a copy (got {_seg2})", _seg2 == [("repeat", 0)])

# whisper looping on one word (dozens of "hello" in a row) is no singing to fill
_loop_lines = [["Load", "up", "on", "guns"], ["Hello,", "hello,", "hello"], ["With", "the", "lights", "out"]]
_loop_heard = words("Load up on guns " + "hello " * 40 + "With the lights out")
_nl, _ol, _rl = lyrics_fill.candidate_lines(_loop_lines, _loop_heard)
check(f"candidate_lines: a whisper loop on one word is not filled (got {[o for o in _ol if not isinstance(o, int)]})",
      _ol == [0, 1, 2])
# one gap gives at most two copies of the same line
_cap_lines = [["eins", "zwei", "drei"], ["vier", "fuenf", "sechs"]]
_nc, _oc, _ = lyrics_fill.candidate_lines(
    _cap_lines, words("eins zwei drei " * 5 + "vier fuenf sechs"))
check(f"candidate_lines: at most two copies of one line per gap (got {_oc})",
      sum(1 for o in _oc if o == ("repeat", 0)) == 2)

check("candidate_lines: no transcript - unchanged",
      lyrics_fill.candidate_lines(_lines2, []) == ([list(l) for l in _lines2], [0, 1], []))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
