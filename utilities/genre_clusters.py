#!/usr/bin/env python3
"""Breaks the genres of a song library down into their bases.

About me: reads the #GENRE of every song file of the library (all
subfolders; nothing is written there), splits it at commas and proposes
the bases of each genre spelling with genre_bases.py ("Alternative Metal"
-> Alternative, Metal; "Hip Hop" -> Hip-Hop; "Ghotic Metal" -> Gothic,
Metal). The placeholders genre_lookup.py fills (Unknown, No idea, Other,
None, Misc) are left out.

Writes three tab-separated files into the output folder:

  genre-clusters.tsv  one row per spelling: genre (its bases, ", "-joined;
                      "-" for a spelling that is dropped - edit them before
                      normalize_genres.sh uses the file), spelling, songs,
                      why (unchanged / spelling / split / kept whole /
                      dropped / translation / synonym / filler dropped /
                      compound / typo?)
  genre-overview.tsv  one row per base: base, songs, from (the spellings it
                      comes from, with their song counts)
  library-genres.tsv  every song: band, title, genre, path (the song list
                      normalize_genres.sh goes through)

The MusicBrainz genre list is downloaded once and kept as
musicbrainz-genres.txt in the output folder (delete it to download it again).

Usage:
  python3 genre_clusters.py --library DIR [--out DIR]
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import genre_bases  # noqa: E402
import genre_lookup  # noqa: E402

CLUSTERS_FILE = "genre-clusters.tsv"
GENRES_OVERVIEW_FILE = "genre-overview.tsv"
# the genre cell of a spelling that is dropped from the songs
DROP = "-"


split_genres = genre_lookup.split_genres


def spelling_rows(counts: dict, splitter) -> list:
    """Each spelling ({spelling: songs}) with its bases and why, most songs
    first: [{"spelling", "songs", "bases", "why"}]."""
    rows = []
    for spelling, songs in counts.items():
        bases, fixes = splitter.explain(spelling)
        if bases == [spelling]:
            why = "unchanged"
        elif fixes:
            why = "; ".join(fixes)
        else:
            why = "split" if len(bases) > 1 else "spelling"
        rows.append({"spelling": spelling, "songs": songs, "bases": bases, "why": why})
    return sorted(rows, key=lambda r: (-r["songs"], r["spelling"].lower(), r["spelling"]))


def rebuild(value: str, mapping: dict) -> str:
    """A #GENRE value with each comma-separated part replaced by its bases
    from mapping ({spelling: "Base, Base"}), duplicates (any case) dropped,
    joined with ", " - the same rule normalize_genres.sh uses. A mapping
    of "-" drops that spelling."""
    out, seen = [], set()
    for part in (p.strip() for p in (value or "").split(",")):
        for new in (n.strip() for n in (mapping.get(part) or part).split(",")):
            if new and new != DROP and new.casefold() not in seen:
                seen.add(new.casefold())
                out.append(new)
    return ", ".join(out)


def run(library: str, out_dir: str, mb_genres: list) -> dict:
    """Scan, break the genres down, write the three files; returns the
    counts."""
    songs = genre_lookup.scan(library)
    os.makedirs(out_dir, exist_ok=True)
    genre_lookup.write_tsv(os.path.join(out_dir, genre_lookup.OVERVIEW_FILE),
                           ["band", "title", "genre", "path"],
                           [[s["band"], s["title"], s["genre"], s["path"]] for s in songs])
    counts = {}
    for song in songs:
        for spelling in set(split_genres(song["genre"])):
            counts[spelling] = counts.get(spelling, 0) + 1
    rows = spelling_rows(counts, genre_bases.Splitter(mb_genres, counts))
    mapping = {r["spelling"]: ", ".join(r["bases"]) or DROP for r in rows}
    genre_lookup.write_tsv(os.path.join(out_dir, CLUSTERS_FILE), ["genre", "spelling", "songs", "why"],
                           [[mapping[r["spelling"]], r["spelling"], r["songs"], r["why"]] for r in rows])
    base_songs, base_from = {}, {}
    for song in songs:
        for base in {b for s in split_genres(song["genre"]) for b in mapping[s].split(", ") if b != DROP}:
            base_songs[base] = base_songs.get(base, 0) + 1
    for r in rows:
        for base in r["bases"]:
            base_from.setdefault(base, []).append(f"{r['spelling']} ({r['songs']})")
    bases = sorted(base_songs, key=lambda b: (-base_songs[b], b.lower()))
    genre_lookup.write_tsv(os.path.join(out_dir, GENRES_OVERVIEW_FILE), ["base", "songs", "from"],
                           [[b, base_songs[b], ", ".join(base_from[b])] for b in bases])
    with_genre = [s for s in songs if split_genres(s["genre"])]
    return {"songs": len(songs), "with genre": len(with_genre), "spellings": len(counts),
            "bases": len(bases),
            "spellings to change": sum(1 for s, g in mapping.items() if s != g),
            "songs to change": sum(1 for s in with_genre
                                   if rebuild(s["genre"], mapping) != s["genre"].strip())}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", required=True, help="the song library (only read)")
    parser.add_argument("--out", default=os.path.join(genre_lookup.REPO, "library analysis"),
                        help="the folder for the result files")
    args = parser.parse_args()
    summary = run(args.library, args.out, genre_lookup.load_mb_genres(args.out))
    print(f"{summary['songs']} songs, {summary['with genre']} with a genre: "
          f"{summary['spellings']} spellings, {summary['bases']} bases")
    print(f"the proposals change {summary['spellings to change']} spellings in "
          f"{summary['songs to change']} songs")
    for name in (CLUSTERS_FILE, GENRES_OVERVIEW_FILE, genre_lookup.OVERVIEW_FILE):
        print(f"written: {os.path.join(args.out, name)}")


if __name__ == "__main__":
    main()
