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


_g_units = repair.parse_lyrics_lines([
    {"text": "ab c", "start": None},
    {"text": "... a", "start": None},
])
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

# a GPU that cannot hold the model must not lose the song: retry on CPU
_g_aligned2, _g_n2 = repair.align_units_globally(
    _g_units, _PlannedModel(_planned_logits(60, _g_plan), fail_on_cuda=True),
    _GMETA, _g_audio, device="cuda")
check("align_units_globally: falls back to the CPU when the GPU run fails",
      _g_n2 == 3 and abs(_g_aligned2[0]["words"][0]["start"] - 0.20) < 1e-9)

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
    "align_units_globally", "build_syllables_from_lyric_units",
    "write_lyrics_result")}


def _fake_align(units, model, meta, audio16k, device="cpu"):
    _wiring["align"] = (units, model, meta, len(audio16k), device)
    return [{"words": [{"start": 0.0, "end": 1.0}] * len(u["words"]),
             "text": u["text"], "score": 0.5,
             "word_orig_starts": [0.0] * len(u["words"]),
             "word_orig_ends": [1.0] * len(u["words"])} for u in units], 5


def _fake_build(units, aligned, lang, silence_sections=None, audio_dur=None):
    _wiring["build"] = (aligned, lang, silence_sections, audio_dur)
    return [[("w ", 0.0, 1.0)]]


def _fake_write(txt, song_dir, out_dir, per_unit, processing_audio, audio_dur=None):
    _wiring["write"] = (per_unit, processing_audio, audio_dur)
    return "/out/song.txt"


try:
    repair.locate_audio = lambda song_dir, txt, work_dir: ("/a.wav", None)
    repair.prepare_processing_audio = lambda audio, work, device: ("/v.wav", "/p.wav")
    repair.get_silence_sections = lambda path, min_silence_len=0: [(1.0, 2.0)]
    repair.load_audio_16k = lambda path: [0.0] * (16000 * 3)
    repair.resolve_language = lambda *a, **k: "en"
    repair.load_aligner = lambda lang, device: ("MODEL", "META", "en")
    repair.align_units_globally = _fake_align
    repair.build_syllables_from_lyric_units = _fake_build
    repair.write_lyrics_result = _fake_write
    _wiring_result = repair.repair_txt_with_lyrics(
        _WiringTxt(), repair.parse_lyrics_lines([{"text": "a b", "start": None}]),
        "/s", "/o", "cuda", tempfile.mkdtemp())
finally:
    for _n, _f in _saved.items():
        setattr(repair, _n, _f)

check("repair_txt_with_lyrics: aligns every unit with one align_units_globally "
      f"call on the loaded aligner and the separated vocals (got {_wiring.get('align', ('',) * 5)[1:]})",
      _wiring["align"][1:] == ("MODEL", "META", 16000 * 3, "cuda"))
check("repair_txt_with_lyrics: build_syllables gets the global alignment, "
      "the detected silence and the real audio length",
      _wiring["build"][0][0]["score"] == 0.5 and
      _wiring["build"][2] == [(1.0, 2.0)] and abs(_wiring["build"][3] - 3.0) < 1e-9)
check("repair_txt_with_lyrics: the ceiling clamp still gets the audio length",
      abs(_wiring["write"][2] - 3.0) < 1e-9)
check("repair_txt_with_lyrics: returns output path and word counts",
      _wiring_result == {"output": "/out/song.txt", "aligned_words": 5, "total_words": 2})

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
