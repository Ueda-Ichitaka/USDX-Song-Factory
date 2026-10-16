#!/usr/bin/env python3
"""Tests for copy_reported_songs.py.

About me: plain assert-based checks (no pytest), host only, no network:

    python3 test_copy_reported_songs.py
"""

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import copy_reported_songs as crs  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


root = tempfile.mkdtemp()
lib = os.path.join(root, "lib")
stack_input = os.path.join(root, "input")
os.makedirs(stack_input)


def song(folder):
    d = os.path.join(lib, folder)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{folder}.txt"), "w", encoding="utf-8") as f:
        f.write("#TITLE:x\n#ARTIST:y\n: 0 4 0 la\nE\n")
    open(os.path.join(d, "a.mp3"), "w").close()


for folder in ("Toto - Africa", "ABBA - Chiquitita", "Tabaluga & Lilli (Peter Maffay) - Nessaja",
               "Nobody", "Busy - Song"):
    song(folder)

check("library_folder: 'band - song name', case-insensitive, the library's own spelling",
      crs.library_folder(lib, "toto", "AFRICA") == "Toto - Africa")
check("library_folder: no band -> the song name is the folder",
      crs.library_folder(lib, "", "Nobody") == "Nobody")
check("library_folder: only the first ' - ' separates band and song name",
      crs.library_folder(lib, "Tabaluga & Lilli (Peter Maffay)", "Nessaja")
      == "Tabaluga & Lilli (Peter Maffay) - Nessaja")
check("library_folder: unknown song -> None", crs.library_folder(lib, "Toto", "Rosanna") is None)

# the dashboard export (with header) and a second one without a header
export1 = os.path.join(root, "broken.csv")
with open(export1, "w", encoding="utf-8") as f:
    f.write("band,song name,category,description,lyrics_url,language,cover_url,genre,trait\n"
            "Toto,Africa,gap,starts late,https://lrclib.net/tracks/1,en,,rock,none\n"
            "Toto,Rosanna,gap,not in the library,,en,,default,none\n"
            "Busy,Song,async,already in the input,,en,,default,none\n")
export2 = os.path.join(root, "broken (1).csv")
with open(export2, "w", encoding="utf-8") as f:
    f.write("ABBA,Chiquitita,audio,no audio,,en,,pop,none\n"
            "Toto,Africa,gap,starts late,https://lrclib.net/tracks/1,en,,rock,none\n")

# the stack input: an older broken.csv already holding "Busy - Song", whose folder is there too
csv_path = os.path.join(stack_input, "broken.csv")
with open(csv_path, "w", encoding="utf-8") as f:
    f.write("band,song name,category,description,lyrics_url,language,cover_url\n"
            "Busy,Song,async,,,en,\n")
os.makedirs(os.path.join(stack_input, "busy - song"))

check("read_export: header or none, rows in the dashboard column order",
      crs.read_export(export1)[0] == ["Toto", "Africa", "gap", "starts late", "https://lrclib.net/tracks/1",
                                      "en", "", "rock", "none"]
      and crs.read_export(export2)[0][:3] == ["ABBA", "Chiquitita", "audio"])

before = open(csv_path, encoding="utf-8").read()
summary = crs.copy_songs([export1, export2], lib, stack_input, apply=False)
check(f"dry run: nothing copied or written (got {summary})",
      not os.path.isdir(os.path.join(stack_input, "Toto - Africa"))
      and open(csv_path, encoding="utf-8").read() == before
      and summary["copied"] == ["Toto - Africa", "ABBA - Chiquitita"])

summary = crs.copy_songs([export1, export2], lib, stack_input, apply=True)
rows = list(csv.reader(open(csv_path, encoding="utf-8")))
check(f"apply: reported folders copied with their files (got {summary})",
      os.path.isfile(os.path.join(stack_input, "Toto - Africa", "Toto - Africa.txt"))
      and os.path.isfile(os.path.join(stack_input, "ABBA - Chiquitita", "a.mp3"))
      and summary["copied"] == ["Toto - Africa", "ABBA - Chiquitita"])
check("apply: a folder already in the input (any case) is left alone",
      summary["present"] == ["Busy - Song"] and not os.listdir(os.path.join(stack_input, "busy - song"))
      and not os.path.isdir(os.path.join(stack_input, "Busy - Song")))
check("apply: a song not in the library is listed", summary["missing"] == ["Toto - Rosanna"])
check(f"apply: broken.csv in the dashboard format, rows merged once, older rows kept (got {rows})",
      rows[0] == crs.HEADER
      and rows[1] == ["Busy", "Song", "async", "", "", "en", "", "default", "none"]
      and rows[2:] == [["Toto", "Africa", "gap", "starts late", "https://lrclib.net/tracks/1", "en", "",
                        "rock", "none"],
                       ["Toto", "Rosanna", "gap", "not in the library", "", "en", "", "default", "none"],
                       ["ABBA", "Chiquitita", "audio", "no audio", "", "en", "", "pop", "none"]]
      and summary["rows_added"] == 3)
check("apply: the old broken.csv is kept as a backup",
      any(n.startswith("broken.csv.bak-") for n in os.listdir(stack_input)))

summary = crs.copy_songs([export1, export2], lib, stack_input, apply=True)
check(f"running again copies and adds nothing (got {summary})",
      summary["copied"] == [] and summary["rows_added"] == 0)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
