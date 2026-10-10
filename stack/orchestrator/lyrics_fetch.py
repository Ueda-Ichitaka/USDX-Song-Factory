#!/usr/bin/env python3
"""Fetch trusted lyrics for a song from the internet.

About me: if a csv-supplied lyrics_url is given, it is tried FIRST and
used directly if it yields anything - fully trusted (confidence 1.0),
not compared against other sources, since the CSV author already vetted
it (see fetch_from_url()). Otherwise (no lyrics_url, or it failed to
yield anything) queries EVERY configured search source - syncedlyrics
(aggregates LRCLIB/NetEase/Musixmatch/..., free, no API key) and Genius
(via lyricsgenius, needs GENIUS_API_KEY) - and keeps the best-scoring
match, rather than stopping at the first source that returns something (a
resolv.conf-style early-stop was considered, but per-song processing
already takes minutes for demucs/whisper, so the extra latency of
querying every source is minor next to the quality benefit of always
picking the best available match). A candidate below MIN_CONFIDENCE is
discarded entirely - the caller then falls back to whisper's own
transcription (the finishing report marks which happened, see
orchestrator.py).

None of the search sources return a "confidence" natively, so one is
derived per source: Genius returns ITS OWN matched artist/title, scored
by string similarity against what was asked for; syncedlyrics/other
sources that return only text (no matched metadata) get a fixed baseline
confidence (synced/timed results are trusted more than plain-text ones).

Whatever the source, lines entirely in round brackets (backing voices
sung at the same time as the lead) are left out, as are section markers,
credit lines and leftover timestamps.

A lyrics_url is fetched directly, not searched: genius.com urls are
scraped via lyricsgenius' own URL-based scraper; lrclib.net track links
(lrclib.net/tracks/<id>) are read from lrclib.net's JSON API, synced lyrics
first, with the User-Agent lrclib.net requires (USER_AGENT); other http(s)
links are fetched as raw plain text or LRC; anything else is a local lyrics file
(plain text or LRC), relative to INPUT_DIR unless the path is absolute.
Arbitrary HTML lyrics sites are NOT scraped -
only Genius, since lyricsgenius already provides/maintains that scraper
(see knowledge/06-IDEAS.md's darklyrics.com writeup for why a hand-rolled
scraper against a site with no official API is a bad trade).

Returns line-delimited lyrics - each physical line is one sung line,
exactly the delimitation the "lyrics" repair/build mode needs to
force-align text to audio. When synced (LRC) lyrics are available, each
line also carries an approximate start time that seeds that alignment;
plain lyrics carry no timing.

Never invents or edits lyric text - a source either has the song or it
doesn't.

Usage: lyrics_fetch.py --artist "X" --title "Y" --out lyrics.json
       [--lyrics-url "https://..."]
Writes a JSON file: {"source": "...", "lines": [{"text": "...", "start":
12.3 or null}, ...]}, or exits 1 with nothing written if no lyrics were
found anywhere (or nothing cleared MIN_CONFIDENCE).
"""

import argparse
import difflib
import json
import os
import re
import sys
import time

# a candidate whose confidence falls below this is discarded - whisper's
# own transcription takes over instead of using a poor lyrics match
MIN_CONFIDENCE = float(os.environ.get("LYRICS_MIN_CONFIDENCE", "0.5"))

# a lyrics_url that is a file path (not an http(s) link) is relative to this
INPUT_DIR = os.environ.get("INPUT_DIR", "/data/input")

# lrclib.net track links: the page (/tracks/<id>) and the API record
# (/api/get/<id>) both name one record
LRCLIB_TRACK_RE = re.compile(
    r"^https?://(?:www\.)?lrclib\.net/(?:tracks|api/get)/(\d+)/?(?:[?#].*)?$", re.IGNORECASE)
LRCLIB_API_GET = "https://lrclib.net/api/get/{}"
# lrclib.net asks a client answering 429/503 to wait Retry-After seconds;
# the stack waits once if that is at most this long, else gives up
LRCLIB_MAX_WAIT_S = float(os.environ.get("LRCLIB_MAX_WAIT_S", "10"))


