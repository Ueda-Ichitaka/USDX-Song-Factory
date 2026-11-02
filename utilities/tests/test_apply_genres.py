#!/usr/bin/env python3
"""Tests for apply_genres.sh.

About me: runs the bash script on song files in a temp folder and checks the
files afterwards - plain assert-based checks like the other utility tests.
Run: python3 tests/test_apply_genres.py (from utilities/)
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # utilities/
SCRIPT = os.path.join(HERE, "apply_genres.sh")
failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


root = tempfile.mkdtemp()
lib = os.path.join(root, "library")


def song(name, data: bytes) -> str:
    path = os.path.join(lib, name, name + ".txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


plain = song("A - Plain", b"#ARTIST:A\n#TITLE:Plain\n#BPM:300\n: 0 4 0 la\nE\n")
placeholder = song("B - Placeholder", b"#ARTIST:B\n#TITLE:Placeholder\n#genre:Unknown\n#BPM:300\n: 0 4 0 la\nE\n")
crlf = song("C - Crlf", b"\xef\xbb\xbf#ARTIST:C\r\n#TITLE:Crlf\r\n#BPM:300\r\n: 0 4 0 la\r\nE\r\n")
cp1252 = song("D - Cp", "#ARTIST:D\n#TITLE:Café\n#BPM:300\n: 0 4 0 la\nE\n".encode("cp1252"))
has_genre = song("E - Has", b"#ARTIST:E\n#TITLE:Has\n#GENRE:Pop\n#BPM:300\n: 0 4 0 la\nE\n")
nothing = song("F - Nothing", b"#ARTIST:F\n#TITLE:Nothing\n#BPM:300\n: 0 4 0 la\nE\n")
others = [song(f"H - {v}", f"#ARTIST:H\n#TITLE:{v}\n#GENRE:{v}\n#BPM:300\n: 0 4 0 la\nE\n".encode())
          for v in ("Other", "None", "Misc")]
no_band = song("I - No band", b"#ARTIST:\n#TITLE:No band\n#BPM:300\n: 0 4 0 la\nE\n")
read_only = song("J - Read only", b"#ARTIST:J\n#TITLE:Read only\n#BPM:300\n: 0 4 0 la\nE\n")
os.chmod(read_only, 0o444)   # the read-only attribute on the NAS shows like this
originals = {p: read(p) for p in (plain, placeholder, crlf, cp1252, has_genre, nothing, *others, no_band,
                                  read_only)}

tsv = os.path.join(root, "genre-predictions.tsv")
with open(tsv, "w", encoding="utf-8") as f:
    f.write("band\ttitle\tpath\tpredicted genre\tfound\n")
    f.write(f"A\tPlain\t{plain}\tMetal, Symphonic\tmusicbrainz: symphonic metal\n")
    f.write(f"B\tPlaceholder\t{placeholder}\tRock\tmusicbrainz: rock\n")
    f.write(f"C\tCrlf\t{crlf}\t\"Gothic, Rock\"\tmusicbrainz: gothic rock\n")
    f.write(f"D\tCafé\t{cp1252}\tNeue Deutsche Härte\tmusicbrainz: neue deutsche härte\n")
    f.write(f"E\tHas\t{has_genre}\tMetal\tmusicbrainz: metal\n")
    f.write(f"F\tNothing\t{nothing}\t\t\n")
    for v, path in zip(("Other", "None", "Misc"), others):
        f.write(f"H\t{v}\t{path}\tPop\tmusicbrainz: pop\n")
    f.write(f"\tNo band\t{no_band}\tPop\tmusicbrainz: pop\n")
    f.write(f"J\tRead only\t{read_only}\tPop\tmusicbrainz: pop\n")
    f.write(f"G\tGone\t{os.path.join(lib, 'gone.txt')}\tPop\tmusicbrainz: pop\n")

dry = subprocess.run(["bash", SCRIPT, tsv], capture_output=True, text=True)
check(f"dry run: exit 0, nothing changed (got {dry.returncode}: {dry.stderr})",
      dry.returncode == 0 and all(read(p) == data for p, data in originals.items()))
check(f"dry run: says what it would set (got {dry.stdout!r})",
      "Metal, Symphonic" in dry.stdout and "dry run" in dry.stdout)

backup = os.path.join(root, "backup")
run = subprocess.run(["bash", SCRIPT, "--apply", "--backup", backup, tsv], capture_output=True, text=True)
check(f"apply: a read-only file does not stop the run, exit 0 (got {run.returncode}: {run.stderr})",
      run.returncode == 0 and read(read_only) == originals[read_only]
      and "read-only J - Read only" in run.stdout and "read-only J - Read only" in dry.stdout)
check(f"apply: a missing #GENRE is added after the header (got {read(plain)!r})",
      read(plain) == b"#ARTIST:A\n#TITLE:Plain\n#BPM:300\n#GENRE:Metal, Symphonic\n: 0 4 0 la\nE\n")
check(f"apply: a placeholder #GENRE is replaced in place (got {read(placeholder)!r})",
      read(placeholder) == b"#ARTIST:B\n#TITLE:Placeholder\n#GENRE:Rock\n#BPM:300\n: 0 4 0 la\nE\n")
check(f"apply: the placeholders Other, None and Misc are replaced (got {[read(p) for p in others]})",
      all(read(p) == f"#ARTIST:H\n#TITLE:{v}\n#GENRE:Pop\n#BPM:300\n: 0 4 0 la\nE\n".encode()
          for v, p in zip(("Other", "None", "Misc"), others)))
check(f"apply: an empty band cell keeps the columns in place (got {read(no_band)!r})",
      read(no_band) == b"#ARTIST:\n#TITLE:No band\n#BPM:300\n#GENRE:Pop\n: 0 4 0 la\nE\n")
check(f"apply: byte order mark and CRLF kept, quoted cell unquoted (got {read(crlf)!r})",
      read(crlf) == b"\xef\xbb\xbf#ARTIST:C\r\n#TITLE:Crlf\r\n#BPM:300\r\n#GENRE:Gothic, Rock\r\n"
                    b": 0 4 0 la\r\nE\r\n")
check(f"apply: a cp1252 file gets the genre in cp1252 (got {read(cp1252)!r})",
      read(cp1252) == "#ARTIST:D\n#TITLE:Café\n#BPM:300\n#GENRE:Neue Deutsche Härte\n: 0 4 0 la\nE\n"
      .encode("cp1252"))
check("apply: a song that has a genre now is left alone",
      read(has_genre) == originals[has_genre])
check("apply: an empty prediction changes nothing", read(nothing) == originals[nothing])
check(f"apply: missing file reported (got {run.stdout!r})", "gone.txt" in run.stdout)
check("apply: the changed files are backed up with their full path",
      read(os.path.join(backup, plain.lstrip("/"))) == originals[plain]
      and not os.path.exists(os.path.join(backup, has_genre.lstrip("/"))))
check(f"apply: summary line (got {run.stdout.splitlines()[-1:]})",
      "8 set" in run.stdout and "1 read-only" in run.stdout)

again = subprocess.run(["bash", SCRIPT, "--apply", "--backup", backup + "2", tsv],
                       capture_output=True, text=True)
check("apply twice: nothing is set again (the songs have a genre now)",
      again.returncode == 0 and "0 set" in again.stdout and read(plain).count(b"#GENRE") == 1)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
