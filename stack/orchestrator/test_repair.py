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

# recreate_song(): the result is the re-created song file, wherever
# UltraSinger put it - a folder left in the work dir by an earlier run makes
# UltraSinger write to "<name> (1)", and the output folder already holds
# language.txt (a .txt that is not a song)

_SONG_TXT = "#ARTIST:moe\n#TITLE:x\n#BPM:300\n#GAP:0\n: 0 4 0 la \nE\n"


def _fake_ultrasinger(make_song):
    def run(cmd, **kwargs):
        us_out = cmd[cmd.index("-o") + 1]
        target = os.path.join(us_out, "moe - x")
        if os.path.exists(target):
            target += " (1)"
        os.makedirs(target)
        if make_song:
            with open(os.path.join(target, "moe - x.txt"), "w", encoding="utf-8") as f:
                f.write(_SONG_TXT)
        return _FakeCompleted()
    return run


def _recreate_case(make_song, stale_folder):
    song_dir = os.path.join(tempfile.mkdtemp(prefix="recreate-in-"), "moe - x")
    os.makedirs(song_dir)
    audio = os.path.join(song_dir, "moe - x.mp3")
    with open(audio, "w", encoding="utf-8") as f:
        f.write("x")
    out_dir = tempfile.mkdtemp(prefix="recreate-out-")
    os.makedirs(os.path.join(out_dir, "moe - x"))
    with open(os.path.join(out_dir, "moe - x", "language.txt"), "w", encoding="utf-8") as f:
        f.write("en\n")
    work_dir = tempfile.mkdtemp(prefix="recreate-work-")
    if stale_folder:
        os.makedirs(os.path.join(work_dir, "recreate_output", "moe - x"))
    _sp.run = _fake_ultrasinger(make_song)
    try:
        return repair.recreate_song(song_dir, out_dir, work_dir, audio, language="en"), out_dir
    finally:
        _sp.run = _orig_run


_expected = lambda out_dir: os.path.join(out_dir, "moe - x", "moe - x.txt")
_got, _out = _recreate_case(make_song=True, stale_folder=False)
check(f"recreate_song returns the song file, not language.txt (got {_got!r})",
      _got == _expected(_out) and os.path.isfile(_got))
_got, _out = _recreate_case(make_song=True, stale_folder=True)
check(f"recreate_song finds the song despite a folder left by an earlier run (got {_got!r})",
      _got == _expected(_out) and os.path.isfile(_got))
_got, _out = _recreate_case(make_song=False, stale_folder=False)
check(f"recreate_song fails when no song file was created (got {_got!r})",
      _got is None)

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
# get_non_silent_subintervals() / distribute_over_non_silent_span() /
# interpolate_word_timings(..., silence_sections=...): a real pause in the
# vocals within an interpolated gap was being completely ignored - the
# unaligned words were blindly stretched across the WHOLE gap (including
# the silent part), so the lyrics "kept going" right through a real pause
# instead of waiting for it to end. Found live 2026-09-16 from the user's
# own manual USDX testing of a full batch regeneration: reported as the
# dominant symptom across most songs ("lyrics directly proceed to next
# line, but vocals have a gap until they actually start" - Bella Ciao,
# Rauta, Moorsoldaten, Omnos, In My Sword I Trust, Mon Amour Mon Ami,
# Lai Lai Hei all independently described the same pattern), and also
# explains wrong #GAP values at the very start (Inis Mona) since a
# leading run has the identical blind spot. Confirmed on real audio:
# Rauta's vocals track measured -20dBFS during real singing vs -65 to
# -86dBFS during the actual pause between lines 4 and 5 - a huge, clean
# margin the existing -50dBFS default threshold already separates
# correctly. The bug was never CONSULTING silence detection at all in
# repair.py's interpolation path, not a broken threshold.
# --------------------------------------------------------------------------

check("get_non_silent_subintervals: no silence -> whole span unchanged",
      repair.get_non_silent_subintervals(10.0, 15.0, []) == [(10.0, 15.0)])
check("get_non_silent_subintervals: silence fully inside the span splits "
      "it into a before/after pair",
      repair.get_non_silent_subintervals(10.0, 15.0, [(11.0, 13.0)]) ==
      [(10.0, 11.0), (13.0, 15.0)])
check("get_non_silent_subintervals: silence at the very start of the "
      "span leaves only the remainder",
      repair.get_non_silent_subintervals(10.0, 15.0, [(9.0, 13.0)]) ==
      [(13.0, 15.0)])
check("get_non_silent_subintervals: silence outside the span is ignored",
      repair.get_non_silent_subintervals(10.0, 15.0, [(20.0, 22.0)]) ==
      [(10.0, 15.0)])
check("get_non_silent_subintervals: multiple silences produce multiple "
      "sub-intervals, in order",
      repair.get_non_silent_subintervals(
          0.0, 10.0, [(2.0, 3.0), (6.0, 7.0)]) ==
      [(0.0, 2.0), (3.0, 6.0), (7.0, 10.0)])

# a real pause (10.0-13.0) sits at the START of the gap (10.0-15.0) -
# words must be packed into the remaining non-silent time (13.0-15.0),
# not stretched across the whole 5s span including the silent part
positions = repair.distribute_over_non_silent_span(
    10.0, 15.0, [1.0, 1.0, 1.0], [(10.0, 13.0)])
check(f"distribute_over_non_silent_span: skips the real silence at the "
      f"start of the gap instead of starting immediately (got {positions!r})",
      positions[0][0] >= 13.0 - 1e-9)
check("distribute_over_non_silent_span: still ends by the gap's own end",
      positions[-1][1] <= 15.0 + 1e-9)
check("distribute_over_non_silent_span: 3 words scaled to fill the 2.0s "
      "of real non-silent time (not the full 5.0s span)",
      abs(positions[-1][1] - positions[0][0] - 2.0) < 1e-6)