def _app_version() -> str:
    """UltraSinger's APP_VERSION (src/Settings.py), read as text so this
    script needs none of UltraSinger's imports: repo checkout or container."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "..", "..", "src", "Settings.py"),
                 "/app/UltraSinger/src/Settings.py"):
        try:
            with open(path, encoding="utf-8") as f:
                m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read())
        except OSError:
            continue
        if m:
            return m.group(1)
    return "unknown"


# lrclib.net requires clients to name themselves: name, version, project link
USER_AGENT = (f"USDX-Song-Factory/{_app_version()} "
              "(https://github.com/Ueda-Ichitaka/USDX-Song-Factory)")

# [mm:ss.xx], [mm:ss:xx] (some sources put a colon before the hundredths)
# and [mm:ss]
LRC_LINE_RE = re.compile(r"^\[(\d+):(\d+)(?:[.:](\d+))?\](.*)$")
# timestamps left at the start of a line of plain lyrics
LEADING_TIMESTAMPS_RE = re.compile(r"^(?:\[\d+:\d+(?:[.:]\d+)?\]\s*)+")
# credit lines some LRC sources (esp. NetEase, in Chinese) place INSIDE
# the timed lyrics themselves rather than as [ar:.../ti:...] tags - these
# are not sung and must not become fake "lyric lines" ahead of the real
# first line. The Chinese credit words match also without a colon ("作曲
# Arkona"), the English ones only with one, so a sung line such as
# "Producer of my dreams" stays
CREDIT_LINE_RE = re.compile(
    r"^(?:(?:作词|作曲|编曲|制作人|混音|母带)(?:\s*[:：]|\s)"
    r"|(?:lyrics?\s*by|lyricist|composer|arranger|producer|mixed\s*by"
    r"|mastered\s*by)\s*[:：])", re.IGNORECASE)


# a line that is one group in round brackets is a backing voice sung at the
# same time as the lead (a canon, the second voice of a duet, a choir call);
# aligned as its own line it pushes the lead lines later (measured on the 8
# fetched songs that have such lines, stack/work/bench/an25_brackets.py:
# the other lines align at least as well without them, Nanowar of Steel -
# Valhalleluja's canon part lands where it is sung)
BACKING_LINE_RE = re.compile(r"^\([^()]*\)$")


def parse_lrc(lrc_text: str) -> list:
    """Parse LRC-format text into [{"text":, "start":}, ...], dropping
    metadata tags ('[ar:...]', '[ti:...]', ...), in-line credit lines
    ('作词 : ...', 'Composer: ...', ...), backing lines entirely in
    brackets and blank lines."""
    lines = []
    for raw in lrc_text.splitlines():
        m = LRC_LINE_RE.match(raw.strip())
        if not m:
            continue
        minutes, seconds, fraction, text = m.groups()
        text = text.strip()
        if not text or CREDIT_LINE_RE.match(text) or BACKING_LINE_RE.match(text):
            continue
        start = int(minutes) * 60 + int(seconds)
        if fraction:
            start += int(fraction) / 10 ** len(fraction)
        lines.append({"text": text, "start": start})
    return lines


# a repeat count such as "x2", "2x", "x 2" or "×2", inside the tag
# ("[Chorus x2]") or after it, bare or bracketed ("[Chorus] [x2]",
# "[Refrain] (2x)", "[Chorus] x2")
_REPEAT_COUNT = r"(?:[x×]\s*\d+|\d+\s*[x×])"
SECTION_TAG_RE = re.compile(
    rf"^\[\s*([^\[\]]+?)(?:\s+{_REPEAT_COUNT})?\s*\]"
    rf"(?:\s*(?:{_REPEAT_COUNT}|\[\s*{_REPEAT_COUNT}\s*\]|\(\s*{_REPEAT_COUNT}\s*\)))?$",
    re.IGNORECASE)


def resolve_and_strip_section_markers(text: str) -> str:
    """Genius (and some plain lyrics_url sources) lyrics carry bracket
    section markers ("[Chorus]", "[Verse 1]", "[Strophe 1]", ...). Left
    as-is these become literal "sung" lines that repair.py's force-
    alignment tries to match against real singing - a bad alignment
    right there that then drifts everything after it.

    A tag's first occurrence with real lyrics following it just has its
    tag line dropped (the lyrics themselves are kept, and remembered). A
    later BARE occurrence of the same tag (nothing but blank lines before
    the next tag or the end of the text) is a shorthand placeholder for
    "repeat that section" and is resolved to the first occurrence's real
    content - dropping it outright would silently delete real sung
    lyrics from the song. A later occurrence that DOES have its own
    (different) content is kept as-is, never overwritten by the first
    capture. A bare tag with nothing captured for it yet is dropped
    silently (nothing to substitute, never guessed).

    A repeat count on a tag ("[Chorus] [x2]", "[Refrain] (2x)",
    "[Chorus x2]") makes it the same tag as without the count; the count
    itself is not applied - the content is used once."""
    raw_lines = text.splitlines()
    n = len(raw_lines)
    captured = {}
    output_lines = []
    i = 0
    while i < n:
        line = raw_lines[i]
        m = SECTION_TAG_RE.match(line.strip())
        if not m:
            output_lines.append(line)
            i += 1
            continue
        tag_key = m.group(1).strip().lower()
        j = i + 1
        content = []
        while j < n and not SECTION_TAG_RE.match(raw_lines[j].strip()):
            content.append(raw_lines[j])
            j += 1
        if any(l.strip() for l in content):
            if tag_key not in captured:
                captured[tag_key] = content
            output_lines.extend(content)
        elif tag_key in captured:
            output_lines.extend(captured[tag_key])
        i = j
    return "\n".join(output_lines)


def parse_plain(text: str) -> list:
    """Plain lyrics: one non-empty line = one sung line, no timing.
    Timestamps left at the start of a line, credit lines and backing lines
    entirely in brackets are dropped."""
    text = resolve_and_strip_section_markers(text)
    lines = []
    for line in text.splitlines():
        line = LEADING_TIMESTAMPS_RE.sub("", line.strip()).strip()
        if line and not CREDIT_LINE_RE.match(line) and not BACKING_LINE_RE.match(line):
            lines.append({"text": line, "start": None})
    return lines


def _similarity(a: str, b: str) -> float:
    """Case/whitespace-insensitive character-level similarity (0..1)."""
    return difflib.SequenceMatcher(
        None, (a or "").lower().strip(), (b or "").lower().strip()).ratio()


def _strip_genius_page_artifacts(lines: list) -> list:
    """Genius' scraped lyrics page text carries two predictable artifacts:
    a leading '<Title> Lyrics' line and a trailing '...Embed'/digit blob
    from their page's share widget. Strip them if present."""
    if lines and lines[0]["text"].lower().endswith("lyrics"):
        lines = lines[1:]
    if lines and re.search(r"\d*\s*embed$", lines[-1]["text"], re.IGNORECASE):
        lines = lines[:-1]
    return lines


