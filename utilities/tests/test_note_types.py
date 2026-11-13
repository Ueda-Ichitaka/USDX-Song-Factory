#!/usr/bin/env python3
"""Tests for note_types.py.

About me: plain assert-based checks (no pytest), host only, no network, no
audio: reading the lyrics lines and notes of a song file, the note type
marks files and writing the wanted note types into a song file. Run (from
utilities/):

    python3 tests/test_note_types.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import note_types as nt  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


def expect_mismatch(data, marks):
    try:
        nt.apply(data, marks)
    except nt.MarksMismatch as e:
        return str(e)
    return ""


SONG = ("#ARTIST:AC/DC\r\n#TITLE:Hells Bells\r\n#BPM:300\r\n#GAP:1000\r\n#AUDIO:a.opus\r\n"
        ": 0 4 0 „am\r\n: 4 4 0  Land\r\n* 8 4 0 gericht”\r\n- 14\r\n"
        "F 16 4 2 Recht\r\nF 20 4 2  kommt\r\n- 26\r\n"
        ": 30 2 0 Recht\r\nF 32 2 0  kommt\r\n- 36\r\n"
        "R 40 4 0 Recht\r\n: 44 8 0  kommt~\r\nE\r\n")
DATA = SONG.encode("utf-8")
root = tempfile.mkdtemp()

# --- reading lines and notes
song = nt.parse(DATA)
check(f"parse: one entry per lyrics line, numbered from 1 (got {[(l.number, l.text) for l in song.lines]})",
      [(l.number, l.text) for l in song.lines]
      == [(1, "„am Landgericht”"), (2, "Recht kommt"), (3, "Recht kommt"), (4, "Recht kommt~")])
check("parse: the note types of each line", [l.types for l in song.lines] == ["::*", "FF", ":F", "R:"])
check("parse: a line's type is its notes' type, or 'mixed'",
      [l.kind for l in song.lines] == ["mixed", "F", "mixed", "mixed"])
first = song.lines[0]
check("parse: the notes of a line: syllable (with its leading space), start beat, length",
      [(n.text, n.start, n.length) for n in first.notes] == [("„am", 0, 4), (" Land", 4, 4), ("gericht”", 8, 4)])
check("parse: the header tags", song.tags["ARTIST"] == "AC/DC" and song.tags["TITLE"] == "Hells Bells")
check("state: the note types of every line", nt.state(song) == {1: "::*", 2: "FF", 3: ":F", 4: "R:"})

# --- times: GAP (ms) + beat * 60 / (BPM * 4)
check(f"seconds: start of line 1 = GAP (got {song.seconds(first.start_beat)})", song.seconds(first.start_beat) == 1.0)
check("seconds: end of line 1 = beat 12 = 1.6 s", abs(song.seconds(first.end_beat) - 1.6) < 1e-9)
check("seconds: a comma as decimal mark in #BPM and #GAP",
      abs(nt.parse(b"#BPM:300,0\n#GAP:1000,5\n: 30 2 0 a\nE\n").seconds(30) - 2.5005) < 1e-9)
check("seconds: unknown with #RELATIVE:yes",
      nt.parse(b"#BPM:300\n#GAP:0\n#RELATIVE:yes\n: 0 2 0 a\n- 4 4\n: 0 2 0 b\nE\n").seconds(0) is None)
check("seconds: unknown without #BPM", nt.parse(b"#GAP:0\n: 0 2 0 a\nE\n").seconds(0) is None)

# --- encodings and odd files
cp = nt.parse("#ARTIST:Die Ärzte\n#TITLE:Schrei\n: 0 2 0 Schöne\nE\n".encode("cp1252"))
check("parse: a cp1252 song file", cp.lines[0].text == "Schöne" and cp.tags["ARTIST"] == "Die Ärzte")
check("parse: a BOM is not part of the first tag",
      nt.parse(b"\xef\xbb\xbf#ARTIST:A\n#TITLE:T\n: 0 2 0 x\nE\n").tags.get("ARTIST") == "A")
duet = nt.parse(b"#ARTIST:A\n#TITLE:T\nP1\n: 0 2 0 one\n- 4\n: 6 2 0 two\nP2\n: 0 2 0 three\nE\n")
check("parse: P1/P2 end a line, numbering goes on in file order",
      [l.text for l in duet.lines] == ["one", "two", "three"])
check("parse: a tab in a syllable becomes a space (the marks file is tab separated)",
      nt.parse(b"#ARTIST:A\n#TITLE:T\n: 0 2 0 a\tb\nE\n").lines[0].text == "a b")

# --- marks files
check("marks_name: 'Band - Title.tsv', a slash becomes a hyphen",
      nt.marks_name("AC/DC", "Hells Bells") == "AC-DC - Hells Bells.tsv")
path = os.path.join(root, "marks", nt.marks_name("AC/DC", "Hells Bells"))
nt.write_marks(path, song, nt.state(song))
written = open(path, encoding="utf-8").read()
check(f"write_marks: band, title, a row per line not all normal, a row per not normal note of a mixed line "
      f"(got {written!r})",
      written == "band\tAC/DC\ntitle\tHells Bells\n"
                 "1\tmixed\t„am Landgericht”\n1.3\t*\tgericht”\n"
                 "2\tF\tRecht kommt\n"
                 "3\tmixed\tRecht kommt\n3.2\tF\t kommt\n"
                 "4\tmixed\tRecht kommt~\n4.1\tR\tRecht\n")
marks = nt.read_marks(path)
check("read_marks: band and title", (marks.band, marks.title) == ("AC/DC", "Hells Bells"))
check("read_marks + wanted: back the song's own state", nt.wanted(song, marks) == nt.state(song))
nt.write_marks(path, song, {1: ":::", 2: "::", 3: "::", 4: "::"})
check("write_marks: all normal: only band and title (lines not listed are normal)",
      open(path, encoding="utf-8").read() == "band\tAC/DC\ntitle\tHells Bells\n")
with open(path, "w", encoding="utf-8-sig", newline="") as f:
    f.write("band\tAC/DC\r\ntitle\tHells Bells\r\n\r\n2\tR\tRecht kommt\r\n")
check("read_marks: BOM, CRLF and empty rows", nt.wanted(song, nt.read_marks(path)) == {1: ":::", 2: "RR", 3: "::", 4: "::"})

# --- checking marks against the song


def marks_of(rows: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write("band\tAC/DC\ntitle\tHells Bells\n" + rows)
    return nt.read_marks(path)


check("check_marks: fitting marks: no problem", nt.check_marks(song, marks_of("2\tG\tRecht kommt\n")) == [])
problems = nt.check_marks(song, marks_of("1\tF\tam Landgericht\n9\tF\tRecht kommt\n"))
check(f"check_marks: a different line text and a line not in the song (got {problems})",
      len(problems) == 2 and "line 1" in problems[0] and "line 9" in problems[1])
problems = nt.check_marks(song, marks_of("2\tX\tRecht kommt\n"))
check(f"check_marks: an unknown note type (got {problems})", len(problems) == 1 and "X" in problems[0])
problems = nt.check_marks(song, marks_of("1\tmixed\t„am Landgericht”\n1.4\t*\tx\n1.2\t*\tLund\n"))
check(f"check_marks: a note not in the line, a different syllable (got {problems})",
      len(problems) == 2 and "1.4" in problems[0] and "1.2" in problems[1])
problems = nt.check_marks(song, marks_of("2\tF\tRecht kommt\n2.1\t*\tRecht\n"))
check(f"check_marks: a note row for a line not marked mixed (got {problems})", len(problems) == 1 and "2.1" in problems[0])
check("check_marks: a syllable's spaces at the ends do not count",
      nt.check_marks(song, marks_of("1\tmixed\t„am Landgericht”\n1.2\t*\tLand\n")) == [])

# --- applying
out = nt.apply(DATA, marks_of("1\tF\t„am Landgericht”\n3\tmixed\tRecht kommt\n3.2\t*\t kommt\n4\tG\tRecht kommt~\n"))
check(f"apply: listed lines get their types, lines not listed become normal (got {out!r})",
      out == ("#ARTIST:AC/DC\r\n#TITLE:Hells Bells\r\n#BPM:300\r\n#GAP:1000\r\n#AUDIO:a.opus\r\n"
              "F 0 4 0 „am\r\nF 4 4 0  Land\r\nF 8 4 0 gericht”\r\n- 14\r\n"
              ": 16 4 2 Recht\r\n: 20 4 2  kommt\r\n- 26\r\n"
              ": 30 2 0 Recht\r\n* 32 2 0  kommt\r\n- 36\r\n"
              "G 40 4 0 Recht\r\nG 44 8 0  kommt~\r\nE\r\n").encode("utf-8"))
nt.write_marks(path, song, nt.state(song))
check("apply: the song's own state comes back byte for byte", nt.apply(DATA, nt.read_marks(path)) == DATA)
check("changes: (row, old, new) of each note that changes",
      nt.changes(song, {1: ":::", 2: "FF", 3: ":F", 4: "R:"}) == [(7, "*", ":")])
raw = b"\xef\xbb\xbf" + "#ARTIST:A\n#TITLE:T\n: 0 2 0 Schöne\nE\n".encode("utf-8")
bom_song = nt.parse(raw)
nt.write_marks(path, bom_song, {1: "*"})
check("apply: BOM and UTF-8 kept", nt.apply(raw, nt.read_marks(path)) == raw.replace(b": 0", b"* 0"))
latin = "#ARTIST:A\n#TITLE:T\n: 0 2 0 Schöne\nE\n".encode("cp1252")
check("apply: a cp1252 file stays cp1252", nt.apply(latin, nt.read_marks(path)) == latin.replace(b": 0", b"* 0"))
check("apply: marks that do not fit are refused (MarksMismatch)",
      "line 1" in expect_mismatch(DATA, marks_of("1\tF\tsomething else\n")))

# --- the song of a marks file in the library
lib = os.path.join(root, "lib")
os.makedirs(os.path.join(lib, "AC-DC - Hells Bells"))
with open(os.path.join(lib, "AC-DC - Hells Bells", "HellsBells.txt"), "wb") as f:
    f.write(DATA)
with open(os.path.join(lib, "AC-DC - Hells Bells", "language.txt"), "w") as f:
    f.write("en\n")
os.makedirs(os.path.join(lib, "Other folder"))
with open(os.path.join(lib, "Other folder", "x.txt"), "w", encoding="utf-8") as f:
    f.write("#ARTIST:Pol1z1stens0hn\n#TITLE:Recht Kommt (K.O. in KA)\n: 0 2 0 a\nE\n")
check("find_song: in the folder 'Band - Title' (slash as hyphen)",
      nt.find_song(lib, "AC/DC", "Hells Bells") == [os.path.join(lib, "AC-DC - Hells Bells", "HellsBells.txt")])
check("find_song: any other folder, by #ARTIST and #TITLE",
      nt.find_song(lib, "Pol1z1stens0hn", "Recht Kommt (K.O. in KA)") == [os.path.join(lib, "Other folder", "x.txt")])
check("find_song: not there: empty", nt.find_song(lib, "Nobody", "Nothing") == [])

# --- settings from stack/.env
env = os.path.join(root, ".env")
with open(env, "w") as f:
    f.write("LIBRARY_DIR=/old\n# LIBRARY_DIR=/comment\nLIBRARY_DIR=\"/lib/songs\"\n")
check("env_value: last line wins, quotes removed", nt.env_value("LIBRARY_DIR", env) == "/lib/songs")
check("env_value: missing file or key: empty", nt.env_value("X", env) == "" and nt.env_value("X", env + "-none") == "")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
