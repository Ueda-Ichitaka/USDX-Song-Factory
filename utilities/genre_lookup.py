#!/usr/bin/env python3
"""List the genres of a song library and look up the missing ones online.

About me: scans a library for every song file (songfile.song_txts(), in
all subfolders) and writes two tab-separated files into the output folder:

  library-genres.tsv     every song: band, title, genre, path
  genre-predictions.tsv  the songs without a genre (no #GENRE, an empty one
                         or a placeholder: Unknown, No idea, Other, None,
                         Misc): band, title, path, predicted genre, found,
                         new bases, current genre

A genre check (--songs FILE, a song list in the library-genres.tsv format)
looks up exactly the listed songs, also those that have a genre, and
writes only genre-check.tsv (--predictions NAME; same columns) - to compare
the current genres with what the sources say. apply_genres.sh --replace
writes such a file's predictions over the existing genres.

For each song without a genre these sources are asked:

  MusicBrainz     the song's recording (search by artist and title; the
                  one with that exact title and the most releases), its
                  album's release group when the recording has no genres,
                  the artist when neither has; genres ordered by votes.
                  At most one request per second, as MusicBrainz asks.
  Metal Archives  the band search; counts only when exactly one band has
                  that exact name. An unofficial search page, no API.
  Wikidata        the artist item (same label, a music act description)
                  and its "genre" statements, sorted by name.
  Last.fm         the artist's top tags, only when LASTFM_API_KEY is set
                  (environment or stack/.env); only tags that are
                  MusicBrainz genres count, at most 5.

combine() turns what the sources found into the predicted genre: each found
genre is broken down into its bases with genre_bases.py, against the
MusicBrainz genre list and the library's own genres ("symphonic black
metal" -> Symphonic, Black, Metal; "hip hop" -> Hip-Hop), the bases most
sources agree on first, all of them, joined with ", ". The "found" column
lists each source's own genres for checking. apply_genres.sh writes the
predicted genres into the song files. The MusicBrainz genre list is
downloaded once into musicbrainz-genres.txt in the output folder
(load_mb_genres(), also used by genre_clusters.py).

Answers are cached per artist (per song for MusicBrainz) in
genre-cache.json in the output folder, so a second run only asks for what
is new; a failed request is not cached. A rate-limit answer is asked again
after a wait; a source that blocks automated requests (HTTP 403 - Metal
Archives does so at times) is left out for the rest of the run. The library
is only read.

  python3 genre_lookup.py --library DIR [--out DIR] [--keep-placeholders]
                          [--no-lastfm] [--fixes FILE]
                          [--songs FILE [--predictions NAME]]
"""

import argparse
import csv
import difflib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import genre_bases  # noqa: E402
import songfile  # noqa: E402

OVERVIEW_FILE = "library-genres.tsv"
PREDICTIONS_FILE = "genre-predictions.tsv"
CHECK_FILE = "genre-check.tsv"
CACHE_FILE = "genre-cache.json"
MB_GENRES_FILE = "musicbrainz-genres.txt"
# genre_clusters.py's proposals, reviewed by the user
CLUSTERS_FILE = "genre-clusters.tsv"
# hand-written fixes for single songs (band, title, genre, note)
FIXES_FILE = os.path.join(HERE, "genre-song-fixes.tsv")
MB_GENRES_URL = "https://musicbrainz.org/ws/2/genre/all"
LASTFM_MAX_TAGS = 5
PLACEHOLDERS = {"unknown", "no idea", "other", "none", "misc"}
MIN_SCORE = 90
MIN_SIMILAR = 0.85
# seconds between two requests to the same site
PAUSE = {"musicbrainz.org": 1.5, "metal-archives.com": 1.5, "wikidata.org": 0.5,
         "audioscrobbler.com": 0.25}
# the source each site belongs to
SITE_SOURCE = {"musicbrainz.org": "musicbrainz", "metal-archives.com": "metal-archives",
               "wikidata.org": "wikidata", "audioscrobbler.com": "lastfm"}
# waits (s) before asking again after a rate-limit answer (429/503)
RETRY_WAITS = (5, 15, 30)
# the answer of a site that blocks automated requests (403)
BLOCKED = object()
# genre nouns a Metal Archives "A/B Metal" shares between its parts
NOUNS = ("metal", "rock", "punk", "pop", "core", "music")
MUSIC_ACT_WORDS = ("band", "group", "singer", "musician", "rapper", "duo", "trio",
                   "composer", "orchestra", "choir", "music project", "musical project",
                   "songwriter", "vocalist", "dj", "girl group", "boy band", "unit")


