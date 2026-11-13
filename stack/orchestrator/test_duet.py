#!/usr/bin/env python3
"""Tests for duet.py (the duet core: reading, shared lines, the timeline,
overlapping lines, writing, singer names).

About me: plain assert-based checks (no pytest), host-runnable, standard
library only; a synthetic duet plus the reference duet
`test data/known working/FAUN & Santiano - Tanz mit mir/... [MULTI].txt`
(read only). Run: python3 test_duet.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duet  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


def raises(fn, *args):
    try:
        fn(*args)
    except duet.DuetError as e:
        return str(e)
    return ""


HEADER = "#TITLE:Song\n#ARTIST:A feat. B\n#BPM:300\n#GAP:1000\n#P1:Anna\n#P2:Bert\n"
SONG = HEADER + (
    "P1\n"
    ": 0 4 5 Hel\n: 4 4 5 lo\n- 10\n"
    ": 20 4 5 We\n: 24 4 5  sing\n- 30\n"
    ": 40 4 5 Mine\n- 46\n"
    ": 60 4 5 Together~\n"
    "P2\n"
    ": 20 4 3 We\n: 24 4 3  sing\n- 30\n"
    ": 42 4 3 Yours\n- 48\n"
    ": 50 4 3 Late\n- 58\n"
    ": 60 4 3 Together\n"
    "E\n")

# --- 1.1 reading
d = duet.parse_duet(SONG)
check("parse: the singer names from #P1/#P2", d.names == {"P1": "Anna", "P2": "Bert"} and d.name_form == "P")
check("parse: the lines of each part",
      [l.text for l in d.parts["P1"]] == ["Hello", "We sing", "Mine", "Together~"]
      and [l.text for l in d.parts["P2"]] == ["We sing", "Yours", "Late", "Together"])
check("parse: a line's notes (kind, beat, length, pitch, syllable)",
      [(n.kind, n.beat, n.length, n.pitch, n.text) for n in d.parts["P1"][1].notes]
      == [(":", 20, 4, 5, "We"), (":", 24, 4, 5, " sing")])
check("parse: start and end beat of a line", (d.parts["P2"][1].start, d.parts["P2"][1].end) == (42, 46))
check(f"join: same start and text (~ and spaces ignored) = one shared line (got {[(l.text, sorted(l.singers)) for l in d.lines]})",
      [(l.text, sorted(l.singers)) for l in d.lines]
      == [("Hello", ["P1"]), ("We sing", ["P1", "P2"]), ("Mine", ["P1"]), ("Yours", ["P2"]),
          ("Late", ["P2"]), ("Together~", ["P1", "P2"])])
shared = d.lines[1]
check("join: a shared line keeps both copies (each with its own pitches)",
      [n.pitch for n in shared.copies["P1"].notes] == [5, 5] and [n.pitch for n in shared.copies["P2"].notes] == [3, 3])
check("parse: no problems in a clean file", d.problems == [])

other_start = duet.parse_duet(SONG.replace(": 50 4 3 Late", ": 70 4 3 Hello"))
check("join: the same text at another start is not shared",
      sum(1 for l in other_start.lines if l.text == "Hello") == 2)
old_names = duet.parse_duet(SONG.replace("#P1:Anna", "#DUETSINGERP1:Anna").replace("#P2:Bert", "#DUETSINGERP2:Bert"))
check("parse: the old #DUETSINGERP1/2 names", old_names.names == {"P1": "Anna", "P2": "Bert"}
      and old_names.name_form == "DUETSINGERP")
no_names = duet.parse_duet(SONG.replace("#P1:Anna\n", "").replace("#P2:Bert\n", ""))
check("parse: no names: empty, no tag form", no_names.names == {"P1": "", "P2": ""} and no_names.name_form is None)
no_marker = duet.parse_duet(SONG.replace("P1\n", "", 1))
check("parse: notes before the first P marker belong to P1", [l.text for l in no_marker.parts["P1"]][0] == "Hello")
spaced = duet.parse_duet(SONG.replace("P1\n", "P 1\n").replace("P2\n", "P 2\n"))
check("parse: 'P 1' / 'P 2' markers", len(spaced.parts["P2"]) == 4)
check("parse: no P2 part: refused", "P2" in raises(duet.parse_duet, HEADER + "P1\n: 0 4 5 a\nE\n"))
check("parse: #RELATIVE:yes refused", "RELATIVE" in raises(duet.parse_duet, "#RELATIVE:yes\n" + SONG))
check("parse: #RELATIVE:no is fine", len(duet.parse_duet("#RELATIVE:No\n" + SONG).lines) == 6)
broken = duet.parse_duet(SONG.replace(": 50 4 3 Late\n", ": 50 4 3 Late\n: 2 4 3  back\n"))
check(f"parse: notes going back in time inside a line are a problem (got {broken.problems})",
      len(broken.problems) == 1 and "P2" in broken.problems[0] and "back in time" in broken.problems[0])

# --- 1.2 the timeline
t = duet.timeline(d)
check("timeline: sorted by start, never back in time",
      all(a.start <= b.start for a, b in zip(t, t[1:])))
check("timeline: every line of both parts once (a shared line once for both)",
      sum(len(l.singers) for l in t) == len(d.parts["P1"]) + len(d.parts["P2"]) and len(t) == 6)

# --- 1.3 overlapping lines
partners = duet.overlap_partners(d)
check(f"overlaps: Yours (P2, 42-46) overlaps Mine (P1, 40-44) by half: the later of two equal "
      f"lines is left out and follows Mine (got {[(a.text, b.text) for a, b in partners.items()]})",
      [(a.text, b.text) for a, b in partners.items()] == [("Yours", "Mine")])
small = duet.parse_duet(SONG.replace(": 42 4 3 Yours", ": 43 8 3 Yours"))
check("overlaps: the shorter line is left out (Mine 40-44 inside Yours 43-51 by 1 beat = 25%: kept)",
      duet.overlap_partners(small) == {})
longer = duet.parse_duet(SONG.replace(": 42 4 3 Yours", ": 41 8 3 Yours"))
check("overlaps: the shorter line is left out, the longer one stays",
      [(a.text, b.text) for a, b in duet.overlap_partners(longer).items()] == [("Mine", "Yours")])
check("aligned_timeline: the timeline without the left-out lines",
      [l.text for l in duet.aligned_timeline(d)] == ["Hello", "We sing", "Mine", "Late", "Together~"])
check("follow_partner: a left-out line keeps its distance to its partner",
      duet.follow_partner(42, 40, 50) == 52 and duet.follow_partner(1.5, 1.0, 0.75) == 1.25)

# --- 1.4 writing
check("write_duet: a canonical file comes back byte for byte", duet.write_duet(d) == SONG)
crlf = ("﻿" + SONG).replace("\n", "\r\n")
check("write_duet: BOM and CRLF stay", duet.write_duet(duet.parse_duet(crlf)) == crlf)
renamed = duet.write_duet(d, names={"P1": "Eminem", "P2": "Rihanna"})
check("write_duet: new names replace the #P1/#P2 values in place",
      "#P1:Eminem\n#P2:Rihanna\n" in renamed and "Anna" not in renamed)
check("write_duet: the old #DUETSINGERP form is kept (format version rule: no conversion)",
      "#DUETSINGERP1:X\n" in duet.write_duet(old_names, names={"P1": "X", "P2": "Y"})
      and "#P1:" not in duet.write_duet(old_names, names={"P1": "X", "P2": "Y"}))
added = duet.write_duet(no_names, names={"P1": "X", "P2": "Y"})
check("write_duet: names added as #P1/#P2 after the header when the file has none",
      added.startswith(HEADER.replace("#P1:Anna\n#P2:Bert\n", "") + "#P1:X\n#P2:Y\nP1\n"))
check("write_duet: notes before the first marker are written into the P1 section",
      duet.write_duet(no_marker) == SONG)
rebuilt = duet.DuetFile(header=d.header, names=d.names, name_form=d.name_form,
                        parts={p: [duet.Line(part=p, notes=l.notes) for l in d.parts[p]] for p in ("P1", "P2")})
check("write_duet: lines without a stored break get '- <end of the line>'",
      duet.write_duet(rebuilt) == SONG.replace("- 10\n", "- 8\n").replace("- 30\n", "- 28\n")
      .replace("- 46\n", "- 44\n").replace("- 48\n", "- 46\n").replace("- 58\n", "- 54\n"))

trailing_break = SONG.replace(": 60 4 5 Together~\nP2\n", ": 60 4 5 Together~\n- 66\nP2\n")
check("write_duet: a line break after the last line of a part (before the next marker) stays",
      duet.write_duet(duet.parse_duet(trailing_break)) == trailing_break)
second_marker = SONG.replace(": 50 4 3 Late\n", "P2\n: 50 4 3 Late\n")
check("write_duet: a repeated marker inside a part stays where it was",
      duet.write_duet(duet.parse_duet(second_marker)) == second_marker)

# --- the reference duet
ref = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "test data", "known working",
                   "FAUN & Santiano - Tanz mit mir", "FAUN & Santiano - Tanz mit mir [MULTI].txt")
if os.path.isfile(ref):
    with open(ref, "rb") as f:
        raw_bytes = f.read()
    raw, encoding = duet.decode(raw_bytes)
    check(f"decode: the reference file is cp1252 (got {encoding})", encoding == "cp1252")
    tanz = duet.parse_duet(raw)
    n_shared = sum(1 for l in tanz.lines if len(l.singers) == 2)
    check(f"Tanz mit mir: {len(tanz.parts['P1'])} P1 lines, 8 shared (got {n_shared})", n_shared == 8)
    check("Tanz mit mir: written back byte for byte (same encoding)",
          duet.write_duet(tanz).encode(encoding) == raw_bytes)
else:
    print(f"[SKIP] reference duet not found: {ref}")

check("decode: UTF-8 (a BOM stays in the text for parse_duet)",
      duet.decode("\ufeff#A:ü".encode("utf-8")) == ("\ufeff#A:ü", "utf-8"))
check("decode: cp1252 when it is not UTF-8", duet.decode("#A:ü".encode("cp1252")) == ("#A:ü", "cp1252"))

# --- 2.1 admin marks in lyrics lines
check("split_singer_mark: P1:, P2:, P1+P2: (any case, spaces, P2+P1 too) -> singer and the sung text",
      [duet.split_singer_mark(t) for t in ("P1: Hello there", "p2:Hi", "P1+P2: Together", " P2 + P1 : Both of us",
                                           "P1 :x")]
      == [("P1", "Hello there"), ("P2", "Hi"), ("both", "Together"), ("both", "Both of us"), ("P1", "x")])
check("split_singer_mark: no mark -> None and the text unchanged",
      [duet.split_singer_mark(t) for t in ("Hello", "P1 is my name", "P3: no", "Eminem: yo", "")]
      == [(None, "Hello"), (None, "P1 is my name"), (None, "P3: no"), (None, "Eminem: yo"), (None, "")])

# --- 1.5 names
check("split_artist: two names from 'feat.', '&', 'and', 'with', 'x', ','",
      [duet.split_artist(a) for a in ("Eminem feat. Rihanna", "Meat Loaf & Cher", "Taylor Swift and Ed Sheeran",
                                      "Michael Jackson with Akon", "Lil Nas X", "AC/DC",
                                      "Simon & Garfunkel feat. X")]
      == [["Eminem", "Rihanna"], ["Meat Loaf", "Cher"], ["Taylor Swift", "Ed Sheeran"],
          ["Michael Jackson", "Akon"], ["Lil Nas X"], ["AC/DC"], ["Simon", "Garfunkel", "X"]])
check("duet_names: exactly two names from the artist, else none",
      duet.duet_names("Eminem feat. Rihanna") == ("Eminem", "Rihanna")
      and duet.duet_names("AC/DC") is None and duet.duet_names("Simon & Garfunkel feat. X") is None)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
