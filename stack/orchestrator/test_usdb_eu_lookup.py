#!/usr/bin/env python3
"""Tests for usdb_eu_lookup.py.

About me: plain assert-based checks (no pytest, matching the other
stack-level test scripts). Host-runnable - requests is faked via
sys.modules, no real network calls or installed deps needed:
`python3 test_usdb_eu_lookup.py`
"""

import io
import json
import os
import sys
import types
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import usdb_eu_lookup as eu  # noqa: E402

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
# _throttle(): usdb.eu rate-limits rapid requests (confirmed live - see
# module docstring) - every request waits out MIN_REQUEST_INTERVAL_S
# since the last one.
# --------------------------------------------------------------------------

sleep_calls = []
orig_sleep = eu.time.sleep
eu.time.sleep = lambda s: sleep_calls.append(s)

eu.MIN_REQUEST_INTERVAL_S = 5.0
eu._last_request_time = eu.time.monotonic()
eu._throttle()
check("_throttle sleeps out the remaining interval when called again too soon",
      len(sleep_calls) == 1 and 0 < sleep_calls[0] <= 5.0)

sleep_calls.clear()
eu._last_request_time = eu.time.monotonic() - 10.0
eu._throttle()
check("_throttle does not sleep when the interval has already elapsed",
      sleep_calls == [])

eu.time.sleep = orig_sleep
eu.MIN_REQUEST_INTERVAL_S = 0.0  # keep the rest of this test suite fast
eu._last_request_time = 0.0

# --------------------------------------------------------------------------
# login(): POST /signin with the real field names scraped from the site's
# own inline JS (actie/mailadres/wachtwoord/onthoudme/botcheck), verified
# via a follow-up GET /home (see RIGHT_BUTTON_RE's docstring - confirmed
# live 2026-09-13 that /signin's own POST response ("[]" on a real
# successful login) is not a reliable success/failure signal by itself)
# --------------------------------------------------------------------------

check("login returns None with no credentials (never imports requests)",
      eu.login("", "") is None and eu.login(None, None) is None)


class FakeResponse:
    def __init__(self, json_data=None, text="", status=200, content=b"",
                content_type="text/html; charset=UTF-8"):
        self._json = json_data
        self.text = text
        self.status_code = status
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


LOGGED_IN_HOME_HTML = '<a class="RightButton no_gsm" href="//usdb.eu/add">'
LOGGED_OUT_HOME_HTML = '<a class="RightButton no_gsm" href="//usdb.eu/signin">'

fake_requests = types.ModuleType("requests")
post_calls = []


class FakeSession:
    home_html = LOGGED_IN_HOME_HTML

    def post(self, url, data=None, timeout=None):
        post_calls.append((url, data))
        return FakeResponse(json_data=[], text="[]")

    def get(self, url, timeout=None):
        return FakeResponse(text=self.home_html)


fake_requests.Session = FakeSession
_install_fake_module("requests", fake_requests)

session = eu.login("me@example.com", "secret")
check("login returns a session when /home shows a logged-in RightButton",
      session is not None)
check("login posts to /signin with the real field names",
      post_calls[-1][0] == "https://usdb.eu/signin" and
      post_calls[-1][1] == {"actie": "inloggen", "mailadres": "me@example.com",
                            "wachtwoord": "secret", "onthoudme": "false",
                            "botcheck": "false"})


class FakeSessionRejects(FakeSession):
    home_html = LOGGED_OUT_HOME_HTML


fake_requests.Session = FakeSessionRejects
check("login returns None when /home still shows the logged-out RightButton "
      "(bad credentials)",
      eu.login("me@example.com", "wrong") is None)


class FakeSessionRaises(FakeSession):
    def post(self, url, data=None, timeout=None):
        raise RuntimeError("network down")


fake_requests.Session = FakeSessionRaises
check("login survives a network failure",
      eu.login("me@example.com", "secret") is None)

fake_requests.Session = FakeSession

# --------------------------------------------------------------------------
# search(): POST /search {actie: zoek, q: ...}, parses the "Songs" section
# of the JSON response, ignores other sections (Artists/Uploaders/...)
# --------------------------------------------------------------------------

songs_response = {
    "secties": {
        "1": {"label": "Songs", "content": [
            {"id": "1", "label": "Youngblood - 5 Seconds of Summer",
             "note": "2018", "href": "//usdb.eu/5SecondsofSummer/Youngblood"},
        ]},
        "2": {"label": "Artists", "content": [
            {"id": "1", "label": "5 Seconds of Summer", "note": "9 songs",
             "href": "//usdb.eu/5SecondsofSummer"},
        ]},
    }
}


