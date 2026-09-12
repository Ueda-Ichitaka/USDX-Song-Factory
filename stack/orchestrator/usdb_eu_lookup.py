#!/usr/bin/env python3
"""usdb.eu lookup for new-song jobs - a second, independent USDB-style
source alongside usdb.animux.de (usdb_lookup.py).

About me: usdb.eu is a separate, newer UltraStar song database - a
different site with a different account/catalog, NOT covered by the
usdb_syncer submodule (which has only ever supported usdb.animux.de -
confirmed by checking its source, README and full git history - see
02-DESIGN.md "USDB integration"). There is no existing Python client for
this site, so this is a small standalone scraper (plain requests, no
usdb_syncer/PySide6 involved). Its search/login/download are undocumented
JSON AJAX endpoints - the exact request shapes below were reverse-
engineered by fetching the real site's pages, reading its own inline
JavaScript directly, and - for the download flow - a real live end-to-end
run against the site (all confirmed 2026-09-12/13, not guessed), see
02-DESIGN.md "usdb.eu integration" for the full trace.

Downloading works nothing like a per-song direct link (that was this
module's first, WRONG assumption - the plain "Txt" link on a song page
turned out to just be dead weight): the site's own "add to my download
list" (the "Zip" button) sets a client-side cookie ("archief", a JSON
array of song ids, max 10 - see request_download_zip()), then a POST to
/download starts async server-side zip preparation, which takes a real
~20s (not just a cosmetic client-side animation) before a follow-up GET
to the same URL returns an actual "application/zip" response instead of
the HTML status page. The zip contains one folder per song with a real
.txt + cover image, and 18-byte PLACEHOLDER .mp3/.mp4 files (usdb.eu's
own README.txt inside the zip: "USDB.eu do not host any audio or video
file, we have include placeholders in the download... replace them") -
consistent with animux.de and with this whole feature's design: audio/
video are always fetched separately via yt-dlp.

Shares its name-matching logic with usdb_lookup.py (find_candidates, and
therefore normalize_name/name_score/match_score) rather than duplicating
it - the algorithm is source-agnostic.

Optional end-to-end: only active when USDB_EU_EMAIL/USDB_EU_PASSWORD are
both set. If either is unset, or any step fails, callers get None and the
job falls back to the next source / full generation - best-effort, must
never block or fail an otherwise-working job.
"""

import io
import json
import os
import re
import sys
import time
import zipfile

from usdb_lookup import find_candidates  # noqa: F401 - re-exported for callers

BASE_URL = "https://usdb.eu"

# usdb.eu rate-limits a rapid sequence of requests (confirmed live
# 2026-09-13: sweeping ~60 songs' searches back-to-back with no delay hit
# "429 Too Many Requests From Scripts", then "503 Service Unavailable") -
# every outgoing request below is throttled to at least this far apart.
MIN_REQUEST_INTERVAL_S = float(os.environ.get("USDB_EU_MIN_REQUEST_INTERVAL_S", "2.0"))
_last_request_time = 0.0


def _throttle():
    global _last_request_time
    wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.monotonic()


# The RightButton link in the page header is "//usdb.eu/signin" when
# logged out and something else (e.g. "//usdb.eu/add") once logged in -
# confirmed live against the real site (2026-09-13). The /signin POST
# itself does NOT reliably signal success/failure in its response body
# (a real successful login was observed to just return "[]" - not the
# {"error": ...} shape its own JS checks for, which may only ever fire on
# an actual rejection) - so login is verified via this separate signal
# instead of trusting that response.
RIGHT_BUTTON_RE = re.compile(r'RightButton[^>]*href="(//usdb\.eu/[^"]*)"')


def login(email, password):
    """Return an authenticated requests.Session, or None (missing creds,
    bad login, or the endpoint itself failing)."""
    email = (email or "").strip()
    password = (password or "").strip()
    if not email or not password:
        return None
    try:
        import requests
    except ImportError as exc:
        print(f"usdb_eu_lookup: requests not available: {exc}", file=sys.stderr)
        return None
    session = requests.Session()
    try:
        _throttle()
        session.post(f"{BASE_URL}/signin", data={
            "actie": "inloggen", "mailadres": email, "wachtwoord": password,
            "onthoudme": "false", "botcheck": "false",
        }, timeout=15).raise_for_status()
        _throttle()
        resp = session.get(f"{BASE_URL}/home", timeout=15)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_eu_lookup: login failed: {exc}", file=sys.stderr)
        return None
    m = RIGHT_BUTTON_RE.search(resp.text)
    if m is None or m.group(1).endswith("/signin"):
        print("usdb_eu_lookup: login rejected (bad credentials?)", file=sys.stderr)
        return None
    return session


