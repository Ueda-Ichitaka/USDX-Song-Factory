#!/usr/bin/env python3
"""Tests for normalize_genres.sh.

About me: plain assert-based checks like the other utility tests; runs the
bash script on a temp library with a hand-written genre-clusters.tsv and
song list. Run: python3 test_normalize_genres.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "normalize_genres.sh")

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


root = tempfile.mkdtemp()
lib = os.path.join(root, "lib")


def song(name, data: bytes) -> str:
    path = os.path.join(lib, name, name + ".txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def plain(band, genre):
    return f"#ARTIST:{band}\n#TITLE:{band}\n#GENRE:{genre}\n#BPM:300\n: 0 4 0 la\nE\n".encode()


a = song("A", plain("A", "Nu-Metal, Rock"))
b = song("B", plain("B", "Nu metal, Nu Metal"))
c = song("C", "#ARTIST:C\n#TITLE:Café\n#GENRE:Ghotic Metal\n#BPM:300\n: 0 4 0 la\nE\n".encode("cp1252"))
d = song("D", b"\xef\xbb\xbf#ARTIST:D\r\n#TITLE:D\r\n#genre:Rock,Pony\r\n#BPM:300\r\n: 0 4 0 la\r\nE\r\n")
e = song("E", plain("E", "Thrash Metal"))
f_ = song("F", b"#ARTIST:F\n#TITLE:F\n#BPM:300\n: 0 4 0 la\nE\n")
g = song("G", plain("G", "Unknown"))
h = song("H", plain("H", "Trash Metal"))
i = song("I", plain("I", "Hip hop, Rap"))
j = song("J", plain("J", "Symphonic Black Metal, Black Metal, Metal"))
k = song("K", plain("K", "Pop, Easy Listening"))
m = song("M", b"#ARTIST:M\n#TITLE:M\n#GENRE:Easy Listening\n#BPM:300\n: 0 4 0 la\nE\n")
n = song("N", b"\xef\xbb\xbf#GENRE:Easy Listening\r\n#ARTIST:N\r\n#TITLE:N\r\n: 0 4 0 la\r\nE\r\n")
songs = [a, b, c, d, e, f_, g, h, i, j, k, m, n]
originals = {p: read(p) for p in songs}

clusters = os.path.join(root, "genre-clusters.tsv")
with open(clusters, "w", encoding="utf-8") as f:
    f.write("genre\tspelling\tsongs\twhy\n"
            "Nu Metal\tNu Metal\t2\tproposed\n"
            "Nu Metal\tNu-Metal\t1\tspelling\n"
            "Nu Metal\tNu metal\t1\tspelling\n"
            "Rock\tRock\t2\tproposed\n"
            "Gothic Metal\tGhotic Metal\t1\ttypo? ghotic -> gothic\n"
            "Thrash Metal\tThrash Metal\t1\tproposed\n"
            "\tTrash Metal\t1\ttypo? trash -> thrash\n"
            "\"Hip-Hop\"\tHip hop\t1\tspelling\n"
            "Symphonic, Black, Metal\tSymphonic Black Metal\t1\tsplit\n"
            "\"Black, Metal\"\tBlack Metal\t1\tsplit\n"
            "-\tEasy Listening\t2\tdropped: Easy Listening\n")
song_list = os.path.join(root, "library-genres.tsv")
with open(song_list, "w", encoding="utf-8") as f:
    f.write("band\ttitle\tgenre\tpath\n")
    for p in songs + [os.path.join(lib, "gone.txt")]:
        # as genre_clusters.py writes it: a song without a genre has an empty cell
        f.write(f"x\t{os.path.basename(p)}\t{'' if p == f_ else 'something'}\t{p}\n")


def run(*args):
    return subprocess.run(["bash", SCRIPT, *args, "--songs", song_list, clusters],
                          capture_output=True, text=True)


dry = run()
check(f"dry run: exit 0, nothing changed (got {dry.returncode}: {dry.stderr})",
      dry.returncode == 0 and all(read(p) == data for p, data in originals.items()))
check(f"dry run: lists the changes (got {dry.stdout!r})",
      "Nu-Metal, Rock -> Nu Metal, Rock" in dry.stdout and "dry run" in dry.stdout)

backup = os.path.join(root, "backup")
done = run("--apply", "--backup", backup)
check(f"apply: exit 0 (got {done.returncode}: {done.stderr})", done.returncode == 0)
check(f"apply: each part mapped (got {read(a)!r})", read(a) == plain("A", "Nu Metal, Rock"))
check(f"apply: duplicates dropped (got {read(b)!r})", read(b) == plain("B", "Nu Metal"))
check(f"apply: a cp1252 file stays cp1252 (got {read(c)!r})",
      read(c) == "#ARTIST:C\n#TITLE:Café\n#GENRE:Gothic Metal\n#BPM:300\n: 0 4 0 la\nE\n".encode("cp1252"))
check(f"apply: unmapped parts stay, the list is joined with ', ', BOM and CRLF kept (got {read(d)!r})",
      read(d) == b"\xef\xbb\xbf#ARTIST:D\r\n#TITLE:D\r\n#GENRE:Rock, Pony\r\n#BPM:300\r\n: 0 4 0 la\r\nE\r\n")
check("apply: an unchanged genre, no genre and a placeholder are not touched",
      read(e) == originals[e] and read(f_) == originals[f_] and read(g) == originals[g])
check(f"apply: an empty genre cell leaves that spelling (got {read(h)!r})", read(h) == originals[h])
check(f"apply: a quoted cell is unquoted (got {read(i)!r})", read(i) == plain("I", "Hip-Hop, Rap"))
check(f"apply: bases of several genres, duplicates dropped across them (got {read(j)!r})",
      read(j) == plain("J", "Symphonic, Black, Metal"))
check(f"apply: '-' drops a spelling (got {read(k)!r})", read(k) == plain("K", "Pop"))
check(f"apply: nothing left -> the #GENRE line is removed (got {read(m)!r})",
      read(m) == b"#ARTIST:M\n#TITLE:M\n#BPM:300\n: 0 4 0 la\nE\n")
check(f"apply: a removed first line keeps the byte order mark (got {read(n)!r})",
      read(n) == b"\xef\xbb\xbf#ARTIST:N\r\n#TITLE:N\r\n: 0 4 0 la\r\nE\r\n")
check("apply: the changed files are backed up with their full path",
      read(os.path.join(backup, a.lstrip("/"))) == originals[a]
      and not os.path.exists(os.path.join(backup, e.lstrip("/"))))
check(f"apply: summary line (got {done.stdout.splitlines()[-2:]})",
      "9 changed, 2 unchanged, 2 without a genre, 1 missing" in done.stdout)

again = run("--apply", "--backup", backup + "2")
check(f"apply twice: nothing left to change (got {again.stdout.splitlines()[-2:]})",
      again.returncode == 0 and "0 changed" in again.stdout and not os.path.exists(backup + "2"))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
