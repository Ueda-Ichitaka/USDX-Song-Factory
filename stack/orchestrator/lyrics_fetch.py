#!/usr/bin/env python3
"""Fetch trusted lyrics for a song from the internet.

About me: queries EVERY configured source - syncedlyrics (aggregates
LRCLIB/NetEase/Musixmatch/..., free, no API key) and Genius (via
lyricsgenius, needs GENIUS_API_KEY) - and keeps the best-scoring match,
rather than stopping at the first source that returns something (a
resolv.conf-style early-stop was considered, but per-song processing
already takes minutes for demucs/whisper, so the extra latency of
querying every source is minor next to the quality benefit of always
picking the best available match). A candidate below MIN_CONFIDENCE is
discarded entirely - the caller then falls back to whisper's own
transcription (the finishing report marks which happened, see
orchestrator.py).

None of the sources return a "confidence" natively, so one is derived
per source: Genius returns ITS OWN matched artist/title, scored by string
similarity against what was asked for; syncedlyrics/other sources that
return only text (no matched metadata) get a fixed baseline confidence
(synced/timed results are trusted more than plain-text ones).

Returns line-delimited lyrics - each physical line is one sung line,
exactly the delimitation the "lyrics" repair/build mode needs to
force-align text to audio. When synced (LRC) lyrics are available, each
line also carries an approximate start time that seeds that alignment;
plain lyrics carry no timing.

Never invents or edits lyric text - a source either has the song or it
doesn't.

Usage: lyrics_fetch.py --artist "X" --title "Y" --out lyrics.json
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

# a candidate whose confidence falls below this is discarded - whisper's
# own transcription takes over instead of using a poor lyrics match
MIN_CONFIDENCE = float(os.environ.get("LYRICS_MIN_CONFIDENCE", "0.5"))

LRC_LINE_RE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\](.*)$")
# credit lines some LRC sources (esp. NetEase, in Chinese) place INSIDE
# the timed lyrics themselves rather than as [ar:.../ti:...] tags - these
# are not sung and must not become fake "lyric lines" at t=0/1/2s ahead of
# the real first line
CREDIT_LINE_RE = re.compile(
    r"^(作词|作曲|编曲|制作人|混音|母带|lyrics?\s*by|lyricist|composer|arranger"
    r"|producer|mixed\s*by|mastered\s*by)\s*[:：]", re.IGNORECASE)


def parse_lrc(lrc_text: str) -> list:
    """Parse LRC-format text into [{"text":, "start":}, ...], dropping
    metadata tags ('[ar:...]', '[ti:...]', ...), in-line credit lines
    ('作词 : ...', 'Composer: ...', ...) and blank lines."""
    lines = []
    for raw in lrc_text.splitlines():
        m = LRC_LINE_RE.match(raw.strip())
        if not m:
            continue
        minutes, seconds, text = m.groups()
        text = text.strip()
        if not text or CREDIT_LINE_RE.match(text):
            continue
        start = int(minutes) * 60 + float(seconds)
        lines.append({"text": text, "start": start})
    return lines


def parse_plain(text: str) -> list:
    """Plain lyrics: one non-empty line = one sung line, no timing."""
    return [{"text": line.strip(), "start": None}
            for line in text.splitlines() if line.strip()]


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


def fetch_lyrics(artist: str, title: str, genius_api_key: str = None):
    """Query every source and keep the best-scoring match (not a
    resolv.conf-style early-stop - see module docstring for why).
    Returns {"source": str, "lines": [...], "confidence": float} or None
    if nothing was found, or nothing cleared MIN_CONFIDENCE."""
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
    args = parser.parse_args()

    genius_api_key = os.environ.get("GENIUS_API_KEY") or None
    result = fetch_lyrics(args.artist, args.title, genius_api_key)
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
