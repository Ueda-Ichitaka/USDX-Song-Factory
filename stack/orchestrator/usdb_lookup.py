#!/usr/bin/env python3
"""USDB (usdb.animux.de) lookup for new-song jobs.

About me: before generating a new song from scratch, a "new" job first
checks whether a matching upload already exists on USDB, via usdb_syncer's
scraper module (git submodule at stack/usdb_syncer; its src/ is added to
sys.path below - see stack/Dockerfile for the runtime deps it needs).

Only usdb_syncer's plain-requests scraping layer (usdb_scraper.py: login,
song list, song details/notes) is reused - never its Qt-based
DownloadManager (song_loader.py), which needs a running Qt event loop and
is an internal, non-public API not meant for headless embedding. Audio/
video are always fetched ourselves via yt-dlp instead: USDB itself never
hosts media (copyright), only notes/cover/background, so "what's missing"
is normally everything - see orchestrator.py's JobRunner._prepare_new_job().
repair.py's existing "gap" mode then re-detects #GAP against whatever
media we actually downloaded, since USDB's community-tuned GAP was tuned
for a different (possibly re-encoded/trimmed) upload of the same audio.

usdb_scraper's own per-song functions (get_usdb_details/get_notes) don't
take a session argument - they fall back to a global SessionManager
singleton that (without a real desktop app's QSettings config) can't log
in on its own. login() below works around this by logging in with our own
plain requests.Session and installing it directly into that singleton
(SessionManager._session/_user) so every later usdb_scraper call
transparently reuses our already-authenticated session.

Optional end-to-end: only active when USDB_USERNAME/USDB_PASSWORD are
both set (USDB requires a login for search/download). If either is
unset, or any step fails, callers get None and the job falls back to
full UltraSinger generation - best-effort, must never block or fail an
otherwise-working job.
"""

import difflib
import json
import os
import re
import sys
import time
import unicodedata

USDB_SYNCER_SRC = os.environ.get(
    "USDB_SYNCER_SRC",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "usdb_syncer", "src"))
if USDB_SYNCER_SRC not in sys.path:
    sys.path.insert(0, USDB_SYNCER_SRC)

def _env_float(name, default):
    # docker-compose's ${VAR:-} substitution sets an EMPTY env var when
    # unset in .env, not a missing key - os.environ.get(name, default)
    # alone would return "" instead of the default in that case.
    val = os.environ.get(name, "").strip()
    return float(val) if val else default


MIN_MATCH_SCORE = _env_float("USDB_MIN_MATCH_SCORE", 0.90)
CATALOG_TTL_HOURS = _env_float("USDB_CATALOG_TTL_HOURS", 24.0)


# --------------------------------------------------------------------------
# name matching (pure, no network - testable without usdb_syncer at all)
# --------------------------------------------------------------------------

def normalize_name(text: str) -> str:
    """Fold a name for fuzzy comparison: strip accents/punctuation, casefold,
    collapse whitespace, so e.g. "Motörhead" ~ "Motorhead" ~ "MOTÖRHEAD"."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).strip().casefold()
    return re.sub(r"\s+", " ", text)


def name_score(a: str, b: str) -> float:
    a, b = normalize_name(a), normalize_name(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_score(entry: dict, band: str, title: str) -> float:
    """Combined artist+title similarity. Uses the WORSE (min) of the two
    scores, not an average - a perfect title match with a wildly different
    artist is still the wrong song."""
    return min(name_score(entry.get("artist", ""), band),
               name_score(entry.get("title", ""), title))


def find_candidates(catalog, band, title, min_score=None, limit=3):
    """Catalog entries scoring >= min_score, best first; ties broken by
    USDB rating then views (prefer the more community-vetted upload)."""
    min_score = MIN_MATCH_SCORE if min_score is None else min_score
    scored = [(match_score(e, band, title), e) for e in catalog]
    scored = [(s, e) for s, e in scored if s >= min_score]
    scored.sort(key=lambda pair: (
        pair[0], pair[1].get("rating", 0), pair[1].get("views", 0)), reverse=True)
    return [e for _, e in scored[:limit]]


# --------------------------------------------------------------------------
# USDB access (usdb_syncer's scraper, thinly wrapped)
# --------------------------------------------------------------------------

def login(username, password):
    """Return an authenticated requests.Session, or None (missing creds,
    bad login, or usdb_syncer not importable)."""
    username = (username or "").strip()
    password = (password or "").strip()
    if not username or not password:
        return None
    try:
        import requests
        from usdb_syncer import usdb_scraper
    except ImportError as exc:
        print(f"usdb_lookup: usdb_syncer not available: {exc}", file=sys.stderr)
        return None
    session = requests.Session()
    try:
        if not usdb_scraper.login_to_usdb(session, username, password):
            print("usdb_lookup: USDB login failed (bad credentials?)", file=sys.stderr)
            return None
        user = usdb_scraper.get_logged_in_usdb_user(session)
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_lookup: USDB login failed: {exc}", file=sys.stderr)
        return None
    if not user:
        print("usdb_lookup: USDB login did not verify", file=sys.stderr)
        return None
    # see module docstring: makes session-less usdb_scraper calls
    # (get_usdb_details/get_notes) transparently use this session
    usdb_scraper.SessionManager._session = session
    usdb_scraper.SessionManager._user = user
    return session


def fetch_catalog(session):
    """The full current USDB song list (artist/title/song_id/rating/views)
    as plain dicts - ~30k songs, paginated. Meant to be cached, see
    load_catalog(); a live call per song lookup would be far too slow."""
    from usdb_syncer import usdb_scraper
    songs = []
    for batch in usdb_scraper._get_songs_from_usdb("id", False, session=session):
        for song in batch:
            songs.append({
                "song_id": str(song.song_id), "artist": song.artist,
                "title": song.title, "rating": song.rating, "views": song.views,
            })
    return songs


def load_catalog(session, cache_path, ttl_hours=None):
    """Cached catalog: reused while younger than ttl_hours, refreshed (and
    re-saved) otherwise. A refresh failure with a stale-but-present cache
    falls back to the stale copy rather than giving up entirely."""
    ttl_hours = CATALOG_TTL_HOURS if ttl_hours is None else ttl_hours
    cached = None
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            cached = data.get("songs")
            age_hours = (time.time() - data.get("fetched_at", 0)) / 3600.0
            if cached and age_hours < ttl_hours:
                return cached
        except (OSError, json.JSONDecodeError, TypeError):
            cached = None
    try:
        songs = fetch_catalog(session)
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_lookup: catalog refresh failed: {exc}", file=sys.stderr)
        return cached or []
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time(), "songs": songs}, f)
        os.replace(tmp, cache_path)
    except OSError as exc:
        print(f"usdb_lookup: could not save catalog cache: {exc}", file=sys.stderr)
    return songs


def fetch_notes(session, song_id):
    """The song's UltraStar .txt content, or None on any failure."""
    from usdb_syncer import usdb_scraper
    from usdb_syncer.logger import song_logger
    from usdb_syncer.usdb_song import SongId
    try:
        sid = SongId.parse(str(song_id))
        return usdb_scraper.get_notes(sid, song_logger(sid))
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_lookup: could not fetch notes for {song_id}: {exc}", file=sys.stderr)
        return None


