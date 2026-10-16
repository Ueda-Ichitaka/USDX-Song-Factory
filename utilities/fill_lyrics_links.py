#!/usr/bin/env python3
"""Fill empty lyrics_url cells of a broken.csv with lrclib.net (or Genius) links.

About me: for every row of the stack's broken.csv without a lyrics_url, the
song's lyrics are looked up online:

  lrclib.net  the search API by artist and title; a hit counts when artist
              and title match closely (difflib similarity >= 0.7 / 0.85,
              bracket parts like "(Album Version)" ignored) and - when the
              song folder in the stack input is found - its length is within
              MAX_LEN_DIFF_S of the folder's own audio (same version).
              Synced lyrics first. Link: https://lrclib.net/tracks/<id>
  Genius      only when GENIUS_API_KEY is set (environment or stack/.env)
              and lrclib.net has nothing: the API search, same name rules;
              the song page URL.

Rows that already have a link are never touched. The requests name the
stack and its GitHub page (User-Agent, as lrclib.net asks for) and wait
`pause` seconds between songs. Without --apply nothing is written.

  python3 fill_lyrics_links.py [--csv stack/input/broken.csv] [--input DIR] [--apply]
"""
import argparse
import csv
import difflib
import io
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import songfile  # noqa: E402

MAX_LEN_DIFF_S = 4.0
MIN_ARTIST = 0.7
MIN_TITLE = 0.85
HEADER = ["band", "song name", "category", "description", "lyrics_url", "language",
          "cover_url", "genre", "trait"]


def user_agent() -> str:
    """The stack's own User-Agent (stack/orchestrator/lyrics_fetch.py)."""
    try:
        sys.path.insert(0, os.path.join(REPO, "stack", "orchestrator"))
        import lyrics_fetch
        return lyrics_fetch.USER_AGENT
    except Exception:  # noqa: BLE001 - the link lookup works without it
        return "USDX-Song-Factory (https://github.com/Ueda-Ichitaka/USDX-Song-Factory)"


def clean(text: str) -> str:
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text or "")
    text = text.replace("’", "'").replace("Disney's", "").replace("Disney’s", "")
    return " ".join(text.lower().split())


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, clean(a), clean(b)).ratio()


def pick_lrclib(hits: list, band: str, title: str, seconds):
    """The id of the best lrclib.net search hit, or None."""
    good = []
    for hit in hits or []:
        if hit.get("instrumental"):
            continue
        if similar(hit.get("artistName", ""), band) < MIN_ARTIST:
            continue
        if similar(hit.get("trackName", ""), title) < MIN_TITLE:
            continue
        diff = abs((hit.get("duration") or 0) - seconds) if seconds else 0.0
        if seconds and diff > MAX_LEN_DIFF_S:
            continue
        good.append((not hit.get("syncedLyrics"), diff, hit["id"]))
    return min(good)[2] if good else None


def pick_genius(hits: list, band: str, title: str):
    """The URL of the Genius search hit with that artist and title, or None."""
    for hit in hits or []:
        result = hit.get("result", {})
        if (similar(result.get("primary_artist", {}).get("name", ""), band) >= MIN_ARTIST
                and similar(result.get("title", ""), title) >= MIN_TITLE):
            return result.get("url")
    return None


def lrclib_search(band: str, title: str) -> list:
    import requests
    response = requests.get("https://lrclib.net/api/search",
                            params={"artist_name": clean(band) or band,
                                    "track_name": clean(title) or title},
                            headers={"User-Agent": user_agent()}, timeout=20)
    return response.json() if response.ok else []


def genius_key():
    key = os.environ.get("GENIUS_API_KEY")
    env_file = os.path.join(REPO, "stack", ".env")
    if not key and os.path.isfile(env_file):
        for line in open(env_file, encoding="utf-8"):
            if line.startswith("GENIUS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    return key or None


def genius_search(band: str, title: str) -> list:
    key = genius_key()
    if not key:
        return []
    import requests
    response = requests.get("https://api.genius.com/search",
                            params={"q": f"{clean(band)} {clean(title)}"},
                            headers={"Authorization": f"Bearer {key}"}, timeout=20)
    return response.json()["response"]["hits"] if response.ok else []


def find_folder(input_dir: str, band: str, title: str):
    """The folder in input_dir whose song file has this artist and title."""
    for name in sorted(os.listdir(input_dir)):
        folder = os.path.join(input_dir, name)
        if not os.path.isdir(folder):
            continue
        for path in songfile.song_txts(folder):
            tags = songfile.read(path).tags
            if similar(tags.get("ARTIST", ""), band) > 0.9 and similar(tags.get("TITLE", ""), title) > 0.9:
                return folder
    return None


def audio_seconds(folder: str):
    """Length of the folder's audio (ffprobe), or None."""
    names = [n for n in sorted(os.listdir(folder))
             if n.lower().endswith((".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".mp4"))]
    for name in names:
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                  "-of", "csv=p=0", os.path.join(folder, name)],
                                 capture_output=True, text=True, timeout=60).stdout
            return float(out.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return None


def read_csv(path: str):
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    return rows[0], [r + [""] * (len(rows[0]) - len(r)) for r in rows[1:] if any(r)]


def write_csv(path: str, header: list, rows: list):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    with open(path, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())


def fill(csv_path: str, input_dir: str, lrclib_search=lrclib_search, genius_search=genius_search,
         apply: bool = False, pause: float = 1.0) -> dict:
    """Look up a link for every row without one; returns {"filled", "not_found"}."""
    header, rows = read_csv(csv_path)
    i_band, i_title, i_url = header.index("band"), header.index("song name"), header.index("lyrics_url")
    filled, not_found = 0, []
    for row in rows:
        if row[i_url].strip():
            continue
        band, title = row[i_band], row[i_title]
        folder = find_folder(input_dir, band, title) if os.path.isdir(input_dir) else None
        seconds = audio_seconds(folder) if folder else None
        url = None
        try:
            track = pick_lrclib(lrclib_search(band, title), band, title, seconds)
            url = f"https://lrclib.net/tracks/{track}" if track else None
            if not url:
                url = pick_genius(genius_search(band, title), band, title)
        except Exception as exc:  # noqa: BLE001 - one failed lookup must not stop the rest
            print(f"  {band} - {title}: lookup failed ({exc})")
        if url:
            row[i_url] = url
            filled += 1
        else:
            not_found.append(f"{band} - {title}")
        print(f"{band} - {title}: {url or 'nothing found'}")
        time.sleep(pause)
    if apply and filled:
        shutil.copy2(csv_path, f"{csv_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        write_csv(csv_path, header, rows)
    return {"filled": filled, "not_found": not_found}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", default=os.path.join(REPO, "stack", "input", "broken.csv"))
    parser.add_argument("--input", default=None, help="the stack input folder (default: next to the csv)")
    parser.add_argument("--apply", action="store_true", help="write the csv (a backup is kept)")
    args = parser.parse_args()
    result = fill(args.csv, args.input or os.path.dirname(os.path.abspath(args.csv)), apply=args.apply)
    print(f"\n{result['filled']} links found, {len(result['not_found'])} without"
          + ("" if args.apply else " (dry run - nothing written, use --apply)"))


if __name__ == "__main__":
    main()
