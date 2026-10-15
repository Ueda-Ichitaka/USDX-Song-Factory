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
# a standalone dash or quote is punctuation, not a sung word - left as a word
# it became a note of its own (found 2026-09-28: 43 such notes in 15
# generated songs, e.g. "-" and "–" in Black Messiah - Wildsau)
_punct = repair.parse_lyrics_lines([
    {"text": "Hello - world \u2013 again", "start": None},
    {"text": '" \u2013 -', "start": None},
    {"text": "Rock'n'Roll 1-2-3 ~", "start": None},
])
check(f"parse_lyrics_lines drops words without a letter or digit (got {[u['words'] for u in _punct]})",
      _punct[0]["words"] == ["Hello", "world", "again"])
check("parse_lyrics_lines drops a line that is punctuation only",
      len(_punct) == 2)
check("parse_lyrics_lines keeps words that contain a letter or digit",
      _punct[1]["words"] == ["Rock'n'Roll", "1-2-3"])
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

# tier 3 with real silence_sections: the proportional-by-word-count
# distribution must skip a real pause within the scaffold span instead of
# treating it as usable time - found live 2026-09-16 from real manual
# USDX testing: most lyrics-mode jobs never have an exact scaffold-line-
# count match (genius's editorial line breaks rarely match whisper's
# own), so this tier-3 fallback is the DOMINANT seeding path for real
# songs, and its blindness to real silence was the true root cause behind
# "rescued lyric line via seed position" landing on badly wrong positions
# even after later rescue attempts (e.g. Korpiklaani - Rauta's "Iske!
# Iske!" chorus line).
units_silence_aware = repair.parse_lyrics_lines([
    {"text": "one", "start": None},
    {"text": "two", "start": None},
    {"text": "three", "start": None},
])
repair.seed_lyric_windows(units_silence_aware, scaffold_lines, txt,
                          audio_dur=60.0, silence_sections=[(2.0, 4.0)])
for u in units_silence_aware:
    check(f"seed_lyric_windows (silence-aware tier 3): no unit's seed "
          f"window overlaps the real silence [2.0, 4.0] (got "
          f"{u['seed_start']!r}-{u['seed_end']!r})",
          u["seed_end"] <= 2.0 + 1e-6 or u["seed_start"] >= 4.0 - 1e-6)
check("seed_lyric_windows (silence-aware tier 3): still starts at the "
      "scaffold start",
      units_silence_aware[0]["seed_start"] == 0.0)
check("seed_lyric_windows (silence-aware tier 3): still ends within the "
      "scaffold span",
      units_silence_aware[-1]["seed_end"] <= 6.0 + 1e-6)

# omitting silence_sections (the default) must behave exactly like
# before - a direct regression guard, reusing the exact same fixture as
# the pre-existing "tier 3" proportional-split check above
units_mismatched_nodefault = repair.parse_lyrics_lines([
    {"text": "one word line", "start": None},
    {"text": "a much longer line with six words", "start": None},
    {"text": "final", "start": None},
])
repair.seed_lyric_windows(units_mismatched_nodefault, scaffold_lines, txt, audio_dur=60.0)
check("seed_lyric_windows: omitting silence_sections keeps the old "
      "proportional-by-word-count behavior unchanged",
      abs(units_mismatched_nodefault[0]["seed_end"] -
          units_mismatched[0]["seed_end"]) < 1e-9)

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

# build_syllables_from_lyric_units must forward silence_sections to
# interpolate_word_timings() so unaligned words skip real pauses instead
# of being blindly stretched across them - verified by spying on the
# real interpolate_word_timings() call
_captured_silence = []
_orig_interpolate = repair.interpolate_word_timings


def _spy_interpolate(flat_words, silence_sections=None, audio_dur=None):
    _captured_silence.append(silence_sections)
    return _orig_interpolate(flat_words, silence_sections=silence_sections,
                             audio_dur=audio_dur)


repair.interpolate_word_timings = _spy_interpolate
try:
    repair.build_syllables_from_lyric_units(
        units_for_syl, aligned_for_syl, language=None,
        silence_sections=[(2.0, 3.0)])
finally:
    repair.interpolate_word_timings = _orig_interpolate
check(f"build_syllables_from_lyric_units forwards silence_sections to "
      f"interpolate_word_timings() (got {_captured_silence!r})",
      _captured_silence == [[(2.0, 3.0)]])

# build_syllables_from_lyric_units must also forward audio_dur, so a
# trailing run of unaligned words at the very end of the song can never
# be placed past the real end of the audio (see interpolate_word_timings'
# audio_dur clamp - found live 2026-09-17 on Lord of the Lost - Beyond
# Beautiful: lyrics ran 20s past the actual end of the audio file)
_captured_audio_dur = []
_orig_interpolate2 = repair.interpolate_word_timings


def _spy_interpolate2(flat_words, silence_sections=None, audio_dur=None):
    _captured_audio_dur.append(audio_dur)
    return _orig_interpolate2(flat_words, silence_sections=silence_sections,
                              audio_dur=audio_dur)


repair.interpolate_word_timings = _spy_interpolate2
try:
    repair.build_syllables_from_lyric_units(
        units_for_syl, aligned_for_syl, language=None, audio_dur=6.5)
finally:
    repair.interpolate_word_timings = _orig_interpolate2
check(f"build_syllables_from_lyric_units forwards audio_dur to "
      f"interpolate_word_timings() (got {_captured_audio_dur!r})",
      _captured_audio_dur == [6.5])

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

# --------------------------------------------------------------------------
# align_units_globally(): one global CTC alignment of every lyric word,
# returned in the per-unit structure build_syllables_from_lyric_units()
# already consumes
# --------------------------------------------------------------------------

import torch  # noqa: E402

_GDICT = {"-": 0, "|": 1, "a": 2, "b": 3, "c": 4, "d": 5}
_GMETA = {"dictionary": _GDICT, "type": "torchaudio"}


def _planned_logits(n_frames, plan):
    logits = torch.full((n_frames, 6), -8.0)
    logits[:, 0] = 0.0
    for frame, cls in plan:
        logits[frame, :] = -8.0
        logits[frame, cls] = 0.0
    return logits


class _PlannedModel:
    """Ignores its input and returns a fixed emission plan."""

    def __init__(self, logits, fail_on_cuda=False):
        self.logits = logits
        self.fail_on_cuda = fail_on_cuda

    def to(self, device):
        if self.fail_on_cuda and device == "cuda":
            raise RuntimeError("out of GPU memory")
        return self

    def __call__(self, wav, lengths=None):
        return self.logits.unsqueeze(0), None


# built directly (parse_lyrics_lines() drops punctuation-only words): the
# aligner itself must still cope with a word it cannot align
_g_units = [
    {"text": "ab c", "words": ["ab", "c"], "start": None},
    {"text": "... a", "words": ["...", "a"], "start": None},
]
# flat tokens: a b | c | a   (the "..." word has nothing alignable)
_g_plan = [(10, 2), (14, 3), (17, 1), (20, 4), (30, 1), (40, 2)]
_g_audio = [0.0] * (320 * 60)
_g_aligned, _g_n = repair.align_units_globally(
    _g_units, _PlannedModel(_planned_logits(60, _g_plan)), _GMETA, _g_audio)

check("align_units_globally: one aligned entry per unit",
      len(_g_aligned) == 2)
check("align_units_globally: words are positional, aligned words carry "
      f"start/end/score (got {_g_aligned[0]['words']!r})",
      abs(_g_aligned[0]["words"][0]["start"] - 0.20) < 1e-9 and
      abs(_g_aligned[0]["words"][0]["end"] - 0.30) < 1e-9 and
      0.0 < _g_aligned[0]["words"][0]["score"] <= 1.0 and
      abs(_g_aligned[0]["words"][1]["start"] - 0.40) < 1e-9)
check("align_units_globally: a word with nothing alignable stays None "
      "(so interpolation can place it) while its neighbour is timed",
      _g_aligned[1]["words"][0] is None and
      abs(_g_aligned[1]["words"][1]["start"] - 0.80) < 1e-9)
check("align_units_globally: reports how many words were timed "
      f"(got {_g_n})", _g_n == 3)
check("align_units_globally: each unit carries its text and a mean score",
      _g_aligned[0]["text"] == "ab c" and 0.0 < _g_aligned[0]["score"] <= 1.0)
check("align_units_globally: fallback natural durations exist for every "
      "word (used by interpolation for untimed words)",
      all(len(a["word_orig_starts"]) == len(u["words"]) ==
          len(a["word_orig_ends"]) for a, u in zip(_g_aligned, _g_units)) and
      _g_aligned[1]["word_orig_ends"][0] > _g_aligned[1]["word_orig_starts"][0])