def fetch_syncedlyrics(artist: str, title: str):
    """Try syncedlyrics (free, no API key). Prefers synced (LRC) output
    for its approximate per-line timing; falls back to plain text.

    syncedlyrics doesn't return which song it actually matched, so there
    is no way to score similarity against the request - a fixed baseline
    confidence is used instead (synced/timed results trusted more than
    plain-text ones, since a curated LRC is usually a stronger signal of
    a correct match than bare scraped text).
    """
    try:
        import syncedlyrics
    except ImportError:
        return None

    query = f"{title} {artist}".strip()
    try:
        lrc = syncedlyrics.search(query)
        if lrc:
            lines = parse_lrc(lrc)
            if lines:
                return {"source": "syncedlyrics (synced)", "lines": lines,
                        "confidence": 0.9}

        plain = syncedlyrics.search(query, plain_only=True)
        if plain:
            lines = parse_plain(plain)
            if lines:
                return {"source": "syncedlyrics (plain)", "lines": lines,
                        "confidence": 0.7}
    except Exception as exc:  # noqa: BLE001
        print(f"lyrics_fetch: syncedlyrics failed: {exc}", file=sys.stderr)
    return None


def fetch_genius(artist: str, title: str, api_key: str):
    """Try Genius (needs an API key: https://genius.com/api-clients).

    Genius returns its OWN matched artist/title, so confidence is scored
    as the average string-similarity between what was asked for and what
    it actually matched - a real signal, unlike syncedlyrics' fixed one.
    """
    if not api_key:
        return None
    try:
        import lyricsgenius
    except ImportError:
        return None

    try:
        genius = lyricsgenius.Genius(api_key)
        genius.verbose = False
        genius.remove_section_headers = True
        genius.skip_non_songs = True
        genius.retries = 2

        song = genius.search_song(title, artist)
        if song is None or not song.lyrics:
            return None

        lines = _strip_genius_page_artifacts(parse_plain(song.lyrics))
        if not lines:
            return None
        confidence = (_similarity(artist, getattr(song, "artist", ""))
                     + _similarity(title, getattr(song, "title", ""))) / 2
        return {"source": "genius", "lines": lines, "confidence": confidence}
    except Exception as exc:  # noqa: BLE001
        print(f"lyrics_fetch: genius failed: {exc}", file=sys.stderr)
    return None