class FakeSessionSearch(FakeSession):
    def post(self, url, data=None, timeout=None):
        post_calls.append((url, data))
        return FakeResponse(songs_response)


fake_requests.Session = FakeSessionSearch
search_session = FakeSessionSearch()
results = eu.search(search_session, "Youngblood")
check("search only returns entries from the 'Songs' section",
      results == [{"artist": "5 Seconds of Summer", "title": "Youngblood",
                   "href": "https://usdb.eu/5SecondsofSummer/Youngblood"}])

empty_search_session = FakeSessionSearch()
empty_search_session.post = lambda url, data=None, timeout=None: FakeResponse({})
check("search returns [] when there are no 'secties' at all",
      eu.search(empty_search_session, "Nobody") == [])

bare_list_session = FakeSessionSearch()
bare_list_session.post = lambda url, data=None, timeout=None: FakeResponse([])
check("search returns [] for a bare '[]' response (confirmed live: this is "
      "how a genuine no-results query responds, not an error)",
      eu.search(bare_list_session, "Nobody") == [])


class FakeSessionSearchRaises(FakeSession):
    def post(self, url, data=None, timeout=None):
        raise RuntimeError("boom")


check("search survives a network/parse failure",
      eu.search(FakeSessionSearchRaises(), "X") == [])

# --------------------------------------------------------------------------
# resolve_song_id(): scrapes the numeric id out of the detail page (search
# results only carry the artist/title slug url, not the id)
# --------------------------------------------------------------------------

detail_html = ('<a href="//usdb.eu/download/1" target="_new" class="file">'
              '<i class="fas fa-file"></i>Txt</a>')


class _HtmlResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeSessionDetail(FakeSession):
    def get(self, url, timeout=None):
        return _HtmlResponse(detail_html)


check("resolve_song_id extracts the numeric id from the download link",
      eu.resolve_song_id(FakeSessionDetail(), "https://usdb.eu/x/y") == "1")


class FakeSessionDetailNoId(FakeSession):
    def get(self, url, timeout=None):
        return _HtmlResponse("<html>no download link here</html>")


check("resolve_song_id returns None when there is no download link",
      eu.resolve_song_id(FakeSessionDetailNoId(), "https://usdb.eu/x/y") is None)


class FakeSessionDetailRaises(FakeSession):
    def get(self, url, timeout=None):
        raise RuntimeError("boom")


check("resolve_song_id survives a network failure",
      eu.resolve_song_id(FakeSessionDetailRaises(), "https://usdb.eu/x/y") is None)

# --------------------------------------------------------------------------
# request_download_zip() / parse_download_zip(): the site's real download
# mechanism (confirmed live 2026-09-13) - set the "archief" cookie, POST
# /download to start async zip prep (~20s real server time), poll GET
# /download until it returns application/zip instead of the HTML status
# page.
# --------------------------------------------------------------------------


def _make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


real_zip_bytes = _make_zip({
    "Mneskin/ZittiEBuoni-2510.txt":
        "#TITLE:Zitti E Buoni\n#ARTIST:Måneskin\n"
        "#COVER: ZittiEBuoni-2510.jpg\n#MP3: ZittiEBuoni-2510.mp3\n"
        "#VIDEO: ZittiEBuoni-2510.mp4\n#BPM:206,00\n#GAP:10710,00\n",
    "Mneskin/ZittiEBuoni-2510.jpg": b"\xff\xd8fake-jpeg-bytes",
    "Mneskin/ZittiEBuoni-2510.mp3": b"This file is empty",
    "Mneskin/ZittiEBuoni-2510.mp4": b"This file is empty",
    "README.txt": "USDB.eu do not host any audio or video file...",
})

eu.time.sleep = lambda s: sleep_calls.append(s)

cookies_set = []
poll_get_calls = []


class FakeSessionZipReadyOnSecondPoll(FakeSession):
    def __init__(self):
        self._gets = 0

    def post(self, url, data=None, timeout=None):
        return FakeResponse(json_data=[], text="[]")

    def get(self, url, timeout=None):
        poll_get_calls.append(url)
        self._gets += 1
        if self._gets < 3:  # first GET (page view) + one HTML poll
            return FakeResponse(text="not ready yet")
        return FakeResponse(content=real_zip_bytes, content_type="application/zip")


class FakeCookieJar:
    def set(self, name, value, domain=None):
        cookies_set.append((name, value, domain))


sleep_calls.clear()
poll_get_calls.clear()
session_zip = FakeSessionZipReadyOnSecondPoll()
session_zip.cookies = FakeCookieJar()
zip_bytes = eu.request_download_zip(session_zip, ["2510"], initial_wait_s=1,
                                    poll_interval_s=1, max_wait_s=10)