# the result must feed build_syllables_from_lyric_units() unchanged and
# leave the untimed word interpolated between its timed neighbours
_g_syl = repair.build_syllables_from_lyric_units(
    _g_units, _g_aligned, None, audio_dur=60 * 0.02)
check("align_units_globally: output plugs into build_syllables_from_lyric_units "
      f"(got {_g_syl!r})",
      len(_g_syl) == 2 and len(_g_syl[1]) == 2 and
      _g_syl[0][0][1] < _g_syl[0][1][1] < _g_syl[1][1][1])

# a word whose letters the aligner could only place far apart (a stretch
# of the song it could not hear) must not become one absurdly long note:
# found live on Therion - Une fleur dans le coeur, where one word got a
# 25 s duration. Truth words are longer than 3 s only ~0.5% of the time.
_long_units = repair.parse_lyrics_lines([{"text": "ab c", "start": None}])
_long_plan = [(10, 2), (400, 3), (403, 1), (406, 4)]  # "a" at 0.2 s, "b" at 8.0 s
_long_aligned, _ = repair.align_units_globally(
    _long_units, _PlannedModel(_planned_logits(420, _long_plan)), _GMETA,
    [0.0] * (320 * 420))
_long_word = _long_aligned[0]["words"][0]
check("align_units_globally: a word's duration is capped at "
      f"{repair.MAX_ALIGNED_WORD_SECONDS}s (got "
      f"{_long_word['end'] - _long_word['start']:.1f}s)",
      _long_word["end"] - _long_word["start"] <= repair.MAX_ALIGNED_WORD_SECONDS + 1e-9)
check("align_units_globally: the cap keeps the word's START",
      abs(_long_word["start"] - 0.20) < 1e-9)

# filler: singing between two lines that the lyrics do not have ("d d d d d d")
# goes to a filler, reported with the number of the line it precedes
_f_units = [{"text": "ab", "words": ["ab"], "start": None},
            {"text": "c", "words": ["c"], "start": None}]
_f_plan = [(2, 2), (4, 3), (6, 1), (10, 5), (12, 5), (14, 5), (16, 5), (18, 5),
           (20, 5), (30, 1), (34, 4)]
_f_fillers = []
_f_aligned, _ = repair.align_units_globally(
    _f_units, _PlannedModel(_planned_logits(40, _f_plan)), _GMETA, [0.0] * (320 * 40),
    fillers_out=_f_fillers)
check(f"align_units_globally: singing not in the lyrics is reported as a filler (got {_f_fillers})",
      len(_f_fillers) == 1 and _f_fillers[0][2] == 1
      and _f_fillers[0][0] <= 0.20 + 1e-9 and _f_fillers[0][1] >= 0.42 - 1e-9)
check("align_units_globally: the filler leaves the lyric words in place",
      abs(_f_aligned[0]["words"][0]["start"] - 0.04) < 1e-9
      and abs(_f_aligned[1]["words"][0]["start"] - 0.68) < 1e-9)
_f_saved = repair.LYRICS_FILLER_ENTER
repair.LYRICS_FILLER_ENTER = 0.0
_f_fillers = []
repair.align_units_globally(
    _f_units, _PlannedModel(_planned_logits(40, _f_plan)), _GMETA, [0.0] * (320 * 40),
    fillers_out=_f_fillers)
repair.LYRICS_FILLER_ENTER = _f_saved
check(f"align_units_globally: LYRICS_FILLER_ENTER=0 switches the filler off (got {_f_fillers})",
      _f_fillers == [])

# filler_notes(): long fillers become readable log lines, short ones are dropped
_fn_units = [{"text": "one two", "words": ["one", "two"]},
             {"text": "three four five six seven eight", "words": "three four five six seven eight".split()}]
_fn = repair.filler_notes([(10.0, 11.0, 1), (148.7, 158.2, 1), (200.0, 205.5, 2)], _fn_units, 2.0)
check(f"filler_notes: long fillers named with time and the following line (got {_fn})",
      _fn == ['2:28.7-2:38.2 before line 2 "three four five six seven..."',
              '3:20.0-3:25.5 after the last line'])

# anchors: the song is aligned piece by piece between anchored lines, and an
# anchored line's first word starts exactly at its anchor time
_a_units = [{"text": "a b", "words": ["a", "b"], "start": None},
            {"text": "c d", "words": ["c", "d"], "start": None}]
# c | d is sung twice - early (frames 12-15) and at the anchor (frames 40-43)
_a_plan = [(5, 2), (6, 1), (8, 3), (10, 1), (12, 4), (13, 1), (15, 5),
           (40, 4), (41, 1), (43, 5)]
_a_audio = [0.0] * (320 * 60)
_a_aligned, _a_n = repair.align_units_globally(
    _a_units, _PlannedModel(_planned_logits(60, _a_plan)), _GMETA, _a_audio,
    anchors=[(1, 0.80)])
check("align_units_globally: an anchored line is aligned after its anchor time "
      f"(got {_a_aligned[1]['words']})",
      abs(_a_aligned[1]["words"][0]["start"] - 0.80) < 1e-9 and
      abs(_a_aligned[1]["words"][1]["start"] - 0.86) < 1e-9)
check("align_units_globally: lines before the anchor stay before it",
      abs(_a_aligned[0]["words"][0]["start"] - 0.10) < 1e-9 and
      abs(_a_aligned[0]["words"][1]["start"] - 0.16) < 1e-9 and _a_n == 4)
_a_pin, _ = repair.align_units_globally(
    _a_units, _PlannedModel(_planned_logits(60, _a_plan)), _GMETA, _a_audio,
    anchors=[(1, 0.70)])
check("align_units_globally: the anchored line's first word starts at the anchor "
      f"time even when the aligner hears it later (got {_a_pin[1]['words'][0]})",
      abs(_a_pin[1]["words"][0]["start"] - 0.70) < 1e-9 and
      _a_pin[1]["words"][0]["end"] > 0.70)
_a_first, _ = repair.align_units_globally(
    _a_units, _PlannedModel(_planned_logits(60, _a_plan)), _GMETA, _a_audio,
    anchors=[(0, 0.10)])
check("align_units_globally: an anchor on the first line is fine",
      abs(_a_first[0]["words"][0]["start"] - 0.10) < 1e-9)

# a GPU that cannot hold the model must not lose the song: retry on CPU
_g_aligned2, _g_n2 = repair.align_units_globally(
    _g_units, _PlannedModel(_planned_logits(60, _g_plan), fail_on_cuda=True),
    _GMETA, _g_audio, device="cuda")
check("align_units_globally: falls back to the CPU when the GPU run fails",
      _g_n2 == 3 and abs(_g_aligned2[0]["words"][0]["start"] - 0.20) < 1e-9)

# --------------------------------------------------------------------------
# refinement of the global alignment (measured on 104 hand-synced songs:
# first word >1 s early 19% -> 13.5%, long-note ends within 0.3 s 55% -> 66%,
# word starts within 0.1 s 55% -> 59%)
# --------------------------------------------------------------------------

def _w(start, end, score=0.9):
    return {"start": start, "end": end, "score": score}


def _unit(words, texts=None):
    return {"words": words, "text": " ".join(texts or ["x"] * len(words)),
            "score": 0.9, "word_orig_starts": [0.0] * len(words),
            "word_orig_ends": [0.1] * len(words)}


# place_line_outliers(): a word far away from the rest of its own line is
# matched to some other sound; it goes back next to its line at natural
# length (0.1 s + 0.06 s per character)
_lo_units = [{"words": ["ab", "c", "d", "efg"]}, {"words": ["h", "i"]}]
_lo = [_unit([_w(0.0, 0.3), _w(10.0, 10.3), _w(10.4, 10.6), _w(30.0, 34.0)]),
       _unit([_w(40.0, 40.3), _w(42.5, 42.8)])]
repair.place_line_outliers(_lo, _lo_units, 3.0)
check("place_line_outliers: an outlier before the rest of its line ends where "
      f"the line starts, at natural length (got {_lo[0]['words'][0]})",
      abs(_lo[0]["words"][0]["start"] - 9.78) < 1e-9 and
      abs(_lo[0]["words"][0]["end"] - 10.0) < 1e-9 and
      _lo[0]["words"][0]["score"] == 0.0)
