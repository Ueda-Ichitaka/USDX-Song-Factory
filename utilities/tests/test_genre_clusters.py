#!/usr/bin/env python3
"""Tests for genre_clusters.py.

About me: plain assert-based checks like the other utility tests, on a temp
library; the MusicBrainz genre list is a small fake, no network. How a
genre becomes bases is tested in test_genre_bases.py. Run:
python3 tests/test_genre_clusters.py (from utilities/)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import genre_bases  # noqa: E402
import genre_clusters as gc  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


MB = ["alternative metal", "black metal", "symphonic black metal", "metal", "rock", "pop",
      "thrash metal", "nu metal", "hip hop", "gothic metal"]

# --------------------------------------------------------------------------
# split_genres(): the genres of one #GENRE value
# --------------------------------------------------------------------------

check("split_genres: commas, spaces trimmed, empty parts dropped",
      gc.split_genres(" Rock,Pony , ,Hip-Hop ") == ["Rock", "Pony", "Hip-Hop"])
check("split_genres: placeholders are left out",
      gc.split_genres("Unknown") == [] and gc.split_genres("Rock, Other") == ["Rock"])

# --------------------------------------------------------------------------
# spelling_rows(): each spelling with its bases and why
# --------------------------------------------------------------------------

counts = {"Metal": 5, "Alternative Metal": 2, "Hip Hop": 1, "rock": 1, "Rock": 3,
          "Trash Metal": 1, "Thrash Metal": 4, "K-Pop": 2}
rows = gc.spelling_rows(counts, genre_bases.Splitter(MB, counts))
by = {r["spelling"]: r for r in rows}
check(f"spelling_rows: bases and why per spelling (got {by['Alternative Metal']})",
      by["Alternative Metal"]["bases"] == ["Alternative", "Metal"] and by["Alternative Metal"]["why"] == "split")
check(f"spelling_rows: a base as it is -> unchanged (got {by['Metal']}, {by['K-Pop']})",
      by["Metal"]["why"] == "unchanged" and by["K-Pop"]["why"] == "unchanged")
check(f"spelling_rows: case only -> spelling (got {by['rock']})",
      by["rock"]["bases"] == ["Rock"] and by["rock"]["why"] == "spelling")
check(f"spelling_rows: fixes named (got {by['Trash Metal']}, {by['Hip Hop']})",
      by["Trash Metal"]["why"] == "typo? trash -> thrash" and by["Hip Hop"]["why"] == "kept whole: Hip-Hop")
_drop = gc.spelling_rows({"Easy Listening": 1}, genre_bases.Splitter(MB))[0]
check(f"spelling_rows: a dropped genre has no bases (got {_drop})",
      _drop["bases"] == [] and _drop["why"] == "dropped: Easy Listening")
check("spelling_rows: most songs first",
      [r["songs"] for r in rows] == sorted((r["songs"] for r in rows), reverse=True))

# --------------------------------------------------------------------------
# rebuild(): a #GENRE value from the spelling -> bases mapping
# --------------------------------------------------------------------------

mapping = {"Alternative Metal": "Alternative, Metal", "Nu-Metal": "Nu, Metal", "rock": "Rock"}
check(f"rebuild: parts replaced by their bases, duplicates dropped (got {gc.rebuild('Alternative Metal, Nu-Metal, rock', mapping)})",
      gc.rebuild("Alternative Metal, Nu-Metal, rock", mapping) == "Alternative, Metal, Nu, Rock")
check("rebuild: '-' drops a spelling",
      gc.rebuild("Pop, Easy Listening", {"Easy Listening": "-"}) == "Pop"
      and gc.rebuild("Easy Listening", {"Easy Listening": "-"}) == "")
check("rebuild: parts without a mapping stay, ', ' joins",
      gc.rebuild("Rock,Pony", {}) == "Rock, Pony")

# --------------------------------------------------------------------------
# run(): the library -> library-genres, genre-clusters, genre-overview
# --------------------------------------------------------------------------

root = tempfile.mkdtemp()
lib = os.path.join(root, "lib")


def song(name, genre):
    folder = os.path.join(lib, name)
    os.makedirs(folder)
    header = f"#ARTIST:{name}\n#TITLE:{name}\n" + (f"#GENRE:{genre}\n" if genre is not None else "")
    with open(os.path.join(folder, name + ".txt"), "w", encoding="utf-8") as f:
        f.write(header + "#BPM:300\n: 0 4 0 la\nE\n")


song("A", "Nu Metal")
song("B", "Nu-Metal, Rock")
song("C", "Symphonic Black Metal, Black Metal")
song("D", "Trash Metal")
song("E", "Metal, Thrash Metal")
song("F", None)
song("G", "Unknown")
song("H", "Easy Listening")
fixes_file = os.path.join(root, "genre-song-fixes.tsv")
with open(fixes_file, "w", encoding="utf-8") as f:
    f.write("band\ttitle\tgenre\tnote\n\"H\"\th\tPop, Comedy\tLibreOffice quotes\nZ\tZ\t\tempty genre: ignored\n")
check(f"load_fixes: (band, title) lower case -> genre (got {gc.load_fixes(fixes_file)})",
      gc.load_fixes(fixes_file) == {("h", "h"): "Pop, Comedy"}
      and gc.load_fixes(os.path.join(root, "none.tsv")) == {})
out = os.path.join(root, "out")
summary = gc.run(lib, out, MB, gc.load_fixes(fixes_file))


def _tsv(name):
    with open(os.path.join(out, name), encoding="utf-8") as f:
        return [line.rstrip("\n").split("\t") for line in f]


rows = _tsv("genre-clusters.tsv")
check(f"run: genre-clusters.tsv, one row per spelling with its bases (got {rows})",
      rows[0] == ["genre", "spelling", "songs", "why"]
      and ["Nu, Metal", "Nu Metal", "1", "split"] in rows
      and ["Thrash, Metal", "Trash Metal", "1", "typo? trash -> thrash"] in rows
      and ["Metal", "Metal", "1", "unchanged"] in rows
      and ["-", "Easy Listening", "1", "dropped: Easy Listening"] in rows
      and not any(r[1] == "Unknown" for r in rows))
over = _tsv("genre-overview.tsv")
check(f"run: genre-overview.tsv, songs per base counted once per song (got {over})",
      over[0] == ["base", "songs", "from"]
      and over[1][:2] == ["Metal", "5"]
      and ["Black", "1", "Black Metal (1), Symphonic Black Metal (1)"] in over
      and ["Nu", "2", "Nu Metal (1), Nu-Metal (1)"] in over
      and ["Comedy", "1", "song fix: H - H"] in over)
check("run: library-genres.tsv lists every song",
      len(_tsv("library-genres.tsv")) == 9)
check(f"run: the summary (got {summary})",
      summary == {"songs": 8, "with genre": 6, "spellings": 9, "bases": 8,
                  "spellings to change": 7, "songs to change": 6, "song fixes": 1})

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