def needs_genre(value: str, keep_placeholders: bool = False) -> bool:
    """True for no genre: empty, or a placeholder unless keep_placeholders."""
    value = (value or "").strip()
    if not value:
        return True
    return not keep_placeholders and value.lower() in PLACEHOLDERS


def tsv_cell(value) -> str:
    """A value as one TSV cell: tabs and line breaks become a space, every
    other character stays (paths can hold two spaces in a row)."""
    return re.sub(r"\r\n|[\t\r\n]", " ", str(value or ""))


def write_tsv(path: str, header: list, rows: list) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in [header] + rows:
            f.write("\t".join(tsv_cell(c) for c in row) + "\n")


def scan(library: str) -> list:
    """Every song file of the library (all subfolders), sorted by path:
    {"band", "title", "genre", "path"}."""
    songs = []
    for root, dirs, files in os.walk(library):
        dirs.sort()
        if not any(f.lower().endswith(".txt") for f in files):
            continue
        for path in songfile.song_txts(root):
            tags = songfile.read(path).tags
            songs.append({"band": tags.get("ARTIST", ""), "title": tags.get("TITLE", ""),
                          "genre": tags.get("GENRE", ""), "path": path})
    return sorted(songs, key=lambda s: s["path"])


# --------------------------------------------------------------------------
# genre names
# --------------------------------------------------------------------------

def split_metal_archives(text: str) -> list:
    """The genres of a Metal Archives genre text, lower case:
    "Speed Metal (early); Power Metal (later)" -> speed metal, power metal;
    "Melodic Death/Black Metal" -> melodic death metal, black metal."""
    out = []
    text = re.sub(r"\([^)]*\)", " ", text or "")
    for item in re.split(r"[;,]", text):
        parts = [" ".join(p.lower().split()) for p in item.split("/")]
        parts = [p for p in parts if p]
        if not parts:
            continue
        noun = parts[-1].split()[-1]
        for part in parts[:-1]:
            out.append(part if part.split()[-1] in NOUNS or noun not in NOUNS else f"{part} {noun}")
        out.append(parts[-1])
    return [g for i, g in enumerate(out) if g not in out[:i]]


def found_bases(genre: str, splitter, clusters: dict) -> list:
    """The bases of one found genre: its row of the reviewed clusters file
    (any case; "-" drops it), else the rules of genre_bases.py."""
    cell = clusters.get(genre.strip().lower())
    if cell is None:
        return splitter.bases(genre)
    return [b.strip() for b in cell.split(",") if b.strip() and b.strip() != "-"]


def combine(found: dict, splitter=None, clusters=None) -> list:
    """The predicted genres of what the sources found ({source: [genre,
    ...]}): each genre's bases (found_bases(); splitter, a
    genre_bases.Splitter; clusters, load_clusters()), the ones the most
    sources name first, then by their best place in a source's list."""
    splitter = splitter or genre_bases.Splitter([])
    clusters = clusters or {}
    sources, best = {}, {}
    order = []
    for source, genres in found.items():
        for rank, genre in enumerate(genres or []):
            for name in found_bases(genre, splitter, clusters):
                if name not in sources:
                    sources[name], best[name] = set(), rank
                    order.append(name)
                sources[name].add(source)
                best[name] = min(best[name], rank)
    order.sort(key=lambda n: (-len(sources[n]), best[n]))
    return order


def describe_found(found: dict) -> str:
    return " | ".join(f"{source}: {', '.join(genres)}" for source, genres in found.items() if genres)


# --------------------------------------------------------------------------
# picking the right search hit
# --------------------------------------------------------------------------

def _clean(text: str) -> str:
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text or "")
    return " ".join(text.replace("’", "'").lower().split())


def _similar(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, _clean(a), _clean(b)).ratio() >= MIN_SIMILAR


