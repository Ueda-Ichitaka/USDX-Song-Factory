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
to disable). A repair job whose input folder has a lyrics.txt runs in
"lyrics" mode instead (see repair.py): force-aligns that trusted text,
replacing the existing lyrics rather than just re-timing them.

While a run is active you can attach to the container
(`docker compose attach ultrasinger`) and use these interactive commands:

  s / status    show the progress overview
  skip          abort the current job and continue with the next one
  stop / q      finish the current job, then exit
"""

import csv
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

import resource_profile

# --------------------------------------------------------------------------
# configuration (env, with sensible defaults)
# --------------------------------------------------------------------------

SONGS_FILE = os.environ.get("SONGS_FILE", "/data/input/songs.csv")
INPUT_DIR = os.environ.get("INPUT_DIR", "/data/input")
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

STATE_FILE = os.path.join(STATE_DIR, "state.json")

PRINT_LOCK = threading.Lock()

# module-level control so signal handlers can reach it
CONTROL = None


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
    musicbrainz_id/lyrics_url are parsed but not yet consumed downstream.
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
            url = lower.get("url") or lower.get("link") or lower.get("youtube") or ""
            band = lower.get("band") or lower.get("artist") or ""
            title = lower.get("title") or lower.get("name") or lower.get("song") or ""
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


def scan_repair_jobs(input_dir: str) -> list:
    """Find song folders in input_dir that contain at least one ultrastar txt."""
    folders = []
    if not os.path.isdir(input_dir):
        return folders
    for name in sorted(os.listdir(input_dir)):
        folder = os.path.join(input_dir, name)
        if not os.path.isdir(folder) or name.startswith("."):
            continue
        for entry in sorted(os.listdir(folder)):
            if entry.lower().endswith(".txt") and \
                    looks_like_ultrastar_txt(os.path.join(folder, entry)):
                folders.append(folder)
                break
    return folders


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

    for folder in scan_repair_jobs(INPUT_DIR):
        name = os.path.basename(folder.rstrip("/"))
        job_id = f"repair|{name}"
        lyrics_file = find_lyrics_file(folder)
        mode = "lyrics" if lyrics_file else REPAIR_MODE
        job = state.get_or_create(
            job_id, kind="repair", label=name, song_dir=folder, mode=mode,
            lyrics_file=lyrics_file)
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
    }


def render_progress(state_data) -> str:
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
        return f"  {name:<9}: {counts}{skip}   ({s['total']} total)"

    lines.append(row("NEW SONGS", new))
    lines.append(row("REPAIRS", rep))

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
                   out_dir: str = None) -> list:
    cmd = [sys.executable, REPAIR_PY,
           "--song-dir", song_dir,
           "--out", out_dir or REPAIRED_DIR,
           "--mode", mode or REPAIR_MODE,
           "--device", DEVICE]
    if lyrics_file:
        cmd += ["--lyrics-file", lyrics_file]
    return cmd


class JobRunner:
    """Runs one job as a subprocess, streaming its output to stdout + logfile."""

    def __init__(self, job, control=None):
        self.job = job
        self.control = control or Control()
        self.proc = None
        self.log_path = os.path.join(LOGS_DIR, slugify(job["id"]) + ".log")

    def command(self) -> list:
        if self.job["kind"] == "new":
            return ultrasinger_command(
                self.job["url"], self.job.get("band"), self.job.get("title"),
                self.job.get("language"), self.job.get("musicbrainz_id"))
        return repair_command(
            self.job["song_dir"], self.job.get("mode"), self.job.get("lyrics_file"))

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
        "quarantined_output", "repair_mode_result", "lyrics_source_result"}."""
        os.makedirs(LOGS_DIR, exist_ok=True)
        os.makedirs(WORK_DIR, exist_ok=True)
        os.makedirs(NEW_SONGS_DIR, exist_ok=True)
        os.makedirs(REPAIRED_DIR, exist_ok=True)

        if self.job["kind"] == "new" and not self.job["url"].startswith("https://"):
            resolved = resolve_song_input(self.job["url"])
            if not os.path.isfile(resolved):
                return {"status": "failed",
                        "error": f"local input file not found: {resolved}",
                        "output_path": None, "duration_s": 0.0,
                        "quarantined_output": None, "repair_mode_result": None,
                        "lyrics_source_result": None}

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
                        "lyrics_source_result": None}

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
                "lyrics_source_result": lyrics_source_result}

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
                # romanization runs on whatever text ends up final
                job["lyrics_source"] = run_lyrics_step(job, result["output_path"])
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
        if job["status"] in ("failed", "running") or \
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
