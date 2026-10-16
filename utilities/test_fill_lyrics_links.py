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

# the free-text search: the artist is often named differently there
# ("Der König der Löwen" for "Disney's Der König der Löwen"), so title and
# the length of the song's own audio decide
free_hits = [
    {"id": 7, "artistName": "Der König der Löwen", "trackName": "Hakuna Matata", "duration": 721.7},
    {"id": 8, "artistName": "Der König Der Löwen", "trackName": "Hakuna Matata", "duration": 196.0},
    {"id": 9, "artistName": "Der König der Löwen", "trackName": "Hakuna Matata", "duration": 213.3,
     "syncedLyrics": "[00:01.00] x"},
    {"id": 10, "artistName": "Somebody", "trackName": "Hakuna Matata Remix", "duration": 213.0},
]
LION = "Disney’s Der König der Löwen"
check("pick_by_title: title and length within 4 s, a word of the band in the artist",
      fl.pick_by_title(free_hits, LION, "Hakuna Matata", 214.0) == 9)
check("pick_by_title: no hit of that length -> None",
      fl.pick_by_title(free_hits, LION, "Hakuna Matata", 300.0) is None)
check("pick_by_title: without the audio length -> None (nothing to verify)",
      fl.pick_by_title(free_hits, LION, "Hakuna Matata", None) is None)
# same title and length by chance: Queen's "Jealousy" for Marina's
check("pick_by_title: no word of the band in the hit -> not taken",
      fl.pick_by_title([{"id": 12, "artistName": "Queen", "trackName": "Jealousy", "duration": 202.0}],
                       "Marina And The Diamonds", "Jealousy", 200.0) is None)
check("pick_by_title: a word of the band in the hit's title counts too",
      fl.pick_by_title([{"id": 13, "artistName": "Jasmin Tabatabai", "trackName": "Ich will keinen Mann (Hercules)",
                         "duration": 140.5}], "Disney’s Hercules", "Ich will keinen Mann", 141.0) == 13)
check("pick_by_title: filler words of the band (the, and, feat) do not count",
      fl.pick_by_title([{"id": 14, "artistName": "The Others and Friends", "trackName": "Song", "duration": 100.0}],
                       "The Band feat. And", "Song", 100.0) is None)

# lrclib.net answers 429/503 when it is overloaded: three more tries, waiting
# 5, 10 and 20 s, then the search counts as empty


class Answer:
    def __init__(self, status, data=None):
        self.status_code, self.ok, self._data = status, status == 200, data

    def json(self):
        return self._data


def fake_get(statuses):
    answers = iter(statuses)
    return lambda *args, **kwargs: next(answers)


waits = []
check("get_json: a 503 and a 429 are tried again",
      fl.get_json("u", {}, {}, get=fake_get([Answer(503), Answer(429), Answer(200, [1])]),
                  sleep=waits.append) == [1] and waits == [5, 10])
waits = []
check("get_json: still overloaded after three more tries -> empty",
      fl.get_json("u", {}, {}, get=fake_get([Answer(503)] * 4), sleep=waits.append) == []
      and waits == [5, 10, 20])
waits = []
check("get_json: another error is not tried again",
      fl.get_json("u", {}, {}, get=fake_get([Answer(404)]), sleep=waits.append) == [] and waits == [])

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
                 genius_search=fake_genius, lrclib_free_search=lambda q: [], apply=True, pause=0)
rows = list(csv.reader(open(csv_path, encoding="utf-8")))
check(f"fill: an empty lyrics_url gets the lrclib.net link (got {rows[1]})",
      rows[1][4] == "https://lrclib.net/tracks/2")
check("fill: a row that has a link is not looked up or changed",
      rows[2][4] == "https://genius.com/x" and ("ABBA", "Chiquitita") not in calls)
check(f"fill: nothing found -> stays empty, reported (got {result})",
      rows[3][4] == "" and result == {"filled": 1, "not_found": ["Nobody - Nothing"]})


# a row nothing else finds goes to the free-text search, first with
# artist and title, then with the title alone
with open(csv_path, "w", encoding="utf-8") as f:
    f.write("band,song name,category,description,lyrics_url,language,cover_url,genre,trait\n"
            "Toto,Africa,async,drift,,en,,pop,none\n")
queries = []


def fake_free(query):
    queries.append(query)
    return [dict(hits[1], artistName="TOTO & friends", id=11)] if query == "africa" else []


result = fl.fill(csv_path, os.path.join(d, "input"), lrclib_search=lambda b, t: [],
                 genius_search=fake_genius, lrclib_free_search=fake_free, apply=True, pause=0)
check(f"fill: the free-text search fills what the strict searches miss (got {queries})",
      list(csv.reader(open(csv_path, encoding="utf-8")))[1][4] == "https://lrclib.net/tracks/11"
      and queries == ["toto africa", "africa"] and result["filled"] == 1)


def broken_search(band, title):
    raise OSError("network down")


with open(csv_path, "w", encoding="utf-8") as f:
    f.write("band,song name,category,description,lyrics_url,language,cover_url,genre,trait\n"
            "Toto,Africa,async,drift,,en,,pop,none\n")
result = fl.fill(csv_path, os.path.join(d, "input"), lrclib_search=broken_search,
                 genius_search=broken_search, lrclib_free_search=lambda q: [], apply=True, pause=0)
check(f"fill: a network error leaves the row empty (got {result})",
      result["filled"] == 0 and list(csv.reader(open(csv_path, encoding="utf-8")))[1][4] == "")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
