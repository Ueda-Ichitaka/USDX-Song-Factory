#!/usr/bin/env python3
"""Tests for usdb_lookup.py.

About me: plain assert-based checks (no pytest, matching the other
stack-level test scripts). Host-runnable - usdb_syncer/requests are faked
via sys.modules, no real network calls, no submodule checkout, no
installed deps needed: `python3 test_usdb_lookup.py`
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import usdb_lookup as ul  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


def _install_fake_module(name, module):
    sys.modules[name] = module
    return module


# --------------------------------------------------------------------------
# _env_float: docker-compose's ${VAR:-} sets an EMPTY env var (not a
# missing key) when unset in .env - must fall back to the default, not "".
# --------------------------------------------------------------------------

check("_env_float falls back to the default for a missing env var",
      ul._env_float("USDB_TEST_UNSET_VAR", 0.90) == 0.90)

os.environ["USDB_TEST_EMPTY_VAR"] = ""
check("_env_float falls back to the default for an EMPTY (docker-compose "
      "${VAR:-}) env var, not float('')",
      ul._env_float("USDB_TEST_EMPTY_VAR", 0.90) == 0.90)
del os.environ["USDB_TEST_EMPTY_VAR"]

os.environ["USDB_TEST_SET_VAR"] = "0.75"
check("_env_float uses the env var's value when actually set",
      ul._env_float("USDB_TEST_SET_VAR", 0.90) == 0.75)
del os.environ["USDB_TEST_SET_VAR"]

# --------------------------------------------------------------------------
# normalize_name / name_score / match_score (pure, no network)
# --------------------------------------------------------------------------

check("normalize_name strips accents",
      ul.normalize_name("Mötörhead") == "motorhead")
check("normalize_name drops punctuation and casefolds",
      ul.normalize_name("AC/DC!") == "ac dc")
check("normalize_name collapses whitespace",
      ul.normalize_name("Some   Band") == "some band")
check("normalize_name handles empty/None",
      ul.normalize_name(None) == "" and ul.normalize_name("") == "")

check("name_score is 1.0 for an exact match (after normalization)",
      ul.name_score("Lacrimosa", "LACRIMOSA") == 1.0)
check("name_score is low for unrelated strings",
      ul.name_score("Lacrimosa", "Metallica") < 0.5)
check("name_score is 0.0 when either side is empty",
      ul.name_score("", "Lacrimosa") == 0.0 and ul.name_score("Lacrimosa", "") == 0.0)

entry = {"artist": "Lacrimosa", "title": "Lichtgestalt"}
check("match_score is high when both artist and title match",
      ul.match_score(entry, "Lacrimosa", "Lichtgestalt") > 0.95)
check("match_score is dragged down by a wrong artist even with a perfect "
      "title match (min, not average)",
      ul.match_score(entry, "Some Other Band", "Lichtgestalt") < 0.5)

# --------------------------------------------------------------------------
# find_candidates: threshold, ordering, rating/views tie-break
# --------------------------------------------------------------------------

catalog = [
    {"song_id": "1", "artist": "Lacrimosa", "title": "Lichtgestalt",
     "rating": 4.0, "views": 100},
    {"song_id": "2", "artist": "Lacrimosa", "title": "Lichtgestalt",
     "rating": 5.0, "views": 50},
    {"song_id": "3", "artist": "Someone Else", "title": "Completely Different",
     "rating": 5.0, "views": 999},
]
candidates = ul.find_candidates(catalog, "Lacrimosa", "Lichtgestalt", min_score=0.9)
check("find_candidates only returns entries above the threshold",
      [c["song_id"] for c in candidates] == ["2", "1"])
check("find_candidates breaks ties on equal name-score by rating",
      candidates[0]["song_id"] == "2")

check("find_candidates respects limit",
      len(ul.find_candidates(catalog, "Lacrimosa", "Lichtgestalt",
                             min_score=0.9, limit=1)) == 1)
check("find_candidates returns nothing when no entry meets min_score",
      ul.find_candidates(catalog, "Nobody At All", "Nothing", min_score=0.9) == [])

# --------------------------------------------------------------------------
# login(): thin wrapper around usdb_scraper, installs the session into its
# SessionManager singleton so later session-less calls reuse it
# --------------------------------------------------------------------------

check("login returns None with no credentials (never imports usdb_syncer)",
      ul.login("", "") is None and ul.login(None, None) is None)


class FakeSessionManager:
    _session = None
    _user = None


class FakeUser:
    name = "tester"


fake_requests = types.ModuleType("requests")


class FakeSession:
    def __init__(self):
        self.closed = False


fake_requests.Session = FakeSession
_install_fake_module("requests", fake_requests)

fake_usdb_syncer = types.ModuleType("usdb_syncer")
fake_scraper = types.ModuleType("usdb_syncer.usdb_scraper")
fake_scraper.SessionManager = FakeSessionManager
login_calls = []


def fake_login_to_usdb(session, user, password):
    login_calls.append((user, password))
    return True


fake_scraper.login_to_usdb = fake_login_to_usdb
fake_scraper.get_logged_in_usdb_user = lambda session: FakeUser()
fake_usdb_syncer.usdb_scraper = fake_scraper
_install_fake_module("usdb_syncer", fake_usdb_syncer)
_install_fake_module("usdb_syncer.usdb_scraper", fake_scraper)

session = ul.login("me", "secret")
check("login returns a session on success", session is not None)
check("login passes the given credentials through",
      login_calls == [("me", "secret")])
check("login installs the session into SessionManager for later reuse",
      FakeSessionManager._session is session)
check("login installs the logged-in user into SessionManager",
      FakeSessionManager._user is not None and FakeSessionManager._user.name == "tester")

FakeSessionManager._session = None
FakeSessionManager._user = None
fake_scraper.login_to_usdb = lambda session, user, password: False
check("login returns None when the site rejects the credentials",
      ul.login("me", "wrong") is None)
check("a failed login does not touch SessionManager",
      FakeSessionManager._session is None)

fake_scraper.login_to_usdb = lambda session, user, password: True
fake_scraper.get_logged_in_usdb_user = lambda session: None
check("login returns None when login looks ok but the user can't be verified",
      ul.login("me", "secret") is None)


def raising_login(session, user, password):
    raise RuntimeError("boom")


fake_scraper.login_to_usdb = raising_login
check("login survives an exception from usdb_scraper",
      ul.login("me", "secret") is None)

del sys.modules["usdb_syncer"]
del sys.modules["usdb_syncer.usdb_scraper"]

# --------------------------------------------------------------------------
# fetch_catalog / load_catalog
# --------------------------------------------------------------------------


class FakeUsdbSong:
    def __init__(self, song_id, artist, title, rating, views):
        self.song_id = song_id
        self.artist = artist
        self.title = title
        self.rating = rating
        self.views = views


fake_usdb_syncer2 = types.ModuleType("usdb_syncer")
fake_scraper2 = types.ModuleType("usdb_syncer.usdb_scraper")


def fake_get_songs_from_usdb(order, descending, content_filter=None, session=None):
    yield [FakeUsdbSong("1", "Band A", "Song A", 4.0, 10)]
    yield [FakeUsdbSong("2", "Band B", "Song B", 3.0, 20)]


fake_scraper2._get_songs_from_usdb = fake_get_songs_from_usdb
fake_usdb_syncer2.usdb_scraper = fake_scraper2
_install_fake_module("usdb_syncer", fake_usdb_syncer2)
_install_fake_module("usdb_syncer.usdb_scraper", fake_scraper2)

catalog_result = ul.fetch_catalog(session=None)
check("fetch_catalog flattens all pages into plain dicts",
      catalog_result == [
          {"song_id": "1", "artist": "Band A", "title": "Song A",
           "rating": 4.0, "views": 10},
          {"song_id": "2", "artist": "Band B", "title": "Song B",
           "rating": 3.0, "views": 20},
      ])

import tempfile  # noqa: E402

tmp_dir = tempfile.mkdtemp(prefix="usdb-lookup-test-")
cache_path = os.path.join(tmp_dir, "catalog.json")

loaded = ul.load_catalog(None, cache_path, ttl_hours=24)
check("load_catalog fetches + saves when there is no cache yet",
      loaded == catalog_result and os.path.isfile(cache_path))

fetch_calls = []
real_fetch_catalog = ul.fetch_catalog
ul.fetch_catalog = lambda session: (fetch_calls.append(1) or real_fetch_catalog(session))
loaded_again = ul.load_catalog(None, cache_path, ttl_hours=24)
check("load_catalog reuses a fresh cache without re-fetching",
      loaded_again == catalog_result and fetch_calls == [])

loaded_stale = ul.load_catalog(None, cache_path, ttl_hours=0)
check("load_catalog re-fetches when the cache is older than ttl_hours",
      loaded_stale == catalog_result and fetch_calls == [1])

ul.fetch_catalog = lambda session: (_ for _ in ()).throw(RuntimeError("boom"))
loaded_fallback = ul.load_catalog(None, cache_path, ttl_hours=0)
check("load_catalog falls back to the stale cache when a refresh fails",
      loaded_fallback == catalog_result)

empty_cache_path = os.path.join(tmp_dir, "missing.json")
check("load_catalog returns [] when there is no cache AND the refresh fails",
      ul.load_catalog(None, empty_cache_path, ttl_hours=24) == [])

ul.fetch_catalog = real_fetch_catalog

del sys.modules["usdb_syncer"]
del sys.modules["usdb_syncer.usdb_scraper"]

# --------------------------------------------------------------------------
# fetch_notes / fetch_details
# --------------------------------------------------------------------------

fake_usdb_syncer3 = types.ModuleType("usdb_syncer")
fake_scraper3 = types.ModuleType("usdb_syncer.usdb_scraper")
fake_logger_mod = types.ModuleType("usdb_syncer.logger")
fake_usdb_song_mod = types.ModuleType("usdb_syncer.usdb_song")


class FakeSongId(int):
    @classmethod
    def parse(cls, value):
        return cls(int(value))


fake_usdb_song_mod.SongId = FakeSongId
fake_logger_mod.song_logger = lambda song_id: f"logger-for-{song_id}"
fake_scraper3.get_notes = lambda song_id, logger: f"#TITLE:x\n(txt for {int(song_id)})"


class FakeDetails:
    def __init__(self, cover_url="https://example.invalid/cover.jpg"):
        self.cover_url = cover_url
        self._videos = []

    def all_comment_videos(self):
        yield from self._videos


fake_details_instance = FakeDetails()
fake_scraper3.get_usdb_details = lambda song_id: fake_details_instance
fake_usdb_syncer3.usdb_scraper = fake_scraper3
fake_usdb_syncer3.logger = fake_logger_mod
fake_usdb_syncer3.usdb_song = fake_usdb_song_mod
_install_fake_module("usdb_syncer", fake_usdb_syncer3)
_install_fake_module("usdb_syncer.usdb_scraper", fake_scraper3)
_install_fake_module("usdb_syncer.logger", fake_logger_mod)
_install_fake_module("usdb_syncer.usdb_song", fake_usdb_song_mod)

check("fetch_notes returns the notes text",
      ul.fetch_notes(None, "12345") == "#TITLE:x\n(txt for 12345)")
check("fetch_details returns the details object",
      ul.fetch_details(None, "12345") is fake_details_instance)


def raising_get_notes(song_id, logger):
    raise RuntimeError("not found")


fake_scraper3.get_notes = raising_get_notes
check("fetch_notes survives an exception (song not found etc.)",
      ul.fetch_notes(None, "99999") is None)

fake_scraper3.get_usdb_details = lambda song_id: (_ for _ in ()).throw(RuntimeError("x"))
check("fetch_details survives an exception",
      ul.fetch_details(None, "99999") is None)

# --------------------------------------------------------------------------
# pick_video_url
# --------------------------------------------------------------------------

check("pick_video_url returns None for None details", ul.pick_video_url(None) is None)

no_videos = FakeDetails()
check("pick_video_url returns None when there are no comment videos",
      ul.pick_video_url(no_videos) is None)

id_details = FakeDetails()
id_details._videos = ["dQw4w9WgXcQ", "https://youtu.be/other"]
check("pick_video_url builds a watch url from a bare video id",
      ul.pick_video_url(id_details) ==
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

url_details = FakeDetails()
url_details._videos = ["https://www.youtube.com/watch?v=abcdefghijk"]
check("pick_video_url passes a full url through unchanged",
      ul.pick_video_url(url_details) == "https://www.youtube.com/watch?v=abcdefghijk")

# --------------------------------------------------------------------------
# extract_gap_hints: GAP values sometimes posted as corrections in USDB
# comments (informational only - never fed back into the actual #GAP
# re-detection, see orchestrator.py's use via append_comment_tag())
# --------------------------------------------------------------------------


class FakeComment:
    def __init__(self, text):
        self.contents = types.SimpleNamespace(text=text)


class DetailsWithComments(FakeDetails):
    def __init__(self, comments):
        super().__init__()
        self.comments = comments


check("extract_gap_hints returns [] for None details",
      ul.extract_gap_hints(None) == [])

no_comments = DetailsWithComments([])
check("extract_gap_hints returns [] when there are no comments",
      ul.extract_gap_hints(no_comments) == [])

unrelated = DetailsWithComments([FakeComment("great song, thanks for the upload!")])
check("extract_gap_hints finds nothing in a comment that never mentions gap",
      ul.extract_gap_hints(unrelated) == [])

single_hint = DetailsWithComments([FakeComment("GAP: 10820 is more accurate")])
check("extract_gap_hints extracts a plain 'GAP: <number>' mention",
      ul.extract_gap_hints(single_hint) == ["10820"])

negative_hint = DetailsWithComments([FakeComment("gap should be -500 imo")])
check("extract_gap_hints handles a negative value",
      ul.extract_gap_hints(negative_hint) == ["-500"])

multi = DetailsWithComments([
    FakeComment("no gap mention here"),
    FakeComment("Correct GAP=10650 for the youtube rip"),
    FakeComment("actually GAP: 10700 works better for me"),
])
check("extract_gap_hints collects hints across multiple comments, in order",
      ul.extract_gap_hints(multi) == ["10650", "10700"])

far_away = DetailsWithComments([
    FakeComment("the gap between verses feels off, also here is a number 42")])
check("extract_gap_hints does not treat an unrelated distant number as a hint",
      ul.extract_gap_hints(far_away) == [])

# --------------------------------------------------------------------------
# fetch_cover_bytes
# --------------------------------------------------------------------------

check("fetch_cover_bytes returns None when there is no url",
      ul.fetch_cover_bytes(None, None) is None)


class FakeCoverResponse:
    content = b"fake-jpeg-bytes"

    def raise_for_status(self):
        pass


fake_requests.get = lambda url, timeout=15: FakeCoverResponse()
check("fetch_cover_bytes returns the response body",
      ul.fetch_cover_bytes(None, "https://example.invalid/cover.jpg") == b"fake-jpeg-bytes")


def raising_get(url, timeout=15):
    raise RuntimeError("network down")


fake_requests.get = raising_get
check("fetch_cover_bytes survives a network failure",
      ul.fetch_cover_bytes(None, "https://example.invalid/cover.jpg") is None)

# --------------------------------------------------------------------------
# find_usdb_song: tries candidates best-first, skips ones with no notes
# --------------------------------------------------------------------------

fake_requests.get = lambda url, timeout=15: FakeCoverResponse()

catalog2 = [
    {"song_id": "111", "artist": "Lacrimosa", "title": "Lichtgestalt",
     "rating": 5.0, "views": 999},
    {"song_id": "222", "artist": "Lacrimosa", "title": "Lichtgestalt",
     "rating": 1.0, "views": 1},
]
notes_by_id = {"222": "#TITLE:Lichtgestalt\n"}
fake_scraper3.get_notes = lambda song_id, logger: notes_by_id.get(str(int(song_id)))
fake_scraper3.get_usdb_details = lambda song_id: fake_details_instance

result = ul.find_usdb_song(None, catalog2, "Lacrimosa", "Lichtgestalt")
check("find_usdb_song skips a higher-ranked candidate with no fetchable notes",
      result is not None and result["song_id"] == "222")
check("find_usdb_song carries the notes text through",
      result["txt"] == "#TITLE:Lichtgestalt\n")
check("find_usdb_song attaches details/video_url",
      result["details"] is fake_details_instance and result["video_url"] is None)
check("find_usdb_song attaches cover_bytes fetched from details.cover_url",
      result["cover_bytes"] == b"fake-jpeg-bytes")

fake_details_no_cover = FakeDetails(cover_url=None)
fake_scraper3.get_usdb_details = lambda song_id: fake_details_no_cover
result_no_cover = ul.find_usdb_song(None, catalog2, "Lacrimosa", "Lichtgestalt")
check("find_usdb_song leaves cover_bytes None when details has no cover_url",
      result_no_cover["cover_bytes"] is None)
fake_scraper3.get_usdb_details = lambda song_id: fake_details_instance

del sys.modules["requests"]

check("find_usdb_song returns None when nothing matches well enough",
      ul.find_usdb_song(None, catalog2, "Totally Unrelated", "Nothing Like It") is None)

fake_scraper3.get_notes = lambda song_id, logger: None
check("find_usdb_song returns None when no candidate has fetchable notes",
      ul.find_usdb_song(None, catalog2, "Lacrimosa", "Lichtgestalt") is None)

del sys.modules["usdb_syncer"]
del sys.modules["usdb_syncer.usdb_scraper"]
del sys.modules["usdb_syncer.logger"]
del sys.modules["usdb_syncer.usdb_song"]

# --------------------------------------------------------------------------

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("All checks passed.")
