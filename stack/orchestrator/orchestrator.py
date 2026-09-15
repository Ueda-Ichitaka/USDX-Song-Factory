#!/usr/bin/env python3
"""UltraSinger batch orchestrator.

Automates UltraSinger inside the docker stack:

  * creates new songs from the song list (songs.csv / songs.txt)
  * repairs existing songs placed in the input folder
  * tracks per-song state (state/state.json) so runs can be resumed
  * streams verbose output of every job to stdout and to logs/<job>.log
  * renders a progress overview (created/left, repaired/left, ETA)

Commands
--------
  (no args)      run all pending jobs
  list           show the planned jobs and their status (dry run)
  report         print + write the tabular finishing report (output/report.md):
                 which songs were created, which were repaired, which failed
  progress       print the progress overview once
  progress -w    continuously refresh the progress overview (watch mode)
  run-one URL    create a single new song (no state tracking)
  repair-one DIR repair a single song folder (no state tracking)
  reset          mark failed jobs as pending again (retry on next run)
  reset --all    mark ALL jobs (incl. done) as pending again

A "new" song's url column may be a YouTube link (starting with "https://")
or a local audio/video file path (resolved relative to input/ when not
absolute) - see resolve_song_input(). A failed job's partial output is
moved to output/failed/<kind>/ so it never pollutes the live song library.
Non-Latin lyrics (Cyrillic/Korean/Japanese) are romanized automatically
after a successful job (set ROMANIZE=0 to disable) - see romanize.py.
Every new song also tries a real online lyrics lookup (syncedlyrics, then
Genius if GENIUS_API_KEY is set) before trusting whisper's own
transcription - see run_lyrics_step()/lyrics_fetch.py (set LYRICS_ENABLED=0
to disable). Before any of that, a new song first checks whether a
matching upload already exists on USDB - usdb.animux.de (when
USDB_USERNAME/USDB_PASSWORD are set), then usdb.eu (when USDB_EU_EMAIL/
USDB_EU_PASSWORD are set) - and, if a confident match is found on either,
reuses its notes/timing instead of generating from scratch - see
prepare_usdb_job()/find_usdb_match()/usdb_lookup.py/usdb_eu_lookup.py.
A repair job whose input folder has a lyrics.txt runs in "lyrics" mode
instead (see repair.py): force-aligns that trusted text, replacing the
existing lyrics rather than just re-timing them.

While a run is active you can attach to the container
(`docker compose attach ultrasinger`) and use these interactive commands:

  s / status    show the progress overview
  skip          abort the current job and continue with the next one
  stop / q      finish the current job, then exit
"""

import csv
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

import broken_report
import resource_monitor
import resource_profile
import usdb_eu_lookup
import usdb_lookup

# --------------------------------------------------------------------------
# configuration (env, with sensible defaults)
# --------------------------------------------------------------------------

SONGS_FILE = os.environ.get("SONGS_FILE", "/data/input/song-requests.csv")
INPUT_DIR = os.environ.get("INPUT_DIR", "/data/input")
BROKEN_CSV = os.environ.get("BROKEN_CSV", "/data/input/broken.csv")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/data/output")
NEW_SONGS_DIR = os.environ.get("NEW_SONGS_DIR", os.path.join(OUTPUT_DIR, "new"))
REPAIRED_DIR = os.environ.get("REPAIRED_DIR", os.path.join(OUTPUT_DIR, "repaired"))
FAILED_DIR = os.environ.get("FAILED_DIR", os.path.join(OUTPUT_DIR, "failed"))
REPORT_FILE = os.environ.get("REPORT_FILE", os.path.join(OUTPUT_DIR, "report.md"))
STATE_DIR = os.environ.get("STATE_DIR", "/data/state")
LOGS_DIR = os.environ.get("LOGS_DIR", "/data/logs")
WORK_DIR = os.environ.get("WORK_DIR", "/data/work")
COOKIES_FILE = os.environ.get("COOKIES_FILE", "/data/cookies/cookies.txt")

DEVICE = os.environ.get("DEVICE", "cpu")
WHISPER_ON_CPU = os.environ.get("WHISPER_ON_CPU", "") not in ("", "0", "false")
REPAIR_MODE = os.environ.get("REPAIR_MODE", "sync")
EXTRA_ARGS = os.environ.get("ULTRASINGER_ARGS", "")
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "2"))
JOB_TIMEOUT_MIN = float(os.environ.get("JOB_TIMEOUT_MIN", "240"))


def _env_float(name):
    val = os.environ.get(name, "").strip()
    return float(val) if val else None


def _env_int(name):
    val = os.environ.get(name, "").strip()
    return int(val) if val else None


# declared resource budget for this stack (all optional) - see
# resource_profile.py for how these drive model/quality auto-selection.
# A safety margin is subtracted from RAM/VRAM before they're used for
# selection - a declared figure (esp. VRAM: the GPU usually also drives
# monitors/a desktop, not just this stack) is rarely ALL available headroom.
# Defaults (1.5 GB RAM, 2 GB VRAM) are a starting point, not a guarantee -
# raise them if you still see memory pressure.
STACK_RAM_SAFETY_MARGIN_GB = _env_float("STACK_RAM_SAFETY_MARGIN_GB")
if STACK_RAM_SAFETY_MARGIN_GB is None:
    STACK_RAM_SAFETY_MARGIN_GB = 1.5
STACK_VRAM_SAFETY_MARGIN_GB = _env_float("STACK_VRAM_SAFETY_MARGIN_GB")
if STACK_VRAM_SAFETY_MARGIN_GB is None:
    STACK_VRAM_SAFETY_MARGIN_GB = 2.0

STACK_RAM_GB = resource_profile.apply_safety_margin(
    _env_float("STACK_RAM_GB"), STACK_RAM_SAFETY_MARGIN_GB)
STACK_SWAP_GB = _env_float("STACK_SWAP_GB")
STACK_CPU_CORES = _env_int("STACK_CPU_CORES")
STACK_VRAM_GB = resource_profile.apply_safety_margin(
    _env_float("STACK_VRAM_GB"), STACK_VRAM_SAFETY_MARGIN_GB)

# explicit overrides always win over auto-selection; auto-selection wins
# over the hardcoded fallback default when a resource budget was given
_WHISPER_MODEL_OVERRIDE = os.environ.get("WHISPER_MODEL", "").strip() or None
_DEMUCS_MODEL_OVERRIDE = os.environ.get("DEMUCS_MODEL", "").strip() or None
_WHISPER_BATCH_SIZE_OVERRIDE = _env_int("WHISPER_BATCH_SIZE")

_auto_whisper_model, _ = resource_profile.select_whisper_model(
    STACK_RAM_GB, STACK_SWAP_GB, STACK_CPU_CORES)
_auto_demucs_model, _ = resource_profile.select_demucs_model(
    DEVICE, STACK_VRAM_GB, STACK_RAM_GB, STACK_SWAP_GB, STACK_CPU_CORES)
_auto_batch_size, _ = resource_profile.select_whisper_batch_size(STACK_CPU_CORES)

WHISPER_MODEL = _WHISPER_MODEL_OVERRIDE or _auto_whisper_model or "large-v2"
DEMUCS_MODEL = _DEMUCS_MODEL_OVERRIDE or _auto_demucs_model or resource_profile.DEMUCS_MODEL_DEFAULT
WHISPER_BATCH_SIZE = _WHISPER_BATCH_SIZE_OVERRIDE or _auto_batch_size  # None = let UltraSinger use its own default

# re-export the RESOLVED values into this process's own environment - every
# job runs as a subprocess with env=os.environ.copy(), so without this a
# child (e.g. repair.py's recreate_song() fallback) would only see the raw,
# still-empty WHISPER_MODEL/DEMUCS_MODEL env vars docker-compose passed in,
# not what auto-selection actually picked
os.environ["WHISPER_MODEL"] = WHISPER_MODEL
os.environ["DEMUCS_MODEL"] = DEMUCS_MODEL
if WHISPER_BATCH_SIZE:
    os.environ["WHISPER_BATCH_SIZE"] = str(WHISPER_BATCH_SIZE)

ULTRASINGER_CWD = "/app/UltraSinger/src"
ULTRASINGER_PY = os.path.join(ULTRASINGER_CWD, "UltraSinger.py")
REPAIR_PY = "/app/orchestrator/repair.py"
ROMANIZE_PY = "/app/orchestrator/romanize.py"
ROMANIZE_ENABLED = os.environ.get("ROMANIZE", "1") not in ("0", "false", "no")
LYRICS_FETCH_PY = "/app/orchestrator/lyrics_fetch.py"
LYRICS_ENABLED = os.environ.get("LYRICS_ENABLED", "1") not in ("0", "false", "no")
GENIUS_API_KEY = os.environ.get("GENIUS_API_KEY", "") or None

# USDB (usdb.animux.de) requires a login for search/download - the whole
# "prefer an existing upload" step (see prepare_usdb_job()) is a no-op
# without both of these set. See usdb_lookup.py for the integration itself.
USDB_USERNAME = os.environ.get("USDB_USERNAME", "")
USDB_PASSWORD = os.environ.get("USDB_PASSWORD", "")
USDB_CATALOG_CACHE = os.path.join(STATE_DIR, "usdb_catalog.json")

# usdb.eu - a separate, newer UltraStar database (different site/account,
# not covered by the usdb_syncer submodule) - see usdb_eu_lookup.py and
# 02-DESIGN.md "usdb.eu integration". Independently optional; tried as a
# second source when usdb.animux.de above has no match, see find_usdb_match().
USDB_EU_EMAIL = os.environ.get("USDB_EU_EMAIL", "")
USDB_EU_PASSWORD = os.environ.get("USDB_EU_PASSWORD", "")

# minimum #ARTIST/#TITLE-vs-broken.csv-row match score (see match_broken_report())
BROKEN_MATCH_MIN_SCORE = _env_float("BROKEN_MATCH_MIN_SCORE")
if BROKEN_MATCH_MIN_SCORE is None:
    BROKEN_MATCH_MIN_SCORE = 0.85