check("place_line_outliers: an outlier after the rest of its line starts where "
      f"the line ends, at natural length - never its old (long) duration "
      f"(got {_lo[0]['words'][3]})",
      abs(_lo[0]["words"][3]["start"] - 10.6) < 1e-9 and
      abs(_lo[0]["words"][3]["end"] - 10.88) < 1e-9)
check("place_line_outliers: the kept words stay where they are",
      _lo[0]["words"][1] == _w(10.0, 10.3) and _lo[0]["words"][2] == _w(10.4, 10.6))
check("place_line_outliers: a line without such a gap stays untouched",
      _lo[1]["words"] == [_w(40.0, 40.3), _w(42.5, 42.8)])
_lo_tie = [_unit([_w(0.0, 0.3, 0.2), _w(10.0, 10.3, 0.8)])]
repair.place_line_outliers(_lo_tie, [{"words": ["a", "b"]}], 3.0)
check("place_line_outliers: between equally large clusters the better-scored "
      f"one is kept (got {_lo_tie[0]['words']})",
      abs(_lo_tie[0]["words"][0]["start"] - 9.84) < 1e-9 and
      _lo_tie[0]["words"][1] == _w(10.0, 10.3, 0.8))
_lo_two = [_unit([_w(0.0, 0.3), _w(0.5, 0.7), _w(10.0, 10.3), _w(10.4, 10.6), _w(10.7, 11.0)])]
repair.place_line_outliers(_lo_two, [{"words": ["a", "b", "c", "d", "e"]}], 3.0)
check("place_line_outliers: several outliers keep their order before the line",
      abs(_lo_two[0]["words"][1]["end"] - 10.0) < 1e-9 and
      abs(_lo_two[0]["words"][0]["end"] - _lo_two[0]["words"][1]["start"]) < 1e-9 and
      abs(_lo_two[0]["words"][0]["start"] - 9.68) < 1e-9)
_lo_none = [_unit([None, _w(10.0, 10.3), None])]
repair.place_line_outliers(_lo_none, [{"words": ["a", "b", "c"]}], 3.0)
check("place_line_outliers: untimed words are left to interpolation",
      _lo_none[0]["words"][0] is None and _lo_none[0]["words"][2] is None)

# place_leading_words(): untimed words before the first timed word
_pl_units = [{"words": ["abc", "de"]}, {"words": ["f"]}]
_pl = [_unit([None, None]), _unit([_w(20.0, 20.3)])]
repair.place_leading_words(_pl, _pl_units, [(0.0, 15.0)])
check("place_leading_words: leading words sit directly before the first timed "
      f"word at natural length (got {[_pl[0]['words'][i] for i in (0, 1)]})",
      abs(_pl[0]["words"][0]["start"] - 19.5) < 1e-9 and
      abs(_pl[0]["words"][0]["end"] - 19.78) < 1e-9 and
      abs(_pl[0]["words"][1]["start"] - 19.78) < 1e-9 and
      abs(_pl[0]["words"][1]["end"] - 20.0) < 1e-9)
check("place_leading_words: placed words carry score 0.0",
      _pl[0]["words"][0]["score"] == 0.0)
_pl2 = [_unit([None, None]), _unit([_w(20.0, 20.3)])]
repair.place_leading_words(_pl2, _pl_units, [(0.0, 19.6)])
check("place_leading_words: a short sung stretch before the first word is "
      "used compressed instead of jumping back into the intro",
      abs(_pl2[0]["words"][0]["start"] - 19.6) < 1e-9 and
      abs(_pl2[0]["words"][1]["end"] - 20.0) < 1e-9)
_pl3 = [_unit([None, None]), _unit([_w(20.0, 20.3)])]
repair.place_leading_words(_pl3, _pl_units, [(0.0, 19.9)])
check("place_leading_words: with no usable stretch at all the words still end "
      "at the first timed word",
      abs(_pl3[0]["words"][1]["end"] - 20.0) < 1e-9 and
      abs(_pl3[0]["words"][0]["start"] - 19.5) < 1e-9)
_pl4 = [_unit([_w(1.0, 1.2), None])]
repair.place_leading_words(_pl4, [{"words": ["a", "b"]}], [])
check("place_leading_words: nothing to do when the first word is timed",
      _pl4[0]["words"][1] is None)
_pl5 = [_unit([None, None])]
repair.place_leading_words(_pl5, [{"words": ["a", "b"]}], [])
check("place_leading_words: nothing to do when no word is timed at all",
      _pl5[0]["words"] == [None, None])

# frame_levels_db(): 20 ms frame levels of the 16 kHz vocal stem
_levels = repair.frame_levels_db([0.5] * 640 + [0.0] * 320)
check("frame_levels_db: one level per 20 ms frame, in dBFS",
      len(_levels) == 3 and abs(_levels[0] + 6.0206) < 1e-3 and _levels[2] < -100)

# extend_word_ends(): a held note keeps sounding after its last letter
_lv = [-40.0] * 50 + [-10.0] * 50 + [-30.0] * 100  # loud from 1.0 s to 2.0 s
_ex = [_unit([_w(1.0, 1.2), _w(3.0, 3.2)])]
repair.extend_word_ends(_ex, _lv, 6.0, 3.0)
check("extend_word_ends: the end follows the singing while it stays within "
      f"6 dB of the word's own level (got {_ex[0]['words'][0]['end']})",
      abs(_ex[0]["words"][0]["end"] - 2.0) < 1e-9)
_ex2 = [_unit([_w(1.0, 1.2), _w(1.5, 1.7)])]
repair.extend_word_ends(_ex2, _lv, 6.0, 3.0)
check("extend_word_ends: never closer than 40 ms to the next word",
      abs(_ex2[0]["words"][0]["end"] - 1.46) < 1e-9)
_ex3 = [_unit([_w(1.0, 1.91)])]
repair.extend_word_ends(_ex3, [-10.0] * 95 + [-40.0] * 105, 6.0, 3.0)
check("extend_word_ends: a word is never shortened (its end frame is already "
      "quiet)", abs(_ex3[0]["words"][0]["end"] - 1.91) < 1e-9)
_ex4 = [_unit([_w(0.0, 0.2)])]
repair.extend_word_ends(_ex4, [-10.0] * 1000, 6.0, 3.0)
check("extend_word_ends: extends at most max_ext seconds",
      abs(_ex4[0]["words"][0]["end"] - 3.2) < 1e-9)
_ex5 = [_unit([_w(0.0, 3.5)])]
repair.extend_word_ends(_ex5, [-10.0] * 1000, 6.0, 3.0)
check("extend_word_ends: never beyond the maximum word length",
      abs(_ex5[0]["words"][0]["end"] - repair.MAX_ALIGNED_WORD_SECONDS) < 1e-9)

# lead_word_starts(): aligned starts are ~50 ms later than hand-synced ones
_ls = [_unit([_w(0.02, 0.5), None, _w(1.0, 1.5)])]
repair.lead_word_starts(_ls, 0.04)
check("lead_word_starts: timed starts move earlier, never below 0, ends stay",
      abs(_ls[0]["words"][0]["start"]) < 1e-9 and
      abs(_ls[0]["words"][2]["start"] - 0.96) < 1e-9 and
      abs(_ls[0]["words"][2]["end"] - 1.5) < 1e-9 and _ls[0]["words"][1] is None)

# refine_global_alignment(): all steps in the measured order
_rf_units = [{"words": ["ab", "c", "d"]}]
_rf = [_unit([_w(0.0, 0.2), _w(10.0, 10.2), _w(10.3, 10.5)])]
repair.refine_global_alignment(_rf, _rf_units, [0.5] * (16000 * 12), [])
check("refine_global_alignment: an outlier opening word is re-placed before "
      f"the rest of its line and all starts lead by 40 ms (got {_rf[0]['words']})",
      abs(_rf[0]["words"][0]["start"] - (10.0 - 0.22 - repair.WORD_START_LEAD_S)) < 1e-9 and
      abs(_rf[0]["words"][1]["start"] - (10.0 - repair.WORD_START_LEAD_S)) < 1e-9)

# loud_high_instruments: word ends follow the singing down to 9 dB below the
# word's level instead of 6 dB (bench19_ends.py: long-note ends 63.9% vs
# 61.7% on the 7 benchmark songs with loud high instruments)
check("word_end_drop_db: 6 dB without traits",
      repair.word_end_drop_db([]) == repair.WORD_END_EXTEND_DROP_DB == 6.0)
check("word_end_drop_db: 9 dB for loud_high_instruments",
      repair.word_end_drop_db(["choir", "loud_high_instruments"])
      == repair.LOUD_INSTRUMENTS_WORD_END_DROP_DB == 9.0)
