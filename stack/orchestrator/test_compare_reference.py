#!/usr/bin/env python3
"""Tests for compare_reference.py.

About me: plain assert-based checks (same style as the other orchestrator
tests) for reading word timings out of UltraStar txt files and comparing a
new generation of a song against its approved reference version. Runs on
the host, no container needed:

    python3 test_compare_reference.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compare_reference as cr  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# 60 BPM in the txt = 240 beats per minute internally -> 0.25 s per beat
# a trailing space on a syllable ends the word (written as \x20 so editors
# cannot strip it)
TXT_A = (
    "#TITLE:Song\n#ARTIST:Band\n#BPM:60\n#GAP:1000\n"
    ": 0 2 5 Hel\n"
    ": 2 2 5 lo\x20\n"
    ": 4 4 5 world\x20\n"
    "- 10\n"
    ": 12 2 5 Again\n"
    "E\n"
)

# --------------------------------------------------------------------------
# word_timings(): words (syllables joined) with start/end in seconds
# --------------------------------------------------------------------------

words_a = cr.word_timings(TXT_A)
check(f"word_timings: syllables join into words (got {[w[0] for w in words_a]})",
      [w[0] for w in words_a] == ["Hello", "world", "Again"])
check(f"word_timings: start/end in seconds from #GAP and #BPM (got {words_a})",
      words_a[0][1:] == (1.0, 2.0) and words_a[1][1:] == (2.0, 3.0) and
      words_a[2][1:] == (4.0, 4.5))
check("word_timings: a decimal comma in #BPM is accepted",
      cr.word_timings(TXT_A.replace("#BPM:60", "#BPM:60,0")) == words_a)
check("word_timings: golden/freestyle/rap notes count as notes",
      [w[0] for w in cr.word_timings(TXT_A.replace(": 12", "* 12"))] ==
      ["Hello", "world", "Again"])

# --------------------------------------------------------------------------
# compare_words(): reference vs candidate, matched by word text
# --------------------------------------------------------------------------

ref = [("Hello", 1.0, 2.0), ("world", 2.0, 3.0), ("Again", 4.0, 4.5)]
same = cr.compare_words(ref, ref)
check(f"compare_words: identical songs match fully (got {same})",
      same["matched"] == 3 and same["ref_words"] == 3 and
      same["within_01"] == 1.0 and same["median_shift"] == 0.0)

cand = [("Hello", 1.05, 2.0), ("world,", 2.5, 3.0), ("Again", 4.0, 4.5)]
diff = cr.compare_words(ref, cand)
check(f"compare_words: punctuation and case do not break the matching (got {diff})",
      diff["matched"] == 3)
check("compare_words: share of word starts within 0.1 s / 0.3 s",
      abs(diff["within_01"] - 2 / 3) < 1e-9 and abs(diff["within_03"] - 2 / 3) < 1e-9)
check("compare_words: median signed start shift (candidate minus reference)",
      abs(diff["median_shift"] - 0.05) < 1e-9)
check(f"compare_words: the largest shifts are listed with word and reference "
      f"time (got {diff['worst']})",
      diff["worst"][0] == ("world", 2.0, 0.5))

cand_changed = [("Hello", 1.0, 2.0), ("new", 2.5, 2.8), ("Again", 4.0, 4.5)]
changed = cr.compare_words(ref, cand_changed)
check("compare_words: words only in one version are left out of the timing "
      f"comparison but counted (got {changed})",
      changed["matched"] == 2 and changed["ref_words"] == 3 and
      changed["within_01"] == 1.0)

empty = cr.compare_words(ref, [])
check("compare_words: an empty candidate gives zero matches, no crash",
      empty["matched"] == 0 and empty["within_01"] is None)

# --------------------------------------------------------------------------
# compare_folders(): every reference song against its new generation
# --------------------------------------------------------------------------

root = tempfile.mkdtemp()
ref_dir = os.path.join(root, "reference")
new_dir = os.path.join(root, "new")
for base in (ref_dir, new_dir):
    os.makedirs(os.path.join(base, "Band - Song"))
os.makedirs(os.path.join(ref_dir, "Band - Missing"))
with open(os.path.join(ref_dir, "Band - Song", "Band - Song.txt"), "w", encoding="utf-8") as f:
    f.write(TXT_A)
with open(os.path.join(new_dir, "Band - Song", "Band - Song.txt"), "w", encoding="utf-8") as f:
    f.write(TXT_A.replace("#GAP:1000", "#GAP:1040"))
with open(os.path.join(ref_dir, "Band - Missing", "Band - Missing.txt"), "w", encoding="utf-8") as f:
    f.write(TXT_A)

for base in (ref_dir, new_dir):
    for extra, content in (("A notes.txt", "just notes"), ("language.txt", "de"),
                           ("lyrics.txt", "Hello world")):
        with open(os.path.join(base, "Band - Song", extra), "w", encoding="utf-8") as f:
            f.write(content)

rows = cr.compare_folders(ref_dir, new_dir)
check(f"compare_folders: one row per reference song, sorted (got {[r['song'] for r in rows]})",
      [r["song"] for r in rows] == ["Band - Missing", "Band - Song"])
check("compare_folders: a song without a new generation is reported as missing",
      rows[0]["missing"] is True)
check("compare_folders: only the txt with a #BPM header counts as the song "
      "(language.txt, lyrics.txt and other text files are ignored)",
      rows[1].get("ref_words") == 3)
check(f"compare_folders: a present song carries its comparison (got {rows[1]})",
      rows[1]["missing"] is False and rows[1]["matched"] == 3 and
      abs(rows[1]["median_shift"] - 0.04) < 1e-9)

table = cr.render_table(rows)
check("render_table: markdown table with a header and one line per song",
      table.splitlines()[0].startswith("| Song |") and
      len(table.splitlines()) == 4 and "missing" in table and "+0.040" in table)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
