#!/usr/bin/env python3
"""Tests for repair.py's "lyrics" mode (force-align trusted external
lyrics to audio, replacing the existing/transcribed lyrics text).

About me: plain assert-based checks for the pure/mockable logic (file
parsing, line-to-window seeding, hyphenation-based syllable building,
output writing) - the actual whisperx alignment calls are stubbed out, so
this does NOT validate real alignment quality (see test_repair.py for
that style of check on the sibling sync-mode pipeline). Needs the heavy
deps repair.py imports - run inside the image:

    docker compose run --rm ultrasinger python /app/orchestrator/test_lyrics_mode.py
"""

import json
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
    """gap=0, real_bpm=60 -> beat_to_sec(beat) == beat."""

    real_bpm = 60.0

    def note_start_sec(self, note):
        return note.beat

    def note_end_sec(self, note):
        return note.beat + note.dur


def make_notes(count, start=0, dur=1.0):
    return [repair.Note(":", float(start + i), dur, 0, f"w{i} ")
            for i in range(count)]


def make_scaffold_lines(notes, per_line):
    return [{"notes": notes[i:i + per_line]}
            for i in range(0, len(notes), per_line)]


# --------------------------------------------------------------------------
# load_lyrics_file: JSON (lyrics_fetch.py output) vs plain text
# --------------------------------------------------------------------------

tmp_json = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
json.dump({"source": "genius", "lines": [
    {"text": "First line", "start": 1.0},
    {"text": "Second line", "start": 4.0},
]}, tmp_json)
tmp_json.close()
source, lines = repair.load_lyrics_file(tmp_json.name)
check("load_lyrics_file reads the JSON source label", source == "genius")
check("load_lyrics_file reads JSON lines with timing",
      lines == [{"text": "First line", "start": 1.0},
                {"text": "Second line", "start": 4.0}])
os.unlink(tmp_json.name)

tmp_plain = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
tmp_plain.write("First line\n\nSecond line\n   \nThird line\n")
tmp_plain.close()
source2, lines2 = repair.load_lyrics_file(tmp_plain.name)
check("load_lyrics_file falls back to plain text for a non-JSON file",
      source2 == "lyrics-file")
check("load_lyrics_file plain lines skip blanks, carry no timing",
      lines2 == [{"text": "First line", "start": None},
                 {"text": "Second line", "start": None},
                 {"text": "Third line", "start": None}])
os.unlink(tmp_plain.name)

# --------------------------------------------------------------------------
# parse_lyrics_lines
# --------------------------------------------------------------------------

units = repair.parse_lyrics_lines([
    {"text": "  Hello world  ", "start": 1.5},
    {"text": "   ", "start": 2.0},  # blank -> dropped
    {"text": "Second line here", "start": None},
])
check("parse_lyrics_lines drops blank lines", len(units) == 2)
check("parse_lyrics_lines splits words", units[0]["words"] == ["Hello", "world"])
check("parse_lyrics_lines keeps start time", units[0]["start"] == 1.5)
check("parse_lyrics_lines keeps None start", units[1]["start"] is None)

# --------------------------------------------------------------------------
# seed_lyric_windows: three priority tiers
# --------------------------------------------------------------------------

txt = FakeTxt()

# tier 1: every unit has its own start time (synced lyrics) -> used directly
units_timed = repair.parse_lyrics_lines([
    {"text": "one two", "start": 5.0},
    {"text": "three four five", "start": 10.0},
])
repair.seed_lyric_windows(units_timed, [], txt, audio_dur=60.0)
check("seed_lyric_windows (timed): uses the real start directly",
      units_timed[0]["seed_start"] == 5.0)
check("seed_lyric_windows (timed): end = next line's start",
      units_timed[0]["seed_end"] == 10.0)
check("seed_lyric_windows (timed): last line gets a default span",
      units_timed[1]["seed_end"] == min(60.0, 10.0 + 8.0))

# tier 2: no timing, but scaffold line count matches -> map 1:1
notes = make_notes(6, start=0, dur=1.0)  # beats 0..5, note i spans [i, i+1]
scaffold_lines = make_scaffold_lines(notes, per_line=3)  # 2 scaffold lines
units_untimed = repair.parse_lyrics_lines([
    {"text": "a b c", "start": None},
    {"text": "d e f", "start": None},
])
repair.seed_lyric_windows(units_untimed, scaffold_lines, txt, audio_dur=60.0)
check("seed_lyric_windows (matched count): unit 0 uses scaffold line 0's span",
      units_untimed[0]["seed_start"] == 0.0 and units_untimed[0]["seed_end"] == 3.0)