check("word_end_drop_db: other traits keep 6 dB",
      repair.word_end_drop_db(["harsh_vocals", "operatic_vocals"]) == 6.0)
# a word at -6 dB (0.0-0.2 s), then singing 7.5 dB lower until 1.0 s, then silence
_held = [0.5] * 3200 + [0.2113] * 12800 + [0.0] * 32000
_rf_plain = [_unit([_w(0.0, 0.2)])]
repair.refine_global_alignment(_rf_plain, [{"words": ["ab"]}], _held, [])
_rf_loud = [_unit([_w(0.0, 0.2)])]
repair.refine_global_alignment(_rf_loud, [{"words": ["ab"]}], _held, [],
                               traits=["loud_high_instruments"])
check("refine_global_alignment: without traits the end stops 6 dB below the word "
      f"(got {_rf_plain[0]['words'][0]['end']})",
      abs(_rf_plain[0]["words"][0]["end"] - 0.2) < 1e-9)
check("refine_global_alignment: loud_high_instruments follows the held note "
      f"down to 9 dB (got {_rf_loud[0]['words'][0]['end']})",
      abs(_rf_loud[0]["words"][0]["end"] - 1.0) < 1e-9)

# --------------------------------------------------------------------------
# write_lyrics_result(): note pitches are normalised (pitch_normalise.py) -
# octave errors folded back, an unsure note keeps its own pitch class
# --------------------------------------------------------------------------

class _PitchTxt:
    path = "song.txt"
    real_bpm = 60.0  # one beat per second
    lines = [{"raw": "#TITLE:Song"}, {"raw": "#BPM:15"}, {"raw": "#GAP:0"}]


class _FakePitched:
    def __init__(self, melody, unsure):
        self.times, self.frequencies, self.confidence = [], [], []
        for i, pitch in enumerate(melody):
            hz = 440.0 * 2 ** ((pitch + 48 - 69) / 12)
            for f in range(100):
                self.times.append(i + f / 100)
                self.frequencies.append(hz)
                self.confidence.append(0.1 if i in unsure else 0.9)


_melody = [4, 5, 7, 5, 28, 2, 4, 5, 46, 9, 7, 5]
_saved_pitch = repair.get_pitch_with_file
_pitch_song = tempfile.mkdtemp()
_pitch_out = tempfile.mkdtemp()
try:
    repair.get_pitch_with_file = lambda path: _FakePitched(_melody, {8})
    _pitch_path = repair.write_lyrics_result(
        _PitchTxt(), _pitch_song, _pitch_out,
        [[(f"w{i} ", float(i), i + 0.8) for i in range(12)]], "/p.wav",
        audio_dur=20.0)
finally:
    repair.get_pitch_with_file = _saved_pitch
_written = [int(l.split()[3]) for l in open(_pitch_path, encoding="utf-8")
            if l.startswith(": ")]
check(f"write_lyrics_result: a note two octaves off is folded back (got {_written})",
      _written[4] == 4)
check("write_lyrics_result: an unsure note keeps its pitch class, only its "
      f"octave is folded (got {_written[8]})",
      _written[8] == 10)
check("write_lyrics_result: correct notes keep their pitch",
      [_written[i] for i in (0, 1, 2, 3, 5, 6, 7, 9, 10, 11)] ==
      [4, 5, 7, 5, 2, 4, 5, 9, 7, 5])


class _NoGapTxt(_PitchTxt):
    lines = [{"raw": "#TITLE:Song"}, {"raw": "#BPM:15"}]


# the notes are placed relative to the first syllable's start - a song
# file without #GAP must get that #GAP, or USDX plays every note too early
_saved_pitch = repair.get_pitch_with_file
try:
    repair.get_pitch_with_file = lambda path: _FakePitched([5] * 3, set())
    _nogap_path = repair.write_lyrics_result(
        _NoGapTxt(), tempfile.mkdtemp(), tempfile.mkdtemp(),
        [[("a ", 2.0, 2.8), ("b ", 3.0, 3.8)]], "/p.wav", audio_dur=20.0)
finally:
    repair.get_pitch_with_file = _saved_pitch
_nogap_text = open(_nogap_path, encoding="utf-8").read()
check(f"write_lyrics_result: a song file without #GAP gets one (got {_nogap_text[:80]!r})",
      "#GAP:2000\n" in _nogap_text)
check(f"write_lyrics_result: a song file without #VERSION gets one (got {_nogap_text[:80]!r})",
      _nogap_text.startswith(f"#VERSION:{repair.FORMAT_VERSION}\n"))

# profile harsh_vocals: an unsure note moves halfway towards the melody, at
# most 3 semitones from it (pitch_normalise.normalise_harsh_pitches());
# without the trait it keeps its own pitch
_harsh_melody = [5, 5, 5, 5, 5, 11, 5, 5, 5, 5, 5]
_harsh_written = {}
_saved_pitch = repair.get_pitch_with_file
try:
    repair.get_pitch_with_file = lambda path: _FakePitched(_harsh_melody, {5})
    for _traits in ((), ("harsh_vocals",)):
        _p = repair.write_lyrics_result(
            _PitchTxt(), tempfile.mkdtemp(), tempfile.mkdtemp(),
            [[(f"w{i} ", float(i), i + 0.8) for i in range(11)]], "/p.wav",
            audio_dur=20.0, traits=_traits)
        _harsh_written[_traits] = [int(l.split()[3]) for l in open(_p, encoding="utf-8")
                                   if l.startswith(": ")]
finally:
    repair.get_pitch_with_file = _saved_pitch
check(f"write_lyrics_result: without traits an unsure note keeps its pitch (got {_harsh_written[()]})",
      _harsh_written[()][5] == 11)
check("write_lyrics_result: harsh_vocals pulls an unsure note towards the melody "
      f"(got {_harsh_written[('harsh_vocals',)]})",
      _harsh_written[("harsh_vocals",)] == [5, 5, 5, 5, 5, 8, 5, 5, 5, 5, 5])

# write_lyrics_result(): lyrics that run past the end of the audio - the
# notes that do not fit are dropped, the rest stays strictly in order
_saved_pitch = repair.get_pitch_with_file
try:
    repair.get_pitch_with_file = lambda path: _FakePitched([5] * 12, set())
    _end_path = repair.write_lyrics_result(
        _PitchTxt(), tempfile.mkdtemp(), tempfile.mkdtemp(),
        [[(f"w{i} ", float(i), i + 0.8) for i in range(0, 6)],
         [(f"x{i} ", float(i), i + 0.8) for i in range(6, 12)]], "/p.wav",
        audio_dur=8.0)
finally:
    repair.get_pitch_with_file = _saved_pitch
_end_lines = [l.rstrip("\n") for l in open(_end_path, encoding="utf-8")
              if l[:1] in ":-"]
_end_notes = [tuple(int(x) for x in l.split()[1:3]) for l in _end_lines if l.startswith(": ")]
check(f"write_lyrics_result: notes past the end of the audio are dropped (got {_end_lines})",
      len(_end_notes) == 8 and all(b + d <= 8 for b, d in _end_notes))
check("write_lyrics_result: the kept notes never overlap",
      all(a[0] + a[1] <= b[0] for a, b in zip(_end_notes, _end_notes[1:])))
check("write_lyrics_result: no line break after the last kept note",
      not _end_lines[-1].startswith("-"))

# pitch_window(): a short note is judged over at least PITCH_MIN_WINDOW_S
# (0.2 s, centred) - measured on 110 hand-made songs, short notes are where
# the detector fails most
_pw = repair.pitch_window(10.0, 10.1)
check(f"pitch_window: a note shorter than the minimum is widened around its centre (got {_pw})",
      abs(_pw[0] - 9.95) < 1e-9 and abs(_pw[1] - 10.15) < 1e-9)
check("pitch_window: a long enough note keeps its own span",
      repair.pitch_window(10.0, 10.6) == (10.0, 10.6))
check("pitch_window: never before the start of the audio",
      repair.pitch_window(0.05, 0.1)[0] == 0.0)
check("pitch_window: the minimum is 0.2 s", repair.PITCH_MIN_WINDOW_S == 0.2)
check("the pitch is read 0.1 s after the note (the singing lags the notes)",
      repair.PITCH_READ_DELAY_S == 0.1)


class _SlotPitched:
    """Per 1 s slot: pitch `first` until `change` seconds into the slot,
    then pitch `then`."""

    def __init__(self, first, then, change, slots):
        self.times, self.frequencies, self.confidence = [], [], []
        for i in range(slots):
            for f in range(100):
                pitch = first if f / 100 < change else then
                self.times.append(i + f / 100)
                self.frequencies.append(440.0 * 2 ** ((pitch + 48 - 69) / 12))
                self.confidence.append(0.9)


