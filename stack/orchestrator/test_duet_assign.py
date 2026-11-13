#!/usr/bin/env python3
"""Tests for duet_assign.py (who sings which line of a duet).

About me: plain assert-based checks (no pytest), host-runnable with numpy;
synthetic voice fingerprints (vectors), no model, no audio. The model part
(embed_lines()) is checked in the container against the stored benchmark
fingerprints (knowledge/07-DUETS.md task 3.2). Run: python3 test_duet_assign.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duet_assign as da  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


rng = np.random.default_rng(7)
DIM = 16
VOICE_A = np.eye(DIM)[0]
VOICE_B = np.eye(DIM)[1]


def voice(v, noise=0.15):
    return v + rng.normal(0, noise, DIM)


def unit(start, end=None, **kw):
    return dict(start=start, end=end if end is not None else start + 2.0, **kw)


# --- 3.1 names
check("same_person: shared name words (3+ letters), any case",
      da.same_person("Marie Fredriksson", "marie") and da.same_person("Eminem", "EMINEM")
      and not da.same_person("Eminem", "Rihanna") and not da.same_person("", "Eminem"))
check("song_names: the song list / broken report columns first",
      da.song_names("Rihanna", "Eminem", ["Eminem", "Rihanna"], "Eminem feat. Rihanna") == ("Rihanna", "Eminem", "columns"))
check("song_names: one column, the other singer from the next source",
      da.song_names("Rihanna", "", [], "Eminem feat. Rihanna") == ("Rihanna", "Eminem", "columns"))
check("song_names: the two names the Genius headers use most",
      da.song_names("", "", ["Eminem", "Rihanna", "Eminem", "Dr. Dre"], "Eminem") == ("Eminem", "Rihanna", "genius"))
check("song_names: Genius names one singer only -> the other from the artist",
      da.song_names("", "", ["Rihanna", "Rihanna"], "Eminem feat. Rihanna") == ("Rihanna", "Eminem", "genius"))
check("song_names: the artist split into exactly two",
      da.song_names("", "", [], "Meat Loaf & Cher") == ("Meat Loaf", "Cher", "artist"))
check("song_names: nothing -> P1 / P2", da.song_names("", "", [], "Faun") == ("P1", "P2", "default"))

# --- 3.3 grouping
vectors = [voice(VOICE_A), voice(VOICE_B), voice(VOICE_A), None, voice(VOICE_B), voice(VOICE_A)]
labels, separation = da.two_groups(vectors, {})
check(f"two_groups: two clear voices, lines without a vector stay None (got {labels})",
      labels[3] is None and labels[0] == labels[2] == labels[5] and labels[1] == labels[4] and labels[0] != labels[1])
labels, _ = da.two_groups(vectors, {1: 0})
check("two_groups: an anchored line decides which group is which", labels[1] == 0 and labels[4] == 0 and labels[0] == 1)
labels, _ = da.two_groups(vectors, {0: 0, 2: 1})
check("two_groups: anchored lines keep their label even against their voice", labels[0] == 0 and labels[2] == 1)
similar = [voice(VOICE_A, 0.15) if i % 2 else voice(VOICE_A * 0.9 + VOICE_B * 0.1, 0.15) for i in range(8)]
_, sep_similar = da.two_groups(similar, {})
check(f"two_groups: similar voices give a much lower separation ({sep_similar:.2f} vs {separation:.2f})",
      sep_similar < separation / 2)
check("two_groups: fewer than two vectors -> no grouping", da.two_groups([voice(VOICE_A), None], {}) == ([None, None], 0.0))
check("fill_short_lines: a line without a label takes the label of the nearest line in time",
      da.fill_short_lines([0, None, 1, None], [1.0, 2.0, 10.0, 9.0]) == [0, 0, 1, 1]
      and da.fill_short_lines([None, None], [1.0, 2.0]) == [None, None])

# --- 3.4 assign
two_voices = [voice(VOICE_A), voice(VOICE_B), voice(VOICE_A), voice(VOICE_B), None]
units = [unit(0), unit(3), unit(6), unit(9), unit(12, 12.5)]
res, summary = da.assign(units, two_voices, artist="Eminem feat. Rihanna")
check(f"assign: fingerprint only - the voices alternate, the short last line takes its neighbour (got {res})",
      [r["singer"] for r in res] == ["P1", "P2", "P1", "P2", "P2"] and {r["source"] for r in res} == {"voice"})
check("assign: P1 is the first to sing", res[0]["singer"] == "P1")
res, summary = da.assign([unit(0), unit(3), unit(6), unit(9)], [voice(VOICE_B), voice(VOICE_A), voice(VOICE_B), voice(VOICE_A)],
                         artist="Eminem feat. Rihanna")
check("assign: the first to sing is P1 whatever the group numbering",
      [r["singer"] for r in res] == ["P1", "P2", "P1", "P2"])

marked = [unit(0, singer="P2"), unit(3, singer="P1"), unit(6), unit(9, singer="both"), unit(12)]
res, summary = da.assign(marked, [voice(VOICE_B), voice(VOICE_A), voice(VOICE_B), voice(VOICE_A), voice(VOICE_A)])
check(f"assign: marks as given (P2 first stays P2 - the admin decided), the rest by the voice anchored on them (got {res})",
      [r["singer"] for r in res] == ["P2", "P1", "P2", "both", "P1"]
      and [r["source"] for r in res] == ["marks", "marks", "voice", "marks", "voice"])
check("assign: summary counts the sources",
      summary["sources"] == {"marks": 3, "genius": 0, "voice": 2})

genius = [unit(0, singers=["Rihanna"]), unit(3, singers=["Eminem"]), unit(6), unit(9, singers=["Rihanna"])]
res, summary = da.assign(genius, [voice(VOICE_A), voice(VOICE_B), voice(VOICE_B), voice(VOICE_A)],
                         artist="Eminem feat. Rihanna")
check(f"assign: Genius singers, the voice for the rest (got {res})",
      [r["singer"] for r in res] == ["P1", "P2", "P2", "P1"]
      and [r["source"] for r in res] == ["genius", "genius", "voice", "genius"])
check(f"assign: names follow the singers - Rihanna sings first, so she is P1 (got {summary['names']})",
      summary["names"] == ("Rihanna", "Eminem") and summary["names_source"] == "genius")

res, summary = da.assign(genius, [voice(VOICE_A), voice(VOICE_B), voice(VOICE_B), voice(VOICE_A)],
                         artist="Eminem feat. Rihanna", singer_p1="Eminem", singer_p2="Rihanna")
check(f"assign: singer_p1 names P1 - no first-to-sing swap (got {res})",
      [r["singer"] for r in res] == ["P2", "P1", "P1", "P2"] and summary["names"] == ("Eminem", "Rihanna"))
res, _ = da.assign([unit(0, singers=["Eminem", "Rihanna"]), unit(3)], [voice(VOICE_A), voice(VOICE_B)],
                   artist="Eminem feat. Rihanna")
check("assign: a 'singers' list naming two counts as unknown (the voice decides)",
      res[0]["source"] == "voice")

# the threshold itself is calibrated on the duet corpus (task 3 acceptance); here one
# between the separations of the synthetic similar and clear voices measured above
da.UNSURE_SEPARATION = (sep_similar + separation) / 2
res, summary = da.assign([unit(3 * i) for i in range(8)], similar, artist="A & B")
check(f"assign: similar voices, nothing else -> unsure (separation {summary['separation']:.2f} "
      f"< {da.UNSURE_SEPARATION:.2f})", summary["unsure"])
res, summary = da.assign([unit(3 * i) for i in range(6)], vectors, artist="A & B")
check(f"assign: clearly different voices -> sure (separation {summary['separation']:.2f})", not summary["unsure"])
res, summary = da.assign([unit(0, singer="P1"), unit(3, singer="P2")], [None, None])
check("assign: every line marked -> sure, no fingerprint needed", not summary["unsure"]
      and [r["singer"] for r in res] == ["P1", "P2"])
res, summary = da.assign([unit(0), unit(3)], [None, None], artist="A & B")
check("assign: no vector and nothing else -> every line P1, unsure",
      [r["singer"] for r in res] == ["P1", "P1"] and summary["unsure"])

# --- free RAM before the model is loaded
check("enough_ram: compares the free RAM with the need",
      da.enough_ram(2.0, read=lambda: {"total_gb": 30.0, "used_gb": 20.0}) == (True, 10.0)
      and da.enough_ram(2.0, read=lambda: {"total_gb": 30.0, "used_gb": 29.0})[0] is False
      and da.enough_ram(2.0, read=lambda: None) == (True, None))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
