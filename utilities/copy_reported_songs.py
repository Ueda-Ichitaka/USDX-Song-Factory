#!/usr/bin/env python3
"""Copy the song folders a dashboard broken.csv export reports into the stack input.

About me: reads one or more karaoke-dashboard exports ("Reported" page,
broken.csv: band, song name, category, description, lyrics_url, language,
cover_url, genre, trait - with or without the header line) and, for every
row:

  1. finds the song folder in the live library the way the dashboard names
     it: "<band> - <song name>" (just "<song name>" without a band),
     case-insensitive - the dashboard takes band and song name from the
     folder name, split at the first " - "
  2. copies that folder into the stack input folder; a folder already there
     (in any case) is left as it is, the library itself is only read
  3. merges the row into the stack input's broken.csv (dashboard format, a
     backup of the old file kept); a song that already has a row there gets
     no second one

Rows whose folder is not in the library are listed and still merged (the
stack ignores a row without a folder). Without --apply nothing is copied or
written.

  python3 copy_reported_songs.py --library DIR --csv FILE [--csv FILE ...]
                                 [--input DIR] [--apply]
"""
import argparse
import csv
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fill_lyrics_links  # noqa: E402
import import_broken_songs  # noqa: E402

HEADER = fill_lyrics_links.HEADER
SEPARATOR = " - "
HEADER_FIRST_CELLS = {"band", "band name"}


def library_folder(library: str, band: str, title: str):
    """The library folder of a reported song (its own spelling), or None."""
    band, title = band.strip(), title.strip()
    wanted = (f"{band}{SEPARATOR}{title}" if band else title).casefold()
    for name in sorted(os.listdir(library)):
        if name.casefold() == wanted and os.path.isdir(os.path.join(library, name)):
            return name
    return None


def read_export(path: str) -> list:
    """The rows of a dashboard export as lists in HEADER order."""
    rows = [r for r in csv.reader(open(path, encoding="utf-8-sig")) if any(r)]
    if rows and rows[0] and rows[0][0].strip().lower() in HEADER_FIRST_CELLS:
        header = ["band" if h.strip().lower() == "band name" else h.strip().lower() for h in rows[0]]
        rows = [[dict(zip(header, r)).get(col, "") for col in HEADER] for r in rows[1:]]
    else:
        rows = [r[:len(HEADER)] + [""] * (len(HEADER) - len(r)) for r in rows]
    return [[cell or import_broken_songs.DEFAULTS.get(col, "") for col, cell in zip(HEADER, r)]
            for r in rows]


def copy_songs(csv_paths: list, library: str, stack_input: str, apply: bool = False) -> dict:
    """Copy the reported folders and merge the rows; returns {"copied",
    "present", "missing", "rows_added"}."""
    broken_csv = os.path.join(stack_input, "broken.csv")
    rows = import_broken_songs.read_broken_csv(broken_csv)
    have = {import_broken_songs.key(r[0], r[1]) for r in rows}
    in_input = {name.casefold() for name in os.listdir(stack_input)}
    copied, present, missing, added = [], [], [], 0
    for path in csv_paths:
        for row in read_export(path):
            band, title = row[0], row[1]
            key = import_broken_songs.key(band, title)
            if key not in have:
                have.add(key)
                rows.append(row)
                added += 1
            folder = library_folder(library, band, title)
            if folder is None:
                name = f"{band}{SEPARATOR}{title}" if band else title
                if name not in missing:
                    missing.append(name)
            elif folder.casefold() in in_input:
                if folder not in present and folder not in copied:
                    present.append(folder)
            else:
                in_input.add(folder.casefold())
                copied.append(folder)
                if apply:
                    shutil.copytree(os.path.join(library, folder), os.path.join(stack_input, folder))
    if apply and added:
        if os.path.isfile(broken_csv):
            shutil.copy2(broken_csv, f"{broken_csv}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        fill_lyrics_links.write_csv(broken_csv, HEADER, rows)
    return {"copied": copied, "present": present, "missing": missing, "rows_added": added}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", required=True, help="the live song library")
    parser.add_argument("--csv", required=True, action="append",
                        help="a dashboard broken.csv export (can be given more than once)")
    parser.add_argument("--input", default=os.path.join(REPO, "stack", "input"),
                        help="the stack input folder (its broken.csv gets the rows)")
    parser.add_argument("--apply", action="store_true", help="copy and write (default: dry run)")
    args = parser.parse_args()
    summary = copy_songs(args.csv, args.library, args.input, args.apply)
    for folder in summary["copied"]:
        print(f"copy     {folder}")
    for folder in summary["present"]:
        print(f"present  {folder} (already in the input, left alone)")
    for name in summary["missing"]:
        print(f"missing  {name} (no such folder in the library)")
    print(f"\n{len(summary['copied'])} folders to copy, {len(summary['present'])} already in the input, "
          f"{len(summary['missing'])} not in the library, {summary['rows_added']} new broken.csv rows")
    if not args.apply:
        print("dry run - nothing copied or written, use --apply")


if __name__ == "__main__":
    main()
