#!/usr/bin/env python3
"""Tests for freestyle.py.

About me: plain assert-based checks (no pytest), host only, no network, no
audio: reading the lyrics lines of a song file, the marks files and turning
marked lines into freestyle notes. Run (from utilities/):

    python3 tests/test_freestyle.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import freestyle as fs  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


SONG = ("#ARTIST:AC/DC\r\n#TITLE:Hells Bells\r\n#BPM:300\r\n#GAP:1000\r\n#AUDIO:a.opus\r\n"
        ": 0 4 0 „am\r\n: 4 4 0  Land\r\n* 8 4 0 gericht”\r\n- 14\r\n"
        "F 16 4 2 Recht\r\nF 20 4 2  kommt\r\n- 26\r\n"
        ": 30 2 0 Recht\r\nF 32 2 0  kommt\r\n- 36\r\n"
        "R 40 4 0 Recht\r\n: 44 8 0  kommt~\r\nE\r\n")
root = tempfile.mkdtemp()

# --- reading the lyrics lines
song = fs.parse(SONG.encode("utf-8"))
check(f"parse: one entry per lyrics line, numbered from 1 (got {[(l.number, l.text) for l in song.lines]})",
      [(l.number, l.text) for l in song.lines]
      == [(1, "„am Landgericht”"), (2, "Recht kommt"), (3, "Recht kommt"), (4, "Recht kommt~")])
check("parse: the note types of each line", [l.types for l in song.lines] == ["::*", "FF", ":F", "R:"])
check("parse: a line is freestyle when all its notes are F",
      [l.freestyle for l in song.lines] == [False, True, False, False])
check("parse: a line partly F is told apart", [l.partly_freestyle for l in song.lines] == [False, False, True, False])
check("parse: the header tags", song.tags["ARTIST"] == "AC/DC" and song.tags["TITLE"] == "Hells Bells")

# --- times: GAP (ms) + beat * 60 / (BPM * 4)
first = song.lines[0]
check(f"seconds: start of line 1 = GAP (got {song.seconds(first.start_beat)})", song.seconds(first.start_beat) == 1.0)
check(f"seconds: end of line 1 = beat 12 = 1.6 s (got {song.seconds(first.end_beat)})",
      abs(song.seconds(first.end_beat) - 1.6) < 1e-9)
check("seconds: a comma as decimal mark in #BPM and #GAP",
      abs(fs.parse(b"#BPM:300,0\n#GAP:1000,5\n: 30 2 0 a\nE\n").seconds(30) - 2.5005) < 1e-9)
check("seconds: unknown with #RELATIVE:yes (beats count from each line)",
      fs.parse(b"#BPM:300\n#GAP:0\n#RELATIVE:yes\n: 0 2 0 a\n- 4 4\n: 0 2 0 b\nE\n").seconds(0) is None)
check("seconds: unknown without #BPM", fs.parse(b"#GAP:0\n: 0 2 0 a\nE\n").seconds(0) is None)

# --- encodings and odd files
cp = fs.parse("#ARTIST:Die Ärzte\n#TITLE:Schrei\n: 0 2 0 Schöne\nE\n".encode("cp1252"))
check("parse: a cp1252 song file", cp.lines[0].text == "Schöne" and cp.tags["ARTIST"] == "Die Ärzte")
bom = fs.parse(b"\xef\xbb\xbf#ARTIST:A\n#TITLE:T\n: 0 2 0 x\nE\n")
check("parse: a BOM is not part of the first tag", bom.tags.get("ARTIST") == "A")
duet = fs.parse(b"#ARTIST:A\n#TITLE:T\nP1\n: 0 2 0 one\n- 4\n: 6 2 0 two\nP2\n: 0 2 0 three\nE\n")
check(f"parse: P1/P2 end a line, numbering goes on in file order (got {[l.text for l in duet.lines]})",
      [l.text for l in duet.lines] == ["one", "two", "three"])
tab = fs.parse(b"#ARTIST:A\n#TITLE:T\n: 0 2 0 a\tb\nE\n")
check("parse: a tab in a syllable becomes a space (the marks file is tab separated)", tab.lines[0].text == "a b")

# --- marks files
check("marks_name: 'Band - Title.tsv', a slash becomes a hyphen",
      fs.marks_name("AC/DC", "Hells Bells") == "AC-DC - Hells Bells.tsv")
path = os.path.join(root, "marks", fs.marks_name("AC/DC", "Hells Bells"))
fs.write_marks(path, "AC/DC", "Hells Bells", [(3, "Recht kommt"), (1, "„am Landgericht”")])
written = open(path, encoding="utf-8").read()
check(f"write_marks: band and title rows, then the marked lines in order (got {written!r})",
      written == "band\tAC/DC\ntitle\tHells Bells\n1\t„am Landgericht”\n3\tRecht kommt\n")
check("read_marks: back what was written",
      fs.read_marks(path) == ("AC/DC", "Hells Bells", [(1, "„am Landgericht”"), (3, "Recht kommt")]))
with open(path, "w", encoding="utf-8-sig", newline="") as f:
    f.write("band\tAC/DC\r\ntitle\tHells Bells\r\n\r\n2\tRecht kommt\r\n")
check("read_marks: BOM, CRLF and empty rows", fs.read_marks(path) == ("AC/DC", "Hells Bells", [(2, "Recht kommt")]))
fs.write_marks(path, "AC/DC", "Hells Bells", [])
check("write_marks: no marked line keeps band and title (the wanted state: no freestyle)",
      fs.read_marks(path) == ("AC/DC", "Hells Bells", []))

# --- checking marks against the song
check("check_marks: number and text match: no problem", fs.check_marks(song, [(2, "Recht kommt"), (3, "Recht kommt")]) == [])
problems = fs.check_marks(song, [(1, "am Landgericht"), (9, "Recht kommt")])
check(f"check_marks: a different text and a line not in the song are named (got {problems})",
      len(problems) == 2 and "line 1" in problems[0] and "line 9" in problems[1])

# --- applying
out = fs.apply(SONG.encode("utf-8"), [(1, "„am Landgericht”"), (4, "Recht kommt~")])
check(f"apply: marked lines become F, every other F note back to ':' (got {out!r})",
      out == ("#ARTIST:AC/DC\r\n#TITLE:Hells Bells\r\n#BPM:300\r\n#GAP:1000\r\n#AUDIO:a.opus\r\n"
              "F 0 4 0 „am\r\nF 4 4 0  Land\r\nF 8 4 0 gericht”\r\n- 14\r\n"
              ": 16 4 2 Recht\r\n: 20 4 2  kommt\r\n- 26\r\n"
              ": 30 2 0 Recht\r\n: 32 2 0  kommt\r\n- 36\r\n"
              "F 40 4 0 Recht\r\nF 44 8 0  kommt~\r\nE\r\n").encode("utf-8"))
check("apply: only the partly F line changes when the F lines are the marked ones",
      fs.apply(SONG.encode("utf-8"), [(2, "Recht kommt")]) == SONG.encode("utf-8").replace(b"F 32", b": 32"))
check("apply: a song file already in the wanted state comes back byte for byte",
      fs.apply(out, [(1, "„am Landgericht”"), (4, "Recht kommt~")]) == out)
raw = b"\xef\xbb\xbf" + "#ARTIST:A\n#TITLE:T\n: 0 2 0 Schöne\nE\n".encode("utf-8")
check("apply: BOM and UTF-8 kept", fs.apply(raw, [(1, "Schöne")]) == raw.replace(b": 0", b"F 0"))
latin = "#ARTIST:Die Ärzte\n#TITLE:Schrei\n: 0 2 0 Schöne\nE\n".encode("cp1252")
check("apply: a cp1252 file stays cp1252", fs.apply(latin, [(1, "Schöne")]) == latin.replace(b": 0", b"F 0"))
try:
    fs.apply(SONG.encode("utf-8"), [(1, "something else")])
    raised = False
except fs.MarksMismatch as e:
    raised = "line 1" in str(e)
check("apply: a mark whose text differs from the line is refused (MarksMismatch)", raised)

# --- the song of a marks file in the library
lib = os.path.join(root, "lib")
os.makedirs(os.path.join(lib, "AC-DC - Hells Bells"))
with open(os.path.join(lib, "AC-DC - Hells Bells", "HellsBells.txt"), "wb") as f:
    f.write(SONG.encode("utf-8"))
with open(os.path.join(lib, "AC-DC - Hells Bells", "language.txt"), "w") as f:
    f.write("en\n")
os.makedirs(os.path.join(lib, "Other folder"))
with open(os.path.join(lib, "Other folder", "x.txt"), "w", encoding="utf-8") as f:
    f.write("#ARTIST:Pol1z1stens0hn\n#TITLE:Recht Kommt (K.O. in KA)\n: 0 2 0 a\nE\n")
check("find_song: in the folder 'Band - Title' (slash as hyphen)",
      fs.find_song(lib, "AC/DC", "Hells Bells") == [os.path.join(lib, "AC-DC - Hells Bells", "HellsBells.txt")])
check("find_song: any other folder, by #ARTIST and #TITLE",
      fs.find_song(lib, "Pol1z1stens0hn", "Recht Kommt (K.O. in KA)") == [os.path.join(lib, "Other folder", "x.txt")])
check("find_song: not there: empty", fs.find_song(lib, "Nobody", "Nothing") == [])

# --- settings from stack/.env
env = os.path.join(root, ".env")
with open(env, "w") as f:
    f.write("LIBRARY_DIR=/old\n# LIBRARY_DIR=/comment\nLIBRARY_DIR=\"/lib/songs\"\n")
check("env_value: last line wins, quotes removed", fs.env_value("LIBRARY_DIR", env) == "/lib/songs")
check("env_value: missing file or key: empty", fs.env_value("X", env) == "" and fs.env_value("X", env + "-none") == "")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
