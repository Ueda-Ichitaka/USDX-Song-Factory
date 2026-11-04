#!/usr/bin/env python3
"""Write the freestyle marks into copies of the song files.

About me: reads every marks file in stack/input/freestyle/ (written by
freestyle_marker.py; per song the band, the title and the lyrics lines
wanted as freestyle - see freestyle.py), finds the song file in the library
by #ARTIST and #TITLE and writes a copy with the marked lines freestyle
(all their notes F) and every other F note back to ':' into
stack/output/freestyle/<library folder>/<song file>. Only changed songs are
written; you copy them into the library. The library is only read. A marks
file whose line number and text do not fit the song, a song not in the
library and a band and title found in several song files are listed and
left out. Without --apply nothing is written.

  python3 apply_freestyle.py [--library DIR] [--marks DIR] [--out DIR] [--apply]

--library defaults to LIBRARY_DIR (environment, then stack/.env).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import freestyle  # noqa: E402

MARKS_DIR = os.path.join(REPO, "stack", "input", "freestyle")
OUT_DIR = os.path.join(REPO, "stack", "output", "freestyle")
RESULTS = ("write", "same", "mismatch", "missing", "several", "no_song_named")


def number_ranges(numbers) -> str:
    """1, 2, 3, 5 -> '1-3, 5'."""
    parts, start, prev = [], None, None
    for n in sorted(numbers):
        if start is not None and n == prev + 1:
            prev = n
            continue
        if start is not None:
            parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    if start is not None:
        parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def run(marks_dir: str, library: str, out_dir: str, apply: bool = False) -> dict:
    """Apply every marks file; returns per result the marks files or songs:
    write: (song, marked lines, notes to F, notes back to ':'), same: song,
    mismatch: (marks file, problems), missing: marks file,
    several: (marks file, songs), no_song_named: marks file."""
    results = {key: [] for key in RESULTS}
    for name in sorted(os.listdir(marks_dir)):
        path = os.path.join(marks_dir, name)
        if not name.lower().endswith(".tsv") or not os.path.isfile(path):
            continue
        band, title, marks = freestyle.read_marks(path)
        if not band or not title:
            results["no_song_named"].append(name)
            continue
        found = freestyle.find_song(library, band, title)
        if not found:
            results["missing"].append(name)
            continue
        if len(found) > 1:
            results["several"].append((name, [os.path.relpath(p, library) for p in found]))
            continue
        song_path = found[0]
        rel = os.path.relpath(song_path, library).replace(os.sep, "/")
        with open(song_path, "rb") as f:
            data = f.read()
        song = freestyle.parse(data)
        problems = freestyle.check_marks(song, marks)
        if problems:
            results["mismatch"].append((name, "; ".join(problems)))
            continue
        numbers = [n for n, _ in marks]
        todo = freestyle.changes(song, set(numbers))
        if not todo:
            results["same"].append(rel)
            continue
        results["write"].append((rel, numbers, sum(1 for c in todo if c[2] == "F"),
                                 sum(1 for c in todo if c[2] == ":")))
        if apply:
            target = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(freestyle.apply(data, marks))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", help="the song library (only read); default LIBRARY_DIR")
    parser.add_argument("--marks", default=MARKS_DIR, help="the folder of the marks files")
    parser.add_argument("--out", default=OUT_DIR, help="where the changed song files go")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args()
    library = (args.library or os.environ.get("LIBRARY_DIR")
               or freestyle.env_value("LIBRARY_DIR", os.path.join(REPO, "stack", ".env")))
    if not library:
        sys.exit("no library: give --library DIR or set LIBRARY_DIR in stack/.env")
    if not os.path.isdir(library):
        sys.exit(f"the library is not there: {library}")
    if not os.path.isdir(args.marks):
        sys.exit(f"no marks folder: {args.marks}")
    r = run(args.marks, library, args.out, args.apply)
    for rel, numbers, to_f, to_normal in r["write"]:
        print(f"write    {rel}: lines {number_ranges(numbers) or '-'} freestyle "
              f"({to_f} notes to F, {to_normal} back to ':')")
    for rel in r["same"]:
        print(f"same     {rel}: already as marked")
    for name, problems in r["mismatch"]:
        print(f"mismatch {name}: {problems} (left out)")
    for name in r["missing"]:
        print(f"missing  {name}: no song file with this #ARTIST and #TITLE in the library")
    for name, songs in r["several"]:
        print(f"several  {name}: {', '.join(songs)} (left out)")
    for name in r["no_song_named"]:
        print(f"no song  {name}: no band and title rows (left out)")
    print(f"\n{len(r['write'])} to write into {args.out}/, {len(r['same'])} already as marked, "
          f"{len(r['mismatch']) + len(r['missing']) + len(r['several']) + len(r['no_song_named'])} left out")
    if not args.apply:
        print("dry run - nothing written, use --apply")


if __name__ == "__main__":
    main()