STATE_FILE = os.path.join(STATE_DIR, "state.json")

PRINT_LOCK = threading.Lock()

# module-level control so signal handlers can reach it
CONTROL = None

# lazy USDB session/catalog, shared across all jobs in this run - see
# get_usdb_session_and_catalog(). Sticky: a failed login/catalog load
# marks USDB unavailable for the rest of the run instead of retrying it
# (and hammering usdb.animux.de) for every remaining job.
_usdb_session = None
_usdb_catalog = None
_usdb_unavailable = False
_usdb_eu_session = None
_usdb_eu_unavailable = False


class Control:
    """Shared flags between the stdin listener, signals and the run loop."""

    def __init__(self):
        self.terminating = False
        self.stop_after_current = False
        self.skip_current = False
        self.lock = threading.Lock()

    def request_stop(self):
        with self.lock:
            self.terminating = True

    def request_skip(self):
        with self.lock:
            self.skip_current = True

    def clear_skip(self):
        with self.lock:
            self.skip_current = False

    def request_stop_after_current(self):
        with self.lock:
            self.stop_after_current = True

    def should_skip(self):
        with self.lock:
            return self.skip_current

    def should_stop_after_current(self):
        with self.lock:
            return self.stop_after_current

    def is_terminating(self):
        with self.lock:
            return self.terminating


def out(msg: str = "") -> None:
    with PRINT_LOCK:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fmt_duration(seconds) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug[:80] or "job"


def safe_dirname(name: str) -> str:
    """A human-readable name (e.g. a job's "Band - Title" label) made safe
    to use AS a directory's own basename. Unlike slugify() (log filenames/
    staging keys - lossy on purpose, never user-facing), this preserves
    casing/spaces/most punctuation: every song folder in this stack
    already looks like "ASP - Ich will brennen" or even "Therion -
    Poupée De Cire, Poupée De Son" - only characters that would actually
    break a path (a literal "/" or a NUL byte) get replaced."""
    name = (name or "").replace("\x00", "-").replace("/", "-").strip(". ")
    return name[:200] or "song"


def job_display_name(job: dict) -> str:
    """The human-readable name for `job`, for anything whose basename ends
    up user-facing (see safe_dirname()) - NOT slugify(), which is for
    internal-only filenames (logs, cache keys). Prefers the job's own
    "label" (what build_job_plan() always sets for a real job); a repair
    job without one falls back to its song_dir's basename (the original
    folder's own name); a new job falls back to "band - title"; anything
    else falls back to a slug of the job id (never empty)."""
    label = (job.get("label") or "").strip()
    if label:
        return label
    if job.get("kind") == "repair" and job.get("song_dir"):
        return os.path.basename(job["song_dir"].rstrip("/"))
    band = (job.get("band") or "").strip()
    title = (job.get("title") or "").strip()
    combined = " - ".join(x for x in (band, title) if x)
    return combined or slugify(job.get("id", "job"))


def get_usdb_session_and_catalog():
    """Log in to USDB and load its song catalog, once per orchestrator
    run (see prepare_usdb_job() for how a job uses this). Returns
    (session, catalog) or (None, None) when USDB isn't configured or
    login/catalog loading failed - callers must treat that as "skip USDB,
    fall back to normal generation", never as an error."""
    global _usdb_session, _usdb_catalog, _usdb_unavailable
    if _usdb_unavailable or not (USDB_USERNAME and USDB_PASSWORD):
        return None, None
    if _usdb_session is None:
        _usdb_session = usdb_lookup.login(USDB_USERNAME, USDB_PASSWORD)
        if _usdb_session is None:
            _usdb_unavailable = True
            return None, None
        _usdb_catalog = usdb_lookup.load_catalog(_usdb_session, USDB_CATALOG_CACHE)
        if not _usdb_catalog:
            _usdb_unavailable = True
            return None, None
    return _usdb_session, _usdb_catalog


def get_usdb_eu_session():
    """Log in to usdb.eu, once per orchestrator run - same sticky-fail
    behavior as get_usdb_session_and_catalog(), independently of it (a
    misconfigured/missing usdb.eu account never blocks the usdb.animux.de
    source, or vice versa)."""
    global _usdb_eu_session, _usdb_eu_unavailable
    if _usdb_eu_unavailable or not (USDB_EU_EMAIL and USDB_EU_PASSWORD):
        return None
    if _usdb_eu_session is None:
        _usdb_eu_session = usdb_eu_lookup.login(USDB_EU_EMAIL, USDB_EU_PASSWORD)
        if _usdb_eu_session is None:
            _usdb_eu_unavailable = True
            return None
    return _usdb_eu_session


def find_usdb_match(band: str, title: str) -> dict:
    """Try usdb.animux.de first (much larger, longer-established catalog
    - see 02-DESIGN.md), then usdb.eu as a second source. Returns a
    uniform {"song_id", "txt", "video_url", "cover_bytes", "site"} dict,
    or None if neither source has (or could reach) a confident match.
    Each source is independently optional - missing/failing credentials
    for one never block the other."""
    session, catalog = get_usdb_session_and_catalog()
    if session is not None:
        match = usdb_lookup.find_usdb_song(session, catalog, band, title)
        if match is not None:
            match["site"] = "animux"
            return match

    eu_session = get_usdb_eu_session()
    if eu_session is not None:
        match = usdb_eu_lookup.find_usdb_eu_song(eu_session, band, title)
        if match is not None:
            # usdb.eu comment-video-link scraping isn't implemented yet
            # (see knowledge/06-IDEAS.md) - the job's own songs.csv url is
            # always used for these matches. cover_bytes IS available
            # (comes straight out of the download zip).
            match["video_url"] = None
            match["site"] = "eu"
            return match
    return None


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

class State:
    def __init__(self):
        self.data = {"version": 1, "jobs": {}}

    def load(self, recover_running=True):
        if os.path.isfile(STATE_FILE):
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    self.data = json.load(f)
                if recover_running:
                    # jobs that were running when the container died -> failed
                    for job in self.data["jobs"].values():
                        if job["status"] == "running":
                            job["status"] = "failed"
                            job["error"] = "interrupted (container stopped)"
            except (json.JSONDecodeError, KeyError):
                print("WARNING: state file is corrupt, starting fresh")
                self.data = {"version": 1, "jobs": {}}

    def save(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)

    def get_or_create(self, job_id: str, **fields) -> dict:
        job = self.data["jobs"].get(job_id)
        if job is None:
            job = {
                "id": job_id,
                "status": "pending",
                "attempts": 0,
                "error": None,
                "output_path": None,
                "quarantined_output": None,
                "repair_mode_result": None,
                "lyrics_source": None,
                "log": None,
                "started_at": None,
                "finished_at": None,
                "duration_s": None,
            }
            job.update(fields)
            self.data["jobs"][job_id] = job
        else:
            # refresh mutable fields (label, url, mode, ...)
            for key, value in fields.items():
                job[key] = value
        return job


# --------------------------------------------------------------------------
# job discovery
# --------------------------------------------------------------------------

ULTRASTAR_NOTE_RE = re.compile(r"^[:FRG*]\s+-?\d")

REPAIR_DONE_RE = re.compile(r"REPAIR_DONE\s+(.*)")
# key=value pairs where a value may itself contain spaces (song folder
# names always do) - value runs until the NEXT " key=" token or line end,
# not just to the next whitespace (a plain line.split() truncates paths)
KV_RE = re.compile(r"(\w+)=(.*?)(?=\s+\w+=|$)")


def parse_repair_done_line(line: str) -> dict:
    """Parse one 'REPAIR_DONE key=value key=value ...' log line into a
    dict, keeping multi-word values (paths) intact."""
    m = REPAIR_DONE_RE.search(line)
    if not m:
        return {}
    return dict(KV_RE.findall(m.group(1)))