def fetch_details(session, song_id):
    """The song's SongDetails (cover_url, comments incl. video links), or
    None on any failure."""
    from usdb_syncer import usdb_scraper
    from usdb_syncer.usdb_song import SongId
    try:
        return usdb_scraper.get_usdb_details(SongId.parse(str(song_id)))
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_lookup: could not fetch details for {song_id}: {exc}", file=sys.stderr)
        return None


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def pick_video_url(details):
    """The first usable YouTube link from the song's comments (latest
    first - see SongDetails.all_comment_videos()), or None if it has
    none. This is USDB's own known-good video source, tried before our
    own songs.csv link (see module docstring / "only fill gaps")."""
    if details is None:
        return None
    for item in details.all_comment_videos():
        if YOUTUBE_ID_RE.match(item):
            return f"https://www.youtube.com/watch?v={item}"
        if item.startswith("http://") or item.startswith("https://"):
            return item
    return None


GAP_HINT_RE = re.compile(r"\bgap\b[^0-9-]{0,20}(-?\d[\d.,]*)", re.IGNORECASE)


def extract_gap_hints(details):
    """GAP values sometimes posted as corrections in a song's USDB
    comments (a community member noticing the original upload's #GAP is
    off, without ever fixing the upload itself). Purely informational -
    see orchestrator.py:prepare_usdb_job()'s use via append_comment_tag()
    - our own audio-based #GAP re-detection (repair.py's "gap" mode,
    always run regardless) is what the pipeline actually trusts; this is
    never fed back into it. A best-effort regex over free text - expect
    occasional noise/false positives, acceptable for a human-readable
    hint, not something acted on automatically."""
    if details is None:
        return []
    hints = []
    for comment in getattr(details, "comments", None) or []:
        text = getattr(getattr(comment, "contents", None), "text", "") or ""
        hints.extend(m.group(1) for m in GAP_HINT_RE.finditer(text))
    return hints


def fetch_cover_bytes(session, cover_url):
    """Best-effort cover image download; None on any failure."""
    if not cover_url:
        return None
    try:
        import requests
        resp = requests.get(cover_url, timeout=15)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:  # noqa: BLE001
        print(f"usdb_lookup: cover download failed: {exc}", file=sys.stderr)
        return None


def find_usdb_song(session, catalog, band, title):
    """Try each name-similarity candidate (best first) until one's notes
    can actually be fetched. Returns {"song_id", "txt", "details",
    "video_url", "cover_bytes"} or None (no confident match / nothing
    fetchable)."""
    for entry in find_candidates(catalog, band, title):
        song_id = entry["song_id"]
        txt = fetch_notes(session, song_id)
        if not txt:
            continue
        details = fetch_details(session, song_id)
        cover_url = getattr(details, "cover_url", None) if details else None
        return {
            "song_id": song_id, "txt": txt, "details": details,
            "video_url": pick_video_url(details),
            "cover_bytes": fetch_cover_bytes(session, cover_url) if cover_url else None,
        }
    return None