# a 0.2 s note whose first 0.15 s carry another pitch: read 0.1 s later, the
# note takes the pitch that follows
_saved_pitch = repair.get_pitch_with_file
try:
    repair.get_pitch_with_file = lambda path: _SlotPitched(9, 5, 0.15, 6)
    _late_path = repair.write_lyrics_result(
        _PitchTxt(), tempfile.mkdtemp(), tempfile.mkdtemp(),
        [[(f"w{i} ", float(i), i + 0.2) for i in range(6)]], "/p.wav",
        audio_dur=20.0)
finally:
    repair.get_pitch_with_file = _saved_pitch
_late = [int(l.split()[3]) for l in open(_late_path, encoding="utf-8")
         if l.startswith(": ")]
check(f"write_lyrics_result: the pitch is read 0.1 s after the note (got {_late})",
      all(p == 5 for p in _late))

# a held syllable that moves up 4 semitones halfway: the syllable plus a `~`
# note on the second pitch; the word end (trailing space) moves to the `~`
_saved_pitch = repair.get_pitch_with_file
try:
    repair.get_pitch_with_file = lambda path: _SlotPitched(5, 9, 0.5, 8)
    _held_path = repair.write_lyrics_result(
        _PitchTxt(), tempfile.mkdtemp(), tempfile.mkdtemp(),
        [[("lem", 0.0, 0.9), ("mel ", 2.0, 2.9)]], "/p.wav", audio_dur=20.0)
finally:
    repair.get_pitch_with_file = _saved_pitch
_held_notes = [l.rstrip("\n") for l in open(_held_path, encoding="utf-8")
               if l.startswith(": ")]
check(f"write_lyrics_result: a held syllable that changes pitch gets a ~ note (got {_held_notes})",
      [l.split(" ", 4)[3:] for l in _held_notes] ==
      [["5", "lem"], ["9", "~"], ["5", "mel"], ["9", "~ "]])


class _GlidePitched:
    """Per 1 s slot: a steady note (pitch `steady`) with a 0.1 s different
    blip (pitch `blip`) exactly where the short syllable sits."""

    def __init__(self, steady, blip, slots):
        self.times, self.frequencies, self.confidence = [], [], []
        for i in range(slots):
            for f in range(100):
                t = i + f / 100
                pitch = blip if 0.3 <= f / 100 < 0.4 else steady
                self.times.append(t)
                self.frequencies.append(440.0 * 2 ** ((pitch + 48 - 69) / 12))
                self.confidence.append(0.9)


_saved_pitch = repair.get_pitch_with_file
_glide_out = tempfile.mkdtemp()
try:
    repair.get_pitch_with_file = lambda path: _GlidePitched(5, 8, 12)
    _glide_path = repair.write_lyrics_result(
        _PitchTxt(), tempfile.mkdtemp(), _glide_out,
        [[(f"w{i} ", i + 0.3, i + 0.4) for i in range(12)]], "/p.wav",
        audio_dur=20.0)
finally:
    repair.get_pitch_with_file = _saved_pitch
_glide = [int(l.split()[3]) for l in open(_glide_path, encoding="utf-8")
          if l.startswith(": ")]
check(f"write_lyrics_result: a short note takes the pitch sung around it, not a "
      f"0.1 s blip (got {_glide})", all(p == 5 for p in _glide))

# sure_pitch_fraction() uses the shared confidence threshold (0.2 for SwiftF0 0.3.0)
class _ConfPitched:
    times = [0.0, 0.1, 0.2, 0.3]
    frequencies = [220.0] * 4
    confidence = [0.1, 0.25, 0.3, 0.9]


check("sure_pitch_fraction: frames above the shared threshold 0.2 count as sure",
      abs(repair.sure_pitch_fraction(0.0, 0.3, _ConfPitched()) - 2 / 3) < 1e-9)

# --------------------------------------------------------------------------
# repair_txt_with_lyrics(): lyrics mode aligns via ONE global pass now
# (everything heavy stubbed out - this checks the wiring only)
# --------------------------------------------------------------------------

class _WiringTxt:
    path = "song.txt"
    real_bpm = 60.0


_wiring = {}
_saved = {n: getattr(repair, n) for n in (
    "locate_audio", "prepare_processing_audio", "get_silence_sections",
    "load_audio_16k", "resolve_language", "load_aligner",
    "align_units_globally", "refine_global_alignment",
    "build_syllables_from_lyric_units", "write_lyrics_result")}


def _fake_align(units, model, meta, audio16k, device="cpu", anchors=None, fillers_out=None,
                line_skip=None):
    _wiring["align"] = (units, model, meta, len(audio16k), device)
    _wiring["anchors"] = anchors
    return [{"words": [{"start": 0.0, "end": 1.0}] * len(u["words"]),
             "text": u["text"], "score": 0.5,
             "word_orig_starts": [0.0] * len(u["words"]),
             "word_orig_ends": [1.0] * len(u["words"])} for u in units], 5


def _fake_refine(aligned, units, audio16k, silence_sections, traits=()):
    _wiring["refine"] = (len(units), len(audio16k), silence_sections)
    _wiring["traits"] = list(traits)
    aligned[0]["refined"] = True


def _fake_build(units, aligned, lang, silence_sections=None, audio_dur=None):
    _wiring["build"] = (aligned, lang, silence_sections, audio_dur)
    return [[("w ", 0.0, 1.0)]]


def _fake_write(txt, song_dir, out_dir, per_unit, processing_audio, audio_dur=None,
                traits=()):
    _wiring["write"] = (per_unit, processing_audio, audio_dur)
    _wiring["write_traits"] = list(traits)
    return "/out/song.txt"


try:
    repair.locate_audio = lambda song_dir, txt, work_dir: ("/a.wav", None)
    repair.prepare_processing_audio = lambda audio, work, device: ("/v.wav", "/p.wav")
    repair.get_silence_sections = lambda path, min_silence_len=0: [(1.0, 2.0)]
    repair.load_audio_16k = lambda path: [0.0] * (16000 * 3)
    repair.resolve_language = lambda *a, **k: "en"
    repair.load_aligner = lambda lang, device: ("MODEL", "META", "en")
    repair.align_units_globally = _fake_align
    repair.refine_global_alignment = _fake_refine
    repair.build_syllables_from_lyric_units = _fake_build
    repair.write_lyrics_result = _fake_write
    _wiring_result = repair.repair_txt_with_lyrics(
        _WiringTxt(), repair.parse_lyrics_lines([{"text": "a b", "start": None}]),
        "/s", "/o", "cuda", tempfile.mkdtemp(), traits=["loud_high_instruments"])
finally:
    for _n, _f in _saved.items():
        setattr(repair, _n, _f)

check("repair_txt_with_lyrics: aligns every unit with one align_units_globally "
      f"call on the loaded aligner and the separated vocals (got {_wiring.get('align', ('',) * 5)[1:]})",
      _wiring["align"][1:] == ("MODEL", "META", 16000 * 3, "cuda"))
check("repair_txt_with_lyrics: refines the alignment with the units, the "
      f"vocals and the detected silence (got {_wiring.get('refine')})",
      _wiring.get("refine") == (1, 16000 * 3, [(1.0, 2.0)]))
check("repair_txt_with_lyrics: the song's traits reach the precision pass",
      _wiring.get("traits") == ["loud_high_instruments"])
check("repair_txt_with_lyrics: build_syllables gets the REFINED alignment",
      _wiring["build"][0][0].get("refined") is True)
check("repair_txt_with_lyrics: build_syllables gets the global alignment, "
      "the detected silence and the real audio length",
      _wiring["build"][0][0]["score"] == 0.5 and
      _wiring["build"][2] == [(1.0, 2.0)] and abs(_wiring["build"][3] - 3.0) < 1e-9)
check("repair_txt_with_lyrics: the song's traits reach the note writer",
      _wiring.get("write_traits") == ["loud_high_instruments"])
check("repair_txt_with_lyrics: the ceiling clamp still gets the audio length",
      abs(_wiring["write"][2] - 3.0) < 1e-9)
check("repair_txt_with_lyrics: returns output path and word counts",
      _wiring_result == {"output": "/out/song.txt", "aligned_words": 5, "total_words": 2})

# --------------------------------------------------------------------------
# lyrics filled from whisper's transcript (lyrics_fill.py): proposed lines go
# in as OPTIONAL lines; the ones the alignment leaves out are dropped
# --------------------------------------------------------------------------

