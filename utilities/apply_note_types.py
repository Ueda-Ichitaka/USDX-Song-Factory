#!/usr/bin/env python3
"""Write the note type marks into copies of the song files.

About me: reads every marks file in stack/input/note-types/ (written by
note_type_editor.py; per song the band, the title and the wanted note types
of its lines and notes - see note_types.py), finds the song file in the
library by #ARTIST and #TITLE and writes a copy with every note set to its
wanted type (lines not in the marks file all normal) into
stack/output/note-types/<library folder>/<song file>. Only changed songs are
written; you copy them into the library. The library is only read. Marks
that do not fit the song, a song not in the library and a band and title
found in several song files are listed and left out. Without --apply
nothing is written.

  python3 apply_note_types.py [--library DIR] [--marks DIR] [--out DIR] [--apply]

--library defaults to LIBRARY_DIR (environment, then stack/.env).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import note_types  # noqa: E402

MARKS_DIR = os.path.join(REPO, "stack", "input", "note-types")
OUT_DIR = os.path.join(REPO, "stack", "output", "note-types")
RESULTS = ("write", "same", "mismatch", "missing", "several", "no_song_named")
SUMMARY_ORDER = ("F", "*", "R", "G", note_types.MIXED)


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


def summary(want: dict) -> str:
    """'F 1-10, 42-44; R 45; mixed 27' for line -> types; normal lines left out."""
    by_kind = {}
    for number, types in want.items():
        kind = types[0] if len(set(types)) == 1 else note_types.MIXED
        if kind != ":":
            by_kind.setdefault(kind, []).append(number)
    parts = [f"{kind} {number_ranges(by_kind[kind])}" for kind in SUMMARY_ORDER if kind in by_kind]
    return "; ".join(parts) or "all normal"


def run(marks_dir: str, library: str, out_dir: str, apply: bool = False) -> dict:
    """Apply every marks file; returns per result the marks files or songs:
    write: (song, summary, notes that change), same: song,
    mismatch: (marks file, problems), missing: marks file,
    several: (marks file, songs), no_song_named: marks file."""
    results = {key: [] for key in RESULTS}
    for name in sorted(os.listdir(marks_dir)):
        path = os.path.join(marks_dir, name)
        if not name.lower().endswith(".tsv") or not os.path.isfile(path):
            continue
        marks = note_types.read_marks(path)
        if not marks.band or not marks.title:
            results["no_song_named"].append(name)
            continue
        found = note_types.find_song(library, marks.band, marks.title)
        if not found:
            results["missing"].append(name)
            continue
        if len(found) > 1:
            results["several"].append((name, [os.path.relpath(p, library) for p in found]))
            continue
        rel = os.path.relpath(found[0], library).replace(os.sep, "/")
        with open(found[0], "rb") as f:
            data = f.read()
        song = note_types.parse(data)
        problems = note_types.check_marks(song, marks)
        if problems:
            results["mismatch"].append((name, "; ".join(problems)))
            continue
        want = note_types.wanted(song, marks)
        todo = note_types.changes(song, want)
        if not todo:
            results["same"].append(rel)
            continue
        results["write"].append((rel, summary(want), len(todo)))
        if apply:
            target = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(note_types.apply(data, marks))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", help="the song library (only read); default LIBRARY_DIR")
    parser.add_argument("--marks", default=MARKS_DIR, help="the folder of the marks files")
    parser.add_argument("--out", default=OUT_DIR, help="where the changed song files go")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args()
    library = (args.library or os.environ.get("LIBRARY_DIR")
               or note_types.env_value("LIBRARY_DIR", os.path.join(REPO, "stack", ".env")))
    if not library:
        sys.exit("no library: give --library DIR or set LIBRARY_DIR in stack/.env")
    if not os.path.isdir(library):
        sys.exit(f"the library is not there: {library}")
    if not os.path.isdir(args.marks):
        sys.exit(f"no marks folder: {args.marks}")
    r = run(args.marks, library, args.out, args.apply)
    for rel, text, count in r["write"]:
        print(f"write    {rel}: {text} ({count} notes change)")
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
    left_out = sum(len(r[k]) for k in ("mismatch", "missing", "several", "no_song_named"))
    print(f"\n{len(r['write'])} to write into {args.out}/, {len(r['same'])} already as marked, {left_out} left out")
    if not args.apply:
        print("dry run - nothing written, use --apply")


if __name__ == "__main__":
    main()