no_silence_positions = repair.distribute_over_non_silent_span(
    10.0, 15.0, [1.0, 1.0, 1.0], [])
check("distribute_over_non_silent_span: with no silence, matches the old "
      "whole-span proportional stretch",
      abs(no_silence_positions[0][0] - 10.0) < 1e-9 and
      abs(no_silence_positions[-1][1] - 15.0) < 1e-9)

check("distribute_over_non_silent_span: falls back to the whole span "
      "instead of crashing when silence covers everything",
      repair.distribute_over_non_silent_span(
          10.0, 15.0, [1.0, 1.0], [(10.0, 15.0)]) is not None)

# --------------------------------------------------------------------------
# distribute_over_non_silent_span(): a SPARSE run (1-2 dropped words) in a
# wide, mostly-non-silent gap (quiet instrumental, or singing Whisper just
# failed to match) was stretched by the same scale as a dense run, gluing
# the word(s) to the far end of the gap instead of near their natural
# position/size. Found live 2026-09-17 from broader real-song testing
# (Feuerschwanz/Therion/Hannes Wader/Letzte Instanz) after the pause-aware
# fix above: Therion - Kali Yuga III's "Mistress of Time" (one source
# line) came out as "Mistress of" ... 21s gap ... "Time", right before the
# next line; Letzte Instanz - Rapunzel's whole opening (GAP=5.97s) landed
# nowhere near the real ~20s vocal onset.
# --------------------------------------------------------------------------

sparse_positions = repair.distribute_over_non_silent_span(
    0.0, 30.0, [1.0], [])
check(f"distribute_over_non_silent_span: a single sparse word in a wide "
      f"30s gap is NOT stretched to fill the whole gap (got "
      f"{sparse_positions!r})",
      sparse_positions[-1][1] < 30.0 - 1e-9)
check(f"distribute_over_non_silent_span: the sparse word stays within a "
      f"capped multiple ({repair.INTERP_MAX_STRETCH_FACTOR}x) of its "
      f"natural 1.0s duration (got {sparse_positions!r})",
      sparse_positions[-1][1] - sparse_positions[0][0] <=
      repair.INTERP_MAX_STRETCH_FACTOR + 1e-9)
check("distribute_over_non_silent_span: the sparse word is anchored at "
      "the near edge of the usable span, not the far edge",
      sparse_positions[0][0] < 1.0)

dense_positions = repair.distribute_over_non_silent_span(
    10.0, 15.0, [1.0, 1.0, 1.0], [])
check("distribute_over_non_silent_span: a dense run whose natural total "
      "(3.0s) is close to the usable span (5.0s) is unaffected by the "
      "stretch cap - still ends exactly at the gap's own end",
      abs(dense_positions[-1][1] - 15.0) < 1e-9)

compressed_positions = repair.distribute_over_non_silent_span(
    10.0, 12.0, [1.0, 1.0, 1.0], [])
check("distribute_over_non_silent_span: a run that needs to COMPRESS "
      "(natural total 3.0s > 2.0s usable span) is unaffected by the "
      "stretch cap (only expansion is capped) - still ends exactly at "
      "the gap's own end",
      abs(compressed_positions[-1][1] - 12.0) < 1e-9)

# interpolate_word_timings() end-to-end: a middle run with a real pause
# right after the previous aligned word (matches the Rauta/Bella Ciao
# pattern exactly) must skip into the non-silent remainder
middle_with_pause = [
    _fake_word(10.0, 10.5, timing={"start": 10.0, "end": 10.0}),
    _fake_word(10.5, 11.5), _fake_word(11.5, 12.5), _fake_word(12.5, 13.5),
    _fake_word(13.5, 14.0, timing={"start": 15.0, "end": 15.5}),
]
repair.interpolate_word_timings(middle_with_pause, silence_sections=[(10.0, 13.0)])
check(f"interpolate_word_timings: with silence_sections, a middle run "
      f"skips a real pause instead of starting immediately after the "
      f"previous word (got {middle_with_pause[1]['interp']!r})",
      middle_with_pause[1]["interp"][0] >= 13.0 - 1e-9)

middle_no_silence_info = [
    _fake_word(10.0, 10.5, timing={"start": 10.0, "end": 10.0}),
    _fake_word(10.5, 11.5), _fake_word(11.5, 12.5), _fake_word(12.5, 13.5),
    _fake_word(13.5, 14.0, timing={"start": 15.0, "end": 15.5}),
]
repair.interpolate_word_timings(middle_no_silence_info)
check("interpolate_word_timings: omitting silence_sections (the default) "
      "behaves exactly like before - starts right after the previous word",
      abs(middle_no_silence_info[1]["interp"][0] - 10.0) < 1e-9)

# leading run with real silence before the first confident word (matches
# the Inis Mona wrong-#GAP pattern) - must not backward-stack across it
leading_with_pause = [
    _fake_word(0.0, 1.0), _fake_word(1.0, 2.0),
    _fake_word(2.0, 2.5, timing={"start": 8.0, "end": 8.5}),
]
repair.interpolate_word_timings(leading_with_pause, silence_sections=[(0.0, 4.0)])
check(f"interpolate_word_timings: a leading run with silence_sections "
      f"does not place words inside real silence before the first "
      f"confident word (got {leading_with_pause[0]['interp']!r})",
      leading_with_pause[0]["interp"][0] >= 4.0 - 1e-9)

# --------------------------------------------------------------------------
# interpolate_word_timings(..., audio_dur=...): a TRAILING run (no aligned
# word AFTER it in the whole song) had NO upper bound at all - if several
# consecutive lines at the end of a song all failed alignment/rescue, their
# natural durations got stacked one after another with nothing to stop
# them running past the real end of the audio. Found live 2026-09-17 on
# real audio: Lord of the Lost - Beyond Beautiful's last lyric line ended
# at 259.5s on a 239.45s audio file - 20 real seconds of lyrics displayed
# after the song had already ended.
# --------------------------------------------------------------------------