def _fill_run(keep_candidate):
    seen = {}

    def fake_align(units, model, meta, audio16k, device="cpu", anchors=None,
                   fillers_out=None, line_skip=None):
        seen["units"] = [u["text"] for u in units]
        seen["line_skip"] = line_skip
        out = []
        for u, cost in zip(units, line_skip or [None] * len(units)):
            sung = cost is None or keep_candidate
            out.append({"words": [{"start": 0.0, "end": 1.0, "score": 0.5} if sung
                                  else repair.ctc_align.NOT_SUNG] * len(u["words"]),
                        "text": u["text"], "score": 0.5 if sung else None,
                        "word_orig_starts": [0.0] * len(u["words"]),
                        "word_orig_ends": [1.0] * len(u["words"])})
        return out, 6

    def fake_refine(aligned, units, audio16k, silence_sections, traits=()):
        seen["refined_units"] = [u["text"] for u in units]

    saved = {n: getattr(repair, n) for n in (
        "locate_audio", "prepare_processing_audio", "get_silence_sections", "load_audio_16k",
        "resolve_language", "load_aligner", "align_units_globally", "refine_global_alignment",
        "build_syllables_from_lyric_units", "write_lyrics_result")}
    try:
        repair.locate_audio = lambda song_dir, txt, work_dir: ("/a.wav", None)
        repair.prepare_processing_audio = lambda audio, work, device: ("/v.wav", "/p.wav")
        repair.get_silence_sections = lambda path, min_silence_len=0: []
        repair.load_audio_16k = lambda path: [0.0] * (16000 * 3)
        repair.resolve_language = lambda *a, **k: "de"
        repair.load_aligner = lambda lang, device: ("MODEL", "META", "de")
        repair.align_units_globally = fake_align
        repair.refine_global_alignment = fake_refine
        repair.build_syllables_from_lyric_units = lambda units, aligned, lang, **k: [[("w ", 0.0, 1.0)]] * len(units)
        repair.write_lyrics_result = lambda *a, **k: "/out/song.txt"
        heard = [(k * 0.5, k * 0.5 + 0.4, w) for k, w in enumerate(
            "eins zwei drei vier fuenf sechs sieben acht neun".split())]
        repair.repair_txt_with_lyrics(
            _WiringTxt(), repair.parse_lyrics_lines([{"text": "eins zwei drei"},
                                                    {"text": "sieben acht neun"}]),
            "/s", "/o", "cuda", tempfile.mkdtemp(), fill_transcript=heard)
    finally:
        for n, f in saved.items():
            setattr(repair, n, f)
    return seen


_fill_kept = _fill_run(True)
check(f"lyrics fill: the proposed line goes in at its place (got {_fill_kept.get('units')})",
      _fill_kept.get("units") == ["eins zwei drei", "vier fuenf sechs", "sieben acht neun"])
check(f"lyrics fill: only the proposed line may be left out (got {_fill_kept.get('line_skip')})",
      _fill_kept.get("line_skip") == [None, repair.LYRICS_FILL_OPTIONAL_COST, None])
check("lyrics fill: a proposed line that is sung stays",
      _fill_kept.get("refined_units") == ["eins zwei drei", "vier fuenf sechs", "sieben acht neun"])
_fill_skipped = _fill_run(False)
check(f"lyrics fill: a proposed line the alignment left out is dropped (got {_fill_skipped.get('refined_units')})",
      _fill_skipped.get("refined_units") == ["eins zwei drei", "sieben acht neun"])

# align_units_globally() hands line skips to the aligner
_ls_seen = {}
_saved_em2, _saved_awg2 = repair.ctc_align.emissions_for_audio, repair.ctc_align.align_words_globally
try:
    repair.ctc_align.emissions_for_audio = lambda *a, **k: "EM"
    repair.ctc_align.align_words_globally = (
        lambda words, em, meta, end_penalty=None, **kw:
        _ls_seen.update(kw) or [(1.0, 1.5, 0.9)] * len(words))
    repair.align_units_globally(repair.parse_lyrics_lines([{"text": "a"}, {"text": "b"}]),
                                "M", {}, [0.0] * 16000, line_skip=[None, 0.5])
finally:
    repair.ctc_align.emissions_for_audio, repair.ctc_align.align_words_globally = _saved_em2, _saved_awg2
check(f"align_units_globally: passes line skips to the aligner (got {_ls_seen.get('line_skip')})",
      _ls_seen.get("line_skip") == [None, 0.5] and _ls_seen.get("line_lengths") == [1, 1])

# --------------------------------------------------------------------------
# lyrics never sung at the end (ctc_align end_penalty): left out before the
# precision pass and the notes, never squeezed into the audio
# --------------------------------------------------------------------------

_NS = repair.ctc_align.NOT_SUNG
_t = {"start": 1.0, "end": 1.5, "score": 0.8}
_du_units = repair.parse_lyrics_lines([{"text": "a b"}, {"text": "c d"}, {"text": "e f"}])
_du_aligned = [
    {"words": [_t, _t], "text": "a b", "score": 0.8,
     "word_orig_starts": [1.0, 1.0], "word_orig_ends": [1.5, 1.5]},
    {"words": [{"start": 2.0, "end": 2.5, "score": 0.6}, _NS], "text": "c d", "score": 0.6,
     "word_orig_starts": [2.0, 0.0], "word_orig_ends": [2.5, 0.3]},
    {"words": [_NS, _NS], "text": "e f", "score": None,
     "word_orig_starts": [0.0, 0.0], "word_orig_ends": [0.3, 0.3]},
]
_du_u, _du_a, _du_n = repair.drop_unsung_tail(_du_units, _du_aligned)
check(f"drop_unsung_tail: lines after the last sung word are left out (got {[u['text'] for u in _du_u]})",
      [u["text"] for u in _du_u] == ["a b", "c"] and len(_du_a) == 2)
check("drop_unsung_tail: a line sung only in part keeps its sung words",
      _du_u[1]["words"] == ["c"] and _du_a[1]["words"] == [{"start": 2.0, "end": 2.5, "score": 0.6}]
      and _du_a[1]["word_orig_starts"] == [2.0] and _du_a[1]["word_orig_ends"] == [2.5]
      and _du_a[1]["text"] == "c")
check(f"drop_unsung_tail: counts the words left out (got {_du_n})", _du_n == 3)
_keep_u, _keep_a, _keep_n = repair.drop_unsung_tail(_du_units[:1], _du_aligned[:1])
check("drop_unsung_tail: everything sung - unchanged",
      _keep_n == 0 and _keep_u == _du_units[:1] and _keep_a == _du_aligned[:1])

# a whole line left out in the MIDDLE (an optional line the path skipped)
# goes alone - the lines after it stay
_mid_units = repair.parse_lyrics_lines([{"text": "a b"}, {"text": "x y"}, {"text": "c d"}])
_mid_aligned = [
    {"words": [_t, _t], "text": "a b", "score": 0.8,
     "word_orig_starts": [1.0, 1.0], "word_orig_ends": [1.5, 1.5]},
    {"words": [_NS, _NS], "text": "x y", "score": None,
     "word_orig_starts": [0.0, 0.0], "word_orig_ends": [0.3, 0.3]},
    {"words": [{"start": 3.0, "end": 3.5, "score": 0.7}] * 2, "text": "c d", "score": 0.7,
     "word_orig_starts": [3.0, 3.0], "word_orig_ends": [3.5, 3.5]},
]
_mu, _ma, _mn = repair.drop_unsung_tail(_mid_units, _mid_aligned)
check(f"drop_unsung_tail: a line left out in the middle goes alone (got {[u['text'] for u in _mu]}, {_mn})",
      [u["text"] for u in _mu] == ["a b", "c d"] and len(_ma) == 2 and _ma[1]["text"] == "c d"
      and _mn == 2)

# align_units_globally(): the end may stay unsung without anchors; with
# anchors (set by an admin) the alignment stays forced
_ag_calls = []
_saved_em, _saved_awg = repair.ctc_align.emissions_for_audio, repair.ctc_align.align_words_globally
try:
    repair.ctc_align.emissions_for_audio = lambda *a, **k: "EM"
    repair.ctc_align.align_words_globally = (
        lambda words, em, meta, end_penalty=None, **filler:
        _ag_calls.append(end_penalty) or [(1.0, 1.5, 0.9), _NS])
    _ag_aligned, _ag_n = repair.align_units_globally(
        repair.parse_lyrics_lines([{"text": "a b"}]), "M", {}, [0.0] * 16000)