check("request_download_zip sets the archief cookie to a JSON id list",
      cookies_set == [("archief", '["2510"]', "usdb.eu")])
check("request_download_zip polls until content-type is application/zip, "
      "then returns the raw bytes",
      zip_bytes == real_zip_bytes and len(poll_get_calls) == 3)

sleep_calls.clear()


class FakeSessionZipNeverReady(FakeSession):
    def post(self, url, data=None, timeout=None):
        return FakeResponse(json_data=[], text="[]")

    def get(self, url, timeout=None):
        return FakeResponse(text="still not ready")


session_never = FakeSessionZipNeverReady()
session_never.cookies = FakeCookieJar()
check("request_download_zip gives up and returns None after max_wait_s",
      eu.request_download_zip(session_never, ["2510"], initial_wait_s=0,
                              poll_interval_s=1, max_wait_s=2) is None)


class FakeSessionZipPostFails(FakeSession):
    def post(self, url, data=None, timeout=None):
        raise RuntimeError("boom")


session_fails = FakeSessionZipPostFails()
session_fails.cookies = FakeCookieJar()
check("request_download_zip survives a network failure on the trigger POST",
      eu.request_download_zip(session_fails, ["2510"]) is None)

eu.time.sleep = orig_sleep

check("parse_download_zip extracts the txt and its referenced cover image",
      eu.parse_download_zip(real_zip_bytes) == {
          "txt": ("#TITLE:Zitti E Buoni\n#ARTIST:Måneskin\n"
                  "#COVER: ZittiEBuoni-2510.jpg\n#MP3: ZittiEBuoni-2510.mp3\n"
                  "#VIDEO: ZittiEBuoni-2510.mp4\n#BPM:206,00\n#GAP:10710,00\n"),
          "cover_bytes": b"\xff\xd8fake-jpeg-bytes",
      })

no_cover_zip = _make_zip({
    "Band/Song-1.txt": "#TITLE:Song\n#ARTIST:Band\n#BPM:100,00\n",
    "README.txt": "placeholder note",
})
check("parse_download_zip works fine without a #COVER tag (cover_bytes None)",
      eu.parse_download_zip(no_cover_zip) ==
      {"txt": "#TITLE:Song\n#ARTIST:Band\n#BPM:100,00\n", "cover_bytes": None})

no_txt_zip = _make_zip({"README.txt": "nothing else in here"})
check("parse_download_zip returns None when there's no real song txt",
      eu.parse_download_zip(no_txt_zip) is None)

check("parse_download_zip survives garbage (not a real zip)",
      eu.parse_download_zip(b"not a zip file at all") is None)

del sys.modules["requests"]

# --------------------------------------------------------------------------
# find_usdb_eu_song(): searches by title, scores with usdb_lookup's
# matcher, tries candidates best-first until one's download succeeds
# --------------------------------------------------------------------------

eu.time.sleep = lambda s: None  # keep this section fast too

search_calls = []


class FakeSessionFull:
    cookies = FakeCookieJar()

    def post(self, url, data=None, timeout=None):
        if "search" in url:
            search_calls.append(data)
            return FakeResponse({
                "secties": {"1": {"label": "Songs", "content": [
                    {"label": "Youngblood - 5 Seconds of Summer",
                     "href": "//usdb.eu/5SecondsofSummer/Youngblood"},
                    {"label": "Completely Different - Someone Else",
                     "href": "//usdb.eu/Someone/Different"},
                ]}}})
        return FakeResponse(json_data=[], text="[]")  # /download POST

    def get(self, url, timeout=None):
        if "download" in url:
            return FakeResponse(content=real_zip_bytes, content_type="application/zip")
        return _HtmlResponse('<a href="//usdb.eu/download/1">Txt</a>')


result = eu.find_usdb_eu_song(FakeSessionFull(), "5 Seconds of Summer",
                              "Youngblood", min_score=0.9)
check("find_usdb_eu_song returns the matching song's id + txt + cover",
      result is not None and result["song_id"] == "1" and
      result["txt"].startswith("#TITLE:Zitti E Buoni") and
      result["cover_bytes"] == b"\xff\xd8fake-jpeg-bytes")
check("find_usdb_eu_song searches usdb.eu by title",
      search_calls[-1] == {"actie": "zoek", "q": "Youngblood"})

check("find_usdb_eu_song returns None when nothing matches well enough",
      eu.find_usdb_eu_song(FakeSessionFull(), "Totally Unrelated Band",
                           "Nothing Like It", min_score=0.9) is None)

eu.time.sleep = orig_sleep

# --------------------------------------------------------------------------

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("All checks passed.")
