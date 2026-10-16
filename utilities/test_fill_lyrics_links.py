#!/usr/bin/env python3
"""Tests for fill_lyrics_links.py.

About me: plain assert-based checks (no pytest), host only, no network (the
searches are replaced by fakes):

    python3 test_fill_lyrics_links.py
"""

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fill_lyrics_links as fl  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


hits = [
    {"id": 1, "artistName": "Toto", "trackName": "Africa", "duration": 301, "syncedLyrics": None},
    {"id": 2, "artistName": "Toto", "trackName": "Africa", "duration": 296, "syncedLyrics": "[00:01.00] x"},
    {"id": 3, "artistName": "Toto", "trackName": "Africa (Live)", "duration": 295, "syncedLyrics": "[00:01.00] x"},
    {"id": 4, "artistName": "Weezer", "trackName": "Africa", "duration": 295, "syncedLyrics": "[00:01.00] x"},
    {"id": 5, "artistName": "Toto", "trackName": "Africa", "duration": 295, "instrumental": True},
]
check("pick_lrclib: same artist/title, length within 4 s, synced first",
      fl.pick_lrclib(hits, "Toto", "Africa", 295.5) == 2)
check("pick_lrclib: no hit within 4 s of the audio -> None",
      fl.pick_lrclib(hits[:2], "Toto", "Africa", 250.0) is None)
check("pick_lrclib: bracket parts like (Album Version) do not count",
      fl.pick_lrclib([dict(hits[1], trackName="Africa (Album Version)")], "Toto", "Africa", 296.0) == 2)
check("pick_lrclib: without the audio length a name match is enough",
      fl.pick_lrclib(hits[:2], "Toto", "Africa", None) == 2)

genius_hits = [{"result": {"primary_artist": {"name": "Weezer"}, "title": "Africa", "url": "u1"}},
               {"result": {"primary_artist": {"name": "TOTO"}, "title": "Africa", "url": "u2"}}]
check("pick_genius: the hit with the right artist and title",
      fl.pick_genius(genius_hits, "Toto", "Africa") == "u2")

# a stack input folder with one song, and a broken.csv
d = tempfile.mkdtemp()
song = os.path.join(d, "input", "Toto - Africa")
os.makedirs(song)
with open(os.path.join(song, "Toto - Africa.txt"), "w", encoding="utf-8") as f:
    f.write("#TITLE:Africa\n#ARTIST:Toto\n#MP3:a.mp3\n: 0 4 0 la\nE\n")
check("find_folder: the input folder whose song file has that artist and title",
      fl.find_folder(os.path.join(d, "input"), "Toto", "Africa") == song)
csv_path = os.path.join(d, "input", "broken.csv")
with open(csv_path, "w", encoding="utf-8") as f:
    f.write("band,song name,category,description,lyrics_url,language,cover_url,genre,trait\n"
            "Toto,Africa,async,drift,,en,,pop,none\n"
            "ABBA,Chiquitita,gap,,https://genius.com/x,en,,pop,none\n"
            "Nobody,Nothing,gap,,,en,,default,none\n")
calls = []


def fake_lrclib(band, title):
    calls.append((band, title))
    return hits if band == "Toto" else []


def fake_genius(band, title):
    return None


fl.audio_seconds = lambda folder: 296.0
result = fl.fill(csv_path, os.path.join(d, "input"), lrclib_search=fake_lrclib,
                 genius_search=fake_genius, apply=True, pause=0)
rows = list(csv.reader(open(csv_path, encoding="utf-8")))
check(f"fill: an empty lyrics_url gets the lrclib.net link (got {rows[1]})",
      rows[1][4] == "https://lrclib.net/tracks/2")
check("fill: a row that has a link is not looked up or changed",
      rows[2][4] == "https://genius.com/x" and ("ABBA", "Chiquitita") not in calls)
check(f"fill: nothing found -> stays empty, reported (got {result})",
      rows[3][4] == "" and result == {"filled": 1, "not_found": ["Nobody - Nothing"]})


def broken_search(band, title):
    raise OSError("network down")


with open(csv_path, "w", encoding="utf-8") as f:
    f.write("band,song name,category,description,lyrics_url,language,cover_url,genre,trait\n"
            "Toto,Africa,async,drift,,en,,pop,none\n")
result = fl.fill(csv_path, os.path.join(d, "input"), lrclib_search=broken_search,
                 genius_search=broken_search, apply=True, pause=0)
check(f"fill: a network error leaves the row empty (got {result})",
      result["filled"] == 0 and list(csv.reader(open(csv_path, encoding="utf-8")))[1][4] == "")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