def _fetch_genius_url(url: str, api_key: str):
    """Scrape a genius.com song page directly (no search involved) - the
    URL itself already identifies the exact song, so this is fully
    trusted (confidence 1.0) once lyrics are actually found."""
    if not api_key:
        return None
    try:
        import lyricsgenius
    except ImportError:
        return None
    try:
        genius = lyricsgenius.Genius(api_key)
        text = genius.lyrics(song_url=url)
        if not text:
            return None
        lines = _strip_genius_page_artifacts(parse_plain(text))
        if not lines:
            return None
        return {"source": "lyrics_url (genius)", "lines": lines, "confidence": 1.0}
    except Exception as exc:  # noqa: BLE001
        print(f"lyrics_fetch: genius url fetch failed: {exc}", file=sys.stderr)
    return None


def _fetch_plain_or_lrc_url(url: str):
    """Fetch a url's raw body and parse it as LRC if it looks timed,
    otherwise as plain text. No HTML scraping - the url must point
    directly at lyrics text (see module docstring)."""
    try:
        import requests
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        text = resp.text
    except Exception as exc:  # noqa: BLE001
        print(f"lyrics_fetch: lyrics_url fetch failed: {exc}", file=sys.stderr)
        return None

    lines = parse_lrc(text)
    if lines:
        return {"source": "lyrics_url (lrc)", "lines": lines, "confidence": 1.0}
    lines = parse_plain(text)
    if lines:
        return {"source": "lyrics_url (plain)", "lines": lines, "confidence": 1.0}
    return None


def _fetch_lrclib_url(url: str):
    """One lrclib.net record, from a track page or API link, read from
    lrclib.net's JSON API: synced lyrics first (their line times seed the
    alignment, as with syncedlyrics), plain lyrics otherwise. A search link
    names no single song and an instrumental record has no lyrics -> None.
    A 429/503 waits Retry-After once when it is at most LRCLIB_MAX_WAIT_S."""
    m = LRCLIB_TRACK_RE.match(url.strip())
    if not m:
        print("lyrics_fetch: lrclib.net link is not a track "
              "(expected lrclib.net/tracks/<id>)", file=sys.stderr)
        return None
    try:
        import requests
        api_url = LRCLIB_API_GET.format(m.group(1))
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(api_url, headers=headers, timeout=15)
        if resp.status_code in (429, 503):
            try:
                wait = float(resp.headers.get("Retry-After", ""))
            except ValueError:
                wait = None
            if wait is not None and 0 <= wait <= LRCLIB_MAX_WAIT_S:
                time.sleep(wait)
                resp = requests.get(api_url, headers=headers, timeout=15)
        if not resp.ok:
            print(f"lyrics_fetch: lrclib.net answered {resp.status_code}", file=sys.stderr)
            return None
        record = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"lyrics_fetch: lrclib.net fetch failed: {exc}", file=sys.stderr)
        return None

    if record.get("instrumental"):
        return None
    lines = parse_lrc(record.get("syncedLyrics") or "")
    if lines:
        return {"source": "lyrics_url (lrclib synced)", "lines": lines, "confidence": 1.0}
    lines = parse_plain(record.get("plainLyrics") or "")
    if lines:
        return {"source": "lyrics_url (lrclib plain)", "lines": lines, "confidence": 1.0}
    return None


