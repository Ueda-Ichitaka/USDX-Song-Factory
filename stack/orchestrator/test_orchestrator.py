#!/usr/bin/env python3
"""Tests for orchestrator.py.

About me: plain assert-based regression checks for orchestrator.py (no
pytest dependency - the image only installs runtime deps). Run directly
with `python3 test_orchestrator.py` on the host (stdlib only, no docker
needed) or inside the container.
"""

import os
import sys
import tempfile

TMP_DATA = tempfile.mkdtemp(prefix="ultrasinger-test-")
os.environ.setdefault("LOGS_DIR", os.path.join(TMP_DATA, "logs"))
os.environ.setdefault("WORK_DIR", os.path.join(TMP_DATA, "work"))
os.environ.setdefault("OUTPUT_DIR", os.path.join(TMP_DATA, "output"))
os.environ.setdefault("STATE_DIR", os.path.join(TMP_DATA, "state"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import orchestrator as orch  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# Control.skip_current must not leak into the next job
# --------------------------------------------------------------------------

control = orch.Control()
check("skip flag starts clear", control.should_skip() is False)

control.request_skip()
check("skip flag set after request_skip", control.should_skip() is True)

control.clear_skip()
check("skip flag cleared by clear_skip()", control.should_skip() is False)

# --------------------------------------------------------------------------
# JobRunner actually clears the flag once it has aborted a job for "skip"
# (regression for: one `skip` command used to cascade and kill every
# subsequent job in the queue)
# --------------------------------------------------------------------------

orig_cwd = orch.ULTRASINGER_CWD
orch.ULTRASINGER_CWD = os.getcwd()  # any dir that exists, we don't run UltraSinger

control2 = orch.Control()
control2.request_skip()
fake_job = {"id": "new|https://example.com/skip-me", "kind": "new",
            "label": "skip-me", "url": "https://example.com/skip-me",
            "attempts": 1}
runner = orch.JobRunner(fake_job, control2)
runner.command = lambda: [sys.executable, "-c", "import time; time.sleep(5)"]
result = runner.run()
orch.ULTRASINGER_CWD = orig_cwd

check("job with pre-set skip flag is aborted", result["status"] == "failed")
check("skip flag cleared after JobRunner consumed it",
      control2.should_skip() is False)

# a *second* job on the same control must run to completion, not be
# instantly skipped by the stale flag from the previous job
fake_job2 = {"id": "new|https://example.com/should-finish", "kind": "new",
             "label": "should-finish", "url": "https://example.com/should-finish",
             "attempts": 1}
orch.ULTRASINGER_CWD = os.getcwd()
runner2 = orch.JobRunner(fake_job2, control2)
runner2.command = lambda: [sys.executable, "-c", "print('ok')"]
result2 = runner2.run()
orch.ULTRASINGER_CWD = orig_cwd
check("next job on the same control is NOT auto-skipped",
      result2["status"] == "done")

# --------------------------------------------------------------------------
# parse_songs_file must tolerate '#' comment lines anywhere in songs.csv
# --------------------------------------------------------------------------

csv_with_comment = """# my hand-picked song list
band,title,url
Metallica,Enter Sandman,https://www.youtube.com/watch?v=CD-E-LDc384
# a second comment in the middle
Queen,Bohemian Rhapsody,https://www.youtube.com/watch?v=fJ9rUzIMcZQ
"""

with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8") as f:
    f.write(csv_with_comment)
    tmp_path = f.name

try:
    entries = orch.parse_songs_file(tmp_path)
    check("comment-prefixed csv still yields 2 songs", len(entries) == 2)
    if entries:
        check("first entry band parsed correctly",
              entries[0]["band"] == "Metallica")
        check("first entry url parsed correctly",
              entries[0]["url"] == "https://www.youtube.com/watch?v=CD-E-LDc384")
finally:
    os.unlink(tmp_path)

# --------------------------------------------------------------------------
# parse_songs_file: optional language/musicbrainz_id/lyrics_url columns
# (requested from karaoke-dashboard's CSV export, see the request doc) -
# must be accepted when present, default to "" when absent (existing
# 3-column band,title,url files must keep working unchanged)
# --------------------------------------------------------------------------

csv_with_extra_fields = (
    "band,title,url,language,musicbrainz_id,lyrics_url\n"
    "Lacrimosa,Lichtgestalt,https://example.com/x,de,"
    "abc-123-mbid,https://example.com/lyrics.txt\n"
)
with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8") as f:
    f.write(csv_with_extra_fields)
    tmp_path2 = f.name
try:
    entries2 = orch.parse_songs_file(tmp_path2)
    check("parse_songs_file reads the optional language column",
          entries2[0]["language"] == "de")
    check("parse_songs_file reads the optional musicbrainz_id column",
          entries2[0]["musicbrainz_id"] == "abc-123-mbid")
    check("parse_songs_file reads the optional lyrics_url column",
          entries2[0]["lyrics_url"] == "https://example.com/lyrics.txt")
finally:
    os.unlink(tmp_path2)

check("parse_songs_file defaults the optional columns to '' when absent",
      entries[0]["language"] == "" and entries[0]["musicbrainz_id"] == "" and
      entries[0]["lyrics_url"] == "")

# --------------------------------------------------------------------------
# parse_songs_file: the REAL karaoke-dashboard export header
# ("band name,song name,youtube link,...", app/routes/admin.py's
# requests_csv()) - regression for a real bug found 2026-09-14: only
# single-word aliases ("band"/"title"/"url") were recognized, so the
# actual multi-word header parsed EVERY row's band/title/url as "" and
# silently imported zero songs (no error at all)
# --------------------------------------------------------------------------

csv_real_dashboard_header = (
    "band name,song name,youtube link,language,musicbrainz_id,lyrics_url\n"
    "Lil Mariko,Catboys,,,,\n"
    "ASP,Zaubererbruder,https://www.youtube.com/watch?v=sifM_9DIVbI,de,,\n"
)
with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8") as f:
    f.write(csv_real_dashboard_header)
    tmp_path_real = f.name
try:
    entries_real = orch.parse_songs_file(tmp_path_real)
    check("parse_songs_file reads the real dashboard header "
          f"(got {len(entries_real)} entries, expected 2)",
          len(entries_real) == 2)
    if len(entries_real) == 2:
        check("parse_songs_file: 'band name' column -> band",
              entries_real[0]["band"] == "Lil Mariko")
        check("parse_songs_file: 'song name' column -> title",
              entries_real[0]["title"] == "Catboys")
        check("parse_songs_file: empty youtube link -> skip",
              entries_real[0]["url"] == "skip")
        check("parse_songs_file: 'youtube link' column -> url",
              entries_real[1]["url"] ==
              "https://www.youtube.com/watch?v=sifM_9DIVbI")
        check("parse_songs_file: 'youtube link' header doesn't break the "
              "language column next to it",
              entries_real[1]["language"] == "de")
finally:
    os.unlink(tmp_path_real)

# --------------------------------------------------------------------------
# ultrasinger_command() must trust the csv band/title over YouTube/
# MusicBrainz naming, and must write new songs under NEW_SONGS_DIR
# --------------------------------------------------------------------------

url = "https://www.youtube.com/watch?v=abc123"
cmd = orch.ultrasinger_command(url, band="Jan Hegenberg",
                               title="Die Allianz schlägt zurück")
check("ultrasinger_command passes --force_artist",
      "--force_artist" in cmd and
      cmd[cmd.index("--force_artist") + 1] == "Jan Hegenberg")
check("ultrasinger_command passes --force_title",
      "--force_title" in cmd and
      cmd[cmd.index("--force_title") + 1] == "Die Allianz schlägt zurück")
check("ultrasinger_command writes into NEW_SONGS_DIR, not OUTPUT_DIR root",
      "-o" in cmd and cmd[cmd.index("-o") + 1] == orch.NEW_SONGS_DIR)
check("NEW_SONGS_DIR defaults to <OUTPUT_DIR>/new",
      orch.NEW_SONGS_DIR == os.path.join(orch.OUTPUT_DIR, "new"))

cmd_no_meta = orch.ultrasinger_command(url, band="", title=None)
check("ultrasinger_command omits --force_artist/--force_title when empty",
      "--force_artist" not in cmd_no_meta and "--force_title" not in cmd_no_meta)

cmd_with_lang = orch.ultrasinger_command(url, language="de")
check("ultrasinger_command passes --language when the csv gives one "
      "(fixes whisper language mis-detection at the source)",
      "--language" in cmd_with_lang and
      cmd_with_lang[cmd_with_lang.index("--language") + 1] == "de")
cmd_no_lang = orch.ultrasinger_command(url, language="")
check("ultrasinger_command omits --language when the csv doesn't give one",
      "--language" not in cmd_no_lang)

cmd_with_mbid = orch.ultrasinger_command(url, musicbrainz_id="abc-123-mbid")
check("ultrasinger_command passes --musicbrainz_id when the csv gives one",
      "--musicbrainz_id" in cmd_with_mbid and
      cmd_with_mbid[cmd_with_mbid.index("--musicbrainz_id") + 1] == "abc-123-mbid")
cmd_no_mbid = orch.ultrasinger_command(url, musicbrainz_id="")
check("ultrasinger_command omits --musicbrainz_id when the csv doesn't give one",
      "--musicbrainz_id" not in cmd_no_mbid)

fake_new_job_mbid = {"id": "new|u", "kind": "new", "url": url, "band": "",
                     "title": "", "language": "", "musicbrainz_id": "abc-123-mbid"}
built_cmd_mbid = orch.JobRunner(fake_new_job_mbid).command()
check("JobRunner.command() passes the job's musicbrainz_id through",
      "--musicbrainz_id" in built_cmd_mbid and
      built_cmd_mbid[built_cmd_mbid.index("--musicbrainz_id") + 1] == "abc-123-mbid")

# --------------------------------------------------------------------------
# resource-budget config: WHISPER_MODEL/DEMUCS_MODEL/WHISPER_BATCH_SIZE fall
# back to the historical hardcoded defaults when no STACK_*/override env
# var is set (this test process's environment), and ultrasinger_command()
# passes them through to UltraSinger.py when resolved to a real value
# --------------------------------------------------------------------------

check("WHISPER_MODEL defaults to large-v2 with no budget/override given",
      orch.WHISPER_MODEL == "large-v2")
check("DEMUCS_MODEL defaults to htdemucs with no budget/override given",
      orch.DEMUCS_MODEL == "htdemucs")
check("WHISPER_BATCH_SIZE is unset (None) with no budget/override given",
      orch.WHISPER_BATCH_SIZE is None)

orig_demucs_model = orch.DEMUCS_MODEL
orig_batch_size = orch.WHISPER_BATCH_SIZE
orch.DEMUCS_MODEL = "mdx_extra_q"
orch.WHISPER_BATCH_SIZE = 4
try:
    cmd_resources = orch.ultrasinger_command(url)
    check("ultrasinger_command passes --demucs when DEMUCS_MODEL is set",
          "--demucs" in cmd_resources and
          cmd_resources[cmd_resources.index("--demucs") + 1] == "mdx_extra_q")
    check("ultrasinger_command passes --whisper_batch_size when set",
          "--whisper_batch_size" in cmd_resources and
          cmd_resources[cmd_resources.index("--whisper_batch_size") + 1] == "4")

    orch.WHISPER_BATCH_SIZE = None
    cmd_no_batch = orch.ultrasinger_command(url)
    check("ultrasinger_command omits --whisper_batch_size when unset",
          "--whisper_batch_size" not in cmd_no_batch)
finally:
    orch.DEMUCS_MODEL = orig_demucs_model
    orch.WHISPER_BATCH_SIZE = orig_batch_size

check("_env_float returns None for an unset var",
      orch._env_float("SOME_VAR_THAT_IS_NOT_SET_XYZ") is None)
check("_env_int returns None for an unset var",
      orch._env_int("SOME_VAR_THAT_IS_NOT_SET_XYZ") is None)

# --------------------------------------------------------------------------
# resolve_song_input(): local audio/video file paths as new-song input,
# distinguished from YouTube links by "https://" (never guessed otherwise)
# --------------------------------------------------------------------------

check("resolve_song_input passes a YouTube URL through unchanged",
      orch.resolve_song_input("https://www.youtube.com/watch?v=abc") ==
      "https://www.youtube.com/watch?v=abc")
check("resolve_song_input resolves a relative path against INPUT_DIR",
      orch.resolve_song_input("media/song.mp3") ==
      os.path.join(orch.INPUT_DIR, "media/song.mp3"))
check("resolve_song_input passes an absolute path through unchanged",
      orch.resolve_song_input("/data/input/song.mp4") == "/data/input/song.mp4")

cmd_local = orch.ultrasinger_command("media/song.mp3", band="X", title="Y")
check("ultrasinger_command resolves local file paths for -i",
      "-i" in cmd_local and
      cmd_local[cmd_local.index("-i") + 1] ==
      os.path.join(orch.INPUT_DIR, "media/song.mp3"))

# a missing local input file must fail fast, without ever spawning UltraSinger
orig_cwd = orch.ULTRASINGER_CWD
orch.ULTRASINGER_CWD = os.getcwd()
fake_job_missing = {"id": "new|missing.mp3", "kind": "new", "label": "missing.mp3",
                    "url": "does-not-exist-anywhere.mp3", "attempts": 1}
runner_missing = orch.JobRunner(fake_job_missing, orch.Control())
result_missing = runner_missing.run()
orch.ULTRASINGER_CWD = orig_cwd
check("missing local input file -> fast-failed job",
      result_missing["status"] == "failed")
check("missing local input file -> error message names the resolved path",
      "not found" in (result_missing["error"] or ""))

# --------------------------------------------------------------------------
# failed-job output quarantine: a partial output folder must be moved out
# of the live output dir, not left to pollute the real song library
# --------------------------------------------------------------------------

qdir = tempfile.mkdtemp(prefix="ultrasinger-quarantine-src-")
orch.FAILED_DIR = os.path.join(TMP_DATA, "failed")

before = set(os.listdir(qdir))
partial = os.path.join(qdir, "Partial Song")
os.makedirs(partial)
with open(os.path.join(partial, "x.txt"), "w", encoding="utf-8") as f:
    f.write("x")

moved = orch.quarantine_partial_output("new", before, qdir)
check("quarantine_partial_output moves exactly the new folder",
      len(moved) == 1 and moved[0][0] == "Partial Song")
check("quarantine_partial_output removes it from the live output dir",
      not os.path.isdir(partial))
check("quarantine_partial_output destination is under FAILED_DIR/<kind>/",
      moved[0][1] == os.path.join(orch.FAILED_DIR, "new", "Partial Song"))

# a second collision on the same name must not clobber the first
os.makedirs(partial)
moved2 = orch.quarantine_partial_output("new", before, qdir)
check("a repeat quarantine of the same name gets a (1) suffix, not overwritten",
      moved2[0][1] == os.path.join(orch.FAILED_DIR, "new", "Partial Song (1)"))

# --------------------------------------------------------------------------
# count_usdb_sourced(): how many completed new songs came from USDB
# --------------------------------------------------------------------------

usdb_count_jobs = [
    {"kind": "new", "status": "done", "lyrics_source": "usdb:animux:123"},
    {"kind": "new", "status": "done", "lyrics_source": "usdb:eu:456"},
    {"kind": "new", "status": "done", "lyrics_source": "transcribed"},
    {"kind": "new", "status": "done", "lyrics_source": "online:genius"},
    {"kind": "new", "status": "failed", "lyrics_source": None},
    {"kind": "repair", "status": "done", "lyrics_source": "usdb:animux:999"},
]
check("count_usdb_sourced counts only done NEW jobs sourced from either "
      "USDB site",
      orch.count_usdb_sourced(usdb_count_jobs) == 2)
check("count_usdb_sourced returns 0 for an empty job list",
      orch.count_usdb_sourced([]) == 0)

# --------------------------------------------------------------------------
# render_progress(): the live status dashboard (docker compose exec ...
# progress -w) - counts, current job, device, resource usage
# --------------------------------------------------------------------------

fake_progress_state = {"jobs": {
    "new|1": {"kind": "new", "status": "done", "label": "Band A - Song A",
             "duration_s": 60, "lyrics_source": "usdb:animux:111",
             "started_at": "2026-01-01T10:00:00+00:00",
             "finished_at": "2026-01-01T10:01:00+00:00"},
    "new|2": {"kind": "new", "status": "pending", "label": "Band B - Song B"},
    "new|3": {"kind": "new", "status": "running", "label": "Band C - Song C",
             "started_at": "2026-01-01T10:05:00+00:00", "attempts": 1},
    "repair|1": {"kind": "repair", "status": "done", "label": "Song D",
                "duration_s": 30},
    "repair|2": {"kind": "repair", "status": "pending", "label": "Song E"},
}}

progress_text = orch.render_progress(
    fake_progress_state, cpu_percent=42.0,
    ram_usage={"used_gb": 5.2, "total_gb": 30.9, "percent": 16.8},
    gpu_usage={"used_gb": 4.7, "total_gb": 15.9, "percent": 29.6},
    device="cuda")
check("render_progress still shows NEW SONGS / REPAIRS counts",
      "NEW SONGS" in progress_text and "REPAIRS" in progress_text)
check("render_progress shows the currently running job",
      "Band C - Song C" in progress_text)
check("render_progress shows how many new songs were pulled from USDB",
      "Pulled from USDB: 1" in progress_text)
check("render_progress shows the device (cpu/cuda)",
      "cuda" in progress_text.lower())
check("render_progress shows CPU/RAM/GPU usage when given",
      "42" in progress_text and "5.2" in progress_text and
      "30.9" in progress_text and "4.7" in progress_text and "15.9" in progress_text)

progress_text_cpu = orch.render_progress(
    fake_progress_state, cpu_percent=10.0,
    ram_usage={"used_gb": 2.0, "total_gb": 8.0, "percent": 25.0},
    gpu_usage=None, device="cpu")
check("render_progress shows 'cpu' as the device and omits GPU stats when "
      "there is none",
      "cpu" in progress_text_cpu.lower() and "VRAM" not in progress_text_cpu)

progress_text_no_resources = orch.render_progress(
    fake_progress_state, cpu_percent=None, ram_usage=None, gpu_usage=None,
    device="cpu")
check("render_progress doesn't crash and just omits the resource line when "
      "readings are unavailable",
      "NEW SONGS" in progress_text_no_resources)

# --------------------------------------------------------------------------
# render_report(): the tabular finishing report (new / repaired / failed)
# --------------------------------------------------------------------------

fake_state = {"jobs": {
    "new|u1": {"kind": "new", "status": "done", "label": "Band A - Song A",
              "output_path": "/data/output/new/Band A - Song A/Band A - Song A.txt",
              "duration_s": 120, "attempts": 1},
    "repair|s1": {"kind": "repair", "status": "done", "label": "Song B",
                 "output_path": "/data/output/repaired/Song B/Song B.txt",
                 "duration_s": 200, "attempts": 1, "repair_mode_result": "sync"},
    "new|u2": {"kind": "new", "status": "failed", "label": "Band C - Song C",
              "error": "exit code 1", "attempts": 2,
              "quarantined_output": "/data/output/failed/new/Band C - Song C"},
}}
report = orch.render_report(fake_state)
check("report lists the new song", "Band A - Song A" in report)
check("report lists the repaired song with its mode",
      "Song B" in report and "sync" in report)
check("report lists the failed job with its error",
      "Band C - Song C" in report and "exit code 1" in report)
check("report mentions the quarantined output path",
      "/data/output/failed/new/Band C - Song C" in report)

# --------------------------------------------------------------------------
# parse_repair_done_line(): output_path / mode must survive song names that
# contain spaces (virtually all of them do) - a naive line.split() on the
# whole REPAIR_DONE line truncates the path at its first space
# --------------------------------------------------------------------------

spaced_line = ("REPAIR_DONE output=/data/output/repaired/ASP - Ich will "
              "brennen/ASP - Ich will brennen.txt gap_old_ms=20834 "
              "gap_new_ms=19334 aligned=207/210 repitched=183 mode=sync")
kv = orch.parse_repair_done_line(spaced_line)
check("parse_repair_done_line keeps the full spaced output path",
      kv.get("output") ==
      "/data/output/repaired/ASP - Ich will brennen/ASP - Ich will brennen.txt")
check("parse_repair_done_line still extracts mode correctly",
      kv.get("mode") == "sync")
check("parse_repair_done_line still extracts gap_old_ms correctly",
      kv.get("gap_old_ms") == "20834")

recreate_line = ("REPAIR_DONE output=/data/output/repaired/Some Song/Some "
                 "Song.txt gap_old_ms=1234 mode=recreated")
kv2 = orch.parse_repair_done_line(recreate_line)
check("parse_repair_done_line handles the shorter 'recreated' summary form",
      kv2.get("output") == "/data/output/repaired/Some Song/Some Song.txt" and
      kv2.get("mode") == "recreated")

check("parse_repair_done_line returns {} for an unrelated log line",
      orch.parse_repair_done_line("some other log line") == {})

# --------------------------------------------------------------------------
# run_romanize_step(): a best-effort post-processing hook, skippable via
# ROMANIZE=0, must never raise even if the subprocess itself misbehaves
# --------------------------------------------------------------------------

romanize_txt = os.path.join(TMP_DATA, "song.txt")
with open(romanize_txt, "w", encoding="utf-8") as f:
    f.write("dummy")


class _FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode


calls = []


def fake_subprocess_run(cmd, **kwargs):
    calls.append(cmd)
    return _FakeCompleted(0)


orig_subprocess_run = orch.subprocess.run
orig_romanize_enabled = orch.ROMANIZE_ENABLED
orch.subprocess.run = fake_subprocess_run
try:
    orch.ROMANIZE_ENABLED = False
    orch.run_romanize_step({"id": "new|x"}, romanize_txt)
    check("ROMANIZE_ENABLED=False skips romanization entirely", calls == [])

    orch.ROMANIZE_ENABLED = True
    orch.run_romanize_step({"id": "new|x"}, romanize_txt)
    check("romanization step invokes romanize.py with --txt <output>",
          len(calls) == 1 and calls[0][-2:] == ["--txt", romanize_txt])

    orch.run_romanize_step({"id": "new|missing"}, "/no/such/file.txt")
    check("romanization step is a no-op when output_path doesn't exist",
          len(calls) == 1)
finally:
    orch.subprocess.run = orig_subprocess_run
    orch.ROMANIZE_ENABLED = orig_romanize_enabled

# --------------------------------------------------------------------------
# find_lyrics_file(): auto-detect a trusted lyrics file next to a broken
# song, and repair_command()/JobRunner.command() must route it through
# --mode lyrics --lyrics-file
# --------------------------------------------------------------------------

lyrics_dir = os.path.join(TMP_DATA, "song-with-lyrics")
os.makedirs(lyrics_dir, exist_ok=True)
check("find_lyrics_file: none present -> None",
      orch.find_lyrics_file(lyrics_dir) is None)
lyrics_path = os.path.join(lyrics_dir, "lyrics.txt")
with open(lyrics_path, "w", encoding="utf-8") as f:
    f.write("line one\nline two\n")
check("find_lyrics_file: finds lyrics.txt",
      orch.find_lyrics_file(lyrics_dir) == lyrics_path)

cmd_lyrics = orch.repair_command("/data/input/Some Song", mode="lyrics",
                                 lyrics_file="/data/input/Some Song/lyrics.txt")
check("repair_command passes --mode lyrics",
      "--mode" in cmd_lyrics and
      cmd_lyrics[cmd_lyrics.index("--mode") + 1] == "lyrics")
check("repair_command passes --lyrics-file",
      "--lyrics-file" in cmd_lyrics and
      cmd_lyrics[cmd_lyrics.index("--lyrics-file") + 1] ==
      "/data/input/Some Song/lyrics.txt")

cmd_default = orch.repair_command("/data/input/Some Song")
check("repair_command omits --lyrics-file when not given",
      "--lyrics-file" not in cmd_default)

fake_repair_job = {"id": "repair|x", "kind": "repair", "song_dir": "/data/input/x",
                   "mode": "lyrics", "lyrics_file": "/data/input/x/lyrics.txt"}
fake_runner = orch.JobRunner(fake_repair_job)
built_cmd = fake_runner.command()
check("JobRunner.command() routes a lyrics-mode repair job correctly",
      "--mode" in built_cmd and
      built_cmd[built_cmd.index("--mode") + 1] == "lyrics" and
      "--lyrics-file" in built_cmd)

# --------------------------------------------------------------------------
# run_lyrics_step(): best-effort online-lyrics fetch + realign, must
# never turn a working song into a failed job on any kind of failure
# --------------------------------------------------------------------------

lyrics_txt = os.path.join(TMP_DATA, "song2.txt")
with open(lyrics_txt, "w", encoding="utf-8") as f:
    f.write("dummy")

check("run_lyrics_step: no band/title -> stays transcribed, no subprocess call",
      orch.run_lyrics_step({"id": "new|x", "band": "", "title": ""}, lyrics_txt)
      == "transcribed")

lyrics_url_calls = []
orig_subprocess_run1b = orch.subprocess.run


def fake_subprocess_run1b(cmd, **kwargs):
    lyrics_url_calls.append(cmd)
    return _FakeCompleted(1)


orch.subprocess.run = fake_subprocess_run1b
try:
    orch.run_lyrics_step(
        {"id": "new|u", "band": "Some Band", "title": "Some Song",
         "lyrics_url": "https://example.com/trusted.txt"}, lyrics_txt)
    fetch_call = next(c for c in lyrics_url_calls if c[1] == orch.LYRICS_FETCH_PY)
    check("run_lyrics_step passes --lyrics-url when the job has one",
          "--lyrics-url" in fetch_call and
          fetch_call[fetch_call.index("--lyrics-url") + 1] ==
          "https://example.com/trusted.txt")

    lyrics_url_calls.clear()
    orch.run_lyrics_step(
        {"id": "new|v", "band": "Some Band", "title": "Some Song"}, lyrics_txt)
    fetch_call_no_url = next(
        c for c in lyrics_url_calls if c[1] == orch.LYRICS_FETCH_PY)
    check("run_lyrics_step omits --lyrics-url when the job has none",
          "--lyrics-url" not in fetch_call_no_url)
finally:
    orch.subprocess.run = orig_subprocess_run1b

orig_subprocess_run2 = orch.subprocess.run
lyrics_calls = []


def fake_subprocess_run2(cmd, **kwargs):
    lyrics_calls.append(cmd)
    if cmd[1] == orch.LYRICS_FETCH_PY:
        return _FakeCompleted(1)  # not found
    return _FakeCompleted(0)


orch.subprocess.run = fake_subprocess_run2
try:
    result_nf = orch.run_lyrics_step(
        {"id": "new|nf", "band": "Some Band", "title": "Some Song"}, lyrics_txt)
    check("run_lyrics_step: fetch not found -> stays transcribed",
          result_nf == "transcribed")
    check("run_lyrics_step: does not call repair.py when nothing was found",
          all(c[1] != orch.REPAIR_PY for c in lyrics_calls))
finally:
    orch.subprocess.run = orig_subprocess_run2

# fetch succeeds -> realign step runs, source comes from its REPAIR_DONE line
orig_subprocess_run3 = orch.subprocess.run


def fake_subprocess_run3(cmd, **kwargs):
    if cmd[1] == orch.LYRICS_FETCH_PY:
        out_path = cmd[cmd.index("--out") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            f.write('{"source": "genius", "lines": [{"text": "hi", "start": null}]}')
        return _FakeCompleted(0)
    if cmd[1] == orch.REPAIR_PY:
        # simulate repair.py writing its REPAIR_DONE summary to the
        # redirected stdout file handle
        kwargs["stdout"].write(
            b"REPAIR_DONE output=/data/x.txt mode=lyrics aligned=5/5 "
            b"lyrics_source=genius\n")
        kwargs["stdout"].flush()
        return _FakeCompleted(0)
    return _FakeCompleted(0)


orch.subprocess.run = fake_subprocess_run3
try:
    job = {"id": "new|ok", "band": "Some Band", "title": "Some Song"}
    result_ok = orch.run_lyrics_step(job, lyrics_txt)
    check(f"run_lyrics_step: success -> online:<source> (got {result_ok!r})",
          result_ok == "online:genius")
finally:
    orch.subprocess.run = orig_subprocess_run3

# --------------------------------------------------------------------------
# render_report(): Lyrics column for new songs
# --------------------------------------------------------------------------

fake_state_lyrics = {"jobs": {
    "new|u1": {"kind": "new", "status": "done", "label": "Band A - Song A",
              "output_path": "/data/output/new/Band A - Song A/x.txt",
              "duration_s": 60, "lyrics_source": "online:genius"},
    "new|u2": {"kind": "new", "status": "done", "label": "Band B - Song B",
              "output_path": "/data/output/new/Band B - Song B/x.txt",
              "duration_s": 60, "lyrics_source": "transcribed"},
}}
report_lyrics = orch.render_report(fake_state_lyrics)
check("report shows the online lyrics source without a warning marker",
      "online:genius" in report_lyrics and
      "Band A - Song A | online:genius |" in report_lyrics)
check("report flags transcribed-only lyrics with a warning marker",
      "transcribed ⚠" in report_lyrics)

# --------------------------------------------------------------------------
# patch_tag(): rewrite/insert a "#TAG:value" line in a txt's raw content
# --------------------------------------------------------------------------

txt_with_video = "#TITLE:X\n#ARTIST:Y\n#VIDEO:old.webm\n#BPM:100\nF 0 1 60 Hi\n"
patched = orch.patch_tag(txt_with_video, "VIDEO", "video.mp4")
check("patch_tag replaces an existing tag value in place",
      "#VIDEO:video.mp4" in patched and "old.webm" not in patched)
check("patch_tag keeps the rest of the file untouched",
      "#TITLE:X" in patched and "F 0 1 60 Hi" in patched)

txt_without_video = "#TITLE:X\n#ARTIST:Y\n#BPM:100\nF 0 1 60 Hi\n"
patched2 = orch.patch_tag(txt_without_video, "VIDEO", "video.mp4")
check("patch_tag inserts a new tag right after the last '#...:' header line",
      patched2.splitlines()[:4] ==
      ["#TITLE:X", "#ARTIST:Y", "#BPM:100", "#VIDEO:video.mp4"])

txt_no_tags = "F 0 1 60 Hi\n"
patched3 = orch.patch_tag(txt_no_tags, "VIDEO", "video.mp4")
check("patch_tag inserts at the very start when there are no '#' tags at all",
      patched3.splitlines()[0] == "#VIDEO:video.mp4")

# --------------------------------------------------------------------------
# append_comment_tag(): unlike patch_tag(), MERGES into an existing
# #COMMENT value instead of overwriting it (that value is often
# legitimate uploader-supplied info, e.g. "Eurovision 2021")
# --------------------------------------------------------------------------

txt_with_comment = "#TITLE:X\n#COMMENT:Eurovision 2021\n#BPM:100\nF 0 1 60 Hi\n"
merged = orch.append_comment_tag(txt_with_comment, "usdb comment GAP hints: 10820")
check("append_comment_tag merges into an existing #COMMENT value rather "
      "than overwriting it",
      "#COMMENT:Eurovision 2021 | usdb comment GAP hints: 10820" in merged)
check("append_comment_tag keeps the rest of the file untouched",
      "#TITLE:X" in merged and "#BPM:100" in merged)

txt_empty_comment = "#TITLE:X\n#COMMENT:\n#BPM:100\n"
merged_empty = orch.append_comment_tag(txt_empty_comment, "usdb comment GAP hints: 10820")
check("append_comment_tag doesn't leave a stray ' | ' when the existing "
      "#COMMENT value is empty",
      "#COMMENT:usdb comment GAP hints: 10820" in merged_empty)

txt_no_comment = "#TITLE:X\n#BPM:100\nF 0 1 60 Hi\n"
inserted = orch.append_comment_tag(txt_no_comment, "usdb comment GAP hints: 10820")
check("append_comment_tag inserts a new #COMMENT tag when the txt has none",
      inserted.splitlines()[:3] ==
      ["#TITLE:X", "#BPM:100", "#COMMENT:usdb comment GAP hints: 10820"])

# --------------------------------------------------------------------------
# download_media(): shells out to yt-dlp, video vs audio format selection
# --------------------------------------------------------------------------

os.makedirs(orch.LOGS_DIR, exist_ok=True)

dl_calls = []


def fake_subprocess_run_dl(cmd, **kwargs):
    dl_calls.append(cmd)
    dest = cmd[cmd.index("-o") + 1]
    with open(dest, "wb") as f:
        f.write(b"fake media bytes")
    return _FakeCompleted(0)


orig_run_dl = orch.subprocess.run
orch.subprocess.run = fake_subprocess_run_dl
try:
    dest_video = os.path.join(TMP_DATA, "video.mp4")
    ok = orch.download_media("https://www.youtube.com/watch?v=x", dest_video,
                             want_video=True,
                             log_path=os.path.join(orch.LOGS_DIR, "dl.log"))
    check("download_media returns True when the file was produced",
          ok and os.path.isfile(dest_video))
    check("download_media requests a combined mp4 for video",
          "--merge-output-format" in dl_calls[-1] and
          dl_calls[-1][dl_calls[-1].index("--merge-output-format") + 1] == "mp4")

    dl_calls.clear()
    dest_audio = os.path.join(TMP_DATA, "audio.mp3")
    orch.download_media("https://www.youtube.com/watch?v=x", dest_audio,
                        want_video=False,
                        log_path=os.path.join(orch.LOGS_DIR, "dl2.log"))
    check("download_media requests audio extraction for audio-only",
          "-x" in dl_calls[-1] and "--audio-format" in dl_calls[-1])
finally:
    orch.subprocess.run = orig_run_dl

orch.subprocess.run = lambda cmd, **kw: _FakeCompleted(1)
try:
    dest_fail = os.path.join(TMP_DATA, "should-not-exist.mp4")
    check("download_media returns False on a non-zero exit code",
          orch.download_media("https://x", dest_fail, want_video=True,
                              log_path=os.path.join(orch.LOGS_DIR, "dl3.log")) is False)
finally:
    orch.subprocess.run = orig_run_dl

orch.subprocess.run = lambda cmd, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    check("download_media survives a subprocess exception",
          orch.download_media("https://x", os.path.join(TMP_DATA, "y.mp4"),
                              want_video=True,
                              log_path=os.path.join(orch.LOGS_DIR, "dl4.log")) is False)
finally:
    orch.subprocess.run = orig_run_dl

# --------------------------------------------------------------------------
# get_usdb_session_and_catalog(): lazy login + catalog cache, sticky-fails
# for the rest of the run rather than retrying every job
# --------------------------------------------------------------------------

orch.USDB_USERNAME = ""
orch.USDB_PASSWORD = ""
orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False
check("get_usdb_session_and_catalog: disabled with no credentials",
      orch.get_usdb_session_and_catalog() == (None, None))

orch.USDB_USERNAME = "me"
orch.USDB_PASSWORD = "secret"
login_calls_g = []
orch.usdb_lookup.login = lambda u, p: login_calls_g.append((u, p)) or "fake-session"
orch.usdb_lookup.load_catalog = lambda session, path, **kw: [{"song_id": "1"}]
session1, catalog1 = orch.get_usdb_session_and_catalog()
check("get_usdb_session_and_catalog logs in + loads the catalog once",
      session1 == "fake-session" and catalog1 == [{"song_id": "1"}] and
      len(login_calls_g) == 1)

session2, catalog2 = orch.get_usdb_session_and_catalog()
check("get_usdb_session_and_catalog reuses the session/catalog on later calls",
      session2 == "fake-session" and len(login_calls_g) == 1)

orch.USDB_USERNAME = "me2"
orch.USDB_PASSWORD = "bad"
orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False
orch.usdb_lookup.login = lambda u, p: None
check("get_usdb_session_and_catalog returns (None, None) when login fails",
      orch.get_usdb_session_and_catalog() == (None, None))
check("get_usdb_session_and_catalog becomes sticky-unavailable after a failed login",
      orch._usdb_unavailable is True)

login_calls_g2 = []
orch.usdb_lookup.login = lambda u, p: login_calls_g2.append(1) or "session"
check("a sticky-unavailable state is not retried for later jobs in the same run",
      orch.get_usdb_session_and_catalog() == (None, None) and login_calls_g2 == [])

orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False

# --------------------------------------------------------------------------
# get_usdb_eu_session(): same lazy-login/sticky-fail shape as
# get_usdb_session_and_catalog(), independent of it
# --------------------------------------------------------------------------

orch.USDB_EU_EMAIL = ""
orch.USDB_EU_PASSWORD = ""
orch._usdb_eu_session = None
orch._usdb_eu_unavailable = False
check("get_usdb_eu_session: disabled with no credentials",
      orch.get_usdb_eu_session() is None)

orch.USDB_EU_EMAIL = "me@example.com"
orch.USDB_EU_PASSWORD = "secret"
eu_login_calls = []
orch.usdb_eu_lookup.login = lambda e, p: eu_login_calls.append((e, p)) or "eu-session"
check("get_usdb_eu_session logs in and returns the session",
      orch.get_usdb_eu_session() == "eu-session" and len(eu_login_calls) == 1)
check("get_usdb_eu_session reuses the session on later calls",
      orch.get_usdb_eu_session() == "eu-session" and len(eu_login_calls) == 1)

orch._usdb_eu_session = None
orch._usdb_eu_unavailable = False
orch.usdb_eu_lookup.login = lambda e, p: None
check("get_usdb_eu_session returns None when login fails",
      orch.get_usdb_eu_session() is None)
eu_login_calls2 = []
orch.usdb_eu_lookup.login = lambda e, p: eu_login_calls2.append(1) or "eu-session"
check("a failed usdb.eu login is sticky too (not retried this run)",
      orch.get_usdb_eu_session() is None and eu_login_calls2 == [])

orch._usdb_eu_session = None
orch._usdb_eu_unavailable = False
orch.USDB_EU_EMAIL = ""
orch.USDB_EU_PASSWORD = ""

# --------------------------------------------------------------------------
# find_usdb_match(): animux.de first, usdb.eu as a second source; each
# side is independently optional
# --------------------------------------------------------------------------

orch.USDB_USERNAME = ""
orch.USDB_PASSWORD = ""
orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False
orch.USDB_EU_EMAIL = ""
orch.USDB_EU_PASSWORD = ""
orch._usdb_eu_session = None
orch._usdb_eu_unavailable = False
check("find_usdb_match returns None when neither source is configured",
      orch.find_usdb_match("Some Band", "Some Song") is None)

orch.USDB_USERNAME = "me"
orch.USDB_PASSWORD = "secret"
orch.usdb_lookup.login = lambda u, p: "session"
orch.usdb_lookup.load_catalog = lambda session, path, **kw: [{"song_id": "1"}]
orch.usdb_lookup.find_usdb_song = lambda session, catalog, band, title: {
    "song_id": "111", "txt": "#TITLE:x\n", "details": None, "video_url": None}
eu_calls_should_not_happen = []
orch.usdb_eu_lookup.find_usdb_eu_song = lambda *a, **kw: (
    eu_calls_should_not_happen.append(1))
result_animux = orch.find_usdb_match("Some Band", "Some Song")
check("find_usdb_match prefers an animux.de match and tags it 'animux'",
      result_animux is not None and result_animux["site"] == "animux" and
      result_animux["song_id"] == "111")
check("find_usdb_match doesn't even try usdb.eu when animux.de already matched",
      eu_calls_should_not_happen == [])

orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False
orch.usdb_lookup.find_usdb_song = lambda session, catalog, band, title: None
orch.USDB_EU_EMAIL = "me@example.com"
orch.USDB_EU_PASSWORD = "secret"
orch.usdb_eu_lookup.login = lambda e, p: "eu-session"
orch.usdb_eu_lookup.find_usdb_eu_song = lambda session, band, title: {
    "song_id": "222", "txt": "#TITLE:y\n", "cover_bytes": b"jpeg-bytes"}
result_eu = orch.find_usdb_match("Some Band", "Some Song")
check("find_usdb_match falls back to usdb.eu when animux.de has no match, "
      "tagging it 'eu'",
      result_eu is not None and result_eu["site"] == "eu" and
      result_eu["song_id"] == "222")
check("find_usdb_match fills in video_url as None for a usdb.eu match "
      "(comment-video scraping not implemented for that source yet), but "
      "passes cover_bytes through unchanged (it comes from the download zip)",
      result_eu["video_url"] is None and result_eu["cover_bytes"] == b"jpeg-bytes")

orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False
orch._usdb_eu_session = None
orch._usdb_eu_unavailable = False
orch.USDB_USERNAME = ""
orch.USDB_PASSWORD = ""
orch.USDB_EU_EMAIL = ""
orch.USDB_EU_PASSWORD = ""

# --------------------------------------------------------------------------
# prepare_usdb_job(): best-effort - source a NEW job from an existing USDB
# upload instead of full generation
# --------------------------------------------------------------------------

orch.USDB_USERNAME = "me"
orch.USDB_PASSWORD = "secret"
orch.usdb_lookup.login = lambda u, p: "session"
orch.usdb_lookup.load_catalog = lambda session, path, **kw: [{"song_id": "1"}]

job_usdb = {"id": "new|x", "band": "Lacrimosa", "title": "Lichtgestalt",
           "url": "https://www.youtube.com/watch?v=fallback"}


class FakeUsdbDetails:
    cover_url = None


def fake_find_usdb_song(session, catalog, band, title):
    return {"song_id": "12345", "txt": "#TITLE:Lichtgestalt\n#BPM:100\n",
            "details": FakeUsdbDetails(), "video_url": None}


orch.usdb_lookup.find_usdb_song = fake_find_usdb_song

dl_calls2 = []


def fake_download_media(url, dest, want_video, log_path):
    dl_calls2.append((url, dest, want_video))
    with open(dest, "wb") as f:
        f.write(b"fake")
    return True


orig_download_media = orch.download_media
orch.download_media = fake_download_media
try:
    result = orch.prepare_usdb_job(job_usdb)
    check("prepare_usdb_job returns a staging dir + a site-prefixed song_id "
          "on success",
          result is not None and result["song_id"] == "animux:12345" and
          os.path.isdir(result["staging_dir"]))
    check("prepare_usdb_job falls back to the job's own url when USDB has no video",
          dl_calls2[0][0] == "https://www.youtube.com/watch?v=fallback")
    with open(os.path.join(result["staging_dir"], "song.txt"), encoding="utf-8") as f:
        written = f.read()
    check("prepare_usdb_job writes a txt with a #VIDEO tag pointing at the download",
          "#VIDEO:video.mp4" in written)
finally:
    orch.download_media = orig_download_media


def fake_find_usdb_song_with_cover(session, catalog, band, title):
    return {"song_id": "555", "txt": "#TITLE:Lichtgestalt\n#BPM:100\n",
            "video_url": None, "cover_bytes": b"\xff\xd8fake-jpeg-bytes"}


orch.usdb_lookup.find_usdb_song = fake_find_usdb_song_with_cover
orch.download_media = fake_download_media
try:
    result_cover = orch.prepare_usdb_job(job_usdb)
    check("prepare_usdb_job writes cover_bytes to cover.jpg in the staging dir",
          result_cover is not None and
          open(os.path.join(result_cover["staging_dir"], "cover.jpg"), "rb").read() ==
          b"\xff\xd8fake-jpeg-bytes")
    with open(os.path.join(result_cover["staging_dir"], "song.txt"), encoding="utf-8") as f:
        written_cover = f.read()
    check("prepare_usdb_job patches a #COVER tag pointing at the written cover",
          "#COVER:cover.jpg" in written_cover)
finally:
    orch.download_media = orig_download_media
    orch.usdb_lookup.find_usdb_song = fake_find_usdb_song


def fake_find_usdb_song_with_details(session, catalog, band, title):
    return {"song_id": "777", "txt": "#TITLE:Lichtgestalt\n#BPM:100\n",
            "video_url": None, "details": "some-details-object"}


orig_extract_gap_hints = orch.usdb_lookup.extract_gap_hints
orch.usdb_lookup.find_usdb_song = fake_find_usdb_song_with_details
orch.usdb_lookup.extract_gap_hints = lambda details: (
    ["10820", "10650"] if details == "some-details-object" else [])
orch.download_media = fake_download_media
try:
    result_hints = orch.prepare_usdb_job(job_usdb)
    with open(os.path.join(result_hints["staging_dir"], "song.txt"), encoding="utf-8") as f:
        written_hints = f.read()
    check("prepare_usdb_job logs usdb comment GAP hints into a #COMMENT tag",
          "#COMMENT:usdb comment GAP hints: 10820, 10650" in written_hints)
finally:
    orch.download_media = orig_download_media
    orch.usdb_lookup.find_usdb_song = fake_find_usdb_song
    orch.usdb_lookup.extract_gap_hints = orig_extract_gap_hints


def fake_find_usdb_song_with_video(session, catalog, band, title):
    return {"song_id": "999", "txt": "#TITLE:X\n#BPM:100\n",
            "details": FakeUsdbDetails(),
            "video_url": "https://www.youtube.com/watch?v=usdb-video"}


orch.usdb_lookup.find_usdb_song = fake_find_usdb_song_with_video
dl_calls2.clear()
orch.download_media = fake_download_media
try:
    orch.prepare_usdb_job(job_usdb)
    check("prepare_usdb_job prefers USDB's own linked video over the job's url "
          "(\"only fill gaps\")",
          dl_calls2[0][0] == "https://www.youtube.com/watch?v=usdb-video")
finally:
    orch.download_media = orig_download_media

orch.usdb_lookup.find_usdb_song = lambda session, catalog, band, title: None
check("prepare_usdb_job returns None when USDB has no confident match",
      orch.prepare_usdb_job(job_usdb) is None)

orch.USDB_USERNAME = ""
orch.USDB_PASSWORD = ""
orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False
match_calls = []
orch.usdb_lookup.find_usdb_song = lambda *a, **kw: match_calls.append(1)
check("prepare_usdb_job returns None immediately when USDB isn't configured",
      orch.prepare_usdb_job(job_usdb) is None and match_calls == [])

orch.USDB_USERNAME = "me"
orch.USDB_PASSWORD = "secret"
orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False
orch.usdb_lookup.login = lambda u, p: "session"
orch.usdb_lookup.load_catalog = lambda session, path, **kw: [{"song_id": "1"}]
orch.usdb_lookup.find_usdb_song = fake_find_usdb_song  # video_url None
job_no_url = {"id": "new|y", "band": "Lacrimosa", "title": "Lichtgestalt", "url": "skip"}
check("prepare_usdb_job returns None when there is no usable video source at all",
      orch.prepare_usdb_job(job_no_url) is None)

orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False
orch.download_media = lambda *a, **kw: False
try:
    check("prepare_usdb_job returns None when the yt-dlp download fails",
          orch.prepare_usdb_job(job_usdb) is None)
finally:
    orch.download_media = orig_download_media

orch._usdb_session = None
orch._usdb_catalog = None
orch._usdb_unavailable = False

# --------------------------------------------------------------------------
# finalize_new_job_lyrics(): a usdb-sourced song keeps its (community-
# verified) lyrics untouched instead of running the online-lyrics pass
# --------------------------------------------------------------------------

check("finalize_new_job_lyrics skips run_lyrics_step for a usdb-sourced song",
      orch.finalize_new_job_lyrics({"id": "new|z"}, "/x/song.txt", "12345") ==
      "usdb:12345")

lyrics_step_calls = []
orig_run_lyrics_step = orch.run_lyrics_step
orch.run_lyrics_step = lambda job, path: lyrics_step_calls.append(1) or "transcribed"
try:
    result_norm = orch.finalize_new_job_lyrics({"id": "new|z"}, "/x/song.txt", None)
    check("finalize_new_job_lyrics runs the normal online-lyrics pass for a "
          "generated (non-usdb) song",
          result_norm == "transcribed" and lyrics_step_calls == [1])
finally:
    orch.run_lyrics_step = orig_run_lyrics_step

# --------------------------------------------------------------------------
# JobRunner.command(): repair.py --mode gap on the usdb staging dir when a
# usdb match was found, ultrasinger otherwise
# --------------------------------------------------------------------------

runner_usdb = orch.JobRunner({"id": "new|j", "kind": "new", "url": "https://x",
                              "band": "B", "title": "T"})
runner_usdb._usdb_match = {"staging_dir": "/data/work/j-usdb", "song_id": "1"}
cmd_usdb = runner_usdb.command()
check("JobRunner.command uses repair.py gap-mode when a usdb match was set",
      "--mode" in cmd_usdb and
      cmd_usdb[cmd_usdb.index("--mode") + 1] == "gap" and
      "/data/work/j-usdb" in cmd_usdb)

runner_gen = orch.JobRunner({"id": "new|j2", "kind": "new", "url": "https://x",
                             "band": "B", "title": "T"})
check("JobRunner.command falls back to ultrasinger when there's no usdb match",
      "repair.py" not in " ".join(runner_gen.command()))

# --------------------------------------------------------------------------
# read_ultrastar_tags() / primary_ultrastar_txt(): lightweight header-tag
# parsing for repair folders (no full repair.py Txt/note parsing needed)
# --------------------------------------------------------------------------

tags_dir = os.path.join(TMP_DATA, "tags-song")
os.makedirs(tags_dir, exist_ok=True)
tags_txt_path = os.path.join(tags_dir, "song.txt")
with open(tags_txt_path, "w", encoding="utf-8") as f:
    f.write("#TITLE:I Want To Break Free\n#ARTIST:Queen\n#MP3:song.mp3\n"
            "#BPM:107.4\n#GAP:40020\n: 0 4 60 Hi\n")

tags = orch.read_ultrastar_tags(tags_txt_path)
check("read_ultrastar_tags reads #ARTIST/#TITLE/#MP3",
      tags.get("ARTIST") == "Queen" and tags.get("TITLE") == "I Want To Break Free"
      and tags.get("MP3") == "song.mp3")
check("read_ultrastar_tags stops at the first note line "
      "(never scans the whole song)",
      "GAP" in tags and len(tags) == 5)
check("read_ultrastar_tags on a missing file returns {}",
      orch.read_ultrastar_tags("/no/such/file.txt") == {})

check("primary_ultrastar_txt finds the one valid ultrastar txt",
      orch.primary_ultrastar_txt(tags_dir) == tags_txt_path)

empty_dir = os.path.join(TMP_DATA, "empty-song-dir")
os.makedirs(empty_dir, exist_ok=True)
check("primary_ultrastar_txt returns None when the folder has no valid txt",
      orch.primary_ultrastar_txt(empty_dir) is None)

# --------------------------------------------------------------------------
# pick_media_candidate(): pure loose-file matching for "link an existing
# file" (missing video/audio) - prefers a name matching the txt's own
# basename, falls back to a lone candidate, refuses to guess when
# ambiguous
# --------------------------------------------------------------------------

check("pick_media_candidate: no matching-extension files -> 'none'",
      orch.pick_media_candidate(["cover.jpg"], "song", (".mp4",))["status"] == "none")

one = orch.pick_media_candidate(["song.mp4", "cover.jpg"], "song", (".mp4",))
check("pick_media_candidate: a single candidate is used even if unnamed like the txt",
      one == {"status": "found", "name": "song.mp4"})

basename_pref = orch.pick_media_candidate(
    ["Other Name.mp4", "song.mp4"], "song", (".mp4",))
check("pick_media_candidate prefers the file matching the txt's own basename",
      basename_pref == {"status": "found", "name": "song.mp4"})

ambiguous = orch.pick_media_candidate(
    ["Other Name.mp4", "Another One.mp4"], "song", (".mp4",))
check("pick_media_candidate refuses to guess between two unrelated candidates",
      ambiguous["status"] == "ambiguous" and
      ambiguous["candidates"] == ["Another One.mp4", "Other Name.mp4"])

# --------------------------------------------------------------------------
# match_broken_report(): matches a repair folder to a broken.csv row via
# its #ARTIST/#TITLE tags (ground truth, not the folder name)
# --------------------------------------------------------------------------

reports = [
    {"band": "Queen", "title": "I Want To Break Free", "category": "gap",
     "description": "", "lyrics_url": ""},
    {"band": "Metric", "title": "Black Sheep", "category": "async",
     "description": "", "lyrics_url": ""},
]
matched = orch.match_broken_report(tags_dir, reports)
check("match_broken_report matches on #ARTIST/#TITLE",
      matched is not None and matched["category"] == "gap")

check("match_broken_report returns None when nothing scores high enough",
      orch.match_broken_report(tags_dir, [
          {"band": "Totally Different", "title": "Unrelated Song",
           "category": "gap", "description": "", "lyrics_url": ""}]) is None)
check("match_broken_report returns None for an empty reports list",
      orch.match_broken_report(tags_dir, []) is None)
check("match_broken_report returns None when the folder has no valid txt",
      orch.match_broken_report(empty_dir, reports) is None)

# --------------------------------------------------------------------------
# build_job_plan(): broken.csv category drives the repair mode; "other"/
# blank/unrecognized category -> "needs_review" (skip automated repair,
# flag for a human) instead of guessing
# --------------------------------------------------------------------------

plan_input = os.path.join(TMP_DATA, "plan-input")
os.makedirs(plan_input, exist_ok=True)


def _write_song(folder_name, artist, title):
    d = os.path.join(plan_input, folder_name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, folder_name + ".txt"), "w", encoding="utf-8") as f:
        f.write(f"#TITLE:{title}\n#ARTIST:{artist}\n#MP3:x.mp3\n#BPM:100\n"
                ": 0 4 60 Hi\n")
    return d


_write_song("Queen - I Want To Break Free", "Queen", "I Want To Break Free")
_write_song("Metric - Black Sheep", "Metric", "Black Sheep")
_write_song("Unlisted Band - Unlisted Song", "Unlisted Band", "Unlisted Song")
_write_song("Some Band - Other Category Song", "Some Band", "Other Category Song")

plan_broken_csv = os.path.join(TMP_DATA, "plan-broken.csv")
with open(plan_broken_csv, "w", encoding="utf-8") as f:
    f.write("band,song name,category,description\n"
            "Queen,I Want To Break Free,gap,\n"
            "Metric,Black Sheep,async,\n"
            "Some Band,Other Category Song,other,needs a human look\n")

orig_input_dir = orch.INPUT_DIR
orig_songs_file = orch.SONGS_FILE
orig_broken_csv = orch.BROKEN_CSV
orch.INPUT_DIR = plan_input
orch.SONGS_FILE = os.path.join(TMP_DATA, "no-such-songs.csv")
orch.BROKEN_CSV = plan_broken_csv
try:
    plan_state = orch.State()
    plan_jobs = orch.build_job_plan(plan_state)
    by_label = {j["label"]: j for j in plan_jobs}
    check("build_job_plan: 'gap' category sets mode 'gap'",
          by_label["Queen - I Want To Break Free"]["mode"] == "gap")
    check("build_job_plan: 'async' category sets mode 'sync'",
          by_label["Metric - Black Sheep"]["mode"] == "sync")
    check("build_job_plan: no broken.csv match keeps today's default mode",
          by_label["Unlisted Band - Unlisted Song"]["mode"] == orch.REPAIR_MODE and
          by_label["Unlisted Band - Unlisted Song"]["status"] == "pending")
    check("build_job_plan: 'other' category is flagged needs_review, not run",
          by_label["Some Band - Other Category Song"]["status"] == "needs_review" and
          by_label["Some Band - Other Category Song"]["description"] ==
          "needs a human look")
finally:
    orch.INPUT_DIR = orig_input_dir
    orch.SONGS_FILE = orig_songs_file
    orch.BROKEN_CSV = orig_broken_csv

# --------------------------------------------------------------------------
# find_sync_meta_source() / parse_sync_meta_source(): a *.usdb sync-meta
# file (written by usdb_syncer) records the original v=/a= youtube ids in
# its meta_tags string - reused as the download source for a "missing
# video"/"missing audio" repair when no local file can be linked
# --------------------------------------------------------------------------

import json as _json
import types as _types

sync_meta_dir = os.path.join(TMP_DATA, "sync-meta-song")
os.makedirs(sync_meta_dir, exist_ok=True)
with open(os.path.join(sync_meta_dir, "12345.usdb"), "w", encoding="utf-8") as f:
    _json.dump({"song_id": 12345,
               "meta_tags": "v=2DG_pIM-Dc4,a=gGwN25z7FrE,co=cover.jpg"}, f)


class _FakeMetaTags:
    def __init__(self, video, audio):
        self.video = video
        self.audio = audio

    @classmethod
    def parse(cls, raw, logger):
        parsed = {}
        for pair in raw.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                parsed[k] = v
        return cls(parsed.get("v"), parsed.get("a"))


def _fake_video_url_from_resource(resource):
    return f"https://www.youtube.com/watch?v={resource}" if resource else None


fake_meta_tags_mod = _types.ModuleType("usdb_syncer.meta_tags")
fake_meta_tags_mod.MetaTags = _FakeMetaTags
fake_utils_mod = _types.ModuleType("usdb_syncer.utils")
fake_utils_mod.video_url_from_resource = _fake_video_url_from_resource
sys.modules["usdb_syncer.meta_tags"] = fake_meta_tags_mod
sys.modules["usdb_syncer.utils"] = fake_utils_mod
try:
    check("parse_sync_meta_source resolves the 'v=' id for category video",
          orch.parse_sync_meta_source(
              os.path.join(sync_meta_dir, "12345.usdb"), "video") ==
          "https://www.youtube.com/watch?v=2DG_pIM-Dc4")
    check("parse_sync_meta_source resolves the 'a=' id for category audio",
          orch.parse_sync_meta_source(
              os.path.join(sync_meta_dir, "12345.usdb"), "audio") ==
          "https://www.youtube.com/watch?v=gGwN25z7FrE")
    check("find_sync_meta_source finds the *.usdb file in the folder",
          orch.find_sync_meta_source(sync_meta_dir, "video") ==
          "https://www.youtube.com/watch?v=2DG_pIM-Dc4")
finally:
    del sys.modules["usdb_syncer.meta_tags"]
    del sys.modules["usdb_syncer.utils"]

check("find_sync_meta_source returns None with no *.usdb file present",
      orch.find_sync_meta_source(tags_dir, "video") is None)
check("parse_sync_meta_source returns None when usdb_syncer isn't importable "
      "(no fake installed, matches the real un-faked container-less state)",
      orch.parse_sync_meta_source(
          os.path.join(sync_meta_dir, "12345.usdb"), "video") is None)

with open(os.path.join(sync_meta_dir, "audio-only.usdb"), "w", encoding="utf-8") as f:
    _json.dump({"song_id": 1, "meta_tags": "v=onlyvideoid1"}, f)
sys.modules["usdb_syncer.meta_tags"] = fake_meta_tags_mod
sys.modules["usdb_syncer.utils"] = fake_utils_mod
try:
    check("parse_sync_meta_source falls back to the video id for category "
          "audio when there is no separate 'a=' resource",
          orch.parse_sync_meta_source(
              os.path.join(sync_meta_dir, "audio-only.usdb"), "audio") ==
          "https://www.youtube.com/watch?v=onlyvideoid1")
finally:
    del sys.modules["usdb_syncer.meta_tags"]
    del sys.modules["usdb_syncer.utils"]
    os.unlink(os.path.join(sync_meta_dir, "audio-only.usdb"))

# --------------------------------------------------------------------------
# prepare_media_repair(): "missing video"/"missing audio" - link an
# existing loose file, or download via the *.usdb sync-meta source; never
# guesses when ambiguous or when there is truly no source
# --------------------------------------------------------------------------

media_dir = os.path.join(TMP_DATA, "media-song")
os.makedirs(media_dir, exist_ok=True)
with open(os.path.join(media_dir, "media-song.txt"), "w", encoding="utf-8") as f:
    f.write("#TITLE:X\n#ARTIST:Y\n#MP3:media-song.mp3\n#BPM:100\n: 0 4 60 Hi\n")
with open(os.path.join(media_dir, "media-song.mp3"), "wb") as f:
    f.write(b"fake mp3")

job_media_present = {"id": "repair|media-song", "kind": "repair",
                     "song_dir": media_dir, "category": "audio"}
check("prepare_media_repair: tag + file already present -> 'already_present'",
      orch.prepare_media_repair(job_media_present) == {"status": "already_present"})

# missing video, no #VIDEO tag at all, but exactly one loose mp4 present
with open(os.path.join(media_dir, "media-song.mp4"), "wb") as f:
    f.write(b"fake mp4")
job_media_video = {"id": "repair|media-song-v", "kind": "repair",
                   "song_dir": media_dir, "category": "video"}
result_link = orch.prepare_media_repair(job_media_video)
check("prepare_media_repair links a loose local file into a staging copy",
      result_link is not None and result_link["source"] == "local" and
      os.path.isdir(result_link["staging_dir"]))
with open(os.path.join(result_link["staging_dir"], "media-song.txt"),
         encoding="utf-8") as f:
    staged_content = f.read()
with open(os.path.join(media_dir, "media-song.txt"), encoding="utf-8") as f:
    original_content_untouched = f.read()
check("prepare_media_repair patches the #VIDEO tag to the linked file, in "
      "a staging copy - the original input folder is never modified",
      "#VIDEO:media-song.mp4" in staged_content and
      "#VIDEO" not in original_content_untouched)

# ambiguous: two unrelated loose video files, neither matching the txt name
ambiguous_dir = os.path.join(TMP_DATA, "ambiguous-media-song")
os.makedirs(ambiguous_dir, exist_ok=True)
with open(os.path.join(ambiguous_dir, "ambiguous-media-song.txt"),
         "w", encoding="utf-8") as f:
    f.write("#TITLE:X\n#ARTIST:Y\n#MP3:a.mp3\n#BPM:100\n: 0 4 60 Hi\n")
with open(os.path.join(ambiguous_dir, "clip one.mp4"), "wb") as f:
    f.write(b"1")
with open(os.path.join(ambiguous_dir, "clip two.mp4"), "wb") as f:
    f.write(b"2")
job_ambiguous = {"id": "repair|ambiguous", "kind": "repair",
                 "song_dir": ambiguous_dir, "category": "video"}
check("prepare_media_repair refuses to guess between ambiguous local candidates",
      orch.prepare_media_repair(job_ambiguous) ==
      {"status": "ambiguous", "candidates": ["clip one.mp4", "clip two.mp4"]})

# no local candidate, no *.usdb source -> None (caller fails the job)
no_source_dir = os.path.join(TMP_DATA, "no-source-song")
os.makedirs(no_source_dir, exist_ok=True)
with open(os.path.join(no_source_dir, "no-source-song.txt"),
         "w", encoding="utf-8") as f:
    f.write("#TITLE:X\n#ARTIST:Y\n#MP3:a.mp3\n#BPM:100\n: 0 4 60 Hi\n")
job_no_source = {"id": "repair|no-source", "kind": "repair",
                 "song_dir": no_source_dir, "category": "video"}
check("prepare_media_repair returns None with no local candidate and no "
      "usdb sync-meta source (never guesses)",
      orch.prepare_media_repair(job_no_source) is None)

# download path: no local candidate, but a *.usdb sync-meta source exists
dl_dir = os.path.join(TMP_DATA, "download-media-song")
os.makedirs(dl_dir, exist_ok=True)
with open(os.path.join(dl_dir, "download-media-song.txt"),
         "w", encoding="utf-8") as f:
    f.write("#TITLE:X\n#ARTIST:Y\n#BPM:100\n: 0 4 60 Hi\n")
with open(os.path.join(dl_dir, "99999.usdb"), "w", encoding="utf-8") as f:
    _json.dump({"song_id": 99999, "meta_tags": "v=downloadvid1"}, f)

sys.modules["usdb_syncer.meta_tags"] = fake_meta_tags_mod
sys.modules["usdb_syncer.utils"] = fake_utils_mod
orig_download_media_media = orch.download_media
dl_media_calls = []


def fake_download_media_for_media_repair(url, dest, want_video, log_path):
    dl_media_calls.append((url, dest, want_video))
    with open(dest, "wb") as f:
        f.write(b"downloaded")
    return True


orch.download_media = fake_download_media_for_media_repair
try:
    job_download = {"id": "repair|download-media-song", "kind": "repair",
                    "song_dir": dl_dir, "category": "video"}
    result_dl = orch.prepare_media_repair(job_download)
    check("prepare_media_repair downloads via the usdb sync-meta source "
          "when nothing can be linked locally",
          result_dl is not None and result_dl["source"] == "usdb-sync-meta" and
          dl_media_calls[0][0] == "https://www.youtube.com/watch?v=downloadvid1" and
          dl_media_calls[0][2] is True)
    with open(os.path.join(result_dl["staging_dir"], "download-media-song.txt"),
             encoding="utf-8") as f:
        dl_staged_content = f.read()
    check("prepare_media_repair patches the #VIDEO tag to the downloaded filename",
          "#VIDEO:download-media-song.mp4" in dl_staged_content)
finally:
    orch.download_media = orig_download_media_media
    del sys.modules["usdb_syncer.meta_tags"]
    del sys.modules["usdb_syncer.utils"]

# --------------------------------------------------------------------------
# JobRunner._prepare_repair_source(): wires prepare_media_repair() and the
# "lyrics" online-fetch path into command()/run(), short-circuiting the
# job cleanly (no repair.py subprocess at all) on failure/needs_review -
# never silently falls back to a blind default repair
# --------------------------------------------------------------------------

orig_prepare_media_repair = orch.prepare_media_repair

# media: success -> command() runs mode "gap" against the staging dir
orch.prepare_media_repair = lambda job: {
    "staging_dir": "/data/work/x-media", "source": "local", "linked": "x.mp4"}
runner_media_ok = orch.JobRunner(
    {"id": "repair|m1", "kind": "repair", "song_dir": "/data/input/m1",
     "mode": "media", "category": "video"})
prep_result_ok = runner_media_ok._prepare_repair_source()
cmd_media_ok = runner_media_ok.command()
check("_prepare_repair_source: media success returns None (no short-circuit)",
      prep_result_ok is None)
check("JobRunner.command() runs 'gap' mode against the media staging dir",
      "--mode" in cmd_media_ok and
      cmd_media_ok[cmd_media_ok.index("--mode") + 1] == "gap" and
      "/data/work/x-media" in cmd_media_ok)

# media: no source at all -> failed, no subprocess
orch.prepare_media_repair = lambda job: None
runner_media_fail = orch.JobRunner(
    {"id": "repair|m2", "kind": "repair", "song_dir": "/data/input/m2",
     "mode": "media", "category": "video"})
prep_result_fail = runner_media_fail._prepare_repair_source()
check("_prepare_repair_source: no media source -> failed, with a clear error",
      prep_result_fail is not None and prep_result_fail["status"] == "failed" and
      "video" in prep_result_fail["error"])

# media: already present -> needs_review, no subprocess
orch.prepare_media_repair = lambda job: {"status": "already_present"}
runner_media_present = orch.JobRunner(
    {"id": "repair|m3", "kind": "repair", "song_dir": "/data/input/m3",
     "mode": "media", "category": "audio"})
prep_result_present = runner_media_present._prepare_repair_source()
check("_prepare_repair_source: media already present -> needs_review, not failed",
      prep_result_present is not None and
      prep_result_present["status"] == "needs_review")

# media: ambiguous candidates -> failed, listing them
orch.prepare_media_repair = lambda job: {
    "status": "ambiguous", "candidates": ["a.mp4", "b.mp4"]}
runner_media_amb = orch.JobRunner(
    {"id": "repair|m4", "kind": "repair", "song_dir": "/data/input/m4",
     "mode": "media", "category": "video"})
prep_result_amb = runner_media_amb._prepare_repair_source()
check("_prepare_repair_source: ambiguous candidates -> failed, names them",
      prep_result_amb is not None and prep_result_amb["status"] == "failed" and
      "a.mp4" in prep_result_amb["error"] and "b.mp4" in prep_result_amb["error"])

orch.prepare_media_repair = orig_prepare_media_repair

# lyrics online-fetch: uses the broken.csv lyrics_url directly when present
orig_subprocess_run_lyrics_repair = orch.subprocess.run
lyrics_repair_calls = []


def fake_subprocess_run_lyrics_repair_ok(cmd, **kwargs):
    lyrics_repair_calls.append(cmd)
    if cmd[1] == orch.LYRICS_FETCH_PY:
        out_path = cmd[cmd.index("--out") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            f.write('{"source": "genius", "lines": []}')
        return _FakeCompleted(0)
    return _FakeCompleted(0)


orch.subprocess.run = fake_subprocess_run_lyrics_repair_ok
try:
    runner_lyrics_url = orch.JobRunner({
        "id": "repair|lyr1", "kind": "repair", "song_dir": tags_dir,
        "mode": "lyrics", "lyrics_file": None,
        "lyrics_url": "https://genius.com/example-lyrics"})
    prep_lyrics = runner_lyrics_url._prepare_repair_source()
    fetch_call_repair = next(
        c for c in lyrics_repair_calls if c[1] == orch.LYRICS_FETCH_PY)
    check("_prepare_repair_source: lyrics category passes --lyrics-url through "
          "when broken.csv provided one",
          prep_lyrics is None and "--lyrics-url" in fetch_call_repair and
          fetch_call_repair[fetch_call_repair.index("--lyrics-url") + 1] ==
          "https://genius.com/example-lyrics")
    cmd_lyrics_repair = runner_lyrics_url.command()
    check("JobRunner.command() uses the fetched lyrics json for a lyrics-category "
          "repair with no local lyrics.txt",
          "--mode" in cmd_lyrics_repair and
          cmd_lyrics_repair[cmd_lyrics_repair.index("--mode") + 1] == "lyrics" and
          "--lyrics-file" in cmd_lyrics_repair and
          cmd_lyrics_repair[cmd_lyrics_repair.index("--lyrics-file") + 1]
          .endswith("-lyrics.json"))
finally:
    orch.subprocess.run = orig_subprocess_run_lyrics_repair

# lyrics online-fetch: no lyrics_url given -> falls back to a normal search
lyrics_repair_calls.clear()


def fake_subprocess_run_lyrics_repair_search(cmd, **kwargs):
    lyrics_repair_calls.append(cmd)
    if cmd[1] == orch.LYRICS_FETCH_PY:
        out_path = cmd[cmd.index("--out") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            f.write('{"source": "syncedlyrics", "lines": []}')
        return _FakeCompleted(0)
    return _FakeCompleted(0)


orch.subprocess.run = fake_subprocess_run_lyrics_repair_search
try:
    runner_lyrics_search = orch.JobRunner({
        "id": "repair|lyr2", "kind": "repair", "song_dir": tags_dir,
        "mode": "lyrics", "lyrics_file": None, "lyrics_url": ""})
    prep_lyrics2 = runner_lyrics_search._prepare_repair_source()
    fetch_call_search = next(
        c for c in lyrics_repair_calls if c[1] == orch.LYRICS_FETCH_PY)
    check("_prepare_repair_source: lyrics category with no lyrics_url tries "
          "an automatic online lookup instead",
          prep_lyrics2 is None and "--lyrics-url" not in fetch_call_search and
          "--artist" in fetch_call_search and
          fetch_call_search[fetch_call_search.index("--artist") + 1] == "Queen")
finally:
    orch.subprocess.run = orig_subprocess_run_lyrics_repair

# lyrics online-fetch: nothing found anywhere -> failed, never falls back
# to a blind sync/gap repair (the category says the LYRICS are wrong -
# silently re-timing the known-wrong lyrics would defeat the point)
lyrics_repair_calls.clear()


def fake_subprocess_run_lyrics_repair_notfound(cmd, **kwargs):
    lyrics_repair_calls.append(cmd)
    return _FakeCompleted(1)


orch.subprocess.run = fake_subprocess_run_lyrics_repair_notfound
try:
    runner_lyrics_nf = orch.JobRunner({
        "id": "repair|lyr3", "kind": "repair", "song_dir": tags_dir,
        "mode": "lyrics", "lyrics_file": None, "lyrics_url": ""})
    prep_lyrics_nf = runner_lyrics_nf._prepare_repair_source()
    check("_prepare_repair_source: lyrics category, nothing found -> failed "
          "(never silently falls back to a blind repair)",
          prep_lyrics_nf is not None and prep_lyrics_nf["status"] == "failed")
finally:
    orch.subprocess.run = orig_subprocess_run_lyrics_repair

# a local lyrics.txt always takes priority - no fetch attempted at all
runner_lyrics_local = orch.JobRunner({
    "id": "repair|lyr4", "kind": "repair", "song_dir": tags_dir,
    "mode": "lyrics", "lyrics_file": "/data/input/lyr4/lyrics.txt",
    "lyrics_url": "https://genius.com/should-be-ignored"})
lyrics_repair_calls.clear()
prep_lyrics_local = runner_lyrics_local._prepare_repair_source()
check("_prepare_repair_source: a local lyrics.txt is used as-is, no online "
      "fetch attempted at all",
      prep_lyrics_local is None and lyrics_repair_calls == [])

# --------------------------------------------------------------------------
# needs_review status: counted, shown in progress/report, and reset() puts
# it back to pending (so fixing the underlying report/category lets it run
# again on the next `docker compose up`)
# --------------------------------------------------------------------------

review_state = {"jobs": {
    "repair|r1": {"kind": "repair", "status": "needs_review",
                 "label": "Some Band - Other Category Song",
                 "category": "other", "description": "needs a human look"},
    "repair|r2": {"kind": "repair", "status": "done", "label": "Fine Song",
                 "duration_s": 10},
}}
review_counts = orch.count_statuses(list(review_state["jobs"].values()), "repair")
check("count_statuses tracks needs_review separately from pending/failed/done",
      review_counts["needs_review"] == 1 and review_counts["done"] == 1)

review_progress = orch.render_progress(review_state, cpu_percent=1, ram_usage=None,
                                       gpu_usage=None, device="cpu")
check("render_progress surfaces the needs_review count",
      "1 needs review" in review_progress or "needs_review" in review_progress)

review_report = orch.render_report(review_state)
check("render_report lists needs_review jobs with their category/description "
      "so a human can act on them",
      "Some Band - Other Category Song" in review_report and
      "needs a human look" in review_report)

review_reset_state = orch.State()
review_reset_state.data = {"version": 1, "jobs": {
    "repair|r1": {"id": "repair|r1", "status": "needs_review", "attempts": 1,
                 "error": "other category: flagged for review"},
}}
orch.cmd_reset(review_reset_state)
check("cmd_reset (no --all) also un-flags needs_review jobs back to pending",
      review_reset_state.data["jobs"]["repair|r1"]["status"] == "pending")

# --------------------------------------------------------------------------

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
