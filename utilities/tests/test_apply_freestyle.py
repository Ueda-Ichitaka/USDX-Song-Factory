#!/usr/bin/env python3
"""Tests for apply_freestyle.py.

About me: plain assert-based checks (no pytest), host only, temp library,
marks and output folders - the real stack/input and library are never
touched. Run (from utilities/): python3 tests/test_apply_freestyle.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
UTILITIES = os.path.dirname(HERE)
sys.path.insert(0, UTILITIES)

import apply_freestyle as af  # noqa: E402
import freestyle as fs  # noqa: E402

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
SAME = b"#ARTIST:Same\n#TITLE:Song\n#BPM:300\n#GAP:0\nF 0 4 0 Intro\n- 6\n: 8 4 0 Sung\nE\n"
write(os.path.join(lib, "Same - Song", "Same - Song.txt"), SAME)
for folder in ("Twice - A", "Twice - B"):
    write(os.path.join(lib, folder, "t.txt"), b"#ARTIST:Twice\n#TITLE:Song\n: 0 4 0 x\nE\n")

fs.write_marks(os.path.join(marks, "Pol1z1stens0hn - Recht Kommt.tsv"), "Pol1z1stens0hn", "Recht Kommt",
               [(1, "Sample"), (3, "Recht kommt")])
fs.write_marks(os.path.join(marks, "Same - Song.tsv"), "Same", "Song", [(1, "Intro")])
fs.write_marks(os.path.join(marks, "Wrong - Text.tsv"), "Pol1z1stens0hn", "Recht Kommt", [(2, "Recht")])
fs.write_marks(os.path.join(marks, "Gone - Song.tsv"), "Gone", "Song", [(1, "x")])
fs.write_marks(os.path.join(marks, "Twice - Song.tsv"), "Twice", "Song", [(1, "x")])
write(os.path.join(marks, "notes.tsv"), b"just\tnotes\n")

check("number_ranges: runs of line numbers", af.number_ranges([1, 2, 3, 5, 7, 8]) == "1-3, 5, 7-8")

dry = af.run(marks, lib, out, apply=False)
check(f"dry run: one result per marks file (got {sorted(dry)})",
      sorted(dry) == ["mismatch", "missing", "no_song_named", "same", "several", "write"])
check(f"dry run: the changed song and what changes (got {dry['write']})",
      dry["write"] == [("Pol1z1stens0hn - Recht Kommt/Recht Kommt.txt", [1, 3], 3, 0)])
check("dry run: a song already as marked", dry["same"] == ["Same - Song/Same - Song.txt"])
check(f"dry run: line number and text differ: named, left out (got {dry['mismatch']})",
      len(dry["mismatch"]) == 1 and dry["mismatch"][0][0] == "Wrong - Text.tsv" and "line 2" in dry["mismatch"][0][1])
check("dry run: no song in the library", dry["missing"] == ["Gone - Song.tsv"])
check("dry run: two song files with the same band and title: left out",
      [name for name, _ in dry["several"]] == ["Twice - Song.tsv"])
check("dry run: a .tsv without band and title rows", dry["no_song_named"] == ["notes.tsv"])
check("dry run: nothing written", not os.path.exists(out))

done = af.run(marks, lib, out, apply=True)
target = os.path.join(out, "Pol1z1stens0hn - Recht Kommt", "Recht Kommt.txt")
check("apply: same results as the dry run", done == dry)
check("apply: the changed song file in <out>/<library folder>/",
      open(target, "rb").read() == SONG.replace(b": 0 4 0 Sample", b"F 0 4 0 Sample")
      .replace(b": 20", b"F 20").replace(b": 24", b"F 24"))
check("apply: only changed songs are written", sorted(os.listdir(out)) == ["Pol1z1stens0hn - Recht Kommt"])
check("apply: the library is only read", open(song_path, "rb").read() == SONG)

# the command line
env = dict(os.environ, LIBRARY_DIR=lib)
r = subprocess.run([sys.executable, os.path.join(UTILITIES, "apply_freestyle.py"), "--marks", marks,
                    "--out", os.path.join(root, "out2")], capture_output=True, text=True, env=env)
check(f"command line: LIBRARY_DIR, a report per marks file, dry run (got {r.stdout!r} {r.stderr!r})",
      r.returncode == 0 and "write    Pol1z1stens0hn - Recht Kommt/Recht Kommt.txt: lines 1, 3 freestyle" in r.stdout
      and "missing  Gone - Song.tsv" in r.stdout and "dry run" in r.stdout
      and not os.path.exists(os.path.join(root, "out2")))
r = subprocess.run([sys.executable, os.path.join(UTILITIES, "apply_freestyle.py"), "--marks", marks,
                    "--library", os.path.join(root, "nothing")], capture_output=True, text=True, env=env)
check(f"command line: a library that is not there (got {r.returncode}: {r.stderr!r})",
      r.returncode != 0 and "library" in r.stderr)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