def pick_musicbrainz_recording(recordings: list, band: str, title: str):
    """The recording of that title by that artist (search score at least
    MIN_SCORE), or None. A song has many recordings (live, edits); the one
    with exactly that title and the most releases is the song itself, the
    same whatever order the search returns them in."""
    hits = [rec for rec in recordings or []
            if rec.get("score", 0) >= MIN_SCORE and _similar(rec.get("title", ""), title)
            and any(_similar(c.get("name", ""), band) for c in rec.get("artist-credit", []))]
    if not hits:
        return None
    return min(hits, key=lambda rec: (rec.get("title", "").casefold() != title.casefold(),
                                      -len(rec.get("releases") or []), rec.get("id", "")))


def release_group(recording: dict):
    """The release group of a recording to ask for genres: a plain album
    (no live, compilation, ... type) first, else its first release's."""
    groups = [r.get("release-group") or {} for r in recording.get("releases") or []]
    for group in groups:
        if group.get("primary-type") == "Album" and not group.get("secondary-types"):
            return group.get("id")
    return groups[0].get("id") if groups else None


def pick_metal_archives(rows: list, band: str):
    """The genre text of the one band with exactly that name, or None (no
    such band, or several of that name)."""
    hits = []
    for row in rows or []:
        m = re.search(r">([^<]*)</a>", row[0] if row else "")
        if m and m.group(1).strip().casefold() == band.strip().casefold():
            hits.append(row[1])
    return hits[0] if len(hits) == 1 else None


def pick_wikidata(hits: list, band: str):
    """The id of the first item with that label whose description names a
    music act, or None."""
    for hit in hits or []:
        description = (hit.get("description") or "").lower()
        if (hit.get("label") or "").casefold() == band.strip().casefold() \
                and any(re.search(rf"\b{re.escape(w)}\b", description) for w in MUSIC_ACT_WORDS):
            return hit.get("id")
    return None


def lastfm_genres(answer: dict, vocabulary: set) -> list:
    """The artist's top tags that are genres (in `vocabulary`, lower case),
    in Last.fm's order, at most LASTFM_MAX_TAGS."""
    tags = ((answer or {}).get("toptags") or {}).get("tag") or []
    if isinstance(tags, dict):
        tags = [tags]
    names = [str(t.get("name", "")).strip().lower() for t in tags if isinstance(t, dict)]
    return [n for n in names if n in vocabulary][:LASTFM_MAX_TAGS]


def by_votes(genres: list) -> list:
    """MusicBrainz genre names, most votes first, equal votes by name."""
    return [g["name"] for g in sorted(genres or [], key=lambda g: (-g.get("count", 0), g["name"]))]


# --------------------------------------------------------------------------
# the online lookup
# --------------------------------------------------------------------------

def user_agent() -> str:
    try:
        sys.path.insert(0, os.path.join(REPO, "stack", "orchestrator"))
        import lyrics_fetch
        return lyrics_fetch.USER_AGENT
    except Exception:  # noqa: BLE001 - the lookup works without it
        return "USDX-Song-Factory (https://github.com/Ueda-Ichitaka/USDX-Song-Factory)"


def http_get(url: str, params: dict, headers: dict, text: bool = False, get=None,
             sleep=time.sleep):
    """The JSON (or text) answer of a GET request; a rate-limit answer
    (429/503) is asked again after each of RETRY_WAITS; BLOCKED for 403 (the
    site blocks automated requests), None on any other error."""
    if get is None:
        import requests
        get = requests.get
    for wait in (*RETRY_WAITS, None):
        try:
            response = get(url, params=params, headers=headers, timeout=30)
        except Exception:  # noqa: BLE001 - a failed source is just no answer
            return None
        if response.ok:
            try:
                return response.text if text else response.json()
            except ValueError:
                return None
        if response.status_code == 403:
            return BLOCKED
        if response.status_code not in (429, 503) or wait is None:
            return None
        sleep(wait)
    return None


