#!/usr/bin/env python3
"""Copy the song files of a dashboard genre edit export into the stack input.

About me: reads the karaoke-dashboard's genre edit export (genre-edits.tsv,
no header; per line "<folder>/<song file>", TAB, the wanted genres - the
path relative to the library) and copies each listed song file from the
library into the stack input's genres/<folder>/ - the song file only, no
audio, video or cover: the stack's genre jobs only change its #GENRE. A
copy already there is replaced by the library's current file (it keeps its
modification time). Rows naming a file that is no song file (language.txt
and the other files the stack writes next to a song, or a .txt without
#TITLE and #ARTIST) and rows whose file is not in the library are listed and
left out. The library is only read. Without --apply nothing is copied.

  python3 copy_genre_songs.py --library DIR [--edits FILE] [--input DIR] [--apply]

factory.sh pull-genres runs it with the library from stack/.env.
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import songfile  # noqa: E402

GENRES = "genres"


def read_edits(path: str) -> list:
    """(folder, song file) of every row of a genre edit export."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if "\t" not in line:
                continue
            folder, _, name = line.split("\t", 1)[0].strip().replace("\\", "/").rpartition("/")
            if folder and name:
                rows.append((folder, name))
    return rows


def is_song_file(path: str) -> bool:
    """A song file: not one of the files next to a song, and #TITLE and
    #ARTIST in its header (songfile.py, the rules of USDX)."""
    if os.path.basename(path).lower() in songfile.ADMIN_FILES:
        return False
    tags = songfile.read(path).tags
    return bool(tags.get("TITLE") and tags.get("ARTIST"))


def copy_songs(edits: str, library: str, stack_input: str, apply: bool = False) -> dict:
    """Copy the listed song files into <stack_input>/genres/<folder>/;
    returns {"copied", "not_song", "missing"} (paths "<folder>/<file>")."""
    copied, not_song, missing = [], [], []
    for folder, name in read_edits(edits):
        rel = f"{folder}/{name}"
        source = os.path.join(library, folder, name)
        if not os.path.isfile(source):
            missing.append(rel)
        elif not is_song_file(source):
            not_song.append(rel)
        elif rel not in copied:
            copied.append(rel)
            if apply:
                target = os.path.join(stack_input, GENRES, folder)
                os.makedirs(target, exist_ok=True)
                shutil.copy2(source, os.path.join(target, name))
    return {"copied": copied, "not_song": not_song, "missing": missing}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", required=True, help="the live song library (only read)")
    parser.add_argument("--edits", default=os.path.join(REPO, "stack", "input", "genre-edits.tsv"),
                        help="the dashboard's genre edit export")
    parser.add_argument("--input", default=os.path.join(REPO, "stack", "input"),
                        help="the stack input folder (the song files go to its genres/)")
    parser.add_argument("--apply", action="store_true", help="copy (default: dry run)")
    args = parser.parse_args()
    if not os.path.isfile(args.edits):
        sys.exit(f"no genre edit export: {args.edits}")
    summary = copy_songs(args.edits, args.library, args.input, args.apply)
    for rel in summary["copied"]:
        print(f"copy     {rel}")
    for rel in summary["not_song"]:
        print(f"no song  {rel} (left out)")
    for rel in summary["missing"]:
        print(f"missing  {rel} (not in the library)")
    print(f"\n{len(summary['copied'])} song files to copy into {os.path.join(args.input, GENRES)}/, "
          f"{len(summary['not_song'])} no song file, {len(summary['missing'])} not in the library")
    if not args.apply:
        print("dry run - nothing copied, use --apply")


if __name__ == "__main__":
    main()
