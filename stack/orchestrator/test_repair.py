#!/usr/bin/env python3
"""Tests for repair.py bug fixes.

About me: plain assert-based regression checks for repair.py's pure logic
(no pytest, no real audio/whisperx - those are stubbed out). repair.py
imports heavy UltraSinger modules at the top, so this must run inside the
docker image:

    docker compose run --rm ultrasinger python /app/orchestrator/test_repair.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import repair  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


class FakeTxt:
    """gap=0, real_bpm=60 -> beat_to_sec(beat) == beat, so note.beat can be
    treated directly as seconds in these tests."""

    def note_start_sec(self, note):
        return note.beat

    def note_end_sec(self, note):
        return note.beat + note.dur

    def beat_to_sec(self, beat):
        return beat


def make_notes(convention, count, start=0, dur=0.8):
    """count single-syllable notes, one word per note, spaced 1s apart."""
    notes = []
    for i in range(count):
        beat = float(start + i)
        if convention == "trailing":
            word = f"w{i} "  # trailing space marks the word as complete
        elif convention == "leading":
            # leading space marks a NEW word starting; the very first note
            # of the whole song has nothing before it, so no marker needed
            word = f"w{i}" if (start + i) == 0 else f" w{i}"
        else:
            raise ValueError(convention)
        notes.append(repair.Note(":", beat, dur, 0, word))
    return notes


def make_lines(notes, per_line=3):
    return [{"notes": notes[i:i + per_line]}
            for i in range(0, len(notes), per_line)]


txt = FakeTxt()

# --------------------------------------------------------------------------
# build_alignment_units must chunk by word count regardless of which
# word-boundary convention the song uses (trailing-space OR leading-space)
# --------------------------------------------------------------------------

for convention in ("trailing", "leading"):
    notes = make_notes(convention, 12)
    lines = make_lines(notes, per_line=3)
    units = repair.build_alignment_units(txt, lines, min_words=8, max_gap=8.0)
    word_counts = [len(repair.group_words(u["notes"])) for u in units]
    check(f"{convention}-space convention: 12 words -> 2 units "
          f"(got {len(units)})", len(units) == 2)
    check(f"{convention}-space convention: unit sizes are [9, 3] "
          f"(got {word_counts})", word_counts == [9, 3])

# --------------------------------------------------------------------------
# compute_orig_word_durs must return REAL per-word durations
# --------------------------------------------------------------------------

notes = make_notes("trailing", 4, dur=0.8)
groups = repair.group_words(notes)
durs = repair.compute_orig_word_durs(txt, notes, groups)
check("compute_orig_word_durs returns one entry per group",
      len(durs) == len(groups))
check(f"compute_orig_word_durs returns real durations, not a placeholder "
      f"(got {durs})", all(abs(d - 0.8) < 1e-6 for d in durs))

# --------------------------------------------------------------------------
# refine_first_units must feed REAL word durations into clean_aligned_words
# (regression for: it used to hardcode [1.0] * len(groups))
# --------------------------------------------------------------------------

captured = {}
_real_clean_aligned_words = repair.clean_aligned_words


def _spy_clean_aligned_words(words, groups, orig_word_durs, *rest, **kwargs):
    captured["durs"] = list(orig_word_durs)
    return _real_clean_aligned_words(words, groups, orig_word_durs, *rest, **kwargs)


repair.clean_aligned_words = _spy_clean_aligned_words
repair.align_line = lambda *a, **kw: []  # stub out whisperx

try:
    notes = make_notes("trailing", 3, start=10, dur=0.8)
    txt.notes = notes  # implied_gap_samples() looks words up via txt.notes
    units = [{"notes": notes}]
    # a strongly deviating aligned start on the first word forces
    # refine_first_units past its "consistent, nothing to do" early-out
    aligned_units = [{"words": [{"start": 100.0, "end": 100.5}, None, None],
                      "score": None}]
    repair.refine_first_units(txt, units, aligned_units,
                              model=None, meta=None, audio16k=None,
                              audio_dur=200.0, med_gap=0.0)
    check("refine_first_units reached clean_aligned_words",
          "durs" in captured)
    if "durs" in captured:
        check(f"refine_first_units passes REAL durations, not [1.0, 1.0, 1.0] "
              f"(got {captured['durs']})",
              all(abs(d - 0.8) < 1e-6 for d in captured["durs"]))
finally:
    repair.clean_aligned_words = _real_clean_aligned_words

# --------------------------------------------------------------------------
# recreate_song(): must pass through an already-resolved language instead
# of letting whisper re-detect from scratch (regression for a real case:
# a purely German song mis-detected as English at 0.41 confidence, because
# the recreate fallback discarded the language the sync attempt had
# already correctly resolved from the txt's own #LANGUAGE tag)
# --------------------------------------------------------------------------

import subprocess as _sp

_recreate_calls = []


class _FakeCompleted:
    returncode = 0


def _fake_run(cmd, **kwargs):
    _recreate_calls.append(cmd)
    return _FakeCompleted()


_orig_run = _sp.run
_sp.run = _fake_run
try:
    _song_dir = tempfile.mkdtemp(prefix="recreate-song-")
    _audio_path = os.path.join(_song_dir, "audio.mp3")
    with open(_audio_path, "w", encoding="utf-8") as f:
        f.write("x")
    _out_dir = tempfile.mkdtemp(prefix="recreate-out-")
    _work_dir = tempfile.mkdtemp(prefix="recreate-work-")

    repair.recreate_song(_song_dir, _out_dir, _work_dir, _audio_path, language="de")
    cmd = _recreate_calls[-1]
    check("recreate_song passes --language when given",
          "--language" in cmd and cmd[cmd.index("--language") + 1] == "de")
    check("recreate_song passes --demucs (module-level DEMUCS_MODEL)",
          "--demucs" in cmd)

    repair.recreate_song(_song_dir, _out_dir, _work_dir, _audio_path, language=None)
    cmd2 = _recreate_calls[-1]
    check("recreate_song omits --language when none is known",
          "--language" not in cmd2)
finally:
    _sp.run = _orig_run

# --------------------------------------------------------------------------
# locate_audio(): extracting audio from a #VIDEO-only song whose audio
# track is Opus (yt-dlp's bestaudio for many YouTube videos, remuxed as-is
# into an mp4 container by --merge-output-format mp4 - a real, valid,
# playable file, ffprobe-confirmed) used to fail outright: the old code
# always stream-copied (acodec="copy") into a ".m4a" file for any ".mp4"
# source, but the MP4/M4A muxer does not support Opus without transcoding
# ("Could not find tag for codec opus in stream #0, codec not currently
# supported in container"). Found live 2026-09-14 testing real
# song-requests.csv rows (USDB-sourced videos): "Cypecore - Identity" and
# "Dropkick Murphys - Rose Tattoo" both failed this way.
# --------------------------------------------------------------------------

import subprocess as _subprocess_real  # noqa: E402

_opus_video_dir = tempfile.mkdtemp(prefix="opus-video-song-")
_opus_video_path = os.path.join(_opus_video_dir, "video.mp4")
_ffmpeg_build = _subprocess_real.run(
    ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
     "-c:v", "libx264", "-c:a", "libopus",
     "-movflags", "+faststart", _opus_video_path],
    capture_output=True)
if _ffmpeg_build.returncode != 0:
    check("locate_audio: could not build the Opus-in-mp4 test fixture "
          f"(ffmpeg: {_ffmpeg_build.stderr[-300:]!r}) - skipping this check",
          False)
else:
    class _OpusVideoTxt:
        audio_ref = None
        video_ref = "video.mp4"

    _opus_work_dir = tempfile.mkdtemp(prefix="opus-video-work-")
    audio_out, err = repair.locate_audio(_opus_video_dir, _OpusVideoTxt(), _opus_work_dir)
    check(f"locate_audio extracts audio from an Opus-in-mp4 video (got err={err!r})",
          err is None and audio_out is not None and os.path.isfile(audio_out))

    # the plain stream-copy path (AAC audio, the common case) was ALSO
    # broken by the same "-vn 1" bug - not Opus-specific
    _aac_video_dir = tempfile.mkdtemp(prefix="aac-video-song-")
    _aac_video_path = os.path.join(_aac_video_dir, "video.mp4")
    _ffmpeg_build_aac = _subprocess_real.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-c:a", "aac", _aac_video_path],
        capture_output=True)
    if _ffmpeg_build_aac.returncode != 0:
        check("locate_audio: could not build the AAC-in-mp4 test fixture "
              f"(ffmpeg: {_ffmpeg_build_aac.stderr[-300:]!r}) - skipping this check",
              False)
    else:
        _aac_work_dir = tempfile.mkdtemp(prefix="aac-video-work-")
        audio_out_aac, err_aac = repair.locate_audio(
            _aac_video_dir, _OpusVideoTxt(), _aac_work_dir)
        check("locate_audio extracts audio from an AAC-in-mp4 video via the "
              f"fast stream-copy path (got err={err_aac!r})",
              err_aac is None and audio_out_aac is not None and
              os.path.isfile(audio_out_aac) and audio_out_aac.endswith(".m4a"))

# --------------------------------------------------------------------------
# interpolate_word_timings(): a LEADING run (no aligned word before it,
# only after) must scale its words to fit the actual time available
# before the next aligned word - just like the middle-run branch already
# does (scale = span / total). Found live 2026-09-15: it didn't - it used
# the run's UNSCALED original durations, so whenever their total exceeds
# the real gap before the first successfully-aligned word (a very
# ordinary situation: the opening lines of a song before the aligner
# locks on), the interpolated words overran straight past where the next
# REAL aligned word starts - corrupting the very beginning of every
# repaired song that hit this path.
# --------------------------------------------------------------------------

def _fake_word(orig_start, orig_end, timing=None):
    return {"timing": timing, "orig_start": orig_start, "orig_end": orig_end,
           "orig_dur": max(0.01, orig_end - orig_start)}


# leading run: 3 words of 2.0s original duration each (total 6.0s), but
# only 1.5s of real time exists before the next aligned word at t=1.5
leading_overrun = [
    _fake_word(0.0, 2.0), _fake_word(2.0, 4.0), _fake_word(4.0, 6.0),
    _fake_word(6.0, 6.5, timing={"start": 1.5, "end": 2.0}),
]
repair.interpolate_word_timings(leading_overrun)
last_leading_end = leading_overrun[2]["interp"][1]
check("interpolate_word_timings: an overrunning LEADING run is scaled "
      f"down to fit before the next aligned word (last interp end "
      f"{last_leading_end} must not exceed the next aligned word's own "
      "start 1.5)",
      last_leading_end <= 1.5 + 1e-9)
check("interpolate_word_timings: the scaled leading run still starts at "
      "or after 0",
      leading_overrun[0]["interp"][0] >= 0.0)

# leading run that already fits comfortably (total 1.0s <= 5.0s
# available) must behave exactly as before: stack unscaled, ending
# exactly at the next aligned word's start
leading_fits = [
    _fake_word(0.0, 0.5), _fake_word(0.5, 1.0),
    _fake_word(1.0, 1.2, timing={"start": 5.0, "end": 5.5}),
]
repair.interpolate_word_timings(leading_fits)
check("interpolate_word_timings: a leading run that already fits is "
      "unaffected by the fix (still lands exactly at the next aligned "
      f"word's start) - got {leading_fits[1]['interp']}",
      abs(leading_fits[1]["interp"][1] - 5.0) < 1e-9)

# --------------------------------------------------------------------------
# resolve_language(): must persist <out_dir>/<song_name>/language.txt (the
# OUTPUT folder - repair.py never writes into the original input song_dir,
# see write_repaired()/write_lyrics_result()'s identical out_song_dir
# pattern) via modules.language_file.resolve_language_with_file(), so a
# saved file wins on the next call even over a conflicting forced/txt
# language, and detect_language() is only ever called when nothing else
# is known.
# --------------------------------------------------------------------------

class _FakeTxtLang:
    def __init__(self, language=None):
        self.language = language


_detect_calls = []
_orig_detect_language = repair.detect_language
repair.detect_language = lambda audio_path: (_detect_calls.append(audio_path) or "ja")

with tempfile.TemporaryDirectory() as _lang_root:
    in_dir = os.path.join(_lang_root, "input")
    out_dir = os.path.join(_lang_root, "output")
    song_dir = os.path.join(in_dir, "Some Song")
    os.makedirs(song_dir, exist_ok=True)

    lang1 = repair.resolve_language(_FakeTxtLang(), "audio.wav", None, song_dir, out_dir)
    check("resolve_language: no file, no forced, no txt.language -> "
          f"auto-detects via detect_language (got {lang1!r})",
          lang1 == "ja" and len(_detect_calls) == 1)

    lang_file_path = os.path.join(out_dir, "Some Song", "language.txt")
    check("resolve_language: the detected language is persisted to "
          f"<out_dir>/<song_name>/language.txt (checked {lang_file_path})",
          os.path.isfile(lang_file_path))
    check("resolve_language: nothing is ever written into the original "
          "input song_dir",
          not os.path.isfile(os.path.join(song_dir, "language.txt")))

    lang2 = repair.resolve_language(
        _FakeTxtLang(language="de"), "audio.wav", "en", song_dir, out_dir)
    check("resolve_language: a saved language.txt wins over BOTH a "
          f"conflicting forced value and txt.language (got {lang2!r})",
          lang2 == "ja")
    check("resolve_language: detect_language is never called again once "
          "a file exists", len(_detect_calls) == 1)

with tempfile.TemporaryDirectory() as _lang_root2:
    in_dir2 = os.path.join(_lang_root2, "input")
    out_dir2 = os.path.join(_lang_root2, "output")
    song_dir2 = os.path.join(in_dir2, "Other Song")
    os.makedirs(song_dir2, exist_ok=True)

    lang3 = repair.resolve_language(
        _FakeTxtLang(), "audio.wav", "fr", song_dir2, out_dir2)
    check(f"resolve_language: forced value used and persisted when no "
          f"file exists yet (got {lang3!r})", lang3 == "fr")
    with open(os.path.join(out_dir2, "Other Song", "language.txt"),
             encoding="utf-8") as f:
        saved = f.read().strip()
    check(f"resolve_language: the forced value was written to the file "
          f"(got {saved!r})", saved == "fr")

repair.detect_language = _orig_detect_language

# --------------------------------------------------------------------------

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