finally:
    repair.ctc_align.emissions_for_audio, repair.ctc_align.align_words_globally = _saved_em, _saved_awg
check(f"align_units_globally: aligns with LYRICS_END_SKIP_PENALTY (got {_ag_calls})",
      _ag_calls == [repair.LYRICS_END_SKIP_PENALTY] and repair.LYRICS_END_SKIP_PENALTY == 4.0)
check(f"align_units_globally: a left-out word is marked NOT_SUNG and not counted (got {_ag_aligned[0]['words']}, {_ag_n})",
      _ag_aligned[0]["words"][1] == _NS and _ag_n == 1)

# repair_txt_with_lyrics(): the precision pass and the notes get the lyrics
# without the unsung end
_wiring.clear()
_saved = {n: getattr(repair, n) for n in (
    "locate_audio", "prepare_processing_audio", "get_silence_sections",
    "load_audio_16k", "resolve_language", "load_aligner",
    "align_units_globally", "refine_global_alignment",
    "build_syllables_from_lyric_units", "write_lyrics_result")}


def _fake_align_tail(units, model, meta, audio16k, device="cpu", anchors=None, fillers_out=None,
                     line_skip=None):
    return [{"words": [_t] * len(u["words"]) if i == 0 else [_NS] * len(u["words"]),
             "text": u["text"], "score": 0.8 if i == 0 else None,
             "word_orig_starts": [1.0] * len(u["words"]),
             "word_orig_ends": [1.5] * len(u["words"])} for i, u in enumerate(units)], 2


try:
    repair.locate_audio = lambda song_dir, txt, work_dir: ("/a.wav", None)
    repair.prepare_processing_audio = lambda audio, work, device: ("/v.wav", "/p.wav")
    repair.get_silence_sections = lambda path, min_silence_len=0: []
    repair.load_audio_16k = lambda path: [0.0] * (16000 * 3)
    repair.resolve_language = lambda *a, **k: "en"
    repair.load_aligner = lambda lang, device: ("MODEL", "META", "en")
    repair.align_units_globally = _fake_align_tail
    repair.refine_global_alignment = _fake_refine
    repair.build_syllables_from_lyric_units = _fake_build
    repair.write_lyrics_result = _fake_write
    repair.repair_txt_with_lyrics(
        _WiringTxt(), repair.parse_lyrics_lines([{"text": "a b"}, {"text": "c d"}]),
        "/s", "/o", "cuda", tempfile.mkdtemp())
finally:
    for _n, _f in _saved.items():
        setattr(repair, _n, _f)
check(f"repair_txt_with_lyrics: the precision pass gets only the sung lines (got {_wiring.get('refine')})",
      _wiring.get("refine", (None,))[0] == 1)
check("repair_txt_with_lyrics: the notes are built from the sung lines only",
      len(_wiring["build"][0]) == 1)

# --------------------------------------------------------------------------
# determine_song_end(): song_end.txt wins, else outro detection (needs the
# recording length), else no cut
# --------------------------------------------------------------------------

import outro  # noqa: E402

_se_dir = tempfile.mkdtemp()
_saved_se = (repair.load_audio_16k, outro.find_outro_cut)
_se_calls = {}
try:
    repair.load_audio_16k = lambda path: _se_calls.setdefault("loaded", path) and [0.1] * 32000
    outro.find_outro_cut = lambda v, i, rec, sample_rate=16000: _se_calls.setdefault("rec", rec) and 1.25

    check("determine_song_end: the end from anchors.txt wins over everything",
          repair.determine_song_end(_se_dir, "/sep/vocals.wav", [0.0] * 32000, 90.0,
                                    anchor_end=1.75) == (1.75, "anchors.txt"))
    check("determine_song_end: no file and no recording length -> no cut",
          repair.determine_song_end(_se_dir, "/sep/vocals.wav", [0.0] * 32000, None) == (None, None))
    check("determine_song_end: the outro detection runs with the recording length "
          "on the instrumental next to the vocals",
          repair.determine_song_end(_se_dir, "/sep/vocals.wav", [0.0] * 32000, 90.0)
          == (1.25, "outro detection") and _se_calls.get("rec") == 90.0
          and _se_calls.get("loaded") == "/sep/no_vocals.wav")
    with open(os.path.join(_se_dir, "song_end.txt"), "w", encoding="utf-8") as _f:
        _f.write("0:01.5\n")
    check("determine_song_end: song_end.txt wins over the detection",
          repair.determine_song_end(_se_dir, "/sep/vocals.wav", [0.0] * 32000, 90.0)
          == (1.5, "song_end.txt"))
    with open(os.path.join(_se_dir, "song_end.txt"), "w", encoding="utf-8") as _f:
        _f.write("soon\n")
    check("determine_song_end: an unreadable song_end.txt falls back to the detection",
          repair.determine_song_end(_se_dir, "/sep/vocals.wav", [0.0] * 32000, 90.0)
          == (1.25, "outro detection"))
    with open(os.path.join(_se_dir, "song_end.txt"), "w", encoding="utf-8") as _f:
        _f.write("9:00\n")
    check("determine_song_end: an end at or after the audio end means no cut",
          repair.determine_song_end(_se_dir, "/sep/vocals.wav", [0.0] * 32000, None)
          == (None, None))
finally:
    repair.load_audio_16k, outro.find_outro_cut = _saved_se

# repair_txt_with_lyrics(): with a song end, only the song is aligned
_wiring.clear()
_saved = {n: getattr(repair, n) for n in (
    "locate_audio", "prepare_processing_audio", "get_silence_sections",
    "load_audio_16k", "resolve_language", "load_aligner",
    "align_units_globally", "refine_global_alignment",
    "build_syllables_from_lyric_units", "write_lyrics_result", "determine_song_end")}
try:
    repair.locate_audio = lambda song_dir, txt, work_dir: ("/a.wav", None)
    repair.prepare_processing_audio = lambda audio, work, device: ("/v.wav", "/p.wav")
    repair.get_silence_sections = lambda path, min_silence_len=0: [(1.0, 2.0)]
    repair.load_audio_16k = lambda path: [0.0] * (16000 * 3)
    repair.resolve_language = lambda *a, **k: "en"
    repair.load_aligner = lambda lang, device: ("MODEL", "META", "en")
    repair.align_units_globally = _fake_align
    repair.refine_global_alignment = _fake_refine
    repair.build_syllables_from_lyric_units = _fake_build
    repair.write_lyrics_result = _fake_write
    repair.determine_song_end = lambda song_dir, vocals, audio16k, rec, anchor_end=None: (
        _wiring.setdefault("end_args", (song_dir, vocals, rec)) and (1.5, "song_end.txt"))
    repair.repair_txt_with_lyrics(
        _WiringTxt(), repair.parse_lyrics_lines([{"text": "a b", "start": None}]),
        "/s", "/o", "cuda", tempfile.mkdtemp(), recording_seconds=200.0)
finally:
    for _n, _f in _saved.items():
        setattr(repair, _n, _f)

check("repair_txt_with_lyrics: asks for the song end with the song folder, the "
      f"vocals and the recording length (got {_wiring.get('end_args')})",
      _wiring.get("end_args") == ("/s", "/v.wav", 200.0))
check("repair_txt_with_lyrics: the aligner only sees the audio up to the song end",
      _wiring["align"][3] == 16000 * 1.5 and _wiring["refine"][1] == 16000 * 1.5)
check("repair_txt_with_lyrics: syllables and the note ceiling use the song end "
      "as the audio length",
      abs(_wiring["build"][3] - 1.5) < 1e-9 and abs(_wiring["write"][2] - 1.5) < 1e-9)

# write_lyrics_result(): song_end.txt travels with the song (like lyrics.txt)
_se_song = tempfile.mkdtemp()
_se_out = tempfile.mkdtemp()
with open(os.path.join(_se_song, "song_end.txt"), "w", encoding="utf-8") as _f:
    _f.write("# outro after this\n3:20\n")
with open(os.path.join(_se_song, "anchors.txt"), "w", encoding="utf-8") as _f:
    _f.write("end 3:20\n")
_saved_pitch = repair.get_pitch_with_file
try:
    repair.get_pitch_with_file = lambda path: _FakePitched([4] * 3, set())
    _se_path = repair.write_lyrics_result(
        _PitchTxt(), _se_song, _se_out, [[("a ", 0.0, 0.8), ("b ", 1.0, 1.8)]], "/p.wav",
        audio_dur=3.0)
