#!/usr/bin/env python3
"""Tests for songfile.py.

About me: plain assert-based checks (no pytest), host only:

    python3 test_songfile.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import songfile  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


_dir = tempfile.mkdtemp()


def write(name, text, encoding="utf-8"):
    path = os.path.join(_dir, name)
    with open(path, "w", encoding=encoding) as f:
        f.write(text)
    return path


_song = write("a.txt", "#TITLE:Märchen\n#ARTIST:Disney\n#LANGUAGE:German\n#GENRE:Pop\n#BPM:256\n"
                       "#GAP:22180\n: 0 4 0 Mär\n: 4 4 0 chen \n- 10\n: 12 4 0 la \nE\n")
info = songfile.read(_song)
check(f"read: header tags (got {info.tags})",
      info.tags["TITLE"] == "Märchen" and info.tags["ARTIST"] == "Disney" and info.tags["GAP"] == "22180")
check(f"read: note count, no duet, absolute timing (got {info.notes}, {info.duet}, {info.relative})",
      info.notes == 3 and not info.duet and not info.relative)

_cp = write("b.txt", "#TITLE:Café\n#ARTIST:X\n#RELATIVE:yes\nP1\n: 0 4 0 a\n- 4 6\nE\n", encoding="cp1252")
info = songfile.read(_cp)
check(f"read: a cp1252 file, duet, #RELATIVE:yes (got {info.tags.get('TITLE')})",
      info.tags["TITLE"] == "Café" and info.duet and info.relative)

# USDX reads the second number of a line break only with #RELATIVE:yes
# (USong.pas, TSong.LoadSong); without the tag the song plays as absolute
_abs = write("c.txt", "#TITLE:T\n#ARTIST:A\n: 0 4 0 a\n- 30 31\n: 32 4 0 b\nE\n")
check("read: '- 30 31' line breaks without #RELATIVE:yes are absolute timing",
      not songfile.read(_abs).relative)

check("language_code: dashboard codes from #LANGUAGE",
      songfile.language_code("German") == "de" and songfile.language_code("Englisch") == "en"
      and songfile.language_code("English, Korean (romanized)") == "en"
      and songfile.language_code("Español") == "es")
check("language_code: romanized and unknown languages -> empty",
      songfile.language_code("Japanese (romanized)") == "" and songfile.language_code("Suisse") == ""
      and songfile.language_code("") == "")
check("genre_code: only an exact dashboard genre code, else 'default'",
      songfile.genre_code("Pop") == "pop" and songfile.genre_code("gothic") == "gothic"
      and songfile.genre_code("Soundtrack") == "default" and songfile.genre_code("") == "default")
check("generated_by_stack: #CREATOR UltraSinger",
      songfile.generated_by_stack("#CREATOR:UltraSinger [GitHub]\n") and not songfile.generated_by_stack("#CREATOR:deeba\n"))
write("lyrics.txt", "eins\n")
write("language.txt", "de\n")
# USDX needs #TITLE and #ARTIST in a song file ("not Ultrastar txt" otherwise)
write("license.txt", "Creative Commons Attribution 3.0\n")
write("README.txt", "#TITLE:only a title\n")
check("song_txts: admin files and .txt files without #TITLE and #ARTIST are not song files",
      [os.path.basename(p) for p in songfile.song_txts(_dir)] == ["a.txt", "b.txt", "c.txt"])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