def lastfm_key():
    key = os.environ.get("LASTFM_API_KEY")
    env_file = os.path.join(REPO, "stack", ".env")
    if not key and os.path.isfile(env_file):
        for line in open(env_file, encoding="utf-8"):
            if line.startswith("LASTFM_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    return key or None


class Lookup:
    """Asks the sources for one song's genres; answers are cached."""

    def __init__(self, fetch=None, cache_path=None, sleep=time.sleep, lastfm_key=None,
                 fetch_text=None):
        self.fetch = fetch or (lambda u, p, h: http_get(u, p, h))
        self.fetch_text = fetch_text or (lambda u, p, h: http_get(u, p, h, text=True))
        self.sleep = sleep
        self.lastfm_key = lastfm_key
        self.cache_path = cache_path
        self.cache = {}
        if cache_path and os.path.isfile(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                self.cache = json.load(f)
        self.headers = {"User-Agent": user_agent(), "Accept": "application/json"}
        self.last = {}
        self.vocabulary = None
        # sources that blocked automated requests in this run
        self.blocked = set()

    def save(self):
        if self.cache_path:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=1, sort_keys=True)

    def _get(self, url, params, text=False):
        """The answer of one request, waiting PAUSE since the last one to
        that site; None when it failed or its source blocks automated
        requests (then it is not asked again in this run)."""
        site = next((s for s in PAUSE if s in url), None)
        source = SITE_SOURCE.get(site)
        if source in self.blocked:
            return None
        if site:
            wait = self.last.get(site, 0) + PAUSE[site] - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self.last[site] = time.monotonic()
        answer = (self.fetch_text if text else self.fetch)(url, params, self.headers)
        if answer is BLOCKED:
            self.blocked.add(source)
            print(f"{source} blocks automated requests (HTTP 403) - not asked again in this run")
            return None
        return answer

    def _cached(self, key, ask):
        """The cached answer of `key`, else ask() - cached unless it failed
        (None)."""
        if key in self.cache:
            return self.cache[key]
        answer = ask()
        if answer is None:
            return []
        self.cache[key] = answer
        return answer

    def musicbrainz(self, band, title):
        def ask():
            mb = "https://musicbrainz.org/ws/2"
            query = f'artist:"{band}" AND recording:"{title}"'
            hits = self._get(f"{mb}/recording", {"query": query, "fmt": "json", "limit": 10})
            if hits is None:
                return None
            rec = pick_musicbrainz_recording(hits.get("recordings"), band, title)
            if rec is None:
                return self._musicbrainz_artist(band)
            lookups = [f"{mb}/recording/{rec['id']}"]
            group = release_group(rec)
            lookups += [f"{mb}/release-group/{group}"] if group else []
            artists = [c.get("artist", {}).get("id") for c in rec.get("artist-credit", [])
                       if _similar(c.get("name", ""), band)]
            lookups += [f"{mb}/artist/{a}" for a in artists[:1] if a]
            for url in lookups:
                answer = self._get(url, {"inc": "genres", "fmt": "json"})
                if answer is None:
                    return None
                genres = by_votes(answer.get("genres"))
                if genres:
                    return genres
            return []
        return self._cached(f"musicbrainz|{band.casefold()}|{title.casefold()}", ask)

    def _musicbrainz_artist(self, band):
        mb = "https://musicbrainz.org/ws/2"
        hits = self._get(f"{mb}/artist", {"query": f'artist:"{band}"', "fmt": "json", "limit": 5})
        if hits is None:
            return None
        ids = [a["id"] for a in hits.get("artists", [])
               if a.get("score", 0) >= MIN_SCORE and _clean(a.get("name", "")) == _clean(band)]
        if len(ids) != 1:
            return []
        answer = self._get(f"{mb}/artist/{ids[0]}", {"inc": "genres", "fmt": "json"})
        return None if answer is None else by_votes(answer.get("genres"))

    def metal_archives(self, band):
        def ask():
            answer = self._get("https://www.metal-archives.com/search/ajax-band-search/",
                               {"field": "name", "query": band, "sEcho": 1, "iColumns": 3,
                                "iDisplayStart": 0, "iDisplayLength": 200})
            if answer is None:
                return None
            return split_metal_archives(pick_metal_archives(answer.get("aaData"), band) or "")
        return self._cached(f"metal-archives|{band.casefold()}", ask)

    def wikidata(self, band):
        def ask():
            hits = self._get("https://www.wikidata.org/w/api.php",
                             {"action": "wbsearchentities", "search": band, "language": "en",
                              "type": "item", "format": "json", "limit": 10})
            if hits is None:
                return None
            qid = pick_wikidata(hits.get("search"), band)
            if not qid:
                return []
            query = (f"SELECT ?gLabel WHERE {{ wd:{qid} wdt:P136 ?g . "
                     'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }')
            answer = self._get("https://query.wikidata.org/sparql",
                               {"query": query, "format": "json"})
            if answer is None:
                return None
            return sorted(b["gLabel"]["value"] for b in answer.get("results", {}).get("bindings", [])
                          if b.get("gLabel", {}).get("value"))
        return self._cached(f"wikidata|{band.casefold()}", ask)

    def lastfm(self, band):
        def ask():
            if self.vocabulary is None:
                text = self._get("https://musicbrainz.org/ws/2/genre/all", {"fmt": "txt"}, text=True)
                if text is None:
                    return None
                self.vocabulary = {line.strip().lower() for line in text.splitlines() if line.strip()}
            answer = self._get("https://ws.audioscrobbler.com/2.0/",
                               {"method": "artist.gettoptags", "artist": band,
                                "api_key": self.lastfm_key, "format": "json", "autocorrect": 1})
            if answer is None:
                return None
            return lastfm_genres(answer, self.vocabulary)
        return self._cached(f"lastfm|{band.casefold()}", ask)

    def find(self, band, title) -> dict:
        """{source: [genre, ...]} for one song."""
        found = {"musicbrainz": self.musicbrainz(band, title),
                 "metal-archives": self.metal_archives(band),
                 "wikidata": self.wikidata(band)}
        if self.lastfm_key:
            found["lastfm"] = self.lastfm(band)
        return found


def split_genres(value: str) -> list:
    """The genres of a #GENRE value: split at commas, trimmed, without empty
    parts and placeholders."""
    parts = [p.strip() for p in (value or "").split(",")]
    return [p for p in parts if p and p.lower() not in PLACEHOLDERS]


def load_mb_genres(out_dir: str, fetch=None) -> list:
    """The MusicBrainz genre names, from out_dir or downloaded once."""
    path = os.path.join(out_dir, MB_GENRES_FILE)
    if not os.path.isfile(path):
        fetch = fetch or (lambda: http_get(MB_GENRES_URL, {"fmt": "txt"},
                                           {"User-Agent": user_agent()}, text=True))
        text = fetch()
        if not isinstance(text, str) or not text.strip():
            sys.exit(f"could not download the MusicBrainz genre list ({MB_GENRES_URL})")
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _rows(path: str) -> list:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.reader(f, delimiter="\t"))[1:]