def _read_lyrics_file(value: str):
    """A local lyrics file (plain text or LRC), relative to INPUT_DIR
    unless absolute - the same rule as a local media file in the url
    column (orchestrator.resolve_song_input())."""
    path = value if os.path.isabs(value) else os.path.join(INPUT_DIR, value)
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"lyrics_fetch: lyrics file not readable: {exc}", file=sys.stderr)
        return None
    lines = parse_lrc(text) or parse_plain(text)
    if not lines:
        return None
    return {"source": "lyrics_url (file)", "lines": lines, "confidence": 1.0}


def fetch_from_url(url: str, genius_api_key: str = None):
    """Fetch lyrics directly from a csv-supplied lyrics_url - explicitly
    trusted (the CSV author already vetted it), so it is used as-is
    rather than confidence-scored against other sources (see
    fetch_lyrics()). genius.com urls are scraped via lyricsgenius;
    lrclib.net track links are read from lrclib.net's API
    (_fetch_lrclib_url()); other http(s) links are fetched as raw plain
    text or LRC - arbitrary HTML
    lyrics sites are NOT scraped (only Genius is, since lyricsgenius
    already provides/maintains that scraper - see 06-IDEAS.md's
    darklyrics lesson on why a hand-rolled scraper is a bad trade).
    Anything else is a local lyrics file (see _read_lyrics_file())."""
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        return _read_lyrics_file(url)
    if "genius.com/" in url.lower():
        return _fetch_genius_url(url, genius_api_key)
    if "lrclib.net/" in url.lower():
        return _fetch_lrclib_url(url)
    return _fetch_plain_or_lrc_url(url)


def fetch_lyrics(artist: str, title: str, genius_api_key: str = None,
                 lyrics_url: str = None):
    """A given lyrics_url is tried first and used directly if it yields
    anything (trusted, not confidence-compared - see fetch_from_url()).
    Otherwise (no lyrics_url, or it failed) queries every search source
    and keeps the best-scoring match (not a resolv.conf-style early-stop
    - see module docstring for why). Returns {"source": str, "lines":
    [...], "confidence": float} or None if nothing was found, or nothing
    cleared MIN_CONFIDENCE."""
    if lyrics_url:
        from_url = fetch_from_url(lyrics_url, genius_api_key)
        if from_url is not None:
            return from_url
        print(f"lyrics_fetch: lyrics_url given but yielded nothing, "
              f"falling back to search sources", file=sys.stderr)

    candidates = [c for c in (
        fetch_syncedlyrics(artist, title),
        fetch_genius(artist, title, genius_api_key),
    ) if c is not None]
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c["confidence"])
    if best["confidence"] < MIN_CONFIDENCE:
        return None
    return best


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--artist", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--lyrics-url", default=None,
                        help="csv-supplied trusted lyrics url or lyrics file "
                             "path (relative to INPUT_DIR), tried first")
    args = parser.parse_args()

    genius_api_key = os.environ.get("GENIUS_API_KEY") or None
    result = fetch_lyrics(args.artist, args.title, genius_api_key, args.lyrics_url)
    if result is None:
        print("LYRICS_NOT_FOUND")
        sys.exit(1)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"LYRICS_FOUND source={result['source']!r} "
          f"confidence={result['confidence']:.2f} lines={len(result['lines'])}")
    sys.exit(0)


if __name__ == "__main__":
    main()
