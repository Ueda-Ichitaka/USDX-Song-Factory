#!/usr/bin/env python3
"""Tests for genre_lookup.py.

About me: plain assert-based checks like the other utility tests, no
network - every online source is a fake. Run: python3 test_genre_lookup.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import genre_bases  # noqa: E402
import genre_lookup as gl  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# needs_genre(): no #GENRE, an empty one or a placeholder
# --------------------------------------------------------------------------

check("needs_genre: empty and placeholders (any case) need one",
      gl.needs_genre("") and gl.needs_genre("  ") and gl.needs_genre("Unknown")
      and gl.needs_genre("no idea"))
check("needs_genre: Other, None and Misc are placeholders too",
      gl.needs_genre("Other") and gl.needs_genre("none") and gl.needs_genre("MISC"))
check("needs_genre: a real genre does not",
      not gl.needs_genre("Pop") and not gl.needs_genre("Power Metal"))
check("needs_genre: placeholders kept with keep_placeholders",
      not gl.needs_genre("Unknown", keep_placeholders=True) and gl.needs_genre("", True))

# --------------------------------------------------------------------------
# split_metal_archives(): the genre text of a Metal Archives band
# --------------------------------------------------------------------------

check("split_metal_archives: periods in brackets dropped, ';' and ',' split",
      gl.split_metal_archives("Speed Metal (early); Power Metal (later)")
      == ["speed metal", "power metal"])
check("split_metal_archives: 'A/B Metal' gives both full names",
      gl.split_metal_archives("Melodic Death/Black Metal, Hard Rock")
      == ["melodic death metal", "black metal", "hard rock"])
check("split_metal_archives: 'Progressive Metal/Rock' keeps each part's own noun",
      gl.split_metal_archives("Progressive Metal/Rock") == ["progressive metal", "rock"])

# --------------------------------------------------------------------------
# combine(): the predicted genres as bases (genre_bases.py) - the ones most
# sources agree on first, then each source's own order, no cap
# --------------------------------------------------------------------------

_sp = genre_bases.Splitter(["power metal", "speed metal", "heavy metal", "symphonic metal", "metal",
                            "pop", "disco", "italo disco", "reggae", "soul"])
check(f"combine: found genres become bases (got {gl.combine({'wikidata': ['symphonic black metal']}, _sp)})",
      gl.combine({"wikidata": ["symphonic black metal"]}, _sp) == ["Symphonic", "Black", "Metal"]
      and gl.combine({"wikidata": ["k-pop", "hip hop", "electronic body music", "r&b", "Hi-NRG", "pop music"]}, _sp)
      == ["K-Pop", "Hip-Hop", "EBM", "R&B", "Hi-NRG", "Pop"])
check(f"combine: Gothic/Doom Metal splits at the slash (got {gl.combine({'metal-archives': ['Gothic/Doom Metal']}, _sp)})",
      gl.combine({"metal-archives": ["Gothic/Doom Metal"]}, _sp) == ["Gothic", "Doom", "Metal"])
_found = {"musicbrainz": ["power metal", "speed metal"],
          "metal-archives": ["speed metal", "power metal"],
          "wikidata": ["heavy metal", "symphonic metal"]}
check(f"combine: shared bases first (got {gl.combine(_found, _sp)})",
      gl.combine(_found, _sp) == ["Metal", "Power", "Speed", "Heavy", "Symphonic"])
_boney = {"wikidata": ["reggae", "pop music", "disco", "soul", "Hi-NRG", "Italo disco", "Eurodisco"]}
check(f"combine: no cap (got {gl.combine(_boney, _sp)})",
      gl.combine(_boney, _sp) == ["Reggae", "Pop", "Disco", "Soul", "Hi-NRG", "Italo", "Eurodisco"])
check("combine: two sources agreeing outrank one source's first genre",
      gl.combine({"wikidata": ["disco", "pop"], "lastfm": ["pop"]}, _sp) == ["Pop", "Disco"])
check("combine: dropped genres give no base",
      gl.combine({"lastfm": ["easy listening", "pop", "singer-songwriter"]}, _sp) == ["Pop"])
check("combine: nothing found -> []", gl.combine({}, _sp) == [] and gl.combine({"wikidata": []}) == [])
check("describe_found: each source with its genres",
      gl.describe_found({"musicbrainz": ["a", "b"], "wikidata": [], "lastfm": ["c"]})
      == "musicbrainz: a, b | lastfm: c")

# --------------------------------------------------------------------------
# the picks: which search hit counts
# --------------------------------------------------------------------------

_ma_rows = [['<a href="https://x/bands/Avatar/1">Avatar</a>  <!-- 9.9 -->', "Symphonic Metal", "Belgium"],
            ['<a href="https://x/bands/Avatar/2">Avatar</a>  <!-- 9.9 -->', "Heavy Metal", "Sweden"],
            ['<a href="https://x/bands/Avatars/3">Avatars</a>  <!-- 5 -->', "Doom Metal", "Chile"]]
check("pick_metal_archives: several bands of that exact name -> none (ambiguous)",
      gl.pick_metal_archives(_ma_rows, "Avatar") is None)
check("pick_metal_archives: exactly one band of that name -> its genre text",
      gl.pick_metal_archives(_ma_rows, "avatars") == "Doom Metal")
check("pick_metal_archives: a.k.a. text after the link does not hide the name",
      gl.pick_metal_archives([['<a href="u">Blind Guardian</a> (<strong>a.k.a.</strong> BGTO) <!-- 7 -->',
                               "Power Metal", "Germany"]], "Blind Guardian") == "Power Metal")

_wd_hits = [{"id": "Q1", "label": "Boney M.", "description": "album by Boney M."},
            {"id": "Q2", "label": "Boney M.", "description": "German and Caribbean disco and funk group"},
            {"id": "Q3", "label": "Boney", "description": "band"}]
check("pick_wikidata: same label and a music act description",
      gl.pick_wikidata(_wd_hits, "Boney M.") == "Q2")
check("pick_wikidata: no music act of that name -> None",
      gl.pick_wikidata(_wd_hits[:1], "Boney M.") is None)

_recs = [{"score": 100, "id": "r1", "title": "Valhalla",
          "artist-credit": [{"name": "Blind Guardian", "artist": {"id": "a1"}}],
          "releases": [{"release-group": {"id": "g1"}}]},
         {"score": 100, "id": "r2", "title": "Valhalla",
          "artist-credit": [{"name": "Deichkind", "artist": {"id": "a2"}}]}]
check("pick_musicbrainz_recording: the hit of that artist and title",
      gl.pick_musicbrainz_recording(_recs[::-1], "Blind Guardian", "Valhalla")["id"] == "r1")
check("pick_musicbrainz_recording: another title -> None",
      gl.pick_musicbrainz_recording(_recs, "Blind Guardian", "Majesty") is None)

check("lastfm_genres: only tags that are genres, in Last.fm's order, at most 5",
      gl.lastfm_genres({"toptags": {"tag": [{"name": "seen live", "count": 100},
                                            {"name": "Power Metal", "count": 90},
                                            {"name": "german", "count": 60},
                                            {"name": "heavy metal", "count": 40}]}},
                       {"power metal", "heavy metal"}) == ["power metal", "heavy metal"])
check("lastfm_genres: an error answer gives []",
      gl.lastfm_genres({"error": 6, "message": "not found"}, {"pop"}) == [])

# --------------------------------------------------------------------------
# Lookup: the sources with a fake fetch, answers cached per artist / song
# --------------------------------------------------------------------------

_calls = []


def _fake_fetch(url, params, headers):
    _calls.append(url)
    if "musicbrainz.org/ws/2/recording" in url and "query" in params:
        return {"recordings": _recs}
    if "/recording/r1" in url:
        return {"genres": []}
    if "/release-group/g1" in url:
        return {"genres": [{"name": "speed metal", "count": 2}, {"name": "power metal", "count": 5}]}
    if "metal-archives.com" in url:
        return {"aaData": [['<a href="u">Blind Guardian</a> <!-- 7 -->', "Speed Metal (early); Power Metal (later)", "Germany"]]}
    if "wikidata.org/w/api.php" in url:
        return {"search": [{"id": "Q9", "label": "Blind Guardian", "description": "German power metal band"}]}
    if "query.wikidata.org" in url:
        return {"results": {"bindings": [{"gLabel": {"value": "power metal"}}]}}
    return None


_cache_dir = tempfile.mkdtemp()
_lookup = gl.Lookup(fetch=_fake_fetch, cache_path=os.path.join(_cache_dir, "cache.json"),
                    sleep=lambda s: None, lastfm_key=None)
_found = _lookup.find("Blind Guardian", "Valhalla")
check(f"Lookup.find: recording without genres -> its release group's, by votes (got {_found})",
      _found["musicbrainz"] == ["power metal", "speed metal"])
check("Lookup.find: Metal Archives and Wikidata of the artist; no Last.fm without a key",
      _found["metal-archives"] == ["speed metal", "power metal"]
      and _found["wikidata"] == ["power metal"] and "lastfm" not in _found)
_n = len(_calls)
_lookup.save()
_lookup2 = gl.Lookup(fetch=_fake_fetch, cache_path=os.path.join(_cache_dir, "cache.json"),
                     sleep=lambda s: None, lastfm_key=None)
check("Lookup: a saved cache answers again without a request",
      _lookup2.find("Blind Guardian", "Valhalla") == _found and len(_calls) == _n)
_lookup3 = gl.Lookup(fetch=lambda u, p, h: None, cache_path=os.path.join(_cache_dir, "other.json"),
                     sleep=lambda s: None, lastfm_key=None)
check("Lookup: a failed request is not cached (tried again next run)",
      _lookup3.find("X", "Y") == {"musicbrainz": [], "metal-archives": [], "wikidata": []}
      and _lookup3.cache == {})

# --------------------------------------------------------------------------
# scan() and run(): every song with its genre, predictions for the ones
# without, both written as TSV
# --------------------------------------------------------------------------

_lib = tempfile.mkdtemp()
for _folder, _body in (("Blind Guardian - Valhalla", "#GENRE:Unknown\n"),
                       ("Toto - Africa", "#GENRE:Rock\n"),
                       ("Nobody - Nothing", "")):
    os.makedirs(os.path.join(_lib, _folder))
    _band, _title = _folder.split(" - ")
    with open(os.path.join(_lib, _folder, _folder + ".txt"), "w", encoding="utf-8") as f:
        f.write(f"#ARTIST:{_band}\n#TITLE:{_title}\n{_body}#BPM:300\n: 0 4 0 la\nE\n")
with open(os.path.join(_lib, "Toto - Africa", "notes.txt"), "w", encoding="utf-8") as f:
    f.write("not a song\n")

_songs = gl.scan(_lib)
check(f"scan: every song file with band, title, genre and path (got {_songs})",
      [(s["band"], s["title"], s["genre"]) for s in _songs]
      == [("Blind Guardian", "Valhalla", "Unknown"), ("Nobody", "Nothing", ""), ("Toto", "Africa", "Rock")]
      and _songs[2]["path"] == os.path.join(_lib, "Toto - Africa", "Toto - Africa.txt"))

_out = tempfile.mkdtemp()
_summary = gl.run(_lib, _out, _lookup2, mb_genres=["power metal", "speed metal", "metal"])
with open(os.path.join(_out, gl.OVERVIEW_FILE), encoding="utf-8") as f:
    _overview = f.read().splitlines()
with open(os.path.join(_out, gl.PREDICTIONS_FILE), encoding="utf-8") as f:
    _pred = [line.split("\t") for line in f.read().splitlines()]
check(f"run: the overview lists every song (got {_overview})",
      _overview[0] == "band\ttitle\tgenre\tpath" and len(_overview) == 4
      and _overview[3].startswith("Toto\tAfrica\tRock\t"))
check(f"run: predictions for the songs without a genre, nothing found -> empty (got {_pred})",
      _pred[0] == ["band", "title", "path", "predicted genre", "found"]
      and _pred[1][:2] == ["Blind Guardian", "Valhalla"] and _pred[1][3] == "Power, Metal, Speed"
      and _pred[1][4].startswith("musicbrainz: power metal, speed metal")
      and _pred[2][:2] == ["Nobody", "Nothing"] and _pred[2][3] == ""
      and len(_pred) == 3)
check(f"run: summary counts (got {_summary})",
      _summary == {"songs": 3, "without genre": 2, "predicted": 1})
_mbdir = tempfile.mkdtemp()
_fetches = []


def _mb_fetch():
    _fetches.append(1)
    return "metal\npower metal\n"


check("load_mb_genres: downloaded once into the output folder, then read from there",
      gl.load_mb_genres(_mbdir, fetch=_mb_fetch) == ["metal", "power metal"]
      and gl.load_mb_genres(_mbdir, fetch=_mb_fetch) == ["metal", "power metal"]
      and len(_fetches) == 1 and os.path.isfile(os.path.join(_mbdir, "musicbrainz-genres.txt")))
check(f"tsv_cell: tabs and line breaks become spaces (got {gl.tsv_cell('a' + chr(9) + 'b')!r})",
      gl.tsv_cell("a\tb\nc") == "a b c" and gl.tsv_cell("a\r\nb") == "a b")
check(f"tsv_cell: other spaces stay - a path can hold two in a row (got {gl.tsv_cell('/x/Boom  Pow.txt')!r})",
      gl.tsv_cell("/x/Boom  Pow.txt") == "/x/Boom  Pow.txt")

# --------------------------------------------------------------------------
# http_get(): 429/503 (rate limit) tried again after a wait; 403 is BLOCKED
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self.ok, self._body = status, status == 200, body
        self.text = "text"

    def json(self):
        return self._body


_answers = [_Resp(503), _Resp(429), _Resp(200, {"ok": 1})]
_waits = []
check("http_get: a rate-limit answer is asked again after a wait",
      gl.http_get("u", {}, {}, get=lambda *a, **k: _answers.pop(0), sleep=_waits.append) == {"ok": 1}
      and len(_waits) == 2)
check("http_get: 403 (the site blocks automated requests) -> BLOCKED",
      gl.http_get("u", {}, {}, get=lambda *a, **k: _Resp(403), sleep=_waits.append) is gl.BLOCKED)
check("http_get: still rate-limited after every wait -> None",
      gl.http_get("u", {}, {}, get=lambda *a, **k: _Resp(503), sleep=lambda s: None) is None)

# a blocked Metal Archives is not asked again in this run
_ma_calls = []


def _blocking_fetch(url, params, headers):
    if "metal-archives" in url:
        _ma_calls.append(url)
        return gl.BLOCKED
    return None


_blocked = gl.Lookup(fetch=_blocking_fetch, cache_path=None, sleep=lambda s: None, lastfm_key=None)
_blocked.find("A", "B")
_blocked.find("C", "D")
check(f"Lookup: Metal Archives is asked once after it blocks (got {len(_ma_calls)} requests)",
      len(_ma_calls) == 1 and "metal-archives" in _blocked.blocked)

check("pick_wikidata: a 'unit' (Babymetal: 'Japanese metal dance unit') is a music act",
      gl.pick_wikidata([{"id": "Q11", "label": "Babymetal",
                         "description": "Japanese metal dance unit"}], "BABYMETAL") == "Q11")

# the studio recording of a song with many live versions: the most releases
# win, whatever order the search returns them in
_asp = [{"score": 100, "id": "live", "title": "Ich will brennen",
         "artist-credit": [{"name": "ASP", "artist": {"id": "a"}}],
         "releases": [{"release-group": {"id": "g-live", "primary-type": "Album",
                                         "secondary-types": ["Live"]}}]},
        {"score": 100, "id": "edit", "title": "Ich will brennen (Supernova edit)",
         "artist-credit": [{"name": "ASP", "artist": {"id": "a"}}],
         "releases": [{"release-group": {"id": "g-x"}}] * 20},
        {"score": 100, "id": "studio", "title": "Ich will brennen",
         "artist-credit": [{"name": "ASP", "artist": {"id": "a"}}],
         "releases": [{"release-group": {"id": "g-comp", "primary-type": "Album",
                                         "secondary-types": ["Compilation"]}},
                      {"release-group": {"id": "g-album", "primary-type": "Album"}},
                      {"release-group": {"id": "g-single", "primary-type": "Single"}}]}]
check("pick_musicbrainz_recording: the exact title with the most releases, in any order",
      gl.pick_musicbrainz_recording(_asp, "ASP", "Ich will brennen")["id"] == "studio"
      and gl.pick_musicbrainz_recording(_asp[::-1], "ASP", "Ich will brennen")["id"] == "studio")
check("release_group: a plain album before compilations, live albums and singles",
      gl.release_group(_asp[2]) == "g-album" and gl.release_group(_asp[0]) == "g-live"
      and gl.release_group({"releases": []}) is None)

check("by_votes: most votes first, equal votes by name (the API's order varies)",
      gl.by_votes([{"name": "rock", "count": 1}, {"name": "pop", "count": 3},
                   {"name": "deathcore", "count": 1}]) == ["pop", "deathcore", "rock"])
_wd_lookup = gl.Lookup(
    fetch=lambda u, p, h: ({"search": [{"id": "Q1", "label": "B", "description": "band"}]}
                           if "w/api.php" in u else
                           {"results": {"bindings": [{"gLabel": {"value": "soul"}},
                                                     {"gLabel": {"value": "disco"}}]}}
                           if "query.wikidata" in u else None),
    cache_path=None, sleep=lambda s: None, lastfm_key=None)
check("Lookup.wikidata: genres sorted by name (the query result has no order)",
      _wd_lookup.wikidata("B") == ["disco", "soul"])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