check("seed_lyric_windows (matched count): unit 1 uses scaffold line 1's span",
      units_untimed[1]["seed_start"] == 3.0 and units_untimed[1]["seed_end"] == 6.0)

# tier 3: no timing, MISMATCHED line count (3 lyric lines vs. 2 scaffold
# lines) -> proportional by word count instead of 1:1 mapping
units_mismatched = repair.parse_lyrics_lines([
    {"text": "one word line", "start": None},
    {"text": "a much longer line with six words", "start": None},
    {"text": "final", "start": None},
])
repair.seed_lyric_windows(units_mismatched, scaffold_lines, txt, audio_dur=60.0)
total_span = 6.0  # scaffold spans [0, 6]
w0 = len(units_mismatched[0]["words"])  # 3
w1 = len(units_mismatched[1]["words"])  # 7
w2 = len(units_mismatched[2]["words"])  # 1
check("seed_lyric_windows (proportional): unit 0 starts at scaffold start",
      units_mismatched[0]["seed_start"] == 0.0)
check(f"seed_lyric_windows (proportional): span split by word count "
      f"(got {units_mismatched[0]['seed_end']})",
      abs(units_mismatched[0]["seed_end"] - total_span * w0 / (w0 + w1 + w2)) < 1e-6)

# no scaffold at all -> spread across the whole audio
units_no_scaffold = repair.parse_lyrics_lines([
    {"text": "one two", "start": None},
    {"text": "three four", "start": None},
])
repair.seed_lyric_windows(units_no_scaffold, [], txt, audio_dur=20.0)
check("seed_lyric_windows (no scaffold): spreads across the whole audio",
      units_no_scaffold[0]["seed_start"] == 0.0 and
      units_no_scaffold[-1]["seed_end"] <= 20.0)

# --------------------------------------------------------------------------
# build_syllables_from_lyric_units: hyphenation reuse + line grouping
# --------------------------------------------------------------------------

units_for_syl = repair.parse_lyrics_lines([
    {"text": "hello world", "start": 0.0},
    {"text": "goodbye", "start": 4.0},
])
# fully-aligned words (no interpolation needed), one per unit's word list
aligned_for_syl = [
    {"words": [{"start": 0.0, "end": 1.0}, {"start": 1.0, "end": 2.0}],
     "word_orig_starts": [0.0, 1.0], "word_orig_ends": [1.0, 2.0]},
    {"words": [{"start": 4.0, "end": 5.0}],
     "word_orig_starts": [4.0], "word_orig_ends": [5.0]},
]
# language=None -> no hyphenation, each whole word is its own "syllable"
per_unit = repair.build_syllables_from_lyric_units(
    units_for_syl, aligned_for_syl, language=None)
check("build_syllables_from_lyric_units returns one list per unit",
      len(per_unit) == 2)
check("build_syllables_from_lyric_units (no hyphenation): unit 0 has 2 words",
      len(per_unit[0]) == 2)
check("build_syllables_from_lyric_units (no hyphenation): unit 1 has 1 word",
      len(per_unit[1]) == 1)
check("build_syllables_from_lyric_units keeps real aligned timing",
      per_unit[0][0][1] == 0.0 and per_unit[0][0][2] == 1.0)
check("build_syllables_from_lyric_units marks word end with a trailing space",
      per_unit[0][0][0] == "hello " and per_unit[1][0][0] == "goodbye ")

# --------------------------------------------------------------------------
# split_long_lyric_units(): force a line break when a unit is too long
# (character cutoff) or has a long internal pause (silence gap) - never
# mid-word. Genius/lyrics-source line breaks alone (what build_syllables_
# from_lyric_units's units are based on) can produce a single "line" that
# is way too long on screen, or spans a real instrumental/breathing gap.
# --------------------------------------------------------------------------

short_unit = [("Hi ", 0.0, 0.5), ("there ", 0.5, 1.0)]
check("split_long_lyric_units leaves a short, gapless unit unchanged",
      repair.split_long_lyric_units([short_unit]) == [short_unit])