trailing_overrun = [
    _fake_word(50.0, 51.0, timing={"start": 8.0, "end": 9.0}),
    _fake_word(51.0, 56.0), _fake_word(56.0, 61.0), _fake_word(61.0, 66.0),
]
repair.interpolate_word_timings(trailing_overrun, audio_dur=10.0)
check(f"interpolate_word_timings: with audio_dur given, a trailing run is "
      f"never placed past the real end of the audio (got "
      f"{[w['interp'] for w in trailing_overrun[1:]]!r})",
      all(w["interp"][1] <= 10.0 + 1e-9 for w in trailing_overrun[1:]))

trailing_no_audio_dur = [
    _fake_word(50.0, 51.0, timing={"start": 8.0, "end": 9.0}),
    _fake_word(51.0, 56.0), _fake_word(56.0, 61.0), _fake_word(61.0, 66.0),
]
repair.interpolate_word_timings(trailing_no_audio_dur)
check("interpolate_word_timings: omitting audio_dur (the default) behaves "
      "exactly like before - a trailing run keeps stacking unclamped",
      trailing_no_audio_dur[-1]["interp"][1] > 10.0)

trailing_fits = [
    _fake_word(8.0, 8.5, timing={"start": 8.0, "end": 8.5}),
    _fake_word(8.5, 9.0), _fake_word(9.0, 9.4),
]
repair.interpolate_word_timings(trailing_fits, audio_dur=10.0)
check(f"interpolate_word_timings: a trailing run that already fits within "
      f"audio_dur is unaffected by the clamp (got "
      f"{trailing_fits[-1]['interp']!r})",
      abs(trailing_fits[-1]["interp"][1] - 9.45) < 1e-6)

# --------------------------------------------------------------------------
# clamp_beat_to_max(): write_lyrics_result()'s existing monotonic-forward
# clamp (a note can never start before the previous note's own end, so
# notes render in order) has NO ceiling at all - a single real but
# slightly out-of-order timestamp anywhere in the song forces every
# LATER note forward by that same offset, compounding with no upper
# bound. Found live 2026-09-17 on real audio: Lord of the Lost - Beyond
# Beautiful's last note ended at beat 4865 (259.5s) on audio only 239.45s
# long - the monotonic clamp alone had pushed the whole tail of the song
# 20 real seconds past where the audio literally ends.
# --------------------------------------------------------------------------

check("clamp_beat_to_max: normal in-order note is unaffected",
      repair.clamp_beat_to_max(10, 5, None, None) == (10, 5, 15))
check("clamp_beat_to_max: the existing forward clamp still applies "
      "(an out-of-order note is pushed to start right after the "
      "previous note's end)",
      repair.clamp_beat_to_max(3, 5, 20, None) == (20, 5, 25))
# a note with no room left before the end of the audio is dropped (None):
# squeezing it onto the last beat made notes overlap (found 2026-09-28 in
# Die Krupps - Nazis auf Speed: three notes stacked on beat 7670, a line
# break between them) - notes after the audio's end are not sung anyway
check(f"clamp_beat_to_max: a note that would start at/after max_beat is "
      f"dropped (got {repair.clamp_beat_to_max(50, 10, None, 30)!r})",
      repair.clamp_beat_to_max(50, 10, None, 30) is None)
check(f"clamp_beat_to_max: a cascading prev_end_beat already at/after "
      f"max_beat drops the note instead of overlapping "
      f"(got {repair.clamp_beat_to_max(5, 5, 200, 30)!r})",
      repair.clamp_beat_to_max(5, 5, 200, 30) is None)
check("clamp_beat_to_max: a note running over max_beat is shortened to end at it",
      repair.clamp_beat_to_max(25, 10, None, 30) == (25, 5, 30))
check("clamp_beat_to_max: a pushed note that still fits keeps its order",
      repair.clamp_beat_to_max(20, 5, 27, 30) == (27, 3, 30))
check("clamp_beat_to_max: a note well within max_beat is untouched",
      repair.clamp_beat_to_max(10, 5, None, 100) == (10, 5, 15))

# --------------------------------------------------------------------------
# set_header_tags(): a header tag is replaced, or added when the song file
# has none (Mein Herz erkennt dich immer has no #GAP line: the gap repair
# reported -4332 ms, but the file kept no #GAP at all)
# --------------------------------------------------------------------------

check("set_header_tags: a missing tag is added at the end of the header",
      repair.set_header_tags(["#TITLE:a", "#BPM:130"], {"GAP": "1000"})
      == ["#TITLE:a", "#BPM:130", "#GAP:1000"])
check("set_header_tags: an existing tag is replaced in place",
      repair.set_header_tags(["#TITLE:a", "#GAP:5", "#BPM:130"], {"GAP": "1000"})
      == ["#TITLE:a", "#GAP:1000", "#BPM:130"])
check("set_header_tags: tag names match without regard to case",
      repair.set_header_tags(["#gap:5"], {"GAP": "7"}) == ["#GAP:7"])
check("set_header_tags: several tags, others untouched",
      repair.set_header_tags(["#TITLE:a", "#MP3:old.mp3"], {"MP3": "new.mp3", "VIDEO": "new.mp4"})
      == ["#TITLE:a", "#MP3:new.mp3", "#VIDEO:new.mp4"])
check("set_header_tags: a tag that only starts like another is not touched",
      repair.set_header_tags(["#GAPLESS:1"], {"GAP": "2"}) == ["#GAPLESS:1", "#GAP:2"])


