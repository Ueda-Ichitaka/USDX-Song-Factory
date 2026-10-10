#!/usr/bin/env python3
"""Tests for lyrics_divergence.py.

About me: plain assert-based checks (same style as the other orchestrator
tests) for comparing a song's lyric lines with an independent transcript of
its vocals, and for the "needs review" note built from that comparison.
Pure standard library, runs on the host:

    python3 test_lyrics_divergence.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lyrics_divergence as ld  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


def words_from(text, start=10.0, step=0.5):
    """Transcript words (start, end, text) at a steady pace."""
    out = []
    for i, w in enumerate(text.split()):
        out.append((start + i * step, start + i * step + 0.4, w))
    return out


# --------------------------------------------------------------------------
# fold_word() / similar_words()
# --------------------------------------------------------------------------

check("fold_word: case, accents and punctuation do not matter",
      ld.fold_word("Élan,") == ld.fold_word("elan"))
check("fold_word: non-Latin letters are kept (Cyrillic vs Cyrillic compares)",
      ld.fold_word("Ярило!") == "ярило")
check("similar_words: small mishearings still count",
      ld.similar_words("schwein", "schweins") and not ld.similar_words("schwein", "stein"))

# --------------------------------------------------------------------------
# compare_lyrics()
# --------------------------------------------------------------------------

lines = ["hear our prayer", "odin our lord", "we sing to you", "valhalla awaits us"]
full = ld.compare_lyrics(lines, words_from("hear our prayer odin our lord we sing to you valhalla awaits us"))
check(f"compare_lyrics: a matching transcript is fully heard (got {full['heard']:.2f})",
      full["heard"] == 1.0 and full["heard_lines"] == [1, 2, 3, 4])
check("compare_lyrics: the line times come from the transcript",
      full["line_times"][0] == 10.0 and full["line_times"][1] == 11.5)
check("compare_lyrics: singing ends at the last transcript word",
      abs(full["singing_ends"] - (10.0 + 12 * 0.5 + 0.4)) < 1e-9)

tail = ld.compare_lyrics(lines, words_from("hear our prayer odin our lord"))
check(f"compare_lyrics: lines never sung are not heard (got {tail['heard_lines']})",
      tail["heard_lines"] == [1, 2] and tail["unheard_lines"] == [3, 4] and tail["heard"] == 6 / 13)

refrain = ["la la land", "verse one here", "la la land", "verse two there", "la la land"]
heard_refrain = ld.compare_lyrics(
    refrain, words_from("la la land verse one here la la land verse two there la la land"))
check("compare_lyrics: a repeated refrain does not confuse the matching",
      heard_refrain["heard"] == 1.0)

misheard = ld.compare_lyrics(["hear our prayer"], words_from("here our prayers"))
check("compare_lyrics: slightly misheard words still count",
      misheard["heard"] == 1.0)

empty = ld.compare_lyrics(lines, [])
check("compare_lyrics: an empty transcript hears nothing, no crash",
      empty["heard"] == 0.0 and empty["singing_ends"] is None)

# --------------------------------------------------------------------------
# line_ranges() / review_note()
# --------------------------------------------------------------------------

check("line_ranges: consecutive numbers become ranges",
      ld.line_ranges([1, 2, 3, 7, 9, 10]) == "1-3, 7, 9-10")
check("line_ranges: empty -> '-'", ld.line_ranges([]) == "-")

check("review_note: no note when enough of the lyrics is heard",
      ld.review_note(full) is None)
note = ld.review_note(tail)
check(f"review_note: a note with heard share, heard/unheard lines and singing end (got {note!r})",
      note is not None and "46%" in note and "heard: lines 1-2" in note
      and "not heard: lines 3-4" in note and "singing ends at 0:12.9" in note)
check("review_note: the threshold can be changed",
      ld.review_note(tail, min_heard=0.4) is None)
check("review_note: no transcript -> no note (nothing to judge)",
      ld.review_note(None) is None)

# --------------------------------------------------------------------------
# second rule: a run of consecutive unheard lines (a missing or extra part)
# --------------------------------------------------------------------------

_vocab = ("apple bridge candle dragon ember falcon garden harbor island jungle "
          "kettle lantern meadow needle orchard pepper quarry river saddle timber "
          "umbrella velvet willow yonder zephyr anchor barley copper dune eagle "
          "forest glacier hollow ivory jasper kernel lotus marble nectar olive "
          "pebble quiver ribbon silver thistle tundra valley walnut yarrow zenith "
          "acorn basalt cobalt dahlia ferret gravel heron indigo juniper kelp").split()
long_lines = [" ".join(_vocab[3 * i:3 * i + 3]) for i in range(20)]
# lines 11-16 are not in the transcript, everything else is
sung = " ".join(l for i, l in enumerate(long_lines, 1) if not 11 <= i <= 16)
local = ld.compare_lyrics(long_lines, words_from(sung))
check(f"compare_lyrics: the longest run of unheard lines is reported (got {local.get('longest_unheard_run')})",
      local["longest_unheard_run"] == (11, 16))
check("review_note: flags a run of 5+ unheard lines even when most words are heard",
      local["heard"] >= 0.6 and ld.review_note(local) is not None and
      "lines 11-16 in a row not heard" in ld.review_note(local))
short = ld.compare_lyrics(long_lines, words_from(" ".join(
    l for i, l in enumerate(long_lines, 1) if not 11 <= i <= 14)))
check("review_note: a run of 4 unheard lines alone is no reason for a note",
      ld.review_note(short) is None)
check("review_note: the run length can be changed",
      ld.review_note(short, max_unheard_run=4) is not None)
check("compare_lyrics: no unheard lines -> no run",
      full["longest_unheard_run"] is None)

# --------------------------------------------------------------------------
# language_checkable(): the song list's language value vs whisper's languages
# --------------------------------------------------------------------------

check("language_checkable: a whisper language is checkable",
      ld.language_checkable("de") == (True, None) and ld.language_checkable(" FI ") == (True, None))
check("language_checkable: no language given -> checkable (whisper detects it)",
      ld.language_checkable("") == (True, None) and ld.language_checkable(None) == (True, None))
ok, why = ld.language_checkable("Gaulish")
check(f"language_checkable: a language whisper does not know is not checkable (got {why!r})",
      not ok and "Gaulish" in why)
ok, why = ld.language_checkable("xx")
check("language_checkable: an unknown two-letter code is not checkable", not ok and "xx" in why)
ok, why = ld.language_checkable("mixed")
check(f"language_checkable: 'mixed' is not checkable (got {why!r})", not ok and "mixed" in why)
check("language_checkable: whisper's three-letter codes count too",
      ld.language_checkable("haw") == (True, None))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