finally:
    repair.get_pitch_with_file = _saved_pitch
_se_copy = os.path.join(os.path.dirname(_se_path), "song_end.txt")
check("write_lyrics_result: anchors.txt is copied into the output song folder",
      os.path.isfile(os.path.join(os.path.dirname(_se_path), "anchors.txt")))
check("write_lyrics_result: song_end.txt is copied into the output song folder",
      os.path.isfile(_se_copy) and open(_se_copy, encoding="utf-8").read()
      == "# outro after this\n3:20\n")

# pin_anchor_words(): refinement (40 ms lead, outlier moves) must not move a pin
_pin = [_unit([_w(0.76, 0.9), _w(1.0, 1.2)]), _unit([None, _w(2.0, 2.2)])]
repair.pin_anchor_words(_pin, [{"words": ["ab", "c"]}, {"words": ["abc", "d"]}],
                        [(0, 0.80), (1, 1.50)])
check("pin_anchor_words: a timed first word gets the anchor start back",
      abs(_pin[0]["words"][0]["start"] - 0.80) < 1e-9 and
      abs(_pin[0]["words"][0]["end"] - 0.9) < 1e-9)
check("pin_anchor_words: an untimed first word gets the anchor start and its "
      f"natural length (got {_pin[1]['words'][0]})",
      abs(_pin[1]["words"][0]["start"] - 1.50) < 1e-9 and
      abs(_pin[1]["words"][0]["end"] - 1.78) < 1e-9)

# repair_txt_with_lyrics(): anchors.txt pins lines and ends the song
_an_song = tempfile.mkdtemp()
with open(os.path.join(_an_song, "anchors.txt"), "w", encoding="utf-8") as _f:
    _f.write("0:01.2 | c d\nend 0:02.5\n")
_wiring.clear()
_saved = {n: getattr(repair, n) for n in (
    "locate_audio", "prepare_processing_audio", "get_silence_sections",
    "load_audio_16k", "resolve_language", "load_aligner",
    "align_units_globally", "refine_global_alignment",
    "build_syllables_from_lyric_units", "write_lyrics_result", "pin_anchor_words")}
try:
    repair.locate_audio = lambda song_dir, txt, work_dir: ("/a.wav", None)
    repair.prepare_processing_audio = lambda audio, work, device: ("/v.wav", "/p.wav")
    repair.get_silence_sections = lambda path, min_silence_len=0: []
    repair.load_audio_16k = lambda path: [0.0] * (16000 * 3)
    repair.resolve_language = lambda *a, **k: "en"
    repair.load_aligner = lambda lang, device: ("MODEL", "META", "en")
    repair.align_units_globally = _fake_align
    repair.refine_global_alignment = _fake_refine
    repair.build_syllables_from_lyric_units = _fake_build
    repair.write_lyrics_result = _fake_write
    repair.pin_anchor_words = lambda aligned, units, anchors: _wiring.setdefault(
        "pinned_after", (anchors, "refine" in _wiring))
    repair.repair_txt_with_lyrics(
        _WiringTxt(), repair.parse_lyrics_lines([{"text": "a b", "start": None},
                                                 {"text": "C, d!", "start": None}]),
        _an_song, "/o", "cuda", tempfile.mkdtemp())
finally:
    for _n, _f in _saved.items():
        setattr(repair, _n, _f)
check(f"repair_txt_with_lyrics: anchors.txt lines reach the aligner as (line, time) "
      f"(got {_wiring.get('anchors')})",
      _wiring.get("anchors") == [(1, 1.2)])
check("repair_txt_with_lyrics: the anchors.txt end cuts the audio",
      _wiring["align"][3] == 16000 * 2.5)
check("repair_txt_with_lyrics: the pins are applied again after the refinement",
      _wiring.get("pinned_after") == ([(1, 1.2)], True))

_an_bad = tempfile.mkdtemp()
with open(os.path.join(_an_bad, "anchors.txt"), "w", encoding="utf-8") as _f:
    _f.write("0:01.2 | not in the song\n")
_wiring.clear()
try:
    for _n, _f in {"locate_audio": lambda song_dir, txt, work_dir: ("/a.wav", None),
                   "prepare_processing_audio": lambda audio, work, device: ("/v.wav", "/p.wav"),
                   "get_silence_sections": lambda path, min_silence_len=0: [],
                   "load_audio_16k": lambda path: [0.0] * (16000 * 3),
                   "resolve_language": lambda *a, **k: "en",
                   "load_aligner": lambda lang, device: ("MODEL", "META", "en"),
                   "align_units_globally": _fake_align, "refine_global_alignment": _fake_refine,
                   "build_syllables_from_lyric_units": _fake_build,
                   "write_lyrics_result": _fake_write}.items():
        setattr(repair, _n, _f)
    repair.repair_txt_with_lyrics(
        _WiringTxt(), repair.parse_lyrics_lines([{"text": "a b", "start": None}]),
        _an_bad, "/o", "cuda", tempfile.mkdtemp())
finally:
    for _n, _f in _saved.items():
        setattr(repair, _n, _f)
check("repair_txt_with_lyrics: an anchors.txt line that is not in the lyrics means "
      "no anchors (the song is still aligned)",
      _wiring.get("anchors") is None and _wiring["align"][3] == 16000 * 3)

# main(): --recording-seconds reaches the lyrics rebuild
_main_calls = {}
_saved_main = {n: getattr(repair, n) for n in (
    "find_ultrastar_txts", "load_lyrics_file", "repair_txt_with_lyrics")}
_saved_argv = sys.argv
try:
    repair.find_ultrastar_txts = lambda song_dir: ["TXT"]
    repair.load_lyrics_file = lambda path: ("file", [{"text": "a b", "start": None}])
    repair.repair_txt_with_lyrics = lambda *a, **k: _main_calls.update(k) or {
        "output": "/o/x.txt", "aligned_words": 2, "total_words": 2}
    sys.argv = ["repair.py", "--song-dir", "/s", "--out", "/o", "--mode", "lyrics",
                "--lyrics-file", "/l.json", "--recording-seconds", "279.2"]
    try:
        repair.main()
    except SystemExit:
        pass
finally:
    sys.argv = _saved_argv
    for _n, _f in _saved_main.items():
        setattr(repair, _n, _f)
check(f"main: --recording-seconds is passed to the lyrics rebuild (got {_main_calls})",
      _main_calls.get("recording_seconds") == 279.2)

# main(): --fill-from-input hands the input txt's words (whisper's transcript
# for a new song) to the lyrics rebuild; without it nothing is filled
_fill_txt_dir = tempfile.mkdtemp()
_fill_txt_path = os.path.join(_fill_txt_dir, "song.txt")
with open(_fill_txt_path, "w", encoding="utf-8") as _f:
    _f.write("#TITLE:x\n#ARTIST:y\n#BPM:150\n#GAP:1000\n: 0 4 0 Hal\n: 4 4 0 lo \n- 10\n"
             ": 10 4 0 Welt\n: 14 4 2 ~ \nE\n")


class _PathTxt:
    path = _fill_txt_path


for _flag in (True, False):
    _main_calls = {}
    _saved_main = {n: getattr(repair, n) for n in (
        "find_ultrastar_txts", "load_lyrics_file", "repair_txt_with_lyrics")}
    _saved_argv = sys.argv
    try:
        repair.find_ultrastar_txts = lambda song_dir: [_PathTxt()]
        repair.load_lyrics_file = lambda path: ("file", [{"text": "a b", "start": None}])
        repair.repair_txt_with_lyrics = lambda *a, **k: _main_calls.update(k) or {
            "output": "/o/x.txt", "aligned_words": 2, "total_words": 2}
        sys.argv = ["repair.py", "--song-dir", "/s", "--out", "/o", "--mode", "lyrics",
                    "--lyrics-file", "/l.json"] + (["--fill-from-input"] if _flag else [])
        try:
            repair.main()
        except SystemExit:
            pass
    finally:
        sys.argv = _saved_argv
        for _n, _f in _saved_main.items():
            setattr(repair, _n, _f)
    if _flag:
        _ft = _main_calls.get("fill_transcript") or []
        check(f"main: --fill-from-input passes the input txt's words (got {_ft})",
              [w for _, _, w in _ft] == ["Hallo", "Welt"] and abs(_ft[0][0] - 1.0) < 1e-9
              and abs(_ft[1][0] - (1.0 + 10 * 0.1)) < 1e-9)
    else:
        check(f"main: without --fill-from-input nothing is filled (got {_main_calls.get('fill_transcript')})",
              _main_calls.get("fill_transcript") is None)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