def search(session, query):
    """Query usdb.eu's quick-search endpoint (the same one behind its
    header search box). Returns a list of {"artist", "title", "href"}
    dicts from its "Songs" result section - other sections (Artists,
    Uploaders, ...) aren't song matches and are skipped."""
    try:
        _throttle()
        resp = session.post(f"{BASE_URL}/search",
                            data={"actie": "zoek", "q": query}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_eu_lookup: search failed: {exc}", file=sys.stderr)
        return []
    if not isinstance(data, dict):
        # confirmed live (2026-09-13): a query with no results returns a
        # bare "[]", not {"secties": {}} - not an error, just nothing found
        return []
    songs = []
    for section in (data.get("secties") or {}).values():
        if section.get("label") != "Songs":
            continue
        for item in section.get("content") or []:
            title, _, artist = (item.get("label") or "").partition(" - ")
            href = item.get("href") or ""
            if href.startswith("//"):
                href = "https:" + href
            if not href:
                continue
            songs.append({"artist": artist.strip(), "title": title.strip(),
                          "href": href})
    return songs


SONG_ID_RE = re.compile(r"/download/(\d+)")


def resolve_song_id(session, href):
    """The song's numeric id (needed for the /download/<id> txt link) -
    scraped from its detail page, since search results only carry the
    artist/title slug url, not the id."""
    try:
        _throttle()
        resp = session.get(href, timeout=15)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_eu_lookup: could not open {href}: {exc}", file=sys.stderr)
        return None
    m = SONG_ID_RE.search(resp.text)
    return m.group(1) if m else None


def request_download_zip(session, song_ids, initial_wait_s=15, poll_interval_s=5,
                         max_wait_s=90):
    """Trigger usdb.eu's batch download (the "Zip" button mechanism - see
    module docstring) for up to 10 song ids and return the raw zip bytes,
    or None on failure/timeout. Real server-side processing takes ~20s
    (confirmed live), so this waits `initial_wait_s` before polling every
    `poll_interval_s` (each poll is just a plain GET /download - it keeps
    returning the HTML status page until the zip is ready) up to a total
    of `max_wait_s`."""
    try:
        session.cookies.set("archief", json.dumps([str(i) for i in song_ids]),
                            domain="usdb.eu")
        _throttle()
        session.get(f"{BASE_URL}/download", timeout=15).raise_for_status()
        _throttle()
        session.post(f"{BASE_URL}/download", data={}, timeout=15).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_eu_lookup: could not start download prep: {exc}", file=sys.stderr)
        return None

    time.sleep(initial_wait_s)
    deadline = time.monotonic() + max_wait_s
    while True:
        try:
            _throttle()
            resp = session.get(f"{BASE_URL}/download", timeout=30)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"usdb_eu_lookup: download poll failed: {exc}", file=sys.stderr)
            return None
        if resp.headers.get("content-type", "").startswith("application/zip"):
            return resp.content
        if time.monotonic() >= deadline:
            print("usdb_eu_lookup: download zip never became ready in time",
                  file=sys.stderr)
            return None
        time.sleep(poll_interval_s)


COVER_TAG_RE = re.compile(r"^#COVER:\s*(.+)$", re.MULTILINE)


def parse_download_zip(zip_bytes):
    """Extract {"txt", "cover_bytes"} from a usdb.eu download zip (one
    song folder + a generic README.txt - see request_download_zip()).
    cover_bytes is None if the txt has no #COVER tag or that file isn't
    in the zip. Audio/video entries are ignored - they're always
    placeholders (see module docstring), never real media. Returns None
    if no song .txt is found at all."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            txt_name = next(
                (n for n in z.namelist() if n.lower().endswith(".txt")
                 and os.path.basename(n).lower() != "readme.txt"),
                None)
            if not txt_name:
                return None
            txt = z.read(txt_name).decode("utf-8", "replace")
            cover_bytes = None
            m = COVER_TAG_RE.search(txt)
            if m:
                cover_path = os.path.join(os.path.dirname(txt_name), m.group(1).strip())
                if cover_path in z.namelist():
                    cover_bytes = z.read(cover_path)
            return {"txt": txt, "cover_bytes": cover_bytes}
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_eu_lookup: could not parse download zip: {exc}", file=sys.stderr)
        return None


def find_usdb_eu_song(session, band, title, min_score=None):
    """Search usdb.eu for `title` and score the results with the same
    matcher usdb_lookup.py uses, best first. Returns {"song_id", "txt",
    "cover_bytes"} or None (no confident match / nothing fetchable)."""
    results = search(session, title)
    candidates = find_candidates(
        [{"href": r["href"], "artist": r["artist"], "title": r["title"]}
         for r in results],
        band, title, min_score=min_score)
    for entry in candidates:
        song_id = resolve_song_id(session, entry["href"])
        if not song_id:
            continue
        zip_bytes = request_download_zip(session, [song_id])
        if not zip_bytes:
            continue
        parsed = parse_download_zip(zip_bytes)
        if parsed:
            return {"song_id": song_id, "txt": parsed["txt"],
                    "cover_bytes": parsed["cover_bytes"]}
    return None