def load_clusters(path: str) -> dict:
    """The reviewed genre-clusters.tsv: {spelling in lower case: genre
    cell}; rows with an empty cell are left out (the first row of a
    spelling wins - the file lists the most songs first); no file -> {}."""
    out = {}
    for r in _rows(path):
        if len(r) >= 2 and r[0].strip():
            out.setdefault(r[1].strip().lower(), r[0].strip())
    return out


def load_fixes(path: str) -> dict:
    """The song fixes of a genre-song-fixes.tsv: {(band, title) in lower
    case: genre}; rows without a genre are left out, no file -> {}."""
    return {(r[0].strip().lower(), r[1].strip().lower()): r[2].strip()
            for r in _rows(path) if len(r) >= 3 and r[2].strip()}


def load_song_list(path: str) -> set:
    """The song file paths of a song list (library-genres.tsv format: a
    header line with a "path" column; without one the fourth column)."""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if not rows:
        return set()
    header = [c.strip().lower() for c in rows[0]]
    col = header.index("path") if "path" in header else 3
    return {r[col].strip() for r in rows[1:] if len(r) > col and r[col].strip()}


def run(library: str, out_dir: str, lookup: Lookup, keep_placeholders: bool = False,
        progress=None, mb_genres=(), clusters=None, fixes=None, only=None,
        predictions_file: str = PREDICTIONS_FILE) -> dict:
    """Scan, look up the songs without a genre, write both files; returns
    {"songs", "without genre", "predicted"}. The found genres become bases
    like the library's (combine(): the reviewed clusters file, else the
    rules against mb_genres and the library's own genres); a song in fixes
    gets its fix instead of a lookup. The "new bases" column names the
    predicted bases the library does not have yet, "current genre" the
    song's #GENRE now.
    only (a set of song file paths, load_song_list()) makes it a genre
    check: exactly those songs are looked up, also the ones with a genre,
    library-genres.tsv is not written; returns {"songs", "listed", "not
    found", "predicted"}."""
    clusters, fixes = clusters or {}, fixes or {}
    songs = scan(library)
    counts = {}
    for song in songs:
        for spelling in set(split_genres(song["genre"])):
            counts[spelling] = counts.get(spelling, 0) + 1
    splitter = genre_bases.Splitter(mb_genres, counts)
    known = {b.lower() for spelling in counts for b in found_bases(spelling, splitter, clusters)}
    known |= {b.strip().lower() for value in fixes.values() for b in value.split(",") if b.strip()}
    os.makedirs(out_dir, exist_ok=True)
    if only is None:
        write_tsv(os.path.join(out_dir, OVERVIEW_FILE), ["band", "title", "genre", "path"],
                  [[s["band"], s["title"], s["genre"], s["path"]] for s in songs])
        missing = [s for s in songs if needs_genre(s["genre"], keep_placeholders)]
    else:
        missing = [s for s in songs if s["path"] in only]
    rows, predicted = [], 0
    for i, song in enumerate(missing, 1):
        fix = fixes.get((song["band"].strip().lower(), song["title"].strip().lower()))
        if fix is not None:
            genres, found_text = [b.strip() for b in fix.split(",") if b.strip()], "song fix"
        else:
            found = lookup.find(song["band"], song["title"])
            genres, found_text = combine(found, splitter, clusters), describe_found(found)
        predicted += bool(genres)
        rows.append([song["band"], song["title"], song["path"], ", ".join(genres), found_text,
                     ", ".join(g for g in genres if g.lower() not in known), song["genre"]])
        if progress:
            progress(f"[{i}/{len(missing)}] {song['band']} - {song['title']}: "
                     f"{', '.join(genres) or '(nothing found)'}")
        if i % 20 == 0:
            lookup.save()
    lookup.save()
    write_tsv(os.path.join(out_dir, predictions_file),
              ["band", "title", "path", "predicted genre", "found", "new bases", "current genre"], rows)
    if only is not None:
        return {"songs": len(songs), "listed": len(only), "not found": len(only) - len(missing),
                "predicted": predicted}
    return {"songs": len(songs), "without genre": len(missing), "predicted": predicted}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", required=True, help="the song library (only read)")
    parser.add_argument("--out", default=os.path.join(REPO, "library analysis"),
                        help="the folder for the result files and the cache")
    parser.add_argument("--keep-placeholders", action="store_true",
                        help="the placeholders (Unknown, No idea, Other, None, Misc) count as a genre")
    parser.add_argument("--no-lastfm", action="store_true", help="do not ask Last.fm")
    parser.add_argument("--fixes", default=FIXES_FILE, help="the song fixes file")
    parser.add_argument("--songs", help="genre check: look up exactly the songs of this song list "
                                        "(library-genres.tsv format), also those with a genre")
    parser.add_argument("--predictions", help="the predictions file name in --out (default: "
                                              f"{PREDICTIONS_FILE}, with --songs {CHECK_FILE})")
    args = parser.parse_args()
    only = load_song_list(args.songs) if args.songs else None
    predictions = args.predictions or (CHECK_FILE if args.songs else PREDICTIONS_FILE)
    key = None if args.no_lastfm else lastfm_key()
    if not key and not args.no_lastfm:
        print("Last.fm: no LASTFM_API_KEY set, asking MusicBrainz, Metal Archives and Wikidata")
    lookup = Lookup(cache_path=os.path.join(args.out, CACHE_FILE), lastfm_key=key)
    os.makedirs(args.out, exist_ok=True)
    summary = run(args.library, args.out, lookup, args.keep_placeholders, progress=print,
                  mb_genres=load_mb_genres(args.out),
                  clusters=load_clusters(os.path.join(args.out, CLUSTERS_FILE)),
                  fixes=load_fixes(args.fixes), only=only, predictions_file=predictions)
    if only is not None:
        print(f"\n{summary['listed']} songs listed, {summary['not found']} of them not in the library, "
              f"{summary['predicted']} with a predicted genre")
        print(f"written: {os.path.join(args.out, predictions)}")
        return
    print(f"\n{summary['songs']} songs, {summary['without genre']} without a genre, "
          f"{summary['predicted']} with a predicted genre")
    print(f"written: {os.path.join(args.out, OVERVIEW_FILE)}")
    print(f"         {os.path.join(args.out, predictions)}")


if __name__ == "__main__":
    main()
