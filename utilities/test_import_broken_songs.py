#!/usr/bin/env python3
"""Tests for import_broken_songs.py.

About me: plain assert-based checks (no pytest), host only, no network:

    python3 test_import_broken_songs.py
"""

import csv
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import import_broken_songs as ib  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


root = tempfile.mkdtemp()
lib = os.path.join(root, "lib")
stack_input = os.path.join(root, "input")
os.makedirs(stack_input)


def song(folder, txt, header):
    d = os.path.join(lib, folder)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, txt), "w", encoding="utf-8") as f:
        f.write(header + ": 0 4 0 la\nE\n")
    open(os.path.join(d, "a.mp3"), "w").close()


song("Toto - Africa", "Toto - Africa.txt", "#TITLE:Africa\n#ARTIST:Toto\n#LANGUAGE:English\n#GENRE:Pop\n#GAP:18220\n#BPM:232\n")
song("Disney - Song", "Disney - Song.txt", "#TITLE:Song\n#ARTIST:Disney\n#LANGUAGE:Deutsch\n#GENRE:Soundtrack\n")
song("Two - Songs", "second.txt", "#TITLE:Second\n#ARTIST:Two\n#LANGUAGE:German\n")
song("Fine - Song", "Fine - Song.txt", "#TITLE:Song\n#ARTIST:Fine\n")
song("Old - Song", "Old - Song.txt", "#TITLE:Song\n#ARTIST:Old\n#LANGUAGE:English\n")
results = os.path.join(root, "results.jsonl")
with open(results, "w", encoding="utf-8") as f:
    for row in (
        {"name": "Toto - Africa", "folder": "Toto - Africa", "txt": "Toto - Africa.txt", "verdict": "drifts",
         "offset_s": -1.18, "drift_s": 1.91, "agree": 0.97, "gap_ms": 18220.0, "bpm": 232.0,
         "gap_fix_ms": 17040, "gap_fix_with_bpm_ms": 17099, "bpm_fix": 234.1},
        {"name": "Disney - Song", "verdict": "#GAP off", "offset_s": -20.95, "drift_s": -0.05,
         "agree": 1.0, "gap_ms": 22180.0, "bpm": 256.0, "gap_fix_ms": 1230},
        {"name": "Two - Songs [second]", "verdict": "#GAP off", "offset_s": -0.5, "drift_s": 0.0,
         "agree": 0.9, "gap_ms": 1000.0, "bpm": 300.0, "gap_fix_ms": 500},
        {"name": "Fine - Song", "verdict": "good"},
        {"name": "Old - Song", "verdict": "#GAP off", "offset_s": 1.0, "drift_s": 0.0, "agree": 0.9,
         "gap_ms": 0.0, "bpm": 300.0, "gap_fix_ms": 1000},
    ):
        f.write(json.dumps(row) + "\n")

check("folder_and_txt: from the row, else from the name 'folder [txt]', else the folder's song file",
      ib.folder_and_txt({"name": "A", "folder": "A", "txt": "x.txt"}, lib) == ("A", "x.txt")
      and ib.folder_and_txt({"name": "Two - Songs [second]"}, lib) == ("Two - Songs", "second.txt")
      and ib.folder_and_txt({"name": "Disney - Song"}, lib) == ("Disney - Song", "Disney - Song.txt"))

# an older broken.csv without genre/trait, already holding "Old - Song"
csv_path = os.path.join(stack_input, "broken.csv")
with open(csv_path, "w", encoding="utf-8") as f:
    f.write("band,song name,category,description,lyrics_url,language,cover_url\n"
            "Old,Song,gap,,,en,\n")
os.makedirs(os.path.join(stack_input, "Disney - Song"))  # already in the input: not copied again

summary = ib.import_songs(results, lib, stack_input, categories=("gap", "drift"), apply=False)
check(f"dry run: nothing copied or written (got {summary})",
      not os.path.isdir(os.path.join(stack_input, "Toto - Africa"))
      and open(csv_path, encoding="utf-8").read().startswith("band,song name,category,description,lyrics_url,language,cover_url\n"))

summary = ib.import_songs(results, lib, stack_input, categories=("gap", "drift"), apply=True)
rows = list(csv.reader(open(csv_path, encoding="utf-8")))
by_name = {(r[0], r[1]): r for r in rows[1:]}
check(f"import: #GAP off and drift songs copied, an existing folder left alone (got {summary})",
      os.path.isfile(os.path.join(stack_input, "Toto - Africa", "Toto - Africa.txt"))
      and os.path.isfile(os.path.join(stack_input, "Two - Songs", "second.txt"))
      and not os.listdir(os.path.join(stack_input, "Disney - Song"))
      and not os.path.isdir(os.path.join(stack_input, "Fine - Song"))
      and summary["copied"] == ["Toto - Africa", "Two - Songs", "Old - Song"])
check(f"import: broken.csv in the dashboard format, older rows kept with defaults (got {rows[0]}, {rows[1]})",
      rows[0] == ib.HEADER and rows[1] == ["Old", "Song", "gap", "", "", "en", "", "default", "none"])
check(f"import: a drift row (got {by_name.get(('Toto', 'Africa'))})",
      by_name[("Toto", "Africa")] == ["Toto", "Africa", "async",
                                      "library check: lyrics drift +1.91 s over the song; fix #GAP 18220 -> 17099, #BPM 232 -> 234.1",
                                      "", "en", "", "pop", "none"])
check(f"import: a #GAP row (got {by_name.get(('Disney', 'Song'))})",
      by_name[("Disney", "Song")] == ["Disney", "Song", "gap",
                                      "library check: lyrics 20.95 s too late; fix #GAP 22180 -> 1230",
                                      "", "de", "", "default", "none"])
check("import: no second row for a song that has one",
      sum(1 for r in rows[1:] if (r[0], r[1]) == ("Old", "Song")) == 1 and summary["rows_added"] == 3)

summary = ib.import_songs(results, lib, stack_input, categories=("gap",), apply=True)
check(f"import: running again adds nothing (got {summary})",
      summary["rows_added"] == 0 and summary["copied"] == [])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
