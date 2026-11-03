#!/usr/bin/env python3
"""Tests for copy_genre_songs.py.

About me: plain assert-based checks (no pytest), host only, no network:

    python3 tests/test_copy_genre_songs.py (from utilities/)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy_genre_songs as cgs  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


root = tempfile.mkdtemp()
lib = os.path.join(root, "lib")
stack_input = os.path.join(root, "input")
os.makedirs(stack_input)


def write(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


hb = os.path.join(lib, "AC-DC - Hells Bells", "HellsBells-638.txt")
write(hb, b"#TITLE:Hells Bells\n#ARTIST:AC/DC\n#GENRE:Hard, Rock\n#BPM:300\n: 0 4 0 la\nE\n")
write(os.path.join(lib, "AC-DC - Hells Bells", "AC-DC - Hells Bells.mp3"), b"audio")
write(os.path.join(lib, "AC-DC - Hells Bells", "language.txt"), b"en\n")
write(os.path.join(lib, "AC-DC - Notes", "notes.txt"), b"just notes\n")
edits = os.path.join(stack_input, "genre-edits.tsv")
with open(edits, "w", encoding="utf-8") as f:
    f.write("AC-DC - Hells Bells/HellsBells-638.txt\tRock, Hard\n"
            "AC-DC - Hells Bells/language.txt\tRock, Hard\n"
            "AC-DC - Notes/notes.txt\tRock\n"
            "Gone - Song/Gone.txt\tPop\n\n")

check(f"read_edits: the song file of each row (got {cgs.read_edits(edits)})",
      cgs.read_edits(edits) == [("AC-DC - Hells Bells", "HellsBells-638.txt"),
                                ("AC-DC - Hells Bells", "language.txt"),
                                ("AC-DC - Notes", "notes.txt"), ("Gone - Song", "Gone.txt")])

dry = cgs.copy_songs(edits, lib, stack_input, apply=False)
check(f"dry run: what would be copied, nothing written (got {dry})",
      dry == {"copied": ["AC-DC - Hells Bells/HellsBells-638.txt"],
              "not_song": ["AC-DC - Hells Bells/language.txt", "AC-DC - Notes/notes.txt"],
              "missing": ["Gone - Song/Gone.txt"]}
      and not os.path.exists(os.path.join(stack_input, "genres")))

done = cgs.copy_songs(edits, lib, stack_input, apply=True)
target = os.path.join(stack_input, "genres", "AC-DC - Hells Bells", "HellsBells-638.txt")
check("apply: only the song file is copied into genres/<folder>/, no media, no language.txt",
      done == dry and open(target, "rb").read() == open(hb, "rb").read()
      and sorted(os.listdir(os.path.dirname(target))) == ["HellsBells-638.txt"])
check("apply: the file keeps its modification time", int(os.path.getmtime(target)) == int(os.path.getmtime(hb)))

write(hb, b"#TITLE:Hells Bells\n#ARTIST:AC/DC\n#GENRE:Metal\n#BPM:300\n: 0 4 0 la\nE\n")
cgs.copy_songs(edits, lib, stack_input, apply=True)
check("apply again: the copy follows the library's current song file",
      b"#GENRE:Metal" in open(target, "rb").read())

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
