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

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
