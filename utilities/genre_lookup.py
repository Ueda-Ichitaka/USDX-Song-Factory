#!/usr/bin/env python3
"""List the genres of a song library and look up the missing ones online.

About me: scans a library for every song file (songfile.song_txts(), in
all subfolders) and writes two tab-separated files into the output folder:

  library-genres.tsv     every song: band, title, genre, path
  genre-predictions.tsv  the songs without a genre (no #GENRE, an empty one
                         or a placeholder like "Unknown"/"No idea"): band,
                         title, path, predicted genre, found

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
genre is mapped to the stack's genre names (map_genre(): "power metal" ->
Metal, "symphonic metal" -> Metal, Symphonic), a genre outside them is kept
as found ("italo disco" -> Italo Disco); the ones most sources agree on come
first, at most MAX_GENRES, joined with ", ". The "found" column lists each
source's own genres for checking. apply_genres.sh writes the predicted
genres into the song files.

Answers are cached per artist (per song for MusicBrainz) in
genre-cache.json in the output folder, so a second run only asks for what
is new; a failed request is not cached. A rate-limit answer is asked again
after a wait; a source that blocks automated requests (HTTP 403 - Metal
Archives does so at times) is left out for the rest of the run. The library
is only read.

  python3 genre_lookup.py --library DIR [--out DIR] [--keep-placeholders]
                          [--no-lastfm]
"""

import argparse
import difflib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import songfile  # noqa: E402

OVERVIEW_FILE = "library-genres.tsv"
PREDICTIONS_FILE = "genre-predictions.tsv"
CACHE_FILE = "genre-cache.json"
MAX_GENRES = 6
LASTFM_MAX_TAGS = 5
PLACEHOLDERS = {"unknown", "no idea"}
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
# genre words -> the stack's genre names (stack/orchestrator/song_style.py)
GENRE_WORDS = {"metal": "Metal", "medieval": "Medieval", "mittelalter": "Medieval",
               "folk": "Folk", "gothic": "Gothic", "goth": "Gothic",
               "symphonic": "Symphonic", "punk": "Punk", "pop": "Pop", "rap": "Rap",
               "hiphop": "Rap", "kpop": "K-Pop", "jpop": "J-Pop", "rock": "Rock",
               "industrial": "Industrial", "ebm": "EBM"}
SPELLINGS = ((re.compile(r"\bk[\s-]?pop\b"), "kpop"), (re.compile(r"\bj[\s-]?pop\b"), "jpop"),
             (re.compile(r"\bhip[\s-]?hop\b"), "hiphop"),
             (re.compile(r"\belectronic body music\b"), "ebm"))
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
    return " ".join(str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").split())


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


def display(name: str) -> str:
    """A found genre as written into #GENRE: each word starts upper case,
    a word with '&' is all upper case (R&B); other letters stay."""
    return " ".join(w.upper() if "&" in w else w[:1].upper() + w[1:] for w in name.split())


def map_genre(name: str) -> list:
    """The stack's genre names a found genre contains, in that order; the
    found genre itself (display()) when it contains none."""
    text = name.lower()
    for pattern, word in SPELLINGS:
        text = pattern.sub(word, text)
    out = []
    for word in re.split(r"[\s/\-]+", text):
        code = GENRE_WORDS.get(word) or ("Metal" if word.startswith("metal") else None)
        if code and code not in out:
            out.append(code)
    return out or [display(name)]


def combine(found: dict) -> list:
    """The predicted genres of what the sources found ({source: [genre,
    ...]}): mapped with map_genre(), the ones the most sources name first,
    then by their best place in a source's list; at most MAX_GENRES."""
    sources, best = {}, {}
    order = []
    for source, genres in found.items():
        for rank, genre in enumerate(genres or []):
            for name in map_genre(genre):
                if name not in sources:
                    sources[name], best[name] = set(), rank
                    order.append(name)
                sources[name].add(source)
                best[name] = min(best[name], rank)
    order.sort(key=lambda n: (-len(sources[n]), best[n]))
    return order[:MAX_GENRES]


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


def run(library: str, out_dir: str, lookup: Lookup, keep_placeholders: bool = False,
        progress=None) -> dict:
    """Scan, look up the songs without a genre, write both files; returns
    {"songs", "without genre", "predicted"}."""
    songs = scan(library)
    os.makedirs(out_dir, exist_ok=True)
    write_tsv(os.path.join(out_dir, OVERVIEW_FILE), ["band", "title", "genre", "path"],
              [[s["band"], s["title"], s["genre"], s["path"]] for s in songs])
    missing = [s for s in songs if needs_genre(s["genre"], keep_placeholders)]
    rows, predicted = [], 0
    for i, song in enumerate(missing, 1):
        found = lookup.find(song["band"], song["title"])
        genres = combine(found)
        predicted += bool(genres)
        rows.append([song["band"], song["title"], song["path"], ", ".join(genres),
                     describe_found(found)])
        if progress:
            progress(f"[{i}/{len(missing)}] {song['band']} - {song['title']}: "
                     f"{', '.join(genres) or '(nothing found)'}")
        if i % 20 == 0:
            lookup.save()
    lookup.save()
    write_tsv(os.path.join(out_dir, PREDICTIONS_FILE),
              ["band", "title", "path", "predicted genre", "found"], rows)
    return {"songs": len(songs), "without genre": len(missing), "predicted": predicted}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", required=True, help="the song library (only read)")
    parser.add_argument("--out", default=os.path.join(REPO, "library analysis"),
                        help="the folder for the result files and the cache")
    parser.add_argument("--keep-placeholders", action="store_true",
                        help="'Unknown' and 'No idea' count as a genre")
    parser.add_argument("--no-lastfm", action="store_true", help="do not ask Last.fm")
    args = parser.parse_args()
    key = None if args.no_lastfm else lastfm_key()
    if not key and not args.no_lastfm:
        print("Last.fm: no LASTFM_API_KEY set, asking MusicBrainz, Metal Archives and Wikidata")
    lookup = Lookup(cache_path=os.path.join(args.out, CACHE_FILE), lastfm_key=key)
    os.makedirs(args.out, exist_ok=True)
    summary = run(args.library, args.out, lookup, args.keep_placeholders, progress=print)
    print(f"\n{summary['songs']} songs, {summary['without genre']} without a genre, "
          f"{summary['predicted']} with a predicted genre")
    print(f"written: {os.path.join(args.out, OVERVIEW_FILE)}")
    print(f"         {os.path.join(args.out, PREDICTIONS_FILE)}")


if __name__ == "__main__":
    main()
