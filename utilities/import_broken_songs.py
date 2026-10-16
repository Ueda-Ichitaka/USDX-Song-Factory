#!/usr/bin/env python3
"""Copy the songs a library check found off into the stack input, with broken.csv rows.

About me: reads library_check.py's results.jsonl and, for every song file
with the chosen verdicts ("#GAP off" -> category gap, "drifts" -> category
async):

  1. copies its folder from the library into the stack input folder (a
     folder already there is left as it is - the library itself is only read)
  2. adds a broken.csv row in the karaoke-dashboard's export format (band,
     song name, category, description, lyrics_url, language, cover_url,
     genre, trait): band and song name from the song file's #ARTIST/#TITLE
     (the stack matches rows to folders by them), the measured shift/drift
     and the #GAP (#BPM) fix as description, language and genre as dashboard
     codes from #LANGUAGE/#GENRE (genre only when it is exactly a dashboard
     code), trait none, cover_url empty (library songs have their cover); a
     song that already has a row gets no second one
  3. fills the lyrics_url of the rows without one (fill_lyrics_links.py:
     lrclib.net, Genius when a key is set)

An older broken.csv is rewritten in the dashboard format (missing genre and
trait become default / none). Without --apply nothing is copied or written.

  python3 import_broken_songs.py --library DIR [--results FILE] [--input DIR]
                                 [--categories gap,drift] [--no-lyrics] [--apply]
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
import library_check  # noqa: E402
import songfile  # noqa: E402

HEADER = fill_lyrics_links.HEADER
DEFAULTS = {"genre": "default", "trait": "none"}
VERDICTS = {"gap": "#GAP off", "drift": "drifts"}
CATEGORY = {"#GAP off": "gap", "drifts": "async"}


def num(value) -> str:
    """A number without a trailing .0."""
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def folder_and_txt(row: dict, library: str):
    """(folder, txt) of a result row; older rows only have the name
    ("folder" or "folder [txt stem]")."""
    if row.get("folder") and row.get("txt"):
        return row["folder"], row["txt"]
    name = row["name"]
    if name.endswith("]") and " [" in name:
        folder, stem = name[:-1].rsplit(" [", 1)
        return folder, stem + ".txt"
    txts = songfile.song_txts(os.path.join(library, name))
    return name, os.path.basename(txts[0]) if txts else None


def description(row: dict) -> str:
    if row["verdict"] == "drifts":
        return (f"library check: lyrics drift {row['drift_s']:+.2f} s over the song; fix #GAP "
                f"{num(row['gap_ms'])} -> {num(row['gap_fix_with_bpm_ms'])}, #BPM {num(row['bpm'])} -> "
                f"{num(row['bpm_fix'])}")
    late = -row["offset_s"]  # sung minus file in the results: negative = the lyrics come too late
    return (f"library check: lyrics {abs(late):.2f} s too {'late' if late > 0 else 'early'}; "
            f"fix #GAP {num(row['gap_ms'])} -> {num(row['gap_fix_ms'])}")


def build_row(txt_path: str, row: dict) -> list:
    tags = songfile.read(txt_path).tags
    return [tags.get("ARTIST", ""), tags.get("TITLE", ""), CATEGORY[row["verdict"]], description(row),
            "", songfile.language_code(tags.get("LANGUAGE", "")), "",
            songfile.genre_code(tags.get("GENRE", "")), "none"]


def read_broken_csv(path: str) -> list:
    """The rows of broken.csv as lists in HEADER order (older column sets mapped by name)."""
    if not os.path.isfile(path):
        return []
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    out = []
    for raw in rows[1:]:
        if not any(raw):
            continue
        cells = dict(zip(header, raw))
        out.append([cells.get(col, "") or DEFAULTS.get(col, "") for col in HEADER])
    return out


def key(band: str, title: str):
    return (fill_lyrics_links.clean(band), fill_lyrics_links.clean(title))


def import_songs(results: str, library: str, stack_input: str, categories=("gap", "drift"),
                 apply: bool = False) -> dict:
    wanted = {VERDICTS[c] for c in categories}
    csv_path = os.path.join(stack_input, "broken.csv")
    rows = read_broken_csv(csv_path)
    have = {key(r[0], r[1]) for r in rows}
    copied, added, missing = [], 0, []
    for result in library_check.load_rows(results):
        if result.get("verdict") not in wanted:
            continue
        folder, txt = folder_and_txt(result, library)
        src = os.path.join(library, folder)
        if not txt or not os.path.isfile(os.path.join(src, txt)):
            missing.append(result["name"])
            continue
        if not os.path.exists(os.path.join(stack_input, folder)):
            copied.append(folder)
            if apply:
                shutil.copytree(src, os.path.join(stack_input, folder))
        new = build_row(os.path.join(src, txt), result)
        if key(new[0], new[1]) not in have:
            have.add(key(new[0], new[1]))
            rows.append(new)
            added += 1
            print(f"+ {new[0]} - {new[1]}: {new[2]}, {new[3]}")
    if apply:
        if os.path.isfile(csv_path):
            shutil.copy2(csv_path, f"{csv_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        fill_lyrics_links.write_csv(csv_path, HEADER, rows)
    return {"copied": copied, "rows_added": added, "missing": missing}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", required=True, help="the library the check ran on")
    parser.add_argument("--results", default=os.path.join(REPO, "library analysis", "results.jsonl"))
    parser.add_argument("--input", default=os.path.join(REPO, "stack", "input"),
                        help="the stack input folder (broken.csv lives there)")
    parser.add_argument("--categories", default="gap,drift",
                        help="gap (#GAP off), drift (drifts) or both, comma-separated")
    parser.add_argument("--no-lyrics", action="store_true", help="skip the lyrics link lookup")
    parser.add_argument("--apply", action="store_true", help="copy and write (default: dry run)")
    args = parser.parse_args()
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    summary = import_songs(args.results, args.library, args.input, categories, args.apply)
    print(f"\n{len(summary['copied'])} folders to copy, {summary['rows_added']} new broken.csv rows"
          + (f", {len(summary['missing'])} not found in the library" if summary["missing"] else ""))
    if not args.no_lyrics:
        print("\nlyrics links:")
        if args.apply:
            fill_lyrics_links.fill(os.path.join(args.input, "broken.csv"), args.input, apply=True)
        else:
            print("  (looked up after a run with --apply, when the rows exist)")
    if not args.apply:
        print("\ndry run - nothing copied or written, use --apply")


if __name__ == "__main__":
    main()