paused_unit = [("Hi ", 0.0, 0.5), ("there ", 4.0, 4.5)]
split_paused = repair.split_long_lyric_units([paused_unit], pause_s=2.5)
check(f"split_long_lyric_units splits on a long internal pause even "
      f"though the line is short (got {split_paused})",
      split_paused == [[("Hi ", 0.0, 0.5)], [("there ", 4.0, 4.5)]])

no_split_short_pause = repair.split_long_lyric_units([paused_unit], pause_s=5.0)
check("split_long_lyric_units does NOT split when the pause is under "
      "the configured threshold",
      no_split_short_pause == [paused_unit])

long_words = [(f"word{i} ", float(i), float(i) + 0.5) for i in range(20)]
split_long = repair.split_long_lyric_units([long_words], max_chars=30, pause_s=999)
check("split_long_lyric_units splits an oversized line into 2+ sub-lines",
      len(split_long) > 1)
check("split_long_lyric_units never exceeds max_chars on a sub-line it "
      "had the option to split earlier for",
      all(sum(len(w.strip()) for w, _, _ in sub) <= 30 for sub in split_long[:-1]))
rejoined = [w for sub in split_long for w in sub]
check("split_long_lyric_units never drops or reorders syllables",
      rejoined == long_words)

mid_word_syllables = [("won", 0.0, 0.3), ("der", 0.3, 0.6), ("ful ", 0.6, 0.9)]
no_mid_word_split = repair.split_long_lyric_units(
    [mid_word_syllables], max_chars=3, pause_s=999)
check("split_long_lyric_units never splits in the middle of a hyphenated "
      "word, even if that word alone exceeds max_chars",
      no_mid_word_split == [mid_word_syllables])

# --------------------------------------------------------------------------
# lyrics_txt_lines(): the persisted lyrics.txt content - reconstructed
# from the FINAL (post section-marker-stripping, post length/pause-
# splitting) per_unit_syllables, so an admin editing it sees the real,
# as-used line structure - not the raw fetched text.
# --------------------------------------------------------------------------

sample_units = [
    [("Hello ", 0.0, 0.5), ("world ", 0.5, 1.0)],
    [("Goodbye ", 2.0, 2.5)],
]
check("lyrics_txt_lines reconstructs one text line per unit",
      repair.lyrics_txt_lines(sample_units) == ["Hello world", "Goodbye"])
check("lyrics_txt_lines skips empty units without leaving a blank line",
      repair.lyrics_txt_lines([[], sample_units[0]]) == ["Hello world"])

# --------------------------------------------------------------------------
# run_alignment_with_retries(): alignment has real run-to-run variance
# (window/anchor choices) - a poor first attempt is often not the best a
# given audio/text pairing can actually do. Retries (bounded, so a
# persistently bad match doesn't loop forever) and always keeps whichever
# attempt scored best, stopping early once one is "good enough".
# --------------------------------------------------------------------------

bad_attempt = (["u"], ["a"], 2, 10)     # 20% aligned
good_attempt = (["u"], ["a"], 9, 10)    # 90% aligned
worse_attempt = (["u"], ["a"], 1, 10)   # 10% aligned

calls = []


def _attempts(sequence):
    it = iter(sequence)

    def _fn():
        result = next(it)
        calls.append(result)
        return result
    return _fn


calls.clear()
result_stops_early = repair.run_alignment_with_retries(
    _attempts([bad_attempt, good_attempt, worse_attempt]), max_attempts=3)
check("run_alignment_with_retries stops early once an attempt is good "
      "enough (>= 0.5 aligned) - does not run a 3rd attempt",
      len(calls) == 2)
check("run_alignment_with_retries returns the good attempt",
      result_stops_early == good_attempt)

calls.clear()
result_keeps_best = repair.run_alignment_with_retries(
    _attempts([bad_attempt, worse_attempt]), max_attempts=2)
check("run_alignment_with_retries runs up to max_attempts when nothing "
      "is ever \"good enough\"",
      len(calls) == 2)
check("run_alignment_with_retries keeps the BEST attempt seen, even "
      f"when none reached the threshold (got {result_keeps_best})",
      result_keeps_best == bad_attempt)

calls.clear()
result_single = repair.run_alignment_with_retries(
    _attempts([good_attempt, bad_attempt]), max_attempts=3)
check("run_alignment_with_retries never even tries a 2nd attempt when "
      "the 1st is already good enough",
      len(calls) == 1 and result_single == good_attempt)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