def _song_without_gap():
    root = tempfile.mkdtemp(prefix="nogap-")
    song_dir = os.path.join(root, "A - B")
    os.makedirs(song_dir)
    with open(os.path.join(song_dir, "A - B.txt"), "w", encoding="utf-8") as f:
        f.write("#TITLE:B\n#ARTIST:A\n#MP3:a.mp3\n#BPM:300\n: 0 4 5 La\n- 6\n: 8 4 5 la\nE\n")
    with open(os.path.join(song_dir, "a.mp3"), "wb") as f:
        f.write(b"x")
    return song_dir, tempfile.mkdtemp(prefix="nogap-out-")


_ng_dir, _ng_out = _song_without_gap()
_ng_path = repair.write_repaired(repair.Txt(os.path.join(_ng_dir, "A - B.txt")), _ng_dir, _ng_out,
                                 gap_ms=1234, note_times=None, pitches=None)
_ng_text = open(_ng_path, encoding="utf-8").read()
check(f"write_repaired: a song without #GAP gets the new #GAP line (got {_ng_text!r})",
      "#GAP:1234\n" in _ng_text and _ng_text.index("#GAP:1234") < _ng_text.index(": 0 4 5 La"))

# --------------------------------------------------------------------------
# a repaired song gets the same format version as a new song
# (song_header.py; the header rules themselves are tested in
# test_song_header.py)
# --------------------------------------------------------------------------

from Settings import Settings  # noqa: E402

_v = repair.FORMAT_VERSION
check(f"FORMAT_VERSION is the version UltraSinger writes into new songs "
      f"(got {_v!r}, Settings has {Settings.format_version.value!r})",
      _v == Settings.format_version.value)

_ng_version = _ng_text.splitlines()
check(f"write_repaired: the repaired song is versioned (got {_ng_version[:5]!r})",
      _ng_version[0] == f"#VERSION:{_v}" and "#AUDIO:a.mp3" in _ng_version)

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
# decide_gap(): the gap check accepts a new start only when the per-word
# values agree (spread between 10th and 90th percentile <= GAP_MAX_SPREAD_S)
# --------------------------------------------------------------------------

check("GAP_MAX_SPREAD_S defaults to 0.3 s", repair.GAP_MAX_SPREAD_S == 0.3)
check("gap_spread: 90th minus 10th percentile, linear interpolation",
      abs(repair.gap_spread([10.0, 10.1, 10.2, 10.3, 10.4]) - 0.32) < 1e-9)

agreeing = [32.00, 32.05, 31.98, 32.10, 32.02, 31.95, 32.04]
d = repair.decide_gap(agreeing, old_gap_ms=36042)
check(f"decide_gap: agreeing values -> their median becomes the new #GAP (got {d})",
      d["gap_ms"] == 32020 and d["kept"] is False and d["spread_s"] <= 0.3)

spread = [31.9, 36.0, 38.5, 33.2, 39.0, 35.1, 32.4]
d = repair.decide_gap(spread, old_gap_ms=31920)
check(f"decide_gap: disagreeing values -> the original #GAP is kept (got {d})",
      d["gap_ms"] == 31920 and d["kept"] is True and d["spread_s"] > 0.3)

d = repair.decide_gap([12.0], old_gap_ms=11500)
check(f"decide_gap: a single value cannot show agreement -> original kept (got {d})",
      d["gap_ms"] == 11500 and d["kept"] is True)

d = repair.decide_gap(agreeing, old_gap_ms=36042, max_spread=0.01)
check("decide_gap: the threshold is a parameter", d["kept"] is True)

# --------------------------------------------------------------------------
# repair_txt(mode="gap"): aligns on the separated vocals, writes the original
# #GAP when the values disagree and reports the decision
# --------------------------------------------------------------------------


class _GapTxt:
    path = "/song/song.txt"
    gap_ms = 31920
    notes = []

    def lyric_lines(self):
        return []


_gap_calls = {}
_gap_saved = {name: getattr(repair, name) for name in (
    "locate_audio", "build_alignment_units", "prepare_processing_audio",
    "load_audio_16k", "resolve_language", "load_aligner",
    "progressively_align", "implied_gap_samples", "write_repaired",
    "group_words")}
try:
    repair.locate_audio = lambda song_dir, txt, work_dir: ("/song/mix.mp3", None)
    repair.build_alignment_units = lambda txt, lines: [{"notes": []}, {"notes": []}]

    def _fake_prepare(audio_path, work_dir, device):
        _gap_calls["separated"] = audio_path
        return "/work/vocals.wav", "/work/mute.wav"
    repair.prepare_processing_audio = _fake_prepare

    def _fake_load(path):
        _gap_calls["aligned_on"] = path
        return [0.0] * 16000 * 60
    repair.load_audio_16k = _fake_load
    repair.resolve_language = lambda *a, **k: "en"
    repair.load_aligner = lambda lang, device: (None, None, lang)
    repair.progressively_align = lambda *a, **k: [{"words": [1]}, {"words": [1]}]
    repair.group_words = lambda notes: []

    def _fake_write(txt, song_dir, out_dir, gap_ms, note_times, pitches):
        _gap_calls["written_gap_ms"] = gap_ms
        return "/out/song.txt"
    repair.write_repaired = _fake_write

    repair.implied_gap_samples = lambda txt, units, aligned: list(spread)
    _gap_dir = tempfile.mkdtemp()
    result = repair.repair_txt(_GapTxt(), "/song", "/out", "gap", "cpu", _gap_dir)
    check(f"repair_txt gap: the vocals are separated from the song audio "
          f"(got {_gap_calls.get('separated')})",
          _gap_calls.get("separated") == "/song/mix.mp3")
    check(f"repair_txt gap: alignment runs on the separated vocals "
          f"(got {_gap_calls.get('aligned_on')})",
          _gap_calls.get("aligned_on") == "/work/vocals.wav")
    check(f"repair_txt gap: disagreeing values -> the original #GAP is written "
          f"(got {_gap_calls.get('written_gap_ms')})",
          _gap_calls.get("written_gap_ms") == 31920)
    check(f"repair_txt gap: the result says the start was kept and why (got {result})",
          result["gap_new_ms"] == 31920 and result["gap_kept"] is True
          and result["gap_spread_s"] > 0.3)

    repair.implied_gap_samples = lambda txt, units, aligned: list(agreeing)
    result = repair.repair_txt(_GapTxt(), "/song", "/out", "gap", "cpu", _gap_dir)
    check(f"repair_txt gap: agreeing values -> the new #GAP is written (got {result})",
          _gap_calls.get("written_gap_ms") == 32020 and result["gap_kept"] is False)