def looks_like_ultrastar_txt(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
    except OSError:
        return False
    lines = head.splitlines()
    has_header = any(l.startswith(("#BPM", "#TITLE", "#ARTIST", "#MP3", "#AUDIO"))
                     for l in lines)
    has_notes = any(ULTRASTAR_NOTE_RE.match(l) for l in lines)
    return has_header and has_notes


def parse_songs_file(path: str) -> list:
    """Parse songs.csv (band,title,url) or a plain songs.txt.

    Returns a list of dicts: {"band", "title", "url", "language",
    "musicbrainz_id", "lyrics_url"} - url may be "skip". The last three are
    optional CSV columns (default "") requested by the karaoke-dashboard
    project's CSV export - language pins whisper's language detection
    (see the Lichtgestalt mis-detection bug in 05-LESSONS.md);
    musicbrainz_id/lyrics_url feed MusicBrainz metadata lookup and the
    trusted-lyrics-url fetch respectively (see ultrasinger_command() /
    run_lyrics_step()).
    """
    entries = []
    if not path or not os.path.isfile(path):
        return entries

    with open(path, encoding="utf-8-sig", errors="replace") as f:
        raw_lines = [l.rstrip("\n").rstrip("\r") for l in f]

    non_empty = [l for l in raw_lines if l.strip() and not l.strip().startswith("#")]
    if not non_empty:
        return entries

    first = non_empty[0].strip()
    delimiter = "," if first.count(",") >= first.count(";") else ";"

    is_csv = first.lower().startswith(
        ("band", "artist", "title", "url", "link", "song", "name"))
    if is_csv:
        # use non_empty (comments/blank lines already stripped) - not
        # raw_lines, or a leading '#' comment would become DictReader's
        # header and silently swallow every row
        rows = list(csv.DictReader(non_empty, delimiter=delimiter))
        for row in rows:
            lower = {k.lower().strip(): (v or "").strip() for k, v in row.items()}
            url = lower.get("url") or lower.get("link") or lower.get("youtube") or \
                lower.get("youtube link") or ""
            band = lower.get("band") or lower.get("band name") or lower.get("artist") or ""
            title = lower.get("title") or lower.get("song name") or \
                lower.get("name") or lower.get("song") or ""
            if not url and not title:
                continue
            language = lower.get("language") or ""
            musicbrainz_id = lower.get("musicbrainz_id") or lower.get("mbid") or ""
            lyrics_url = lower.get("lyrics_url") or ""
            entries.append({
                "band": band, "title": title, "url": url,
                "language": language, "musicbrainz_id": musicbrainz_id,
                "lyrics_url": lyrics_url,
            })
    else:
        # plain text: "url" per line or "band - title - url"
        for line in non_empty:
            line = line.strip()
            url_m = re.search(r"(https?://\S+)", line)
            if url_m:
                url = url_m.group(1)
                rest = line.replace(url, "").strip().rstrip("-").strip()
                band, title = "", rest
                if " - " in rest:
                    band, title = rest.split(" - ", 1)
                entries.append({
                    "band": band.strip(), "title": title.strip(), "url": url,
                    "language": "", "musicbrainz_id": "", "lyrics_url": "",
                })
            else:
                # no link -> cannot create, treated as skipped
                entries.append({
                    "band": "", "title": line, "url": "skip",
                    "language": "", "musicbrainz_id": "", "lyrics_url": "",
                })

    # normalize: empty / '-' / 'skip' links are skips
    for e in entries:
        if e["url"].strip().lower() in ("", "-", "skip", "none", "x"):
            e["url"] = "skip"
    return entries


def primary_ultrastar_txt(folder: str):
    """The first valid ultrastar txt in `folder`, or None. Used both to
    decide whether a folder is a repair job at all (scan_repair_jobs()) and
    to read its #ARTIST/#TITLE/#VIDEO/#MP3/#AUDIO tags (see
    read_ultrastar_tags(), match_broken_report(), prepare_media_repair())."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    for entry in names:
        path = os.path.join(folder, entry)
        if entry.lower().endswith(".txt") and looks_like_ultrastar_txt(path):
            return path
    return None


def scan_repair_jobs(input_dir: str) -> list:
    """Find song folders in input_dir that contain at least one ultrastar txt."""
    folders = []
    if not os.path.isdir(input_dir):
        return folders
    for name in sorted(os.listdir(input_dir)):
        folder = os.path.join(input_dir, name)
        if not os.path.isdir(folder) or name.startswith("."):
            continue
        if primary_ultrastar_txt(folder):
            folders.append(folder)
    return folders


TAG_VALUE_RE = re.compile(r"^#([A-Za-z0-9_]+):(.*)$")


def read_ultrastar_tags(txt_path: str) -> dict:
    """The "#TAG:value" header lines of an ultrastar txt, as {TAG: value}
    (keys upper-cased). Stops at the first note line - never scans a whole
    (possibly huge) song just to read its header. Missing/unreadable file
    -> {}."""
    tags = {}
    try:
        with open(txt_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if ULTRASTAR_NOTE_RE.match(stripped):
                    break
                m = TAG_VALUE_RE.match(stripped)
                if m:
                    tags[m.group(1).upper()] = m.group(2).strip()
    except OSError:
        pass
    return tags


def pick_media_candidate(filenames: list, txt_basename: str, extensions: tuple) -> dict:
    """Pure matching for "link an existing file" (missing video/audio -
    see prepare_media_repair()). Prefers a file named exactly like the txt
    (the convention every existing song folder in this stack already
    follows); falls back to a lone candidate of the right type; refuses to
    guess between two+ unrelated candidates (returns "ambiguous" instead -
    see the "NO DO GUESSING" rule).

    Returns {"status": "none"} | {"status": "found", "name": str} |
    {"status": "ambiguous", "candidates": [str, ...]}."""
    candidates = [f for f in filenames if os.path.splitext(f)[1].lower() in extensions]
    if not candidates:
        return {"status": "none"}
    for name in candidates:
        if os.path.splitext(name)[0] == txt_basename:
            return {"status": "found", "name": name}
    if len(candidates) == 1:
        return {"status": "found", "name": candidates[0]}
    return {"status": "ambiguous", "candidates": sorted(candidates)}


def find_loose_media_candidate(song_dir: str, txt_basename: str, extensions: tuple) -> dict:
    try:
        filenames = os.listdir(song_dir)
    except OSError:
        filenames = []
    return pick_media_candidate(filenames, txt_basename, extensions)


def match_broken_report(folder: str, reports: list):
    """The best-scoring broken.csv row for `folder`, matched via its
    #ARTIST/#TITLE tags (the actual song metadata - not a folder-name
    guess, since folder naming isn't guaranteed to split cleanly on " - ",
    e.g. "Tabaluga & Lilli (Peter Maffay) - Nessaja"). Reuses
    usdb_lookup.name_score() (the same fuzzy matcher already used for USDB
    catalog matching) rather than a second bespoke implementation. None
    when there's no txt, no #ARTIST/#TITLE, or nothing scores high enough
    (see BROKEN_MATCH_MIN_SCORE)."""
    if not reports:
        return None
    txt_path = primary_ultrastar_txt(folder)
    if not txt_path:
        return None
    tags = read_ultrastar_tags(txt_path)
    band = tags.get("ARTIST", "")
    title = tags.get("TITLE", "")
    if not band or not title:
        return None
    best, best_score = None, 0.0
    for report in reports:
        score = min(usdb_lookup.name_score(report["band"], band),
                    usdb_lookup.name_score(report["title"], title))
        if score > best_score:
            best, best_score = report, score
    if best is not None and best_score >= BROKEN_MATCH_MIN_SCORE:
        return best
    return None


LYRICS_FILE_NAMES = ("lyrics.txt", "lyric.txt")


def find_lyrics_file(folder: str):
    """A user-supplied trusted-lyrics file next to a broken song, if any -
    triggers repair "lyrics" mode for that job instead of the default
    REPAIR_MODE (see repair.py: force-aligns this text to the audio,
    replacing the existing/wrong lyrics rather than just re-timing them)."""
    for name in LYRICS_FILE_NAMES:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def build_job_plan(state: State, only=None) -> list:
    """Collect all jobs (new songs + repairs) and register them in state."""
    jobs = []

    for song in parse_songs_file(SONGS_FILE):
        url = song["url"]
        label = " - ".join(x for x in (song["band"], song["title"]) if x) or url
        if url == "skip":
            job_id = f"new|skip|{label}"
        else:
            job_id = f"new|{url}"
        job = state.get_or_create(
            job_id, kind="new", label=label, url=url,
            band=song["band"], title=song["title"],
            language=song["language"], musicbrainz_id=song["musicbrainz_id"],
            lyrics_url=song["lyrics_url"])
        if url == "skip":
            job["status"] = "skipped"
        jobs.append(job)

    broken_reports = broken_report.parse_broken_csv(BROKEN_CSV)

    for folder in scan_repair_jobs(INPUT_DIR):
        name = os.path.basename(folder.rstrip("/"))
        job_id = f"repair|{name}"
        lyrics_file = find_lyrics_file(folder)
        report = match_broken_report(folder, broken_reports)
        category = report["category"] if report else ""
        description = report["description"] if report else ""
        report_lyrics_url = report.get("lyrics_url", "") if report else ""
        report_language = report.get("language", "") if report else ""

        # a manually-supplied lyrics.txt always wins (existing mechanism,
        # unconditional); otherwise the broken.csv category picks a
        # specific, cheap fix instead of always running the same blind
        # full repair - see 02-DESIGN.md "broken.csv-driven repair"
        if lyrics_file:
            mode = "lyrics"
        elif category == "gap":
            mode = "gap"
        elif category == "async":
            mode = "sync"
        elif category == "lyrics":
            # no local file: JobRunner._prepare_repair_source() fetches
            # trusted lyrics online (lyrics_url if given, else a normal
            # search) before repair.py ever runs - see that method.
            mode = "lyrics"
        elif category in ("video", "audio"):
            # resolved lazily by JobRunner._prepare_repair_source() /
            # prepare_media_repair() - repair.py has no "media" mode of
            # its own, this is a synthetic marker only.
            mode = "media"
        else:
            mode = REPAIR_MODE

        job = state.get_or_create(
            job_id, kind="repair", label=name, song_dir=folder, mode=mode,
            lyrics_file=lyrics_file, category=category, description=description,
            lyrics_url=report_lyrics_url, language=report_language)

        # "other"/blank/unrecognized category: the defect is unknown or
        # free-text only - don't guess an action, flag it for a human
        # instead (only for a BRAND NEW job - never overwrite a job that
        # already ran under some other status, e.g. "done" from before the
        # report/category existed or changed - see 02-DESIGN.md)
        if report and category in ("", "other") and not lyrics_file \
                and job["status"] == "pending":
            job["status"] = "needs_review"
        jobs.append(job)

    if only:
        jobs = [j for j in jobs if j["kind"] in only]
    return jobs


# --------------------------------------------------------------------------
# progress rendering
# --------------------------------------------------------------------------

def count_statuses(jobs, kind):
    of_kind = [j for j in jobs if j["kind"] == kind]
    return {
        "total": len(of_kind),
        "done": sum(1 for j in of_kind if j["status"] == "done"),
        "failed": sum(1 for j in of_kind if j["status"] == "failed"),
        "running": sum(1 for j in of_kind if j["status"] == "running"),
        "pending": sum(1 for j in of_kind if j["status"] == "pending"),
        "skipped": sum(1 for j in of_kind if j["status"] == "skipped"),
        "needs_review": sum(1 for j in of_kind if j["status"] == "needs_review"),
    }


def count_usdb_sourced(jobs) -> int:
    """How many completed "new" jobs were sourced from USDB (animux.de or
    usdb.eu) rather than generated - see finalize_new_job_lyrics()'s
    "usdb:<site>:<id>" lyrics_source convention. Report-only: this counts
    what already happened, never a prediction of what's still pending -
    predicting it upfront would need an expensive, rate-limited usdb.eu
    search per pending song (see 02-DESIGN.md "USDB integration")."""
    return sum(1 for j in jobs if j["kind"] == "new" and j["status"] == "done"
              and (j.get("lyrics_source") or "").startswith("usdb:"))


def render_progress(state_data, cpu_percent=None, ram_usage=None,
                    gpu_usage=None, device=None) -> str:
    """The live status dashboard (docker compose exec ... progress [-w]).
    cpu_percent/ram_usage/gpu_usage/device default to live readings
    (resource_monitor.py / the DEVICE env var) - tests pass explicit
    values instead for determinism."""
    if cpu_percent is None:
        cpu_percent = resource_monitor.read_cpu_percent()
    if ram_usage is None:
        ram_usage = resource_monitor.read_ram_usage()
    if device is None:
        device = DEVICE
    if gpu_usage is None and device != "cpu":
        gpu_usage = resource_monitor.read_gpu_usage()

    jobs = list(state_data["jobs"].values())
    new = count_statuses(jobs, "new")
    rep = count_statuses(jobs, "repair")

    done_jobs = [j for j in jobs if j["status"] == "done" and j.get("duration_s")]
    running = next((j for j in jobs if j["status"] == "running"), None)

    started = [j["started_at"] for j in jobs if j.get("started_at")]
    elapsed = None
    if started:
        t0 = min(datetime.fromisoformat(s) for s in started)
        t1 = max(
            [datetime.fromisoformat(j["finished_at"]) for j in jobs
             if j.get("finished_at")]
            or [datetime.now().astimezone()]
        )
        elapsed = (t1 - t0).total_seconds()

    lines = []
    lines.append("=" * 74)
    lines.append(f"  UltraStar Song Factory - Batch Progress"
                 f"        {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 74)

    def row(name, s):
        counts = (f"{s['done']:>3} done | {s['failed']:>2} failed | "
                  f"{s['running']:>2} running | {s['pending']:>3} pending")
        skip = f" | {s['skipped']} skipped" if s["skipped"] else ""
        review = f" | {s['needs_review']} needs review" if s["needs_review"] else ""
        return f"  {name:<9}: {counts}{skip}{review}   ({s['total']} total)"

    lines.append(row("NEW SONGS", new))
    lines.append(row("REPAIRS", rep))
    lines.append(f"  Pulled from USDB: {count_usdb_sourced(jobs)}")

    device_label = {"cpu": "CPU", "cuda": "GPU (cuda/ROCm)"}.get(device, device)
    resource_bits = []
    if cpu_percent is not None:
        resource_bits.append(f"CPU ~{cpu_percent:.0f}%")
    if ram_usage is not None:
        resource_bits.append(
            f"RAM {ram_usage['used_gb']:.1f}/{ram_usage['total_gb']:.1f} GB "
            f"({ram_usage['percent']:.0f}%)")
    if gpu_usage is not None:
        resource_bits.append(
            f"GPU VRAM {gpu_usage['used_gb']:.1f}/{gpu_usage['total_gb']:.1f} GB "
            f"({gpu_usage['percent']:.0f}%)")
    lines.append(f"  Device: {device_label}" + (
        "   " + " | ".join(resource_bits) if resource_bits else ""))

    if elapsed is not None:
        n_done = len(done_jobs)
        avg = sum(j["duration_s"] for j in done_jobs) / n_done if n_done else None
        remaining = new["pending"] + new["failed"] + rep["pending"] + rep["failed"]
        eta = avg * remaining if avg and remaining else None
        avg_s = f" | avg {fmt_duration(avg)}/song" if avg else ""
        eta_s = f" | ETA ~{fmt_duration(eta)}" if eta else ""
        lines.append(f"  Elapsed {fmt_duration(elapsed)}{avg_s}{eta_s}")

    if running:
        run_secs = None
        if running.get("started_at"):
            run_secs = (datetime.now().astimezone()
                        - datetime.fromisoformat(running["started_at"])).total_seconds()
        lines.append(f"  Current: {running['label']} - running for "
                     f"{fmt_duration(run_secs)} (attempt {running['attempts']})")

    failed = [j for j in jobs if j["status"] == "failed"]
    if failed:
        lines.append("-" * 74)
        lines.append("  Failed jobs:")
        for j in failed[:8]:
            err = (j.get("error") or "")[:100]
            lines.append(f"   - {j['label']}  [{j['kind']}]  {err}")
        if len(failed) > 8:
            lines.append(f"   ... and {len(failed) - 8} more (see `list`)")

    lines.append("=" * 74)
    return "\n".join(lines)


def _folder_of(output_path) -> str:
    return os.path.basename(os.path.dirname(output_path)) if output_path else "-"


def render_report(state_data) -> str:
    """Tabular finishing report: which songs were created, which were
    repaired, and which jobs failed (with where their partial output was
    quarantined to)."""
    jobs = list(state_data["jobs"].values())
    new_done = [j for j in jobs if j["kind"] == "new" and j["status"] == "done"]
    repair_done = [j for j in jobs if j["kind"] == "repair" and j["status"] == "done"]
    failed = [j for j in jobs if j["status"] == "failed"]
    skipped = [j for j in jobs if j["status"] == "skipped"]
    needs_review = [j for j in jobs if j["status"] == "needs_review"]
    by_label = lambda j: j["label"].lower()  # noqa: E731

    lines = []
    lines.append(f"# UltraStar Song Factory - Report "
                 f"({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    lines.append("")
    lines.append(f"## New songs created ({len(new_done)})")
    lines.append("")
    if new_done:
        lines.append("| Song | Lyrics | Duration | Output folder |")
        lines.append("|---|---|---|---|")
        for j in sorted(new_done, key=by_label):
            lyrics = j.get("lyrics_source") or "transcribed"
            marker = "" if lyrics.startswith("online:") else " ⚠"
            lines.append(f"| {j['label']} | {lyrics}{marker} | "
                         f"{fmt_duration(j.get('duration_s'))} | "
                         f"`{_folder_of(j.get('output_path'))}` |")
        lines.append("")
        lines.append("_Lyrics marked ⚠ came from audio transcription "
                     "(whisper), not a verified online source - more "
                     "likely to contain mis-heard words._")
    else:
        lines.append("_none_")

    lines.append("")
    lines.append(f"## Songs repaired ({len(repair_done)})")
    lines.append("")
    if repair_done:
        lines.append("| Song | Mode | Duration | Output folder |")
        lines.append("|---|---|---|---|")
        for j in sorted(repair_done, key=by_label):
            lines.append(f"| {j['label']} | {j.get('repair_mode_result') or '-'} | "
                         f"{fmt_duration(j.get('duration_s'))} | "
                         f"`{_folder_of(j.get('output_path'))}` |")
    else:
        lines.append("_none_")

    lines.append("")
    lines.append(f"## Failed jobs ({len(failed)})")
    lines.append("")
    if failed:
        lines.append("| Song | Kind | Attempts | Error | Quarantined output |")
        lines.append("|---|---|---|---|---|")
        for j in sorted(failed, key=by_label):
            q = j.get("quarantined_output")
            q_cell = f"`{q}`" if q else "-"
            err = (j.get("error") or "")[:120].replace("|", "\\|")
            lines.append(f"| {j['label']} | {j['kind']} | {j.get('attempts', '-')} | "
                         f"{err} | {q_cell} |")
    else:
        lines.append("_none_")

    if needs_review:
        lines.append("")
        lines.append(f"## Needs review ({len(needs_review)})")
        lines.append("")
        lines.append("_Category is 'other', blank, or unrecognized (or the "
                     "reported defect doesn't reproduce) - no automated repair "
                     "was attempted. Fix the category/description in broken.csv "
                     "(or resolve the underlying folder), then `reset` the job._")
        lines.append("")
        lines.append("| Song | Category | Description |")
        lines.append("|---|---|---|")
        for j in sorted(needs_review, key=by_label):
            category = j.get("category") or "-"
            desc = (j.get("description") or j.get("error") or "-").replace("|", "\\|")
            lines.append(f"| {j['label']} | {category} | {desc} |")

    if skipped:
        lines.append("")
        lines.append(f"## Skipped ({len(skipped)})")
        lines.append("")
        lines.append(", ".join(j["label"] for j in sorted(skipped, key=by_label)))

    return "\n".join(lines) + "\n"


def write_report(state_data) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    text = render_report(state_data)
    tmp = REPORT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, REPORT_FILE)
    return REPORT_FILE


def run_romanize_step(job: dict, output_path: str) -> None:
    """Romanize non-Latin lyrics (Cyrillic/Korean/Japanese) of a just-created
    txt in place. Best-effort: a failure here must never fail the song job -
    the song is already fine, only its script stays un-romanized."""
    if not ROMANIZE_ENABLED or not output_path or not os.path.isfile(output_path):
        return
    log_path = os.path.join(LOGS_DIR, slugify(job["id"]) + "-romanize.log")
    cmd = [sys.executable, ROMANIZE_PY, "--txt", output_path]
    try:
        with open(log_path, "wb") as logfile:
            proc = subprocess.run(cmd, stdout=logfile, stderr=subprocess.STDOUT,
                                  timeout=180)
        with open(log_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for line in text.splitlines():
            if line.startswith("ROMANIZE"):
                out(f"  .. {line}")
        if proc.returncode != 0:
            out(f"  !! romanization step exited {proc.returncode} "
                "(lyrics left as-is)")
    except Exception as exc:  # noqa: BLE001
        out(f"  !! romanization step failed: {exc} (lyrics left as-is)")


def run_lyrics_step(job: dict, output_path: str) -> str:
    """After a successful NEW song, try to fetch trusted lyrics online and
    force-align them in place, replacing whisper's own (possibly
    mis-heard) transcription. Best-effort: on any failure or "not found"
    the song simply keeps its transcribed lyrics - this must never turn a
    working song into a failed job. Returns the lyrics_source value for
    the finishing report ("transcribed" or "online:<source>")."""
    if not LYRICS_ENABLED or not output_path or not os.path.isfile(output_path):
        return "transcribed"
    band = (job.get("band") or "").strip()
    title = (job.get("title") or "").strip()
    if not band or not title:
        return "transcribed"

    os.makedirs(WORK_DIR, exist_ok=True)
    slug = slugify(job["id"])
    lyrics_json = os.path.join(WORK_DIR, slug + "-lyrics.json")
    fetch_log_path = os.path.join(LOGS_DIR, slug + "-lyrics-fetch.log")
    fetch_cmd = [sys.executable, LYRICS_FETCH_PY,
                "--artist", band, "--title", title, "--out", lyrics_json]
    lyrics_url = (job.get("lyrics_url") or "").strip()
    if lyrics_url:
        fetch_cmd += ["--lyrics-url", lyrics_url]
    try:
        with open(fetch_log_path, "wb") as logfile:
            proc = subprocess.run(fetch_cmd, stdout=logfile, stderr=subprocess.STDOUT,
                                  timeout=60)
        with open(fetch_log_path, encoding="utf-8", errors="replace") as f:
            fetch_text = f.read()
        for line in fetch_text.splitlines():
            if line.startswith(("LYRICS_FOUND", "LYRICS_NOT_FOUND")):
                out(f"  .. {line}")
    except Exception as exc:  # noqa: BLE001
        out(f"  !! lyrics fetch failed: {exc} (keeping transcribed lyrics)")
        return "transcribed"

    if proc.returncode != 0 or not os.path.isfile(lyrics_json):
        return "transcribed"

    song_dir = os.path.dirname(output_path)
    align_log_path = os.path.join(LOGS_DIR, slug + "-lyrics-align.log")
    align_cmd = [sys.executable, REPAIR_PY,
                "--song-dir", song_dir, "--out", NEW_SONGS_DIR,
                "--mode", "lyrics", "--lyrics-file", lyrics_json,
                "--device", DEVICE]
    try:
        with open(align_log_path, "wb") as logfile:
            proc2 = subprocess.run(align_cmd, stdout=logfile, stderr=subprocess.STDOUT,
                                   timeout=JOB_TIMEOUT_MIN * 60)
        with open(align_log_path, encoding="utf-8", errors="replace") as f:
            align_text = f.read()
    except Exception as exc:  # noqa: BLE001
        out(f"  !! lyrics realignment failed: {exc} (keeping transcribed lyrics)")
        return "transcribed"

    source = None
    for line in align_text.splitlines():
        kv = parse_repair_done_line(line)
        if "lyrics_source" in kv:
            source = kv["lyrics_source"]
    if proc2.returncode != 0 or source is None:
        out("  !! lyrics realignment did not complete (keeping transcribed lyrics)")
        return "transcribed"

    out(f"  .. lyrics replaced from {source}")
    return f"online:{source}"


def finalize_new_job_lyrics(job: dict, output_path: str, usdb_song_id: str = None) -> str:
    """A usdb-sourced song already has trusted, community-verified lyrics
    and note timing (prepare_usdb_job()'s repair.py "gap" pass only ever
    touches #GAP) - running the online-lyrics search on it would throw
    that away, so it is skipped entirely for those. A generated song
    still gets the usual online-lyrics pass (run_lyrics_step)."""
    if usdb_song_id:
        return f"usdb:{usdb_song_id}"
    return run_lyrics_step(job, output_path)


TAG_LINE_RE = re.compile(r"^\s*#([A-Za-z0-9_]+):")


def patch_tag(txt_content: str, tag: str, value: str) -> str:
    """Rewrite (or insert) a single "#TAG:value" line in raw ultrastar txt
    content. An existing tag's value is replaced in place; a missing one
    is inserted right after the last existing "#TAG:" header line (or at
    the very top if the txt has none), keeping every other line as-is."""
    prefix = f"#{tag}:"
    lines = txt_content.splitlines()
    out_lines = []
    replaced = False
    last_tag_idx = -1
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            out_lines.append(prefix + value)
            replaced = True
            continue
        out_lines.append(line)
        if TAG_LINE_RE.match(line):
            last_tag_idx = len(out_lines) - 1
    if not replaced:
        insert_at = last_tag_idx + 1 if last_tag_idx >= 0 else 0
        out_lines.insert(insert_at, prefix + value)
    return "\n".join(out_lines) + "\n"


def append_comment_tag(txt_content: str, extra: str) -> str:
    """Append `extra` to the txt's #COMMENT tag (the official UltraStar
    "extended header" for arbitrary human-only text - implementations
    must not assign it any meaning, so it's always safe to write to).
    Unlike patch_tag(), this MERGES into an existing value rather than
    overwriting it - a #COMMENT is often legitimate uploader-supplied
    info (e.g. "Eurovision 2021") that must not be discarded."""
    prefix = "#COMMENT:"
    lines = txt_content.splitlines()
    out_lines = []
    appended = False
    last_tag_idx = -1
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            existing = stripped[len(prefix):].strip()
            merged = f"{existing} | {extra}" if existing else extra
            out_lines.append(prefix + merged)
            appended = True
            continue
        out_lines.append(line)
        if TAG_LINE_RE.match(line):
            last_tag_idx = len(out_lines) - 1
    if not appended:
        insert_at = last_tag_idx + 1 if last_tag_idx >= 0 else 0
        out_lines.insert(insert_at, prefix + extra)
    return "\n".join(out_lines) + "\n"


def download_media(url: str, dest_path: str, want_video: bool, log_path: str) -> bool:
    """Fetch `url` with yt-dlp straight to `dest_path`. Video: a combined
    mp4 (bestvideo[ext=mp4]+bestaudio/best, merged to mp4) - the same
    format UltraSinger's own downloader uses (src/modules/Audio/
    youtube.py), so repair.py's locate_audio() can extract the audio
    track from it exactly like it already does for any #VIDEO-only song.
    Audio: yt-dlp's own best-audio extraction to mp3. Returns whether
    dest_path exists afterwards - best-effort, never raises."""
    if want_video:
        cmd = ["yt-dlp", "-f", "bestvideo[ext=mp4]+bestaudio/best",
               "--merge-output-format", "mp4", "-o", dest_path, url]
    else:
        cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "-o", dest_path, url]
    if os.path.isfile(COOKIES_FILE):
        cmd += ["--cookiefile", COOKIES_FILE]
    try:
        with open(log_path, "wb") as logfile:
            proc = subprocess.run(cmd, stdout=logfile, stderr=subprocess.STDOUT,
                                  timeout=600)
    except Exception as exc:  # noqa: BLE001
        out(f"  !! yt-dlp failed: {exc}")
        return False
    return proc.returncode == 0 and os.path.isfile(dest_path)


def prepare_usdb_job(job: dict) -> dict:
    """Best-effort: try to source a NEW job from an existing USDB upload
    (usdb.animux.de, then usdb.eu - see find_usdb_match()) instead of
    full UltraSinger generation (see 02-DESIGN.md "USDB integration").
    USDB itself never hosts audio/video (copyright) - only notes/cover -
    so the matched song's media is always fetched via yt-dlp: the
    source's own comment-linked video first ("only fill gaps") where that
    is supported, falling back to the job's own songs.csv url otherwise.
    Returns {"staging_dir", "song_id"} on success (the caller runs
    repair.py --mode gap against staging_dir to re-detect #GAP for
    whatever media actually got downloaded) or None to fall back to
    normal generation - must never raise or fail the job."""
    band = (job.get("band") or "").strip()
    title = (job.get("title") or "").strip()
    if not band or not title:
        return None
    match = find_usdb_match(band, title)
    if match is None:
        return None
    match_label = f"{match['site']}#{match['song_id']}"

    video_url = match["video_url"] or job.get("url")
    if not video_url or not video_url.startswith("https://"):
        out(f"  !! usdb match {match_label} has no usable video source "
            "(no linked video, no youtube url in the song list either)")
        return None

    slug = slugify(job["id"])
    # the staging dir's OWN basename becomes the final output folder name
    # (repair.py's write_repaired() derives it from song_dir's basename) -
    # must be the human-readable song name, not a url/id-based slug (see
    # job_display_name()/safe_dirname())
    staging_dir = os.path.join(WORK_DIR, safe_dirname(job_display_name(job)))
    if os.path.isdir(staging_dir):
        shutil.rmtree(staging_dir)
    os.makedirs(staging_dir, exist_ok=True)

    video_path = os.path.join(staging_dir, "video.mp4")
    log_path = os.path.join(LOGS_DIR, slug + "-usdb-ytdlp.log")
    out(f"  .. found on USDB ({match_label}), downloading media via yt-dlp")
    if not download_media(video_url, video_path, want_video=True, log_path=log_path):
        out(f"  !! usdb: could not download media for {match_label}, "
            "falling back to normal generation")
        return None

    txt_content = patch_tag(match["txt"], "VIDEO", "video.mp4")

    gap_hints = usdb_lookup.extract_gap_hints(match.get("details"))
    if gap_hints:
        out(f"  .. usdb comments mention GAP hint(s): {', '.join(gap_hints)} "
            "(logged only - our own re-detection below is authoritative)")
        txt_content = append_comment_tag(
            txt_content, f"usdb comment GAP hints: {', '.join(gap_hints)}")

    cover_bytes = match.get("cover_bytes")
    if cover_bytes:
        with open(os.path.join(staging_dir, "cover.jpg"), "wb") as f:
            f.write(cover_bytes)
        txt_content = patch_tag(txt_content, "COVER", "cover.jpg")

    with open(os.path.join(staging_dir, "song.txt"), "w", encoding="utf-8") as f:
        f.write(txt_content)

    out(f"  .. sourced from USDB {match_label} - re-detecting #GAP "
        "against our own download")
    return {"staging_dir": staging_dir, "song_id": f"{match['site']}:{match['song_id']}"}


# --------------------------------------------------------------------------
# broken.csv "missing video"/"missing audio" media repair
# --------------------------------------------------------------------------

VIDEO_EXTENSIONS = (".mp4", ".webm", ".mkv", ".avi", ".mov")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".ogg", ".flac", ".opus")


class _NullLogger:
    """Duck-types usdb_syncer's Logger just enough for MetaTags.parse()
    (warning()/debug() only) - we don't want its actual log output."""

    def warning(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


def parse_sync_meta_source(path: str, category: str):
    """The downloadable URL for `category` ("video"/"audio") recorded in
    one usdb_syncer sync-meta file (a "*.usdb" JSON sidecar usdb_syncer
    itself writes next to every song it downloads - see that project's
    sync_meta.py/meta_tags.py). "audio" falls back to the video id when
    there is no separate audio-only resource (yt-dlp can extract audio
    from a video URL). Reuses usdb_syncer's own MetaTags.parse() and
    video_url_from_resource() (percent-encoding/escaping, youtube-id vs.
    vimeo-id vs. full-URL resource forms are that project's concern, not
    ours to re-implement) - lazily imported, same as usdb_lookup.py,
    since usdb_syncer may not be installed/importable in every context
    this module runs in (e.g. host test runs)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    meta_tags_str = data.get("meta_tags")
    if not meta_tags_str:
        return None
    try:
        from usdb_syncer.meta_tags import MetaTags
        from usdb_syncer.utils import video_url_from_resource
    except ImportError:
        return None
    tags = MetaTags.parse(meta_tags_str, _NullLogger())
    resource = (tags.audio if category == "audio" and tags.audio else None) or tags.video
    if not resource:
        return None
    return video_url_from_resource(resource)


def find_sync_meta_source(song_dir: str, category: str):
    """The first usable download URL for `category` across every *.usdb
    sync-meta file in `song_dir` (there is usually at most one)."""
    for path in sorted(glob.glob(os.path.join(song_dir, "*.usdb"))):
        url = parse_sync_meta_source(path, category)
        if url:
            return url
    return None


def prepare_media_repair(job: dict):
    """Best-effort: restore a "missing video"/"missing audio" broken.csv
    job's media, in a staging COPY of the folder (the original input
    folder is never modified - same rule as repair.py). Tries, in order:

      1. the tag already points at a file that exists -> nothing to do
         (the report doesn't reproduce) - {"status": "already_present"}
      2. an unambiguous loose file already sitting in the folder -> just
         link it (patch the tag) - never guesses between two+ unrelated
         candidates ({"status": "ambiguous", "candidates": [...]})
      3. a *.usdb sync-meta file's original v=/a= source -> download it
         via yt-dlp (same download_media() new songs use)

    Returns None when none of the above found a source at all (caller
    fails the job - never silently skips or falls back to a blind
    repair)."""
    song_dir = job["song_dir"]
    category = job.get("category")
    txt_path = primary_ultrastar_txt(song_dir)
    if not txt_path:
        return None
    tags = read_ultrastar_tags(txt_path)
    txt_basename = os.path.splitext(os.path.basename(txt_path))[0]

    if category == "video":
        tag_name = "VIDEO"
        extensions = VIDEO_EXTENSIONS
        existing_value = tags.get("VIDEO")
    else:
        extensions = AUDIO_EXTENSIONS
        tag_name = "MP3" if "MP3" in tags else ("AUDIO" if "AUDIO" in tags else "MP3")
        existing_value = tags.get("MP3") or tags.get("AUDIO")

    if existing_value and os.path.isfile(os.path.join(song_dir, existing_value)):
        return {"status": "already_present"}

    candidate = find_loose_media_candidate(song_dir, txt_basename, extensions)
    if candidate["status"] == "ambiguous":
        return {"status": "ambiguous", "candidates": candidate["candidates"]}

    slug = slugify(job["id"])
    # same reasoning as prepare_usdb_job() - the staging dir's basename
    # becomes the final output folder name
    staging_dir = os.path.join(WORK_DIR, safe_dirname(job_display_name(job)))
    if os.path.isdir(staging_dir):
        shutil.rmtree(staging_dir)

    if candidate["status"] == "found":
        shutil.copytree(song_dir, staging_dir)
        staged_txt = os.path.join(staging_dir, os.path.basename(txt_path))
        with open(staged_txt, encoding="utf-8", errors="replace") as f:
            content = f.read()
        content = patch_tag(content, tag_name, candidate["name"])
        with open(staged_txt, "w", encoding="utf-8") as f:
            f.write(content)
        out(f"  .. linked existing {candidate['name']!r} for the "
            f"missing {category}")
        return {"staging_dir": staging_dir, "source": "local", "linked": candidate["name"]}

    url = find_sync_meta_source(song_dir, category)
    if not url:
        return None

    os.makedirs(WORK_DIR, exist_ok=True)
    shutil.copytree(song_dir, staging_dir)
    dest_name = txt_basename + (".mp4" if category == "video" else ".mp3")
    dest_path = os.path.join(staging_dir, dest_name)
    log_path = os.path.join(LOGS_DIR, slug + "-media-ytdlp.log")
    os.makedirs(LOGS_DIR, exist_ok=True)
    out(f"  .. downloading missing {category} via yt-dlp (source: usdb sync-meta)")
    if not download_media(url, dest_path, want_video=(category == "video"),
                          log_path=log_path):
        shutil.rmtree(staging_dir, ignore_errors=True)
        return None

    staged_txt = os.path.join(staging_dir, os.path.basename(txt_path))
    with open(staged_txt, encoding="utf-8", errors="replace") as f:
        content = f.read()
    content = patch_tag(content, tag_name, dest_name)
    with open(staged_txt, "w", encoding="utf-8") as f:
        f.write(content)
    return {"staging_dir": staging_dir, "source": "usdb-sync-meta", "downloaded": dest_name}


# --------------------------------------------------------------------------
# job execution
# --------------------------------------------------------------------------

def resolve_song_input(value: str) -> str:
    """Resolve a songs.csv 'url' cell to what UltraSinger's -i expects.

    A YouTube link (always "https://...") passes through unchanged - that
    is the one and only way a link is recognized, never guessed from the
    text otherwise. Anything else is a local media file (.mp4/.mp3/...),
    resolved relative to INPUT_DIR when it is not already an absolute
    path, so a csv row can just name a file placed under input/ (e.g.
    "media/song.mp3"). UltraSinger itself already handles a local
    audio/video file the same way it handles a YouTube download - MusicBrainz
    enrichment, video/audio separation when a video track is present, plain
    audio-only output (no #VIDEO tag) otherwise.
    """
    if value.startswith("https://"):
        return value
    if os.path.isabs(value):
        return value
    return os.path.join(INPUT_DIR, value)


def unused_path(path: str) -> str:
    """Like UltraSinger's own get_unused_song_output_dir: append ' (n)'
    until `path` does not already exist, so a quarantine move never
    clobbers an earlier one."""
    if not os.path.exists(path):
        return path
    n = 1
    while os.path.exists(f"{path} ({n})"):
        n += 1
    return f"{path} ({n})"


def quarantine_partial_output(kind: str, before_names: set, output_dir: str) -> list:
    """Move any folder that appeared in `output_dir` during a failed job
    out to FAILED_DIR/<kind>/, so partial/broken output never lingers in
    the live output tree. Returns [(original_name, destination_path), ...].
    """
    moved = []
    try:
        after_names = set(os.listdir(output_dir))
    except OSError:
        return moved
    new_names = sorted(after_names - before_names)
    if not new_names:
        return moved
    dest_root = os.path.join(FAILED_DIR, kind)
    os.makedirs(dest_root, exist_ok=True)
    for name in new_names:
        src = os.path.join(output_dir, name)
        dst = unused_path(os.path.join(dest_root, name))
        try:
            shutil.move(src, dst)
            moved.append((name, dst))
        except OSError as exc:
            out(f"  !! could not quarantine {src}: {exc}")
    return moved


def ultrasinger_command(url: str, band: str = None, title: str = None,
                        language: str = None, musicbrainz_id: str = None) -> list:
    cmd = [sys.executable, ULTRASINGER_PY, "-i", resolve_song_input(url), "-o", NEW_SONGS_DIR]
    if WHISPER_MODEL:
        cmd += ["--whisper", WHISPER_MODEL]
    if WHISPER_BATCH_SIZE:
        cmd += ["--whisper_batch_size", str(WHISPER_BATCH_SIZE)]
    if DEMUCS_MODEL:
        cmd += ["--demucs", DEMUCS_MODEL]
    if os.path.isfile(COOKIES_FILE):
        cmd += ["--cookiefile", COOKIES_FILE]
        out(f"  .. using youtube cookies from {COOKIES_FILE}")
    if WHISPER_ON_CPU:
        cmd += ["--force_whisper_cpu"]
    # trust the songs.csv band/title over YouTube channel names and
    # MusicBrainz alternate-release matches (both can rename a song away
    # from what the input file already got right)
    if band and band.strip():
        cmd += ["--force_artist", band.strip()]
    if title and title.strip():
        cmd += ["--force_title", title.strip()]
    # a csv-provided language pins whisper's language detection, avoiding
    # mis-detections on short/ambiguous audio (see the Lichtgestalt bug in
    # 05-LESSONS.md, where whisper detected "en" at 0.41 confidence for a
    # purely German song)
    if language and language.strip():
        cmd += ["--language", language.strip()]
    # a csv-provided musicbrainz_id gets more reliable supplementary
    # metadata (cover art, year, genres) than the fuzzy search - does not
    # affect naming, --force_artist/--force_title above still win
    if musicbrainz_id and musicbrainz_id.strip():
        cmd += ["--musicbrainz_id", musicbrainz_id.strip()]
    if EXTRA_ARGS:
        cmd += shlex.split(EXTRA_ARGS)
    return cmd


def repair_command(song_dir: str, mode: str = None, lyrics_file: str = None,
                   out_dir: str = None, language: str = None) -> list:
    cmd = [sys.executable, REPAIR_PY,
           "--song-dir", song_dir,
           "--out", out_dir or REPAIRED_DIR,
           "--mode", mode or REPAIR_MODE,
           "--device", DEVICE]
    if lyrics_file:
        cmd += ["--lyrics-file", lyrics_file]
    # a broken.csv-provided language pins whisper's re-alignment language,
    # same "forced" input a saved language.txt still takes priority over -
    # see src/modules/language_file.py
    if language and language.strip():
        cmd += ["--language", language.strip()]
    return cmd


class JobRunner:
    """Runs one job as a subprocess, streaming its output to stdout + logfile."""

    def __init__(self, job, control=None):
        self.job = job
        self.control = control or Control()
        self.proc = None
        self.log_path = os.path.join(LOGS_DIR, slugify(job["id"]) + ".log")
        # set by _try_usdb_source() - when a usdb match was found,
        # command() runs repair.py's gap mode on its staging dir instead
        # of a full ultrasinger generation
        self._usdb_match = None
        # set by _prepare_repair_source() - overrides song_dir/mode/
        # lyrics_file for a broken.csv-categorized repair job whose real
        # mode needed resolving first (media relink/download, or an
        # online lyrics fetch) - see that method
        self._repair_prep = None

    def _try_usdb_source(self):
        if self.job["kind"] != "new":
            return
        try:
            self._usdb_match = prepare_usdb_job(self.job)
        except Exception as exc:  # noqa: BLE001
            out(f"  !! usdb lookup failed unexpectedly: {exc} "
                "(falling back to normal generation)")
            self._usdb_match = None

    def _prepare_lyrics_repair(self):
        """category "lyrics", no local lyrics.txt: fetch trusted lyrics
        online (broken.csv's lyrics_url if given - "always use this one" -
        else a normal artist/title search, same mechanism new songs use -
        see run_lyrics_step()) before repair.py ever runs. Unlike that
        best-effort new-song pass, failure here FAILS the job outright:
        the category says the lyrics are wrong, so silently falling back
        to a blind timing-only repair would leave the known-wrong lyrics
        in place."""
        job = self.job
        song_dir = job["song_dir"]
        txt_path = primary_ultrastar_txt(song_dir)
        tags = read_ultrastar_tags(txt_path) if txt_path else {}
        band = tags.get("ARTIST", "")
        title = tags.get("TITLE", "")
        if not band or not title:
            return {"status": "failed",
                    "error": "lyrics category: could not read #ARTIST/#TITLE "
                             "from the song's txt"}

        os.makedirs(WORK_DIR, exist_ok=True)
        os.makedirs(LOGS_DIR, exist_ok=True)
        slug = slugify(job["id"])
        lyrics_json = os.path.join(WORK_DIR, slug + "-lyrics.json")
        fetch_log_path = os.path.join(LOGS_DIR, slug + "-lyrics-fetch.log")
        fetch_cmd = [sys.executable, LYRICS_FETCH_PY,
                    "--artist", band, "--title", title, "--out", lyrics_json]
        lyrics_url = (job.get("lyrics_url") or "").strip()
        if lyrics_url:
            fetch_cmd += ["--lyrics-url", lyrics_url]
        try:
            with open(fetch_log_path, "wb") as logfile:
                proc = subprocess.run(fetch_cmd, stdout=logfile,
                                      stderr=subprocess.STDOUT, timeout=60)
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "error": f"lyrics fetch failed: {exc}"}

        if proc.returncode != 0 or not os.path.isfile(lyrics_json):
            source_desc = "the given lyrics_url" if lyrics_url else "an online search"
            return {"status": "failed",
                    "error": f"lyrics category: no trusted lyrics found via {source_desc}"}

        self._repair_prep = {"song_dir": song_dir, "mode": "lyrics",
                             "lyrics_file": lyrics_json}
        return None

    def _prepare_media_repair_job(self):
        """category "video"/"audio": restore the missing media (see
        prepare_media_repair()) before running the cheap "gap" pass on it
        as a safety net (a relinked/re-downloaded file may not be exactly
        byte-identical to the original, e.g. different silence padding)."""
        job = self.job
        try:
            result = prepare_media_repair(job)
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed",
                    "error": f"{job.get('category')} category: media repair "
                             f"failed unexpectedly: {exc}"}
        category = job.get("category")
        if result is None:
            return {"status": "failed",
                    "error": f"{category} category: no existing file to link and "
                             "no usdb sync-meta source to download from"}
        if result.get("status") == "already_present":
            return {"status": "needs_review",
                    "error": f"{category} category: reported missing, but the "
                             "tag and file already exist - nothing to repair"}
        if result.get("status") == "ambiguous":
            cands = ", ".join(result.get("candidates", []))
            return {"status": "failed",
                    "error": f"{category} category: multiple candidate files "
                             f"found ({cands}) - rename to match the txt, or "
                             "remove the extra one"}
        self._repair_prep = {"song_dir": result["staging_dir"], "mode": "gap",
                             "lyrics_file": None}
        return None

    def _prepare_repair_source(self):
        """Resolves a broken.csv-categorized repair job's real mode/source
        before command() builds the repair.py invocation. Returns None
        when the job can proceed as-is (nothing to override, or an
        override was set on self._repair_prep) - or a
        {"status": "failed"|"needs_review", "error": ...} dict for run()
        to short-circuit on, without ever running a subprocess."""
        if self.job["kind"] != "repair":
            return None
        mode = self.job.get("mode")
        if mode == "lyrics" and not self.job.get("lyrics_file"):
            return self._prepare_lyrics_repair()
        if mode == "media":
            return self._prepare_media_repair_job()
        return None

    def command(self) -> list:
        if self.job["kind"] == "new":
            if self._usdb_match:
                return repair_command(
                    self._usdb_match["staging_dir"], mode="gap", out_dir=NEW_SONGS_DIR,
                    language=self.job.get("language"))
            return ultrasinger_command(
                self.job["url"], self.job.get("band"), self.job.get("title"),
                self.job.get("language"), self.job.get("musicbrainz_id"))
        if self._repair_prep:
            return repair_command(
                self._repair_prep["song_dir"], self._repair_prep["mode"],
                self._repair_prep["lyrics_file"], language=self.job.get("language"))
        return repair_command(
            self.job["song_dir"], self.job.get("mode"), self.job.get("lyrics_file"),
            language=self.job.get("language"))

    def _stream(self, pipe, logfile):
        for raw in iter(pipe.readline, b""):
            logfile.write(raw)
            logfile.flush()
            with PRINT_LOCK:
                sys.stdout.write(raw.decode("utf-8", "replace"))
                sys.stdout.flush()
        pipe.close()

    def run(self) -> dict:
        """Returns {"status", "error", "output_path", "duration_s",
        "quarantined_output", "repair_mode_result", "lyrics_source_result",
        "usdb_song_id"}."""
        os.makedirs(LOGS_DIR, exist_ok=True)
        os.makedirs(WORK_DIR, exist_ok=True)
        os.makedirs(NEW_SONGS_DIR, exist_ok=True)
        os.makedirs(REPAIRED_DIR, exist_ok=True)

        self._try_usdb_source()

        if (self.job["kind"] == "new" and not self._usdb_match
                and not self.job["url"].startswith("https://")):
            resolved = resolve_song_input(self.job["url"])
            if not os.path.isfile(resolved):
                return {"status": "failed",
                        "error": f"local input file not found: {resolved}",
                        "output_path": None, "duration_s": 0.0,
                        "quarantined_output": None, "repair_mode_result": None,
                        "lyrics_source_result": None, "usdb_song_id": None}

        prep_result = self._prepare_repair_source()
        if prep_result is not None:
            out(f"  !! {prep_result['status']}: {prep_result['error']}")
            return {"status": prep_result["status"], "error": prep_result["error"],
                    "output_path": None, "duration_s": 0.0,
                    "quarantined_output": None, "repair_mode_result": None,
                    "lyrics_source_result": None, "usdb_song_id": None}

        out_dir = NEW_SONGS_DIR if self.job["kind"] == "new" else REPAIRED_DIR
        before_names = set(os.listdir(out_dir)) if os.path.isdir(out_dir) else set()

        cmd = self.command()
        out("")
        out("=" * 74)
        out(f"  START {self.job['kind'].upper()}: {self.job['label']}  "
            f"(attempt {self.job.get('attempts', 1)})")
        out(f"  cmd: {' '.join(shlex.quote(c) for c in cmd)}")
        out("=" * 74)

        start = time.time()
        output_path = None
        error = None
        stopped_reason = None

        with open(self.log_path, "wb") as logfile:
            try:
                self.proc = subprocess.Popen(
                    cmd, cwd=ULTRASINGER_CWD,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, env=os.environ.copy())
            except Exception as exc:  # noqa: BLE001
                return {"status": "failed", "error": f"spawn error: {exc}",
                        "output_path": None, "duration_s": 0.0,
                        "quarantined_output": None, "repair_mode_result": None,
                        "lyrics_source_result": None, "usdb_song_id": None}

            reader = threading.Thread(
                target=self._stream, args=(self.proc.stdout, logfile), daemon=True)
            reader.start()

            timeout = JOB_TIMEOUT_MIN * 60
            while True:
                try:
                    self.proc.wait(timeout=2)
                    break
                except subprocess.TimeoutExpired:
                    if self.control.is_terminating():
                        stopped_reason = "stopped by user"
                        break
                    if self.control.should_skip():
                        stopped_reason = "skipped by user"
                        # consume the flag now - otherwise it would abort
                        # every subsequent job in the queue too
                        self.control.clear_skip()
                        break
                    if time.time() - start > timeout:
                        stopped_reason = "timeout"
                        break

            if stopped_reason:
                self._terminate(stopped_reason)
            reader.join(timeout=10)

            if stopped_reason:
                status = "failed"
                error = stopped_reason
            elif self.proc.returncode == 0:
                status = "done"
            else:
                status = "failed"
                error = f"exit code {self.proc.returncode}"

            # find produced ultrastar file / repair summary in the log
            repair_mode_result = None
            lyrics_source_result = None
            try:
                with open(self.log_path, encoding="utf-8", errors="replace") as f:
                    log_text = f.read()
                for line in log_text.splitlines():
                    if output_path is None and "Creating UltraStar file" in line:
                        m = re.search(r"Creating UltraStar file (.+)$", line.strip())
                        if m:
                            output_path = m.group(1).strip()
                    kv = parse_repair_done_line(line)
                    if "output" in kv:
                        output_path = kv["output"]
                    if "mode" in kv:
                        repair_mode_result = kv["mode"]
                    if "lyrics_source" in kv:
                        lyrics_source_result = kv["lyrics_source"]
            except OSError:
                pass

        # a failed job must never leave partial/broken output in the live
        # tree - quarantine anything the job created before it died
        quarantined_output = None
        if status == "failed":
            for name, dst in quarantine_partial_output(
                    self.job["kind"], before_names, out_dir):
                out(f"  .. moved partial output to {dst}")
                quarantined_output = quarantined_output or dst
                if output_path and os.path.basename(os.path.dirname(output_path)) == name:
                    output_path = os.path.join(dst, os.path.basename(output_path))
                    quarantined_output = dst

        duration = time.time() - start
        return {"status": status, "error": error,
                "output_path": output_path, "duration_s": duration,
                "quarantined_output": quarantined_output,
                "repair_mode_result": repair_mode_result,
                "lyrics_source_result": lyrics_source_result,
                "usdb_song_id": self._usdb_match["song_id"] if self._usdb_match else None}

    def _terminate(self, reason):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        out(f"  !! job {reason}")


# --------------------------------------------------------------------------
# interactive commands (stdin, e.g. via `docker compose attach`)
# --------------------------------------------------------------------------

def stdin_listener(cmd_queue, control, state_data_fn):
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:  # noqa: BLE001
            return
        if not line:
            return
        cmd = line.strip().lower()
        if not cmd:
            continue
        if cmd in ("s", "status", "progress"):
            out(render_progress(state_data_fn()))
        elif cmd == "skip":
            control.request_skip()
            out(">> skipping current job ...")
        elif cmd in ("stop", "q", "quit", "exit"):
            control.request_stop()
            out(">> stopping after current job ...")
        elif cmd in ("h", "help", "?"):
            out("commands: s=status  skip=abort current  stop=finish current then exit")
        else:
            out(f"?? unknown command {cmd!r} "
                "(s=status, skip=abort current, stop=finish then exit)")


# --------------------------------------------------------------------------
# main command handlers
# --------------------------------------------------------------------------

def cmd_run(state, only=None, control=None):
    control = control or CONTROL or Control()
    jobs = build_job_plan(state, only=only)
    state.save()

    eligible = [j for j in jobs if j["status"] == "pending"]
    retryable = [j for j in jobs
                 if j["status"] == "failed" and j["attempts"] < MAX_ATTEMPTS]
    run_queue = eligible + retryable

    if not run_queue:
        out(render_progress(state.data))
        out("Nothing to do - all jobs are done or skipped. "
            "(Use `reset --all` to re-run everything.)")
        return

    out(f"Jobs to process: {len(run_queue)} "
        f"({len(eligible)} pending, {len(retryable)} retries)")
    out(resource_profile.describe_profile(
        STACK_RAM_GB, STACK_SWAP_GB, STACK_CPU_CORES, STACK_VRAM_GB, DEVICE))
    out(f"  (resolved: whisper={WHISPER_MODEL} batch_size={WHISPER_BATCH_SIZE or 'default'} "
        f"demucs={DEMUCS_MODEL})")
    out("Interactive commands: s=status  skip=abort current  stop=finish current then exit")
    out("Attach to this session any time with:  docker compose attach ultrasinger")

    listener = threading.Thread(
        target=stdin_listener,
        args=(None, control, lambda: state.data), daemon=True)
    listener.start()

    total = len(run_queue)
    for idx, job in enumerate(run_queue, start=1):
        if control.is_terminating() or control.should_stop_after_current():
            break

        out("")
        out(f">>> [{idx}/{total}] {job['kind'].upper()}: {job['label']}")

        job["status"] = "running"
        job["attempts"] = job.get("attempts", 0) + 1
        job["started_at"] = now_iso()
        job["finished_at"] = None
        job["log"] = os.path.relpath(
            os.path.join(LOGS_DIR, slugify(job["id"]) + ".log"), "/data")
        state.save()

        runner = JobRunner(job, control)
        result = runner.run()
        job["status"] = result["status"]
        job["error"] = result["error"]
        job["output_path"] = result["output_path"]
        job["quarantined_output"] = result.get("quarantined_output")
        if result.get("repair_mode_result"):
            job["repair_mode_result"] = result["repair_mode_result"]
        if result.get("lyrics_source_result"):
            job["lyrics_source"] = result["lyrics_source_result"]
        job["finished_at"] = now_iso()
        job["duration_s"] = result["duration_s"]
        state.save()

        mark = "DONE" if result["status"] == "done" else "FAILED"
        out(f"<<< [{idx}/{total}] {mark} in {fmt_duration(result['duration_s'])} - "
            f"{job['label']}")
        if result["output_path"]:
            out(f"    output: {result['output_path']}")

        if result["status"] == "done" and result["output_path"]:
            if job["kind"] == "new":
                # replace whisper's own (possibly mis-heard) lyrics with a
                # trusted online source when one can be found, BEFORE
                # romanization runs on whatever text ends up final - a
                # usdb-sourced song skips this (see finalize_new_job_lyrics)
                job["lyrics_source"] = finalize_new_job_lyrics(
                    job, result["output_path"], result.get("usdb_song_id"))
                state.save()
            run_romanize_step(job, result["output_path"])

    if control.is_terminating():
        state.save()
        out("Stopped. Run `docker compose up` again to resume the remaining jobs.")
    else:
        out("")
        out("All jobs processed. Final state:")
    report_path = write_report(state.data)
    out(f"Report written to {report_path}")
    out(render_progress(state.data))


def cmd_list(state, only=None):
    jobs = build_job_plan(state, only=only)
    state.save()
    out(f"{'STATUS':<9} {'KIND':<7} LABEL")
    out("-" * 74)
    for j in jobs:
        out(f"{j['status']:<9} {j['kind']:<7} {j['label']}")
    out("-" * 74)
    out(render_progress(state.data))


def cmd_report(state, only=None):
    jobs = build_job_plan(state, only=only)
    state.save()
    text = render_report(state.data)
    out(text)
    path = write_report(state.data)
    out(f"Report written to {path}")


def cmd_progress(watch=False, interval=10):
    state = State()
    while True:
        state.load(recover_running=False)  # do not mutate running jobs for display
        text = render_progress(state.data)
        if watch:
            sys.stdout.write("\x1b[2J\x1b[H")  # clear screen
        sys.stdout.write(text + "\n")
        if not watch:
            return
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return


def cmd_run_one(url):
    if not url.startswith("http"):
        print("run-one expects a youtube url")
        sys.exit(1)
    fake = {"id": f"new|{url}", "kind": "new", "label": url, "url": url, "attempts": 1}
    runner = JobRunner(fake)
    result = runner.run()
    out(f"result: {result['status']} ({result['error'] or 'ok'})")
    sys.exit(0 if result["status"] == "done" else 1)


def looks_like_ultrastar_txt_folder(folder):
    return any(
        e.lower().endswith(".txt") and looks_like_ultrastar_txt(os.path.join(folder, e))
        for e in os.listdir(folder)
    )


def cmd_repair_one(folder):
    folder = os.path.abspath(folder)
    if not looks_like_ultrastar_txt_folder(folder):
        print(f"{folder} does not contain an ultrastar txt")
        sys.exit(1)
    fake = {"id": f"repair|{os.path.basename(folder)}", "kind": "repair",
            "label": os.path.basename(folder), "song_dir": folder, "attempts": 1}
    runner = JobRunner(fake)
    result = runner.run()
    out(f"result: {result['status']} ({result['error'] or 'ok'})")
    sys.exit(0 if result["status"] == "done" else 1)


def cmd_reset(state, all_jobs=False):
    n = 0
    for job in state.data["jobs"].values():
        if job["status"] in ("failed", "running", "needs_review") or \
                (all_jobs and job["status"] == "done"):
            job["status"] = "pending"
            job["error"] = None
            job["attempts"] = 0
            n += 1
    state.save()
    print(f"reset {n} jobs to pending")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def handle_signals(signum, _frame):
    out(f"\n>> received signal {signum}, stopping current job ...")
    if CONTROL is not None:
        CONTROL.request_stop()
    else:
        sys.exit(143)


def main():
    global CONTROL
    args = sys.argv[1:]
    state = State()

    if not args or args[0] == "run":
        CONTROL = Control()
        signal.signal(signal.SIGTERM, handle_signals)
        signal.signal(signal.SIGINT, handle_signals)
        signal.signal(signal.SIGTERM, handle_signals)
        signal.signal(signal.SIGINT, handle_signals)
        state.load()
        only = None
        if "--only-new" in args:
            only = ["new"]
        elif "--only-repairs" in args:
            only = ["repair"]
        cmd_run(state, only=only)
    elif args[0] == "list":
        state.load()
        cmd_list(state)
    elif args[0] == "report":
        state.load()
        cmd_report(state)
    elif args[0] == "progress":
        cmd_progress(watch="-w" in args or "--watch" in args)
    elif args[0] == "run-one" and len(args) > 1:
        cmd_run_one(args[1])
    elif args[0] == "repair-one" and len(args) > 1:
        cmd_repair_one(args[1])
    elif args[0] == "reset":
        state.load()
        cmd_reset(state, all_jobs="--all" in args)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
