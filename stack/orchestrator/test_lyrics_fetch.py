#!/usr/bin/env python3
"""Tests for lyrics_fetch.py.

About me: plain assert-based checks (no pytest, matching the other
stack-level test scripts). Host-runnable - syncedlyrics/lyricsgenius are
mocked via sys.modules, no real network calls or installed deps needed:
`python3 test_lyrics_fetch.py`
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lyrics_fetch as lf  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# parse_lrc / parse_plain
# --------------------------------------------------------------------------

lrc_sample = """[ar:Some Artist]
[ti:Some Title]
[00:12.34]First line of the song
[00:15.00]Second line

[00:20.50]Third line after a blank
"""
lrc_lines = lf.parse_lrc(lrc_sample)
check("parse_lrc drops metadata/blank lines, keeps 3 lyric lines",
      len(lrc_lines) == 3)
check("parse_lrc parses minutes/seconds correctly",
      abs(lrc_lines[0]["start"] - 12.34) < 1e-6)
check("parse_lrc keeps line text", lrc_lines[1]["text"] == "Second line")
check("parse_lrc timestamps are increasing",
      lrc_lines[0]["start"] < lrc_lines[1]["start"] < lrc_lines[2]["start"])

# regression: a real syncedlyrics/NetEase result for "Lacrimosa -
# Lichtgestalt" placed Chinese credit lines INSIDE the timed lyrics
# (not as [ar:]/[ti:] tags), which would otherwise become fake lyric
# lines at t=0/1s ahead of the real first line at t=35s
lrc_with_credits = """[00:00.00]作词 : Tilo Wolff
[00:01.00]作曲 : Tilo Wolff
[00:35.01]Ich bin der Atem auf deiner Haut
[00:38.09]Ich bin der Samt um deinen Korper
"""
lrc_credit_lines = lf.parse_lrc(lrc_with_credits)
check(f"parse_lrc drops Chinese credit lines (got {len(lrc_credit_lines)} lines)",
      len(lrc_credit_lines) == 2)
check("parse_lrc's first real line is the actual lyric, not a credit line",
      lrc_credit_lines[0]["text"] == "Ich bin der Atem auf deiner Haut")

lrc_with_english_credits = """[00:00.00]Composer: Some Composer
[00:01.00]Lyrics by: Some Writer
[00:10.00]Real lyric line
"""
check("parse_lrc drops English credit lines too",
      lf.parse_lrc(lrc_with_english_credits) ==
      [{"text": "Real lyric line", "start": 10.0}])

plain_sample = "First line\n\nSecond line\n   \nThird line\n"
plain_lines = lf.parse_plain(plain_sample)
check("parse_plain skips blank lines, keeps 3", len(plain_lines) == 3)
check("parse_plain lines carry no timing",
      all(l["start"] is None for l in plain_lines))

# --------------------------------------------------------------------------
# Genius page-artifact stripping
# --------------------------------------------------------------------------

genius_raw = lf.parse_plain(
    "Some Song Lyrics\nFirst real line\nSecond real line\n99Embed")
stripped = lf._strip_genius_page_artifacts(genius_raw)
check("strips the leading '... Lyrics' title line",
      stripped[0]["text"] == "First real line")
check("strips the trailing '...Embed' artifact",
      stripped[-1]["text"] == "Second real line")
check("no artifacts left over", len(stripped) == 2)

no_artifacts = lf.parse_plain("Just a line\nAnother line")
check("does not eat real lines when no artifacts are present",
      lf._strip_genius_page_artifacts(no_artifacts) == no_artifacts)

# --------------------------------------------------------------------------
# fetch_syncedlyrics: prefers synced (LRC) over plain, handles absence
# --------------------------------------------------------------------------


def _install_fake_module(name, module):
    sys.modules[name] = module
    return module


fake_syncedlyrics = types.ModuleType("syncedlyrics")
calls = []


def fake_search(query, plain_only=False, **kwargs):
    calls.append((query, plain_only))
    if not plain_only:
        return "[00:01.00]La la la\n[00:02.00]La la la again\n"
    return "La la la\nLa la la again\n"


fake_syncedlyrics.search = fake_search
_install_fake_module("syncedlyrics", fake_syncedlyrics)

result = lf.fetch_syncedlyrics("Some Artist", "Some Title")
check("fetch_syncedlyrics prefers the synced (LRC) result",
      result is not None and result["source"] == "syncedlyrics (synced)")
check("fetch_syncedlyrics only calls plain_only=False first (LRC tried first)",
      calls[0] == ("Some Title Some Artist", False))
check("fetch_syncedlyrics carries timing from the LRC result",
      result["lines"][0]["start"] == 1.0)

# only plain available -> falls back to it
calls.clear()
fake_syncedlyrics.search = lambda query, plain_only=False, **kw: (
    None if not plain_only else "Only plain text here\nSecond plain line\n")
result_plain = lf.fetch_syncedlyrics("Some Artist", "Some Title")
check("fetch_syncedlyrics falls back to plain text when no synced lyrics exist",
      result_plain is not None and result_plain["source"] == "syncedlyrics (plain)")
check("fetch_syncedlyrics plain fallback lines carry no timing",
      result_plain["lines"][0]["start"] is None)

# nothing found anywhere
fake_syncedlyrics.search = lambda query, plain_only=False, **kw: None
check("fetch_syncedlyrics returns None when nothing is found",
      lf.fetch_syncedlyrics("Nobody", "Nothing") is None)

# a raising provider must not crash the whole fetch, just report nothing
fake_syncedlyrics.search = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
check("fetch_syncedlyrics survives a provider exception",
      lf.fetch_syncedlyrics("X", "Y") is None)

del sys.modules["syncedlyrics"]

# --------------------------------------------------------------------------
# fetch_genius: needs an api key, uses the mocked lyricsgenius client
# --------------------------------------------------------------------------

check("fetch_genius returns None with no api key (never imports the lib)",
      lf.fetch_genius("Artist", "Title", None) is None)


class FakeSong:
    lyrics = "Some Song Lyrics\nReal first line\nReal second line\n3Embed"
    artist = "Artist"
    title = "Title"


class FakeGeniusClient:
    def __init__(self, token):
        self.token = token

    def search_song(self, title, artist):
        assert title == "Title" and artist == "Artist"
        return FakeSong()


fake_lyricsgenius = types.ModuleType("lyricsgenius")
fake_lyricsgenius.Genius = FakeGeniusClient
_install_fake_module("lyricsgenius", fake_lyricsgenius)

result_genius = lf.fetch_genius("Artist", "Title", "fake-api-key")
check("fetch_genius returns lyrics when an api key + client are present",
      result_genius is not None and result_genius["source"] == "genius")
check("fetch_genius strips the page artifacts too",
      result_genius["lines"] == [{"text": "Real first line", "start": None},
                                 {"text": "Real second line", "start": None}])
check(f"fetch_genius scores confidence from the matched artist/title "
      f"(exact match here, got {result_genius['confidence']})",
      result_genius["confidence"] > 0.95)


class FakeGeniusClientNoResult(FakeGeniusClient):
    def search_song(self, title, artist):
        return None


fake_lyricsgenius.Genius = FakeGeniusClientNoResult
check("fetch_genius returns None when the song isn't found",
      lf.fetch_genius("Artist", "Title", "fake-api-key") is None)

del sys.modules["lyricsgenius"]

# --------------------------------------------------------------------------
# fetch_lyrics: queries EVERY source (not resolv.conf-style early-stop)
# and keeps the best-scoring match; a candidate below MIN_CONFIDENCE is
# discarded so whisper's own transcription takes over instead
# --------------------------------------------------------------------------

genius_calls = []


class FakeGeniusClientTracking(FakeGeniusClient):
    def search_song(self, title, artist):
        genius_calls.append((title, artist))
        return FakeSong()  # exact-match artist/title -> confidence ~1.0


fake_syncedlyrics.search = lambda query, plain_only=False, **kw: (
    "[00:00.00]Synced result\n" if not plain_only else None)  # fixed 0.9
_install_fake_module("syncedlyrics", fake_syncedlyrics)
fake_lyricsgenius.Genius = FakeGeniusClientTracking
_install_fake_module("lyricsgenius", fake_lyricsgenius)

genius_calls.clear()
combined = lf.fetch_lyrics("Artist", "Title", genius_api_key="fake-key")
check("fetch_lyrics queries genius even though syncedlyrics ALSO succeeded "
      "(query-all, not early-stop)", len(genius_calls) == 1)
check(f"fetch_lyrics keeps the best-scoring candidate (genius ~1.0 beats "
      f"syncedlyrics' fixed 0.9 here, got {combined['source']!r})",
      combined["source"] == "genius")

# syncedlyrics finds nothing -> genius's result is used if good enough
fake_syncedlyrics.search = lambda *a, **kw: None
combined2 = lf.fetch_lyrics("Artist", "Title", genius_api_key="fake-key")
check("fetch_lyrics falls back to genius when syncedlyrics finds nothing",
      combined2 is not None and combined2["source"] == "genius")

# neither finds anything
fake_lyricsgenius.Genius = FakeGeniusClientNoResult
combined3 = lf.fetch_lyrics("Artist", "Title", genius_api_key="fake-key")
check("fetch_lyrics returns None when nothing is found anywhere",
      combined3 is None)


# a poor match (low similarity to what was requested) must be discarded
# entirely, not returned just because it's the only candidate
class FakeSongPoorMatch:
    lyrics = "Lyrics\nSome line\nAnother line\n1Embed"
    artist = "A Totally Different Artist Name Entirely"
    title = "A Completely Unrelated Title Altogether"


class FakeGeniusClientPoorMatch(FakeGeniusClient):
    def search_song(self, title, artist):
        return FakeSongPoorMatch()


fake_lyricsgenius.Genius = FakeGeniusClientPoorMatch
combined4 = lf.fetch_lyrics("Artist", "Title", genius_api_key="fake-key")
check(f"fetch_lyrics discards a candidate below MIN_CONFIDENCE "
      f"(got {combined4!r})", combined4 is None)

del sys.modules["syncedlyrics"]
del sys.modules["lyricsgenius"]

# --------------------------------------------------------------------------

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