finally:
    for name, func in _gap_saved.items():
        setattr(repair, name, func)

# --------------------------------------------------------------------------
# other_media(): the other recordings in a song folder, one entry per file
# name without extension (Sieben: the txt points to the live video, the
# studio mp3/mp4 next to it fits the notes; Mein Herz: a second video only)
# --------------------------------------------------------------------------


class _MediaTxt:
    def __init__(self, audio_ref, video_ref):
        self.audio_ref, self.video_ref = audio_ref, video_ref


_md = tempfile.mkdtemp(prefix="media-")
for _f in ("live.mp3", "live.mp4", "studio [x].mp3", "studio [x].mp4", "cover [CO].jpg",
           "song.txt", "x.usdb"):
    open(os.path.join(_md, _f), "w").close()
check("other_media: the other recording with its audio and video, the referenced one left out",
      repair.other_media(_md, _MediaTxt("live.mp3", "live.mp4"), os.path.join(_md, "live.mp3"))
      == [{"stem": "studio [x]", "audio": os.path.join(_md, "studio [x].mp3"),
           "video": os.path.join(_md, "studio [x].mp4")}])
_md2 = tempfile.mkdtemp(prefix="media2-")
for _f in ("ASP - Mein Herz.mp3", "ASP - Mein Herz.mp4", "ASP Mein Herz.mp4"):
    open(os.path.join(_md2, _f), "w").close()
check("other_media: a recording with a video only",
      repair.other_media(_md2, _MediaTxt("ASP - Mein Herz.mp3", "ASP - Mein Herz.mp4"),
                         os.path.join(_md2, "ASP - Mein Herz.mp3"))
      == [{"stem": "ASP Mein Herz", "audio": None, "video": os.path.join(_md2, "ASP Mein Herz.mp4")}])
check("other_media: nothing else in the folder",
      repair.other_media(_md2, _MediaTxt("ASP - Mein Herz.mp3", None),
                         os.path.join(_md2, "ASP - Mein Herz.mp3"))[0]["stem"] == "ASP Mein Herz"
      and len(repair.other_media(_md2, _MediaTxt("ASP - Mein Herz.mp3", "ASP Mein Herz.mp4"),
                                 os.path.join(_md2, "ASP - Mein Herz.mp3"))) == 0)

# --------------------------------------------------------------------------
# recording_fit(): does a recording fit the song file? Mean alignment score
# of the first lines and the largest share of first-word values inside one
# RECORDING_FIT_WINDOW_S window (bench20_shift.py/an20.py: score >= 0.6 and
# share >= 0.4 finds 70% of fitting recordings, 6% of wrong ones)
# --------------------------------------------------------------------------

_fit = repair.recording_fit([{"score": 0.7}, {"score": 0.5}, {"score": None}],
                            [13.0, 13.1, 13.2, 10.0, 11.0])
check(f"recording_fit: mean score of the aligned lines, share in one window (got {_fit})",
      abs(_fit["score"] - 0.6) < 1e-9 and abs(_fit["share"] - 0.6) < 1e-9 and _fit["n"] == 5)
check("recording_fit: nothing aligned",
      repair.recording_fit([{"score": None}], []) == {"score": 0.0, "share": 0.0, "n": 0})
check("recording_seems_to_fit: score and share above the limits",
      repair.recording_seems_to_fit({"score": 0.66, "share": 0.5, "n": 20}))
check("recording_seems_to_fit: a low score does not fit",
      not repair.recording_seems_to_fit({"score": 0.55, "share": 0.9, "n": 20}))
check("recording_seems_to_fit: scattered values do not fit",
      not repair.recording_seems_to_fit({"score": 0.8, "share": 0.3, "n": 20}))
check("recording_seems_to_fit: fewer than three values do not fit",
      not repair.recording_seems_to_fit({"score": 0.8, "share": 1.0, "n": 2}))

# --------------------------------------------------------------------------
# repair_txt(mode="gap"): no word of the first lines aligns near #GAP -> the
# repair fails with the reason; the other recordings of the folder are
# checked and the one that seems to fit is named - #MP3/#VIDEO are never
# changed automatically (user decision 2026-10-06)
# --------------------------------------------------------------------------

_mm = {}
_mm_saved = {name: getattr(repair, name) for name in (
    "locate_audio", "build_alignment_units", "prepare_processing_audio",
    "load_audio_16k", "resolve_language", "load_aligner", "progressively_align",
    "implied_gap_samples", "write_repaired", "group_words", "other_media",
    "audio_from_video", "global_timing_correction")}


