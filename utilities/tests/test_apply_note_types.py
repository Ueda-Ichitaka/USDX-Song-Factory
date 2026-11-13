#!/usr/bin/env python3
"""Tests for apply_note_types.py.

About me: plain assert-based checks (no pytest), host only, temp library,
marks and output folders - the real stack/input and library are never
touched. Run (from utilities/): python3 tests/test_apply_note_types.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
UTILITIES = os.path.dirname(HERE)
sys.path.insert(0, UTILITIES)

import apply_note_types as ant  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


def write(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


root = tempfile.mkdtemp()
lib, marks, out = (os.path.join(root, d) for d in ("lib", "marks", "out"))
os.makedirs(marks)
SONG = (b"#ARTIST:Pol1z1stens0hn\n#TITLE:Recht Kommt\n#BPM:300\n#GAP:0\n"
        b": 0 4 0 Sample\n- 6\n: 8 4 0 Recht\n: 12 4 0  kommt\n- 18\n: 20 4 0 Recht\n: 24 4 0  kommt\nE\n")
song_path = os.path.join(lib, "Pol1z1stens0hn - Recht Kommt", "Recht Kommt.txt")
write(song_path, SONG)
write(os.path.join(lib, "Same - Song", "Same - Song.txt"),
      b"#ARTIST:Same\n#TITLE:Song\n#BPM:300\n#GAP:0\nF 0 4 0 Intro\n- 6\n: 8 4 0 Sung\nE\n")
for folder in ("Twice - A", "Twice - B"):
    write(os.path.join(lib, folder, "t.txt"), b"#ARTIST:Twice\n#TITLE:Song\n: 0 4 0 x\nE\n")


def marks_file(name, band, title, rows):
    write(os.path.join(marks, name), f"band\t{band}\ntitle\t{title}\n{rows}".encode("utf-8"))


marks_file("Pol1z1stens0hn - Recht Kommt.tsv", "Pol1z1stens0hn", "Recht Kommt",
           "1\tF\tSample\n2\tmixed\tRecht kommt\n2.2\t*\t kommt\n3\tR\tRecht kommt\n")
marks_file("Same - Song.tsv", "Same", "Song", "1\tF\tIntro\n")
marks_file("Wrong - Text.tsv", "Pol1z1stens0hn", "Recht Kommt", "2\tF\tRecht\n")
marks_file("Gone - Song.tsv", "Gone", "Song", "1\tF\tx\n")
marks_file("Twice - Song.tsv", "Twice", "Song", "1\tF\tx\n")
write(os.path.join(marks, "notes.tsv"), b"just\tnotes\n")

check("number_ranges: runs of line numbers", ant.number_ranges([1, 2, 3, 5, 7, 8]) == "1-3, 5, 7-8")
check("summary: the lines per type, normal lines left out",
      ant.summary({1: "FF", 2: "FF", 3: "::", 4: ":*", 5: "RR", 6: "F"}) == "F 1-2, 6; R 5; mixed 4")
check("summary: all normal", ant.summary({1: "::"}) == "all normal")

dry = ant.run(marks, lib, out, apply=False)
check(f"dry run: one result per marks file (got {sorted(k for k, v in dry.items() if v)})",
      sorted(k for k, v in dry.items() if v) == ["mismatch", "missing", "no_song_named", "same", "several", "write"])
check(f"dry run: the changed song, its types and the number of changed notes (got {dry['write']})",
      dry["write"] == [("Pol1z1stens0hn - Recht Kommt/Recht Kommt.txt", "F 1; R 3; mixed 2", 4)])
check("dry run: a song already as marked", dry["same"] == ["Same - Song/Same - Song.txt"])
check(f"dry run: marks that do not fit: named, left out (got {dry['mismatch']})",
      len(dry["mismatch"]) == 1 and dry["mismatch"][0][0] == "Wrong - Text.tsv" and "line 2" in dry["mismatch"][0][1])
check("dry run: no song in the library", dry["missing"] == ["Gone - Song.tsv"])
check("dry run: two song files with the same band and title: left out",
      [name for name, _ in dry["several"]] == ["Twice - Song.tsv"])
check("dry run: a .tsv without band and title rows", dry["no_song_named"] == ["notes.tsv"])
check("dry run: nothing written", not os.path.exists(out))

done = ant.run(marks, lib, out, apply=True)
target = os.path.join(out, "Pol1z1stens0hn - Recht Kommt", "Recht Kommt.txt")
check("apply: same results as the dry run", done == dry)
check("apply: the changed song file in <out>/<library folder>/",
      open(target, "rb").read() == SONG.replace(b": 0 4 0 Sample", b"F 0 4 0 Sample")
      .replace(b": 12", b"* 12").replace(b": 20", b"R 20").replace(b": 24", b"R 24"))
check("apply: only changed songs are written", sorted(os.listdir(out)) == ["Pol1z1stens0hn - Recht Kommt"])
check("apply: the library is only read", open(song_path, "rb").read() == SONG)

# the command line
env = dict(os.environ, LIBRARY_DIR=lib)
script = os.path.join(UTILITIES, "apply_note_types.py")
r = subprocess.run([sys.executable, script, "--marks", marks, "--out", os.path.join(root, "out2")],
                   capture_output=True, text=True, env=env)
check(f"command line: LIBRARY_DIR, a report per marks file, dry run (got {r.stdout!r} {r.stderr!r})",
      r.returncode == 0
      and "write    Pol1z1stens0hn - Recht Kommt/Recht Kommt.txt: F 1; R 3; mixed 2 (4 notes change)" in r.stdout
      and "missing  Gone - Song.tsv" in r.stdout and "dry run" in r.stdout
      and not os.path.exists(os.path.join(root, "out2")))
r = subprocess.run([sys.executable, script, "--marks", marks, "--library", os.path.join(root, "nothing")],
                   capture_output=True, text=True, env=env)
check(f"command line: a library that is not there (got {r.returncode}: {r.stderr!r})",
      r.returncode != 0 and "library" in r.stderr)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
