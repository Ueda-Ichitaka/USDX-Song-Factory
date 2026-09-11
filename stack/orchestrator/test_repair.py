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

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