def _mm_run(recordings):
    import contextlib
    import io
    repair.other_media = lambda song_dir, txt, current: recordings
    _mm.pop("written", None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = repair.repair_txt(_GapTxt(), "/song", "/out", "gap", "cpu", tempfile.mkdtemp())
    return res, buf.getvalue()


try:
    # the whole song does not fit the file either (no consistent shift)
    repair.global_timing_correction = lambda *a, **k: None
    repair.locate_audio = lambda song_dir, txt, work_dir: ("/song/live.mp3", None)
    repair.build_alignment_units = lambda txt, lines: [{"notes": []}, {"notes": []}]
    repair.prepare_processing_audio = lambda audio, work, device: (audio + ".vocals.wav", audio + ".mute.wav")

    def _mm_load(path):
        _mm["loaded"] = path
        return [0.0] * 16000 * 60
    repair.load_audio_16k = _mm_load
    repair.resolve_language = lambda *a, **k: "de"
    repair.load_aligner = lambda lang, device: (None, None, lang)
    # the studio recording aligns well, everything else badly
    repair.progressively_align = lambda *a, **k: [
        {"words": [1], "score": 0.7 if "studio" in _mm["loaded"] else 0.3}] * 2
    repair.group_words = lambda notes: []
    repair.implied_gap_samples = lambda txt, units, aligned: (
        [] if "live" in _mm["loaded"] else list(agreeing) if "studio" in _mm["loaded"] else list(spread))
    repair.write_repaired = lambda *a, **k: _mm.update(written=True) or "/out/song.txt"
    repair.audio_from_video = lambda video, work_dir: ("/work/" + os.path.basename(video) + ".m4a", None)

    _res, _log = _mm_run([{"stem": "other", "audio": "/song/other.mp3", "video": None},
                          {"stem": "studio", "audio": "/song/studio.mp3", "video": "/song/studio.mp4"}])
    check(f"repair_txt gap: no word near #GAP -> the repair fails, nothing is written (got {_res})",
          _res is None and "written" not in _mm)
    check(f"repair_txt gap: the reason names the recording that seems to fit (got {_log!r})",
          "audio does not match the song file" in _log and "live.mp3" in _log
          and "studio.mp3" in _log and "seems to fit" in _log and "by hand" in _log)

    _res, _log = _mm_run([{"stem": "studio", "audio": None, "video": "/song/studio.mp4"}])
    check(f"repair_txt gap: a video-only recording is checked through its sound and named "
          f"by the video file (got {_log!r})",
          _res is None and "studio.mp4 seems to fit" in _log)

    _res, _log = _mm_run([{"stem": "other", "audio": "/song/other.mp3", "video": None}])
    check(f"repair_txt gap: no other recording fits -> said so (got {_log!r})",
          _res is None and "none of the other recordings in the folder fits" in _log
          and "other.mp3" in _log)

    _res, _log = _mm_run([])
    check(f"repair_txt gap: no other recording in the folder (got {_log!r})",
          _res is None and "no other recording in the folder" in _log)
finally:
    for name, func in _mm_saved.items():
        setattr(repair, name, func)


# --------------------------------------------------------------------------
# whole-song timing correction (timing_fit.py): gap mode uses it when no word
# of the first lines is found near #GAP; sync mode corrects #GAP/#BPM first
# when the song is clearly off, so its line windows start in the right place
# --------------------------------------------------------------------------

_tc = {}
_tc_saved = {name: getattr(repair, name) for name in (
    "locate_audio", "build_alignment_units", "prepare_processing_audio",
    "load_audio_16k", "resolve_language", "load_aligner", "progressively_align",
    "implied_gap_samples", "write_repaired", "group_words", "other_media",
    "global_timing_correction", "reanchor_outlier_units", "rescue_dropped_units",
    "refine_first_units", "detect_lyrics_mismatch")}


class _TcTxt:
    path = "/song/song.txt"
    notes = []

    def __init__(self):
        self.gap_ms, self.bpm_tag = 31920.0, 300.0

    def lyric_lines(self):
        return []

    def beat_to_sec(self, beat):
        return 0.0

    @property
    def gap_s(self):
        return self.gap_ms / 1000.0


try:
    repair.locate_audio = lambda song_dir, txt, work_dir: ("/song/mix.mp3", None)
    repair.build_alignment_units = lambda txt, lines: [{"notes": []}, {"notes": []}]
    repair.prepare_processing_audio = lambda audio, work, device: ("/w/vocals.wav", "/w/mute.wav")
    repair.load_audio_16k = lambda path: [0.0] * 16000 * 60
    repair.resolve_language = lambda *a, **k: "de"
    repair.load_aligner = lambda lang, device: (None, None, lang)
    repair.group_words = lambda notes: []
    repair.other_media = lambda song_dir, txt, current: []

    def _tc_write(txt, song_dir, out_dir, gap_ms, note_times, pitches):
        _tc["written"] = (gap_ms, txt.bpm_tag)
        return "/out/song.txt"
    repair.write_repaired = _tc_write

    def _tc_corr(txt, model, meta, audio16k, device, gap_only):
        _tc["gap_only"] = gap_only
        return _tc["corr"]
    repair.global_timing_correction = _tc_corr

    # gap mode: nothing near #GAP, but the whole song is shifted by -22.5 s
    repair.progressively_align = lambda *a, **k: [{"words": [None]}] * 2
    repair.implied_gap_samples = lambda txt, units, aligned: []
    _tc["corr"] = {"gap_ms": 9420.4, "bpm": 300.0, "offset_s": -22.5, "drift_s": 0.1, "agree": 0.95}
    _res = repair.repair_txt(_TcTxt(), "/song", "/out", "gap", "cpu", tempfile.mkdtemp())
    check(f"gap: no word near #GAP -> the whole-song fit gives the new #GAP (got {_res}, {_tc})",
          _res is not None and _tc.get("written") == (9420, 300.0) and _tc["gap_only"] is True
          and _res["gap_new_ms"] == 9420 and _res["gap_kept"] is False
          and "whole song" in _res.get("note", ""))

    # gap mode: words found near #GAP but in the wrong place (they disagree)
    # -> the whole-song fit wins (Maerchen schreibt die Zeit: 8 of 19 words
    # "found" near the old #GAP, the song is 21 s off)
    repair.progressively_align = lambda *a, **k: [{"words": [1]}] * 2
    repair.implied_gap_samples = lambda txt, units, aligned: [30.0, 35.5, 41.0]
    _tc["corr"] = {"gap_ms": 11230.0, "bpm": 300.0, "offset_s": -20.69, "drift_s": 0.0, "agree": 1.0}
    _res = repair.repair_txt(_TcTxt(), "/song", "/out", "gap", "cpu", tempfile.mkdtemp())
    check(f"gap: disagreeing words near #GAP -> the whole-song #GAP (got {_res})",
          _res["gap_new_ms"] == 11230 and _res["gap_kept"] is False and "whole song" in _res["note"])
    # agreeing words near #GAP, whole song within 0.3 s -> the opening's (finer) value
    repair.implied_gap_samples = lambda txt, units, aligned: [32.02, 32.03, 32.01]
    _tc["corr"] = {"gap_ms": 32200.0, "bpm": 300.0, "offset_s": 0.28, "drift_s": 0.0, "agree": 1.0}
    _res = repair.repair_txt(_TcTxt(), "/song", "/out", "gap", "cpu", tempfile.mkdtemp())
    check(f"gap: opening and whole song agree -> the opening's #GAP (got {_res})",
          _res["gap_new_ms"] == 32020 and "note" not in _res)
    # agreeing words near #GAP but the whole song says otherwise -> whole song
    _tc["corr"] = {"gap_ms": 27000.0, "bpm": 300.0, "offset_s": -4.92, "drift_s": 0.0, "agree": 0.9}
    _res = repair.repair_txt(_TcTxt(), "/song", "/out", "gap", "cpu", tempfile.mkdtemp())
    check(f"gap: opening and whole song disagree -> the whole song's #GAP (got {_res})",
          _res["gap_new_ms"] == 27000 and "whole song" in _res["note"])
    # no consistent whole-song fit -> the opening's decision as before
    _tc["corr"] = None
    _res = repair.repair_txt(_TcTxt(), "/song", "/out", "gap", "cpu", tempfile.mkdtemp())
    check(f"gap: no whole-song fit -> the opening's #GAP (got {_res})", _res["gap_new_ms"] == 32020)

    # sync mode: clearly off (+5 s, drifting 1.2 s) -> #GAP/#BPM corrected before the line search
    def _tc_align(txt, units, model, meta, audio16k, audio_dur):
        _tc["seen_by_search"] = (txt.gap_ms, txt.bpm_tag)
        return [{"words": [], "score": 0.9}] * 2
    repair.progressively_align = _tc_align
    repair.reanchor_outlier_units = lambda *a, **k: None
    repair.rescue_dropped_units = lambda *a, **k: None
    repair.refine_first_units = lambda *a, **k: None
    repair.detect_lyrics_mismatch = lambda *a, **k: (True, 1.0, "test")
    _saved_fb = os.environ.get("REPAIR_FALLBACK")
    os.environ["REPAIR_FALLBACK"] = "0"
    _tc["corr"] = {"gap_ms": 36920.0, "bpm": 297.0, "offset_s": 5.0, "drift_s": 1.2, "agree": 0.9}
    repair.repair_txt(_TcTxt(), "/song", "/out", "sync", "cpu", tempfile.mkdtemp())
    check(f"sync: a clearly shifted song is corrected before the line search (got {_tc.get('seen_by_search')})",
          _tc.get("seen_by_search") == (36920.0, 297.0) and _tc["gap_only"] is False)
    _tc["corr"] = {"gap_ms": 32020.0, "bpm": 300.0, "offset_s": 0.1, "drift_s": 0.05, "agree": 0.9}
    repair.repair_txt(_TcTxt(), "/song", "/out", "sync", "cpu", tempfile.mkdtemp())
    check(f"sync: a small shift is left to the line search (got {_tc.get('seen_by_search')})",
          _tc.get("seen_by_search") == (31920.0, 300.0))
    if _saved_fb is None:
        os.environ.pop("REPAIR_FALLBACK", None)
    else:
        os.environ["REPAIR_FALLBACK"] = _saved_fb
finally:
    for name, func in _tc_saved.items():
        setattr(repair, name, func)

# write_repaired(): a changed #BPM is written, an unchanged one stays as it is
_bpm_dir = tempfile.mkdtemp()
_bpm_song = os.path.join(_bpm_dir, "Song")
os.makedirs(_bpm_song)
_bpm_txt = os.path.join(_bpm_song, "song.txt")
with open(_bpm_txt, "w", encoding="utf-8") as _f:
    _f.write("#TITLE:x\n#ARTIST:y\n#BPM:300\n#GAP:1000\n: 0 4 0 la\nE\n")
_t = repair.Txt(_bpm_txt)
_out = repair.write_repaired(_t, _bpm_song, os.path.join(_bpm_dir, "out"), 1000, None, None)
check("write_repaired: an unchanged #BPM stays as written",
      "#BPM:300\n" in open(_out, encoding="utf-8").read())
_t.bpm_tag = 297.123456
_out = repair.write_repaired(_t, _bpm_song, os.path.join(_bpm_dir, "out2"), 1000, None, None)
check(f"write_repaired: a corrected #BPM is written",
      "#BPM:297.1235\n" in open(_out, encoding="utf-8").read())

# global_line_points(): one (file time, sung time) point per lyric line, from
# the first word of the line the whole-song alignment placed
_glp_dir = tempfile.mkdtemp()
_glp_path = os.path.join(_glp_dir, "song.txt")
with open(_glp_path, "w", encoding="utf-8") as _f:
    _f.write("#TITLE:x\n#ARTIST:y\n#BPM:150\n#GAP:2000\n"
             ": 0 4 0 eins \n: 4 4 0 zwei \n- 10\n: 10 4 0 drei \n: 14 4 0 vier \nE\n")
_glp = {}
_glp_saved = (repair.ctc_align.emissions_for_audio, repair.ctc_align.align_words_globally)
try:
    repair.ctc_align.emissions_for_audio = lambda *a, **k: "EM"

    def _glp_align(words, em, meta, end_penalty=None, **kw):
        _glp["words"], _glp["kw"] = words, kw
        return [None, (7.0, 7.3, 0.9), (9.5, 9.8, 0.9), (10.0, 10.2, 0.9)]
    repair.ctc_align.align_words_globally = _glp_align
    _pts = repair.global_line_points(repair.Txt(_glp_path), None, {}, [0.0] * 16000, "cpu")
finally:
    repair.ctc_align.emissions_for_audio, repair.ctc_align.align_words_globally = _glp_saved
check(f"global_line_points: the file's own words, line by line (got {_glp.get('words')}, {_glp.get('kw', {}).get('line_lengths')})",
      _glp.get("words") == ["eins", "zwei", "drei", "vier"] and _glp["kw"].get("line_lengths") == [2, 2])
check(f"global_line_points: first placed word per line against its file time (got {_pts})",
      _pts == [(2.4, 7.0), (3.0, 9.5)])

def _gap_main_output(result, extra_argv=(), all_lines=False):
    import contextlib
    import io
    saved = {n: getattr(repair, n) for n in ("find_ultrastar_txts", "repair_txt")}
    saved_argv = sys.argv
    buffer = io.StringIO()
    try:
        repair.find_ultrastar_txts = lambda song_dir: ["TXT"]
        repair.repair_txt = lambda *a, **k: dict(result)
        sys.argv = ["repair.py", "--song-dir", "/s", "--out", "/o", "--mode", "gap",
                    *extra_argv]
        with contextlib.redirect_stdout(buffer):
            try:
                repair.main()
            except SystemExit:
                pass
    finally:
        sys.argv = saved_argv
        for n, f in saved.items():
            setattr(repair, n, f)
    lines = buffer.getvalue().splitlines()
    return lines if all_lines else [l for l in lines if l.startswith("REPAIR_DONE")]


_gap_base = {"output": "/o/x.txt", "gap_old_ms": 31920, "gap_new_ms": 31920,
             "aligned_words": 12, "total_words": 14, "repitched": 0, "mode": "gap"}
_done = _gap_main_output(dict(_gap_base, gap_kept=True, gap_spread_s=6.5))
check(f"main gap: REPAIR_DONE says the start was kept, with the spread (got {_done})",
      len(_done) == 1 and "gap_check=kept" in _done[0] and "gap_spread_s=6.50" in _done[0])
_done = _gap_main_output(dict(_gap_base, gap_new_ms=32020, gap_kept=False, gap_spread_s=0.1))
check(f"main gap: REPAIR_DONE says the start was moved (got {_done})",
      len(_done) == 1 and "gap_check=moved" in _done[0] and "gap_new_ms=32020" in _done[0])
_done = _gap_main_output(dict(_gap_base, gap_kept=True, gap_spread_s=None))
check(f"main gap: too few words -> gap_spread_s=none (got {_done})",
      len(_done) == 1 and "gap_spread_s=none" in _done[0])
_done = _gap_main_output(dict(_gap_base, mode="sync"))
check(f"main sync: no gap check fields (got {_done})",
      len(_done) == 1 and "gap_check" not in _done[0])


# --traits: the song's traits from the song list (orchestrator/song_style.py)
check("parse_traits_arg: comma-joined, blank parts dropped",
      repair.parse_traits_arg("choir, harsh_vocals,,") == ["choir", "harsh_vocals"])
check("parse_traits_arg: nothing given", repair.parse_traits_arg(None) == [])
_out = _gap_main_output(_gap_base, ("--traits", "choir,harsh_vocals"), all_lines=True)
check(f"main: --traits is accepted and the traits are logged (got {_out})",
      any("song traits: choir, harsh_vocals" in l for l in _out)
      and any(l.startswith("REPAIR_DONE") for l in _out))
_out = _gap_main_output(_gap_base, (), all_lines=True)
check("main: no traits line without --traits",
      not any("song traits" in l for l in _out))


def _lyrics_main_kwargs(extra_argv=()):
    """kwargs main() hands repair_txt_with_lyrics() in lyrics mode."""
    import contextlib
    import io
    names = ("find_ultrastar_txts", "load_lyrics_file", "parse_lyrics_lines",
             "repair_txt_with_lyrics")
    saved = {n: getattr(repair, n) for n in names}
    saved_argv = sys.argv
    seen = {}
    try:
        repair.find_ultrastar_txts = lambda song_dir: ["TXT"]
        repair.load_lyrics_file = lambda path: ("genius", [])
        repair.parse_lyrics_lines = lambda data: ["UNIT"]
        repair.repair_txt_with_lyrics = lambda *a, **k: seen.update(k) or {
            "output": "/o/x.txt", "aligned_words": 1, "total_words": 1}
        sys.argv = ["repair.py", "--song-dir", "/s", "--out", "/o", "--mode", "lyrics",
                    "--lyrics-file", "/l.json", *extra_argv]
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                repair.main()
            except SystemExit:
                pass
    finally:
        sys.argv = saved_argv
        for n, f in saved.items():
            setattr(repair, n, f)
    return seen


check("main lyrics: --traits reach repair_txt_with_lyrics",
      _lyrics_main_kwargs(("--traits", "loud_high_instruments")).get("traits")
      == ["loud_high_instruments"])
check("main lyrics: no traits -> empty list",
      _lyrics_main_kwargs().get("traits") == [])

# --------------------------------------------------------------------------

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
