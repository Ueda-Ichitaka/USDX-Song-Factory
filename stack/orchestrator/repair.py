#!/usr/bin/env python3
"""Repair existing UltraStar songs.

Fixes two common defects of existing UltraStar songs:

  1. wrong #GAP - the initial offset between the start of the audio file
     and the first sung lyrics (mode: "gap")
  2. lyrics drifting out of sync mid-song - text and audio become
     asynchronous over time (mode: "sync", default)

Both modes keep the ORIGINAL lyrics/syllables of the song. Nothing is
re-transcribed; instead the known lyrics are force-aligned to the vocals
of the audio (wav2vec2 alignment via whisperx).

Modes
-----
gap   (cheap)   No track separation. Aligns the first lyric lines directly
                against the original audio, measures where the first words
                really start and shifts #GAP accordingly. Beats, durations
                and pitches are kept as-is.

sync  (default) Full repair: separates vocals (demucs), force-aligns every
                lyric line progressively (so cumulative drift is corrected),
                rebuilds all beats/durations, re-detects the pitch of every
                note (SwiftF0) and rewrites #GAP. Line breaks are kept.

If the lyrics do not match the audio at all (wrong song version, missing
verses - detected via alignment quality), "sync" falls back to a full
re-creation of the song with UltraSinger (REPAIR_FALLBACK=0 disables the
fallback and fails instead).

The repaired song folder (a full copy incl. audio/video/cover) is written to
<out>/<song folder name>/. The original input folder is never modified.

For every processed txt a final line `REPAIR_DONE ...` is printed for the
orchestrator to parse.
"""

import argparse
import copy
import json
import os
import re
import shutil
import sys

sys.path.insert(0, "/app/UltraSinger/src")

from modules.Audio.convert_audio import convert_audio_to_mono_wav  # noqa: E402
from modules.Audio.denoise import denoise_vocal_audio  # noqa: E402
from modules.Audio.separation import DemucsModel  # noqa: E402
from modules.Audio.separation import separate_vocal_from_audio  # noqa: E402
from modules.Audio.silence_processing import mute_no_singing_parts  # noqa: E402
from modules.Midi.midi_creator import create_midi_note_from_pitched_data  # noqa: E402
from modules.Pitcher.pitcher import get_pitch_with_file  # noqa: E402
from modules.console_colors import (  # noqa: E402
    ULTRASINGER_HEAD,
    blue_highlighted,
    bright_green_highlighted,
    cyan_highlighted,
    gold_highlighted,
    green_highlighted,
    red_highlighted,
)

NOTE_LINE_TYPES = (":", "F", "R", "G", "*")
# type beat dur pitch [1 separator space] raw-word-field (verbatim)
NOTE_LINE_RE = re.compile(
    r"^\s*([:FRG*])[ \t]+(-?[0-9.,]+)[ \t]+(-?[0-9.,]+)[ \t]+(-?[0-9.,]+)[ \t]?(.*)$")
ALIGN_PAD = float(os.environ.get("REPAIR_ALIGN_PAD", "6.0"))
MIN_WORD_SCORE = float(os.environ.get("REPAIR_MIN_WORD_SCORE", "0.6"))
MAX_LOCAL_DEVIATION = float(os.environ.get("REPAIR_MAX_LOCAL_DEVIATION", "3.0"))
# resource-budget auto-selection (or an explicit override) picks this in
# orchestrator.py and passes it through - see resource_profile.py
DEMUCS_MODEL = DemucsModel(os.environ.get("DEMUCS_MODEL", "htdemucs") or "htdemucs")


# --------------------------------------------------------------------------
# txt parsing
# --------------------------------------------------------------------------

def _to_float(value) -> float:
    return float(str(value).strip().replace(",", "."))


def _fmt_num(value) -> str:
    """Print floats without trailing .0 (keeps original txt look)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class Note:
    __slots__ = ("type", "beat", "dur", "pitch", "word")

    def __init__(self, type_, beat, dur, pitch, word):
        self.type = type_
        self.beat = beat
        self.dur = dur
        self.pitch = pitch
        self.word = word  # raw word field incl. leading/trailing spaces / '~'

    @property
    def syllable(self) -> str:
        """Word field without trailing spaces (leading spaces may be
        word-boundary markers and are handled by has_leading_space)."""
        return self.word.rstrip()

    @property
    def has_leading_space(self) -> bool:
        # some songs mark a new word with a LEADING space on its first syllable
        return self.word != self.word.lstrip()

    @property
    def is_word_end(self) -> bool:
        # other songs mark the end of a word with a TRAILING space
        return self.word != self.word.rstrip()

    @property
    def is_hold(self) -> bool:
        return self.syllable.strip("~ ") == ""


class Txt:
    def __init__(self, path):
        self.path = path
        self.lines = []        # list of dicts describing every original line
        self.notes = []        # flat list of Note (in file order)
        self.gap_ms = 0.0      # a missing #GAP tag means gap 0
        self.bpm_tag = None
        self.audio_ref = None
        self.video_ref = None
        self.language = None
        self._parse()

    def _parse(self):
        with open(self.path, encoding="utf-8", errors="replace") as f:
            raw = f.read().splitlines()
        self.lines = [{"raw": l} for l in raw]

        for line in self.lines:
            stripped = line["raw"].strip()
            if stripped.startswith("#"):
                if stripped.startswith("#GAP:"):
                    self.gap_ms = _to_float(stripped[5:])
                elif stripped.startswith("#BPM:"):
                    self.bpm_tag = _to_float(stripped[5:])
                elif stripped.startswith("#MP3:"):
                    self.audio_ref = stripped[5:].strip()
                elif stripped.startswith("#AUDIO:"):
                    self.audio_ref = self.audio_ref or stripped[7:].strip()
                elif stripped.startswith("#VIDEO:"):
                    self.video_ref = stripped[7:].strip()
                elif stripped.startswith("#LANGUAGE:"):
                    self.language = stripped[10:].strip()
                continue
            m = NOTE_LINE_RE.match(line["raw"])
            if m:
                try:
                    note = Note(m.group(1), _to_float(m.group(2)),
                                _to_float(m.group(3)), int(_to_float(m.group(4))),
                                m.group(5) or "")
                except ValueError:
                    continue
                line["note"] = note
                self.notes.append(note)
            elif stripped.startswith("-") and len(stripped.split()) >= 2:
                try:
                    line["break_beat"] = _to_float(stripped.split()[1])
                except ValueError:
                    pass

    @property
    def real_bpm(self) -> float:
        # the txt BPM tag is a fourth of the real BPM
        return (self.bpm_tag or 0) * 4.0

    @property
    def gap_s(self) -> float:
        return (self.gap_ms or 0) / 1000.0

    def beat_to_sec(self, beat: float) -> float:
        return beat * 60.0 / self.real_bpm if self.real_bpm else 0.0

    def note_start_sec(self, note: Note) -> float:
        return self.gap_s + self.beat_to_sec(note.beat)

    def note_end_sec(self, note: Note) -> float:
        return self.gap_s + self.beat_to_sec(note.beat + note.dur)

    def lyric_lines(self) -> list:
        """Group notes into lyric lines, split at '-' linebreaks.

        Returns a list of dicts: {"notes": [Note, ...], "break_line": idx|None}
        """
        result = []
        current = []
        for idx, entry in enumerate(self.lines):
            if "note" in entry:
                current.append(entry["note"])
            elif "break_beat" in entry and current:
                result.append({"notes": current, "break_line": idx})
                current = []
        if current:
            result.append({"notes": current, "break_line": None})
        return result


def find_ultrastar_txts(song_dir: str) -> list:
    txts = []
    for name in sorted(os.listdir(song_dir)):
        path = os.path.join(song_dir, name)
        if not name.lower().endswith(".txt") or not os.path.isfile(path):
            continue
        try:
            txt = Txt(path)
        except OSError:
            continue
        if txt.bpm_tag and txt.notes:
            txts.append(txt)
    return txts


# --------------------------------------------------------------------------
# word / unit structure helpers
# --------------------------------------------------------------------------

def starts_new_word(notes, i: int) -> bool:
    """True if a word boundary follows note index i within the line.

    Handles both conventions found in the wild:
      * trailing space on the last syllable of a word  ("herrsch " style)
      * leading space on the first syllable of the next word (" zu" style)
    """
    if i >= len(notes) - 1:
        return True  # end of line ends the word
    if notes[i].is_word_end:
        return True
    if notes[i + 1].has_leading_space:
        return True
    return False


def group_words(notes) -> list:
    """Group note indices into words. Returns list of list[note_idx]."""
    words = []
    current = []
    for i in range(len(notes)):
        current.append(i)
        if starts_new_word(notes, i):
            words.append(current)
            current = []
    if current:
        words.append(current)
    return words


def word_text(notes, group) -> str:
    return "".join(
        notes[i].syllable.strip().replace("~", "")
        for i in group if not notes[i].is_hold)


def line_text(notes) -> str:
    """Build the aligner text for one lyric line / unit.

    * hold syllables ("~") contribute nothing to the text
    * '~' chars inside syllables are stripped (visual hold markers)
    * words are separated by single spaces (both boundary conventions)
    * if the notes have no word-boundary markers at all, everything becomes
      one word (better than exploding every syllable)
    """
    parts = []
    for group in group_words(notes):
        w = word_text(notes, group)
        if w:
            parts.append(w)
    return " ".join(parts)


def text_word_groups(notes) -> list:
    """Word groups that contribute text to the aligner (hold-only groups are
    aligned via interpolation instead)."""
    return [g for g in group_words(notes) if word_text(notes, g)]


def build_alignment_units(txt, lines, min_words=8, max_gap=8.0) -> list:
    """Merge lyric lines into alignment units.

    Aligning very short lines standalone is unreliable (they are often
    fragments of a longer sung phrase, e.g. "Deine Haut" / "und Stolz
    bleibt mir..."), so lines are merged until a unit has at least
    `min_words` words. Lines whose last word continues into the next line
    (mid-word line splits) are always merged.

    Units are split again at large original gaps (`max_gap`, e.g. a 30s
    instrumental break): a text spanning such a gap cannot be aligned as
    one block (the aligner squishes it into the first seconds).

    This only affects ALIGNMENT - the repaired txt keeps the original
    line breaks.
    """
    units = []
    cur = None
    for li, line in enumerate(lines):
        if cur is not None and cur["notes"] and line["notes"]:
            gap = (txt.note_start_sec(line["notes"][0])
                   - txt.note_end_sec(cur["notes"][-1]))
            if gap > max_gap:
                units.append(cur)
                cur = None
        if cur is None:
            cur = {"notes": [], "line_idxs": []}
        cur["notes"].extend(line["notes"])
        cur["line_idxs"].append(li)
        last = cur["notes"][-1]
        # a word only continues into the next line if NEITHER boundary
        # convention marks it as ended (trailing space on this note, or
        # leading space on the next line's first note - see starts_new_word)
        next_note = None
        if li < len(lines) - 1 and lines[li + 1]["notes"]:
            next_note = lines[li + 1]["notes"][0]
        word_ended = last.is_word_end or (
            next_note is not None and next_note.has_leading_space)
        mid_word = not word_ended and li < len(lines) - 1
        if mid_word:
            continue
        if len(group_words(cur["notes"])) >= min_words or li == len(lines) - 1:
            units.append(cur)
            cur = None
    if cur is not None:
        units.append(cur)
    return units


# --------------------------------------------------------------------------
# audio preparation
# --------------------------------------------------------------------------

def locate_audio(song_dir: str, txt: Txt, work_dir: str):
    """Return path of the audio file used for alignment/pitching."""
    if txt.audio_ref:
        candidate = os.path.join(song_dir, txt.audio_ref)
        if os.path.isfile(candidate):
            return candidate, None
        candidate2 = os.path.join(song_dir, os.path.basename(txt.audio_ref))
        if os.path.isfile(candidate2):
            return candidate2, None
    if txt.video_ref:
        video = os.path.join(song_dir, txt.video_ref)
        if os.path.isfile(video):
            ext = ".m4a" if video.lower().endswith(".mp4") else ".audio"
            audio_out = os.path.join(work_dir, os.path.basename(video) + ext)
            if not os.path.isfile(audio_out):
                import ffmpeg  # ffmpeg-python
                print(f"{ULTRASINGER_HEAD} extracting audio from {video}")
                try:
                    # stream copy: fast, no quality loss - works whenever
                    # the video's audio codec (usually AAC) is valid inside
                    # an m4a/mp4 container. vn is a bare flag (no value) -
                    # ffmpeg-python only omits the value for kwargs=None;
                    # vn=1/True was a real, longstanding bug here (emitted
                    # "-vn 1", which ffmpeg parsed as a second, bogus
                    # output target "1" and always failed - regardless of
                    # video source - "Unable to find a suitable output
                    # format for '1'")
                    (
                        ffmpeg.input(video)
                        .output(audio_out, vn=None, acodec="copy")
                        .overwrite_output()
                        .run(capture_stdout=True, capture_stderr=True)
                    )
                except ffmpeg.Error as copy_exc:
                    # some sources (e.g. yt-dlp's bestaudio merged into an
                    # mp4 container) carry Opus audio, which the m4a/mp4
                    # muxer cannot hold via stream copy ("Could not find
                    # tag for codec opus in stream ..., codec not
                    # currently supported in container") - fall back to a
                    # real transcode into WAV (codec-agnostic: ffmpeg can
                    # always decode the source and re-encode to PCM,
                    # regardless of what codec it started as)
                    wav_out = os.path.join(
                        work_dir, os.path.basename(video) + ".wav")
                    try:
                        (
                            ffmpeg.input(video)
                            .output(wav_out, vn=None)
                            .overwrite_output()
                            .run(capture_stdout=True, capture_stderr=True)
                        )
                    except ffmpeg.Error:
                        return None, ("audio extraction failed (stream copy: "
                                      f"{copy_exc.stderr[-300:]})")
                    audio_out = wav_out
            return audio_out, None
    return None, "no audio file found (#MP3/#AUDIO missing and no usable #VIDEO)"


def prepare_processing_audio(audio_path: str, work_dir: str, device: str):
    """Separate vocals + denoise + mono + mute (mirrors UltraSinger's pipeline).

    Returns (vocals_path, muted_path):
      * vocals_path - raw separated vocals, used for lyric alignment
        (wav2vec2 needs the continuous signal; denoise/mute would destroy it)
      * muted_path  - denoised/muted vocal audio, used for pitch detection
    """
    basename = os.path.splitext(os.path.basename(audio_path))[0]
    cache = os.path.join(work_dir, "cache")
    os.makedirs(cache, exist_ok=True)

    separation_path = separate_vocal_from_audio(
        cache, audio_path, True, True, device, DEMUCS_MODEL, False)
    vocals = os.path.join(separation_path, "vocals.wav")

    denoised = os.path.join(cache, basename + "_denoised.wav")
    denoise_vocal_audio(vocals, denoised, False)

    mono = os.path.join(cache, basename + "_mono.wav")
    convert_audio_to_mono_wav(denoised, mono)

    muted = os.path.join(cache, basename + "_mute.wav")
    mute_no_singing_parts(mono, muted)
    return vocals, muted


# --------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------

def load_audio_16k(path: str):
    import whisperx
    return whisperx.load_audio(path)


def detect_language(audio_path: str) -> str:
    """Detect the singing language with whisper 'tiny' (first 60 s)."""
    import whisperx
    print(f"{ULTRASINGER_HEAD} detecting language with whisper tiny")
    model = whisperx.load_model("tiny", "cpu", compute_type="int8")
    audio = load_audio_16k(audio_path)[: 60 * 16000]
    result = model.transcribe(audio, batch_size=4)
    lang = result.get("language", "en")
    print(f"{ULTRASINGER_HEAD} detected language: {blue_highlighted(lang)}")
    return lang


def resolve_language(txt: Txt, audio_path: str, forced: str = None) -> str:
    if forced:
        print(f"{ULTRASINGER_HEAD} using forced language: "
              f"{blue_highlighted(forced)}")
        return forced
    if txt.language:
        try:
            import langcodes
            lang = langcodes.find(txt.language).language
            print(f"{ULTRASINGER_HEAD} language from txt "
                  f"({txt.language}): {blue_highlighted(lang)}")
            return lang
        except Exception as exc:  # noqa: BLE001
            print(f"{ULTRASINGER_HEAD} {red_highlighted('could not parse language tag')} "
                  f"({txt.language}): {exc}")
    return detect_language(audio_path)


def load_aligner(language: str, device: str = "cpu"):
    import whisperx
    try:
        model, meta = whisperx.load_align_model(language_code=language,
                                                device=device)
        return model, meta, language
    except ValueError:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('no aligner for language')} "
              f"{blue_highlighted(language)} - falling back to 'en'")
        model, meta = whisperx.load_align_model(language_code="en", device=device)
        return model, meta, "en"


def align_line(model, meta, audio16k, text: str, t1: float, t2: float,
               device="cpu"):
    """Align one text inside the window [t1, t2]."""
    import whisperx
    if not text or t2 - t1 < 0.2:
        return []
    result = whisperx.align(
        [{"start": max(0.0, t1), "end": t2, "text": text}],
        model, meta, audio16k, device, return_char_alignments=False)
    return result.get("word_segments", [])


def compute_orig_word_durs(txt, notes, groups) -> list:
    """Original (txt) duration of each word group.

    Used to judge whether an aligned word's duration is plausible - must be
    the REAL per-word duration, a placeholder here silently disables the
    smear/squish checks in clean_aligned_words.
    """
    durs = []
    for g in groups:
        first = notes[g[0]]
        last = notes[g[-1]]
        durs.append(max(0.01, txt.note_end_sec(last) - txt.note_start_sec(first)))
    return durs


def clean_aligned_words(words, groups, orig_word_durs, text, audio_dur,
                        last_end, orig_word_starts=None, orig_word_ends=None):
    """Filter/normalize aligned words for one unit.

    Returns (cleaned, new_last_end, score_sum, score_n):
      cleaned - list with one entry per text-word-group (dict or None)

    Words are dropped when their duration is implausible vs. the original,
    when they are squished, or when they are placed far too early relative
    to the previous word (inter-word gap much smaller than in the txt,
    e.g. when the aligner skips a long instrumental gap).
    """
    cleaned = [None] * len(groups)
    score_sum = 0.0
    score_n = 0
    prev_aligned_end = None
    prev_orig_end = None
    for wi, w in enumerate(words):
        if wi >= len(groups):
            break
        start = w.get("start")
        end = w.get("end")
        if start is None or end is None:
            prev_aligned_end = None
            prev_orig_end = None
            continue  # unalignable word: stays None -> interpolation
        start = max(0.0, start)
        end = max(end, start + 0.03)
        od = orig_word_durs[wi]
        word_repr = w.get("word", "") or text[:20]
        dropped = False
        if end - start > max(3.0 * od, od + 2.0):
            print(f"{ULTRASINGER_HEAD} {red_highlighted('implausible alignment for')} "
                  f"{word_repr!r} ({end - start:.1f}s vs {od:.1f}s) "
                  "- dropping, will interpolate")
            dropped = True
        elif end - start < 0.25 * od and end - start < 0.1:
            dropped = True  # squished word: stays None -> interpolation
        elif (prev_aligned_end is not None and orig_word_starts is not None
              and wi > 0 and orig_word_ends is not None):
            aligned_gap = start - prev_aligned_end
            orig_gap = orig_word_starts[wi] - prev_orig_end
            if orig_gap > 3.0 and aligned_gap < orig_gap - max(3.0, 0.6 * orig_gap):
                print(f"{ULTRASINGER_HEAD} {red_highlighted('implausible word gap:')} "
                      f"{word_repr!r} ({aligned_gap:.1f}s vs {orig_gap:.1f}s in txt) "
                      "- dropping, will interpolate")
                dropped = True
        if dropped:
            prev_aligned_end = None
            prev_orig_end = None
            continue
        if start < last_end - 0.001:
            start = last_end
            end = max(end, start + 0.03)
        if end > audio_dur:
            end = audio_dur
            if end - start < 0.02:
                prev_aligned_end = None
                prev_orig_end = None
                continue
        cleaned[wi] = {"start": start, "end": end}
        if w.get("score") is not None:
            score_sum += w["score"]
            score_n += 1
        last_end = end
        prev_aligned_end = end
        prev_orig_end = orig_word_ends[wi] if orig_word_ends else None
    return cleaned, last_end, score_sum, score_n


def progressively_align(txt, units, model, meta, audio16k, audio_dur):
    """Force-align all units with progressive anchored windows.

    Each unit is searched relative to where the PREVIOUS unit actually
    ended (plus padding) - this corrects cumulative mid-song drift. The
    window start is floored at the original position minus ALIGN_PAD so a
    single bad unit cannot cascade.

    Returns a list (per unit) of dicts:
      {"words": [timing-dict | None, ...], "text": str, "score": float|None}
    (positional: index i belongs to text-word-group i of that unit)
    """
    results = []
    expected = None
    last_end = 0.0
    for unit in units:
        notes = unit["notes"]
        text = line_text(notes)
        orig_start = txt.note_start_sec(notes[0]) if notes else 0.0
        orig_dur = (txt.note_end_sec(notes[-1]) - orig_start) if notes else 0.0
        if expected is None:
            expected = orig_start

        t1 = max(0.0, expected - 1.5, orig_start - ALIGN_PAD)
        t2 = max(t1 + 0.5, expected + orig_dur + 1.0,
                 orig_start + orig_dur + ALIGN_PAD)
        t2 = min(t2, audio_dur - 0.05)

        words = align_line(model, meta, audio16k, text, t1, t2)

        groups = text_word_groups(notes)
        orig_word_durs = compute_orig_word_durs(txt, notes, groups)
        orig_word_starts = [txt.note_start_sec(notes[g[0]]) for g in groups]
        orig_word_ends = [txt.note_end_sec(notes[g[-1]]) for g in groups]

        cleaned, last_end, score_sum, score_n = clean_aligned_words(
            words, groups, orig_word_durs, text, audio_dur, last_end,
            orig_word_starts, orig_word_ends)

        timed = [c for c in cleaned if c]
        if timed:
            span = timed[-1]["end"] - timed[0]["start"]
            if span < 0.35 * orig_dur or span > 3.5 * orig_dur + 3.0:
                # whole unit squished or smeared -> unusable
                print(f"{ULTRASINGER_HEAD} {red_highlighted('implausible span:')} "
                      f"{text[:40]!r} ({span:.1f}s vs {orig_dur:.1f}s) "
                      "- keeping original timing")
                cleaned = [None] * len(groups)
                timed = []
        if timed:
            expected = timed[-1]["end"]
        else:
            print(f"{ULTRASINGER_HEAD} {red_highlighted('could not align:')} "
                  f"{text[:40]!r} - keeping original timing")
            expected = t1 + max(orig_dur, 1.0)

        avg_score = (score_sum / score_n) if score_n else None
        results.append({"words": cleaned, "text": text, "score": avg_score})
    return results


def implied_gap_samples(txt, units, aligned_units):
    """Per-word implied gaps: aligned_start - beat_time(note.beat).

    For a constant-offset error all samples are equal; deviations show
    desync or bad alignments.
    """
    samples = []
    base = 0
    for unit, aligned in zip(units, aligned_units):
        notes = unit["notes"]
        all_groups = group_words(notes)
        text_pos = {}
        for gi, g in enumerate(all_groups):
            if word_text(notes, g):
                text_pos[gi] = len(text_pos)
        timings = aligned["words"]
        for gi, g in enumerate(all_groups):
            ti = text_pos.get(gi)
            timing = timings[ti] if ti is not None and ti < len(timings) else None
            if timing:
                note = txt.notes[base + g[0]]
                samples.append(timing["start"] - txt.beat_to_sec(note.beat))
        base += len(notes)
    return samples


def refine_first_units(txt, units, aligned_units, model, meta, audio16k,
                       audio_dur, med_gap):
    """Re-align the first units with tight windows if they deviate from the
    song-wide median offset (their initial windows were the widest and most
    prone to locking onto the wrong position)."""
    for ui in range(min(2, len(units))):
        unit = units[ui]
        notes = unit["notes"]
        samples = implied_gap_samples(txt, [unit], [aligned_units[ui]])
        if not samples:
            continue
        if abs(samples[0] - med_gap) < 2.0:
            continue  # consistent, nothing to do
        orig_start = txt.note_start_sec(notes[0])
        orig_dur = txt.note_end_sec(notes[-1]) - orig_start
        predicted = med_gap + txt.beat_to_sec(notes[0].beat)
        t1 = max(0.0, predicted - 3.0)
        t2 = min(audio_dur - 0.05, max(t1 + 0.5, predicted + orig_dur + 3.0))
        text = line_text(notes)
        words = align_line(model, meta, audio16k, text, t1, t2)
        groups = text_word_groups(notes)
        orig_word_durs = compute_orig_word_durs(txt, notes, groups)
        cleaned, _, _, _ = clean_aligned_words(
            words, groups, orig_word_durs, text, audio_dur, 0.0)
        timed = [c for c in cleaned if c]
        if not timed:
            continue
        span = timed[-1]["end"] - timed[0]["start"]
        if span < 0.35 * orig_dur or span > 3.5 * orig_dur + 3.0:
            continue  # re-alignment also implausible: keep the original one
        if abs((timed[0]["start"] - txt.beat_to_sec(notes[0].beat))
               - med_gap) < abs(samples[0] - med_gap):
            print(f"{ULTRASINGER_HEAD} re-anchored unit {ui + 1} "
                  f"({samples[0] - med_gap:+.1f}s off) via median offset")
            aligned_units[ui]["words"] = cleaned

def reanchor_outlier_units(txt, units, aligned_units, model, meta, audio16k,
                           audio_dur):
    """Fix units that locked onto the wrong position (e.g. the wrong chorus).

    A unit whose implied gap deviates strongly from the running consensus of
    the preceding units is re-aligned at the consensus-predicted position.
    The re-anchor is only accepted when the consensus position aligns at
    least as well as the original one - this keeps legitimate mid-song jumps
    (txt missing a verse, audio has 30s more content) intact, because there
    the consensus position simply does not contain the lyrics.
    """
    import statistics

    def unit_gap(ui):
        unit = units[ui]
        notes = unit["notes"]
        groups = group_words(notes)
        for gi, g in enumerate(groups):
            if not word_text(notes, g):
                continue
            timing = aligned_units[ui]["words"][gi] \
                if gi < len(aligned_units[ui]["words"]) else None
            if timing:
                return timing["start"] - txt.beat_to_sec(notes[g[0]].beat)
        return None

    for ui in range(len(units)):
        gap_i = unit_gap(ui)
        if gap_i is None:
            continue
        history = [unit_gap(j) for j in range(max(0, ui - 10), ui)]
        history = [h for h in history if h is not None]
        if len(history) < 3:
            continue
        med = statistics.median(history)
        if abs(gap_i - med) <= 4.0:
            continue

        unit = units[ui]
        notes = unit["notes"]
        orig_start = txt.note_start_sec(notes[0])
        orig_dur = txt.note_end_sec(notes[-1]) - orig_start
        predicted = med + txt.beat_to_sec(notes[0].beat)
        t1 = max(0.0, predicted - 3.0)
        t2 = min(audio_dur - 0.05, max(t1 + 0.5, predicted + orig_dur + 3.0))
        text = line_text(notes)
        words = align_line(model, meta, audio16k, text, t1, t2)
        groups = text_word_groups(notes)
        orig_word_durs = compute_orig_word_durs(txt, notes, groups)
        cleaned, _, score_sum, score_n = clean_aligned_words(
            words, groups, orig_word_durs, text, audio_dur, 0.0,
            [txt.note_start_sec(notes[g[0]]) for g in groups],
            [txt.note_end_sec(notes[g[-1]]) for g in groups])
        timed = [c for c in cleaned if c]
        if not timed:
            continue  # consensus position has no match -> keep original
        span = timed[-1]["end"] - timed[0]["start"]
        if span < 0.35 * orig_dur or span > 3.5 * orig_dur + 3.0:
            continue
        new_gap = timed[0]["start"] - txt.beat_to_sec(notes[0].beat)
        if abs(new_gap - med) >= abs(gap_i - med):
            continue  # did not get closer to the consensus
        old_score = aligned_units[ui]["score"] or 0.0
        new_score = (score_sum / score_n) if score_n else 0.0
        if new_score < old_score - 0.05:
            continue  # consensus position matches much worse -> real jump
        print(f"{ULTRASINGER_HEAD} re-anchored unit {ui + 1} "
              f"({gap_i - med:+.1f}s off consensus, score "
              f"{old_score:.2f} -> {new_score:.2f})")
        aligned_units[ui]["words"] = cleaned
        aligned_units[ui]["score"] = new_score or None


def rescue_dropped_units(txt, units, aligned_units, model, meta, audio16k,
                         audio_dur):
    """Retry units that could not be aligned, anchored at their ORIGINAL txt
    position (the progressive anchor can be far off after long instrumental
    gaps, which makes the first attempt fail in an over-wide window).

    Rescues are chained: a successfully rescued unit tightens the search
    window for the next dropped unit."""
    prev_end = None
    for ui, (unit, aligned) in enumerate(zip(units, aligned_units)):
        notes = unit["notes"]
        timed = [w for w in aligned["words"] if w]
        if timed:
            prev_end = timed[-1]["end"]
            continue
        orig_start = txt.note_start_sec(notes[0])
        orig_dur = txt.note_end_sec(notes[-1]) - orig_start
        if prev_end is not None:
            t1 = max(0.0, prev_end - 1.5, orig_start - 12.0)
            t2 = max(t1 + 0.5, prev_end + orig_dur + 1.0,
                     orig_start + orig_dur + 6.0)
        else:
            t1 = max(0.0, orig_start - 12.0)
            t2 = max(t1 + 0.5, orig_start + orig_dur + 6.0)
        t2 = min(t2, audio_dur - 0.05)
        text = line_text(notes)
        words = align_line(model, meta, audio16k, text, t1, t2)
        groups = text_word_groups(notes)
        orig_word_durs = compute_orig_word_durs(txt, notes, groups)
        cleaned, _, _, _ = clean_aligned_words(
            words, groups, orig_word_durs, text, audio_dur, 0.0,
            [txt.note_start_sec(notes[g[0]]) for g in groups],
            [txt.note_end_sec(notes[g[-1]]) for g in groups])
        timed = [c for c in cleaned if c]
        if not timed:
            continue
        span = timed[-1]["end"] - timed[0]["start"]
        if span < 0.35 * orig_dur or span > 3.5 * orig_dur + 3.0:
            continue
        print(f"{ULTRASINGER_HEAD} rescued unit {ui + 1} via original position "
              f"({text[:40]!r})")
        aligned_units[ui]["words"] = cleaned
        prev_end = timed[-1]["end"]


def detect_lyrics_mismatch(txt, units, aligned_units):
    """Heuristic detection whether the txt lyrics match the audio at all.

    A unit is suspicious when it
      * could not be aligned (dropped words / implausible span),
      * has a low average word alignment score, or
      * deviates strongly from the *local* median offset of its neighbours
        (catches lyrics locking onto the wrong chorus etc.).

    Returns (is_mismatch, suspicious_fraction, detail_string).
    """
    # per-unit implied gap of the first timed word
    starts = []
    for unit, aligned in zip(units, aligned_units):
        notes = unit["notes"]
        s = None
        groups = group_words(notes)
        for gi, g in enumerate(groups):
            if not word_text(notes, g):
                continue
            timing = aligned["words"][gi] if gi < len(aligned["words"]) else None
            if timing:
                first_note = notes[g[0]]
                s = timing["start"] - txt.beat_to_sec(first_note.beat)
                break
        starts.append(s)

    n_suspicious = 0
    reasons = {"dropped": 0, "low_score": 0, "offset": 0}
    for i, (unit, aligned) in enumerate(zip(units, aligned_units)):
        words = aligned["words"]
        timed = [w for w in words if w]
        suspicious = False
        if not timed or len(timed) < 0.5 * len(words):
            reasons["dropped"] += 1
            suspicious = True
        elif aligned.get("score") is not None and aligned["score"] < MIN_WORD_SCORE:
            reasons["low_score"] += 1
            suspicious = True
        elif starts[i] is not None:
            local = [starts[j]
                     for j in range(max(0, i - 2), min(len(starts), i + 3))
                     if starts[j] is not None]
            if len(local) >= 2:
                local.sort()
                lm = local[len(local) // 2]
                if abs(starts[i] - lm) > MAX_LOCAL_DEVIATION:
                    reasons["offset"] += 1
                    suspicious = True
        if suspicious:
            n_suspicious += 1

    frac = n_suspicious / len(units) if units else 0.0
    detail = f"{n_suspicious}/{len(units)} units suspicious ({reasons})"
    return frac > 0.35, frac, detail


def interpolate_word_timings(flat_words: list) -> None:
    """Fill in flat_words[i]['interp'] = (start, end) for every entry
    whose 'timing' is falsy, given only the generic fields 'timing',
    'orig_start', 'orig_end', 'orig_dur' - works for ANY flat word list
    (note-syllable-derived or plain-lyric-derived) since it never looks at
    notes/beats directly. Mutates in place; entries that already have a
    real 'timing' are left untouched.

    Every maximal run of unaligned words is distributed proportionally (by
    original duration) over the gap between the surrounding aligned words;
    a trailing run keeps its ORIGINAL timing if it starts after the last
    aligned word (e.g. an outro after a long gap); a leading run stacks
    backwards from the first aligned word.
    """
    n_words = len(flat_words)
    i = 0
    while i < n_words:
        if flat_words[i]["timing"]:
            i += 1
            continue
        j = i
        while j < n_words and not flat_words[j]["timing"]:
            j += 1
        run = flat_words[i:j]
        prv = flat_words[i - 1]["timing"] if i > 0 else None
        nxt = flat_words[j]["timing"] if j < n_words else None
        orig_durs = [w["orig_dur"] for w in run]
        total = sum(orig_durs)

        if prv is None and nxt is None:
            for w in run:
                w["interp"] = (w["orig_start"], w["orig_end"])
        elif prv is None:
            nxt_start = max(0.05, nxt["start"])
            span = min(total, max(0.1, nxt_start))
            cursor = nxt_start - span
            for w, d in zip(run, orig_durs):
                w["interp"] = (cursor, cursor + d)
                cursor += d
        elif nxt is None:
            # trailing run: keep the original txt timing when it stays after
            # the last aligned word (e.g. an outro after a long gap)
            if run[0]["orig_start"] >= prv["end"] + 0.05:
                for w in run:
                    s = max(w["orig_start"], prv["end"] + 0.05)
                    e = max(w["orig_end"], s + 0.03)
                    w["interp"] = (s, e)
            else:
                cursor = prv["end"] + 0.05
                for w, d in zip(run, orig_durs):
                    w["interp"] = (cursor, cursor + d)
                    cursor += d
        else:
            span = max(0.03, nxt["start"] - prv["end"])
            scale = span / total
            cursor = prv["end"]
            for w, d in zip(run, orig_durs):
                s = cursor
                e = cursor + d * scale
                w["interp"] = (s, max(e, s + 0.03))
                cursor = max(e, s + 0.03)
        i = j


def map_words_to_notes(txt, units, aligned_units):
    """Distribute aligned word timings onto the notes (syllables).

    Returns a list of (start, end) tuples in note (file) order. Words that
    could not be aligned get interpolated timings.
    """
    # 1. flat word list (word = one entry per group, incl. hold-only groups)
    flat_words = []
    base = 0
    for unit, aligned in zip(units, aligned_units):
        notes = unit["notes"]
        all_groups = group_words(notes)
        text_pos = {}
        for gi, g in enumerate(all_groups):
            if word_text(notes, g):
                text_pos[gi] = len(text_pos)
        timings = aligned["words"]
        for gi, g in enumerate(all_groups):
            ti = text_pos.get(gi)
            timing = timings[ti] if ti is not None and ti < len(timings) else None
            first = notes[g[0]]
            last = notes[g[-1]]
            orig_start = txt.note_start_sec(first)
            orig_end = txt.note_end_sec(last)
            flat_words.append({
                "notes": notes,
                "base": base,
                "idxs": g,
                "timing": timing,
                "orig_start": orig_start,
                "orig_end": orig_end,
                "orig_dur": max(0.01, orig_end - orig_start),
            })
        base += len(notes)

    # 2. interpolate missing word timings (shared with the lyrics-mode
    #    pipeline - see interpolate_word_timings)
    interpolate_word_timings(flat_words)

    # 3. distribute word timing onto syllables (proportional to original durs)
    note_times = [None] * len(txt.notes)
    for w in flat_words:
        if w["timing"]:
            wstart, wend = w["timing"]["start"], w["timing"]["end"]
        else:
            wstart, wend = w["interp"]
        wend = max(wend, wstart + 0.05)
        notes = w["notes"]
        orig_durs = [max(0.01, txt.note_end_sec(notes[k]) - txt.note_start_sec(notes[k]))
                     for k in w["idxs"]]
        total = sum(orig_durs)
        avail = wend - wstart
        for k, idx in enumerate(w["idxs"]):
            s = wstart + avail * (sum(orig_durs[:k]) / total)
            e = wstart + avail * (sum(orig_durs[:k + 1]) / total)
            e = max(e, s + 0.03)
            note_times[w["base"] + idx] = (s, min(e, wend + 0.05))

    # safety net: notes not covered by any word keep their original timing
    for i, nt in enumerate(note_times):
        if nt is None:
            note = txt.notes[i]
            note_times[i] = (txt.note_start_sec(note), txt.note_end_sec(note))
    return note_times


# --------------------------------------------------------------------------
# fallback: full re-creation
# --------------------------------------------------------------------------

def recreate_song(song_dir: str, out_dir: str, work_dir: str, audio_path: str,
                  language: str = None):
    """Fallback for songs whose lyrics do not match the audio: run the full
    UltraSinger automation on the song's audio and integrate the result into
    the repaired output folder.

    `language` should be the ALREADY-RESOLVED language from the sync-mode
    attempt that just ran (from the txt's own #LANGUAGE tag, or a forced
    --language) - passing it through as --language is important: without
    it, whisper re-detects the language from scratch on just the first 30s
    of audio, which can mis-detect entirely (verified case: a purely
    German song detected as "en" at only 0.41 confidence, corrupting the
    whole re-transcription - see 05-LESSONS.md "recreate_song lost the
    known language").
    """
    print(f"{ULTRASINGER_HEAD} {gold_highlighted('Re-creating song from audio with UltraSinger')} "
          f"(original lyrics do not match this audio)")

    song_name = os.path.basename(song_dir.rstrip("/"))
    out_song_dir = os.path.join(out_dir, song_name)
    os.makedirs(out_song_dir, exist_ok=True)
    for name in os.listdir(song_dir):
        src = os.path.join(song_dir, name)
        if os.path.isfile(src) and not name.lower().endswith(".txt"):
            dst = os.path.join(out_song_dir, name)
            if not os.path.isfile(dst):
                shutil.copy2(src, dst)

    us_out = os.path.join(work_dir, "recreate_output")
    os.makedirs(us_out, exist_ok=True)
    cmd = [sys.executable, "UltraSinger.py", "-i", audio_path, "-o", us_out]
    whisper_model = os.environ.get("WHISPER_MODEL", "") or "large-v2"
    cmd += ["--whisper", whisper_model]
    cmd += ["--demucs", DEMUCS_MODEL.value]
    batch_size = os.environ.get("WHISPER_BATCH_SIZE", "").strip()
    if batch_size:
        cmd += ["--whisper_batch_size", batch_size]
    if language:
        # avoid re-detecting from scratch (can mis-detect entirely on just
        # the first 30s of audio) - we already know the language from the
        # txt/sync-mode attempt that triggered this fallback
        cmd += ["--language", language]
    if os.environ.get("WHISPER_ON_CPU", "") not in ("", "0", "false"):
        cmd += ["--force_whisper_cpu"]
    extra = os.environ.get("ULTRASINGER_ARGS", "")
    if extra:
        cmd += extra.split()

    print(f"{ULTRASINGER_HEAD} running: {' '.join(cmd)}")
    import subprocess
    proc = subprocess.run(cmd, cwd="/app/UltraSinger/src")
    if proc.returncode != 0:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('UltraSinger recreation failed')}")
        return None

    created = [d for d in os.listdir(us_out)
               if os.path.isdir(os.path.join(us_out, d))]
    if not created:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('no output created')}")
        return None
    src_dir = os.path.join(us_out, created[0])
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        dst = os.path.join(out_song_dir, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.move(src, dst)
        elif os.path.isfile(src):
            os.remove(src)

    for name in sorted(os.listdir(out_song_dir)):
        if name.lower().endswith(".txt"):
            return os.path.join(out_song_dir, name)
    return None


# --------------------------------------------------------------------------
# repair pipeline
# --------------------------------------------------------------------------

def repair_txt(txt: Txt, song_dir: str, out_dir: str, mode: str, device: str,
               work_dir: str, language: str = None, align_model: str = None):
    print(f"{ULTRASINGER_HEAD} {gold_highlighted('Repair')} "
          f"{blue_highlighted(txt.path)} ({mode} mode)")

    os.makedirs(work_dir, exist_ok=True)
    audio_path, err = locate_audio(song_dir, txt, work_dir)
    if err:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('Error:')} {err}")
        return None

    lines = txt.lyric_lines()
    units = build_alignment_units(txt, lines)
    n_notes = len(txt.notes)
    print(f"{ULTRASINGER_HEAD} {len(lines)} lyric lines ({len(units)} alignment units), "
          f"{n_notes} notes, audio: {os.path.basename(audio_path)}")

    if mode == "sync":
        print(f"{ULTRASINGER_HEAD} separating vocals (demucs, device={device})")
        vocals_audio, processing_audio = prepare_processing_audio(
            audio_path, work_dir, device)
        align_audio = vocals_audio  # raw vocals: best signal for alignment
    else:
        align_audio = audio_path

    audio16k = load_audio_16k(align_audio)
    audio_dur = len(audio16k) / 16000.0

    if align_model:
        import whisperx
        print(f"{ULTRASINGER_HEAD} loading align model {align_model}")
        model, meta = whisperx.load_align_model(language_code="en", device="cpu",
                                                model_name=align_model)
        lang = language or "en"
    else:
        lang = resolve_language(txt, align_audio, language)
        model, meta, lang = load_aligner(lang, "cpu")

    if mode == "gap":
        # align the first units to find the real start of the vocals
        first_units = units[:2]
        aligned = progressively_align(txt, first_units, model, meta, audio16k,
                                      audio_dur)
        samples = implied_gap_samples(txt, first_units, aligned)
        if not samples:
            msg = "could not align the first lines - aborting"
            print(f"{ULTRASINGER_HEAD} {red_highlighted(msg)}")
            return None
        import statistics
        new_gap_ms = round(statistics.median(samples) * 1000)
        n_aligned = sum(1 for a in aligned for w in a["words"] if w)
        out_path = write_repaired(txt, song_dir, out_dir, gap_ms=new_gap_ms,
                                  note_times=None, pitches=None)
        return {
            "output": out_path,
            "gap_old_ms": txt.gap_ms, "gap_new_ms": new_gap_ms,
            "aligned_words": n_aligned,
            "total_words": sum(len(group_words(u["notes"])) for u in first_units),
            "repitched": 0, "mode": mode,
        }

    # ---------------- sync mode ----------------
    aligned_units = progressively_align(txt, units, model, meta, audio16k,
                                        audio_dur)
    reanchor_outlier_units(txt, units, aligned_units, model, meta, audio16k,
                           audio_dur)
    rescue_dropped_units(txt, units, aligned_units, model, meta, audio16k,
                         audio_dur)

    # robust GAP: median implied gap of the first units (the GAP anchors the
    # song start; later units may legitimately drift due to the desync)
    import statistics
    head_units = units[:3]
    head_aligned = aligned_units[:3]
    samples = implied_gap_samples(txt, head_units, head_aligned)
    all_samples = implied_gap_samples(txt, units, aligned_units)
    if not samples and all_samples:
        samples = all_samples
    med_gap = statistics.median(samples) if samples else txt.gap_s
    if all_samples:
        refine_first_units(txt, units, aligned_units, model, meta, audio16k,
                           audio_dur, med_gap)
        samples = implied_gap_samples(txt, head_units, head_aligned) or \
            implied_gap_samples(txt, units, aligned_units)
        med_gap = statistics.median(samples) if samples else txt.gap_s

    # do the lyrics match the audio at all?
    mismatch, susp_frac, detail = detect_lyrics_mismatch(txt, units, aligned_units)
    print(f"{ULTRASINGER_HEAD} alignment quality: {detail}")
    if mismatch:
        if os.environ.get("REPAIR_FALLBACK", "1") in ("0", "false", "no"):
            msg = f"lyrics do not match the audio ({detail}) - cannot repair"
            print(f"{ULTRASINGER_HEAD} {red_highlighted(msg)}")
            return None
        out_path = recreate_song(song_dir, out_dir, work_dir, audio_path, lang)
        if out_path is None:
            return None
        return {
            "output": out_path,
            "gap_old_ms": txt.gap_ms, "gap_new_ms": None,
            "aligned_words": 0, "total_words": 0,
            "repitched": 0, "mode": "recreated",
            "note": f"lyrics mismatch detected ({detail}) - song re-created "
                    "from audio with new transcription",
        }

    n_aligned = sum(1 for a in aligned_units for w in a["words"] if w)
    total_words = sum(len(group_words(u["notes"])) for u in units)

    note_times = map_words_to_notes(txt, units, aligned_units)
    if len(note_times) != n_notes:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('internal error:')} "
              f"note timing mismatch ({len(note_times)} != {n_notes})")
        return None

    # new gap from the median implied gap, but never after the first note
    # (beats must stay non-negative)
    first_note = txt.notes[0]
    implied_first = note_times[0][0] - txt.beat_to_sec(first_note.beat)
    gap_s_new = min(med_gap, implied_first)
    new_gap_ms = round(gap_s_new * 1000)

    # re-pitch every note with SwiftF0 on the processed vocal audio
    print(f"{ULTRASINGER_HEAD} re-pitching notes with SwiftF0")
    pitched = get_pitch_with_file(processing_audio)
    import librosa
    pitches = []
    repitched = 0
    for i, note in enumerate(txt.notes):
        start, end = note_times[i]
        try:
            seg = create_midi_note_from_pitched_data(
                start, max(end, start + 0.05), pitched,
                note.syllable or "~", None)
            new_pitch = int(librosa.note_to_midi(seg.note)) - 48
            pitches.append(new_pitch)
            if new_pitch != note.pitch:
                repitched += 1
        except Exception:  # noqa: BLE001
            pitches.append(note.pitch)

    out_path = write_repaired(txt, song_dir, out_dir, gap_ms=new_gap_ms,
                              note_times=note_times, pitches=pitches)
    return {
        "output": out_path,
        "gap_old_ms": txt.gap_ms, "gap_new_ms": new_gap_ms,
        "aligned_words": n_aligned, "total_words": total_words,
        "repitched": repitched, "mode": mode,
    }


def write_repaired(txt: Txt, song_dir: str, out_dir: str, gap_ms: int,
                   note_times, pitches):
    """Write the repaired song folder + txt (originals in input stay as-is)."""
    song_name = os.path.basename(song_dir.rstrip("/"))
    out_song_dir = os.path.join(out_dir, song_name)
    os.makedirs(out_song_dir, exist_ok=True)

    # copy everything except the txt files (they are written below)
    for name in os.listdir(song_dir):
        src = os.path.join(song_dir, name)
        if os.path.isfile(src) and not name.lower().endswith(".txt"):
            dst = os.path.join(out_song_dir, name)
            if not os.path.isfile(dst):
                shutil.copy2(src, dst)

    out_path = os.path.join(out_song_dir, os.path.basename(txt.path))
    real_bpm = txt.real_bpm

    def sec_to_beat(sec):
        return sec * real_bpm / 60.0

    gap_s = gap_ms / 1000.0
    lines = copy.deepcopy(txt.lines)
    prev_end_beat = None
    note_i = 0

    for line in lines:
        if "note" in line:
            note = line["note"]
            if note_times is None:
                # gap mode: only the header changes
                new_beat, new_dur, new_pitch = note.beat, note.dur, note.pitch
            else:
                start, end = note_times[note_i]
                new_pitch = pitches[note_i] if pitches is not None else note.pitch
                new_beat = round(sec_to_beat(start - gap_s))
                new_dur = max(1, round(sec_to_beat(end - start)))
                new_beat = max(0, new_beat)
                if prev_end_beat is not None:
                    new_beat = max(new_beat, prev_end_beat)
                prev_end_beat = new_beat + new_dur
            line["raw"] = (f"{note.type} {_fmt_num(new_beat)} {_fmt_num(new_dur)} "
                           f"{_fmt_num(new_pitch)} {note.word}")
            note_i += 1
        elif "break_beat" in line:
            if note_times is not None and prev_end_beat is not None:
                # show the next line when the current line's last note ends
                line["raw"] = f"- {_fmt_num(prev_end_beat)}"
        elif line["raw"].strip().startswith("#GAP:"):
            line["raw"] = f"#GAP:{gap_ms}"

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for line in lines:
            f.write(line["raw"].rstrip("\r\n") + "\n")

    print(f"{ULTRASINGER_HEAD} {green_highlighted('wrote')} {out_path}")
    return out_path


# --------------------------------------------------------------------------
# lyrics mode: force-align TRUSTED external lyrics (fetched online, or
# supplied by the user as a plain text file) to the audio, building a
# FRESH note sequence. Unlike sync mode (which never touches the lyrics,
# only their timing), this REPLACES the lyrics text - use it when the
# existing/transcribed lyrics are known to be wrong. Requires an existing
# scaffold txt (for its BPM tag and, when the lyrics source has no timing
# of its own, a rough per-line time estimate) - same input shape as
# gap/sync mode, so it works equally on a broken song being repaired or a
# freshly created song whose whisper-transcribed lyrics are being swapped
# for online ones.
# --------------------------------------------------------------------------

def load_lyrics_file(path: str):
    """Load a lyrics source: either lyrics_fetch.py's JSON
    ({"source": str, "lines": [{"text":, "start":}, ...]}) or a plain text
    file (one non-empty line = one sung line, no timing - this is the
    "delimitation for lyric lines" the alignment needs). Returns
    (source_label, line_dicts)."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "lines" in data:
            return data.get("source") or "lyrics-file", data["lines"]
    except (json.JSONDecodeError, ValueError):
        pass
    lines = [{"text": line.strip(), "start": None}
             for line in raw.splitlines() if line.strip()]
    return "lyrics-file", lines


def parse_lyrics_lines(lines_data: list) -> list:
    """[{"text":, "start":}, ...] -> [{"text":, "words":[...], "start":}, ...],
    dropping empty lines / lines with no words."""
    units = []
    for entry in lines_data:
        text = (entry.get("text") or "").strip()
        words = text.split()
        if not words:
            continue
        units.append({"text": text, "words": words, "start": entry.get("start")})
    return units


def seed_lyric_windows(units: list, scaffold_lines: list, txt: Txt,
                       audio_dur: float) -> None:
    """Fill in unit['seed_start']/unit['seed_end'] in place - the rough
    per-line time estimate progressively_align_lyrics() anchors its search
    windows on.

    Priority: real per-line timing from the lyrics source itself (e.g. a
    synced/LRC lookup) when EVERY unit has one; else map 1:1 onto the
    scaffold's existing lines when the counts match exactly; else
    distribute proportionally (by word count) across the scaffold's total
    vocal-active span (or the whole audio if there is no usable scaffold).
    """
    if units and all(u["start"] is not None for u in units):
        for i, u in enumerate(units):
            u["seed_start"] = max(0.0, u["start"])
            nxt = units[i + 1]["start"] if i + 1 < len(units) else None
            u["seed_end"] = min(audio_dur, nxt) if nxt is not None \
                else min(audio_dur, u["seed_start"] + 8.0)
        return

    scaffold_spans = []
    for line in scaffold_lines:
        notes = line["notes"]
        if not notes:
            continue
        scaffold_spans.append(
            (txt.note_start_sec(notes[0]), txt.note_end_sec(notes[-1])))

    if scaffold_spans and len(scaffold_spans) == len(units):
        for u, (s, e) in zip(units, scaffold_spans):
            u["seed_start"], u["seed_end"] = s, min(audio_dur, e)
        return

    total_start, total_end = (scaffold_spans[0][0], scaffold_spans[-1][1]) \
        if scaffold_spans else (0.0, audio_dur)
    total_words = sum(len(u["words"]) for u in units) or 1
    span = max(0.1, min(audio_dur, total_end) - total_start)
    cursor = total_start
    for u in units:
        dur = span * (len(u["words"]) / total_words)
        u["seed_start"] = cursor
        u["seed_end"] = min(audio_dur, cursor + dur)
        cursor += dur


def progressively_align_lyrics(units, model, meta, audio16k, audio_dur):
    """Like progressively_align(), but the units are plain lyric lines
    (dict with text/words/seed_start/seed_end) with no pre-existing note
    structure of their own - the per-word "original" duration/position
    used for plausibility checks is an even split of the unit's seed
    window rather than a real note duration.

    Returns a list (per unit) of dicts: {"words": [timing|None, ...],
    "text", "score", "word_orig_starts", "word_orig_ends"} - positional:
    index i belongs to word i of that unit's word list.
    """
    results = []
    expected = None
    last_end = 0.0
    for unit in units:
        words_list = unit["words"]
        text = unit["text"]
        orig_start = unit["seed_start"]
        orig_dur = max(0.5, unit["seed_end"] - unit["seed_start"])
        if expected is None:
            expected = orig_start

        t1 = max(0.0, expected - 1.5, orig_start - ALIGN_PAD)
        t2 = max(t1 + 0.5, expected + orig_dur + 1.0, orig_start + orig_dur + ALIGN_PAD)
        t2 = min(t2, audio_dur - 0.05)

        aligned_words = align_line(model, meta, audio16k, text, t1, t2)

        n = len(words_list)
        even_dur = orig_dur / n if n else orig_dur
        orig_word_durs = [max(0.15, even_dur)] * n
        orig_word_starts = [orig_start + i * even_dur for i in range(n)]
        orig_word_ends = [orig_start + (i + 1) * even_dur for i in range(n)]

        cleaned, last_end, score_sum, score_n = clean_aligned_words(
            aligned_words, list(range(n)), orig_word_durs, text, audio_dur,
            last_end, orig_word_starts, orig_word_ends)

        timed = [c for c in cleaned if c]
        if timed:
            span = timed[-1]["end"] - timed[0]["start"]
            if span < 0.3 * orig_dur or span > 4.0 * orig_dur + 3.0:
                print(f"{ULTRASINGER_HEAD} {red_highlighted('implausible span:')} "
                      f"{text[:40]!r} ({span:.1f}s vs {orig_dur:.1f}s) "
                      "- interpolating instead")
                cleaned = [None] * n
                timed = []
        if timed:
            expected = timed[-1]["end"]
        else:
            expected = t1 + max(orig_dur, 1.0)

        avg_score = (score_sum / score_n) if score_n else None
        results.append({
            "words": cleaned, "text": text, "score": avg_score,
            "word_orig_starts": orig_word_starts, "word_orig_ends": orig_word_ends,
        })
    return results


def reanchor_outlier_lyric_units(units, aligned_units, model, meta, audio16k, audio_dur):
    """Fix lyric lines that locked onto the wrong position (e.g. the wrong
    repeat of a chorus) - mirrors reanchor_outlier_units() for note-based
    sync mode. Without this, a single bad/drifted alignment in one line
    corrupts the 'expected' position progressively_align_lyrics() chains
    forward into every following line (verified empirically: a smoke test
    on a song with known-good reference timing showed a later line landing
    ~3s off after an earlier line's alignment drifted).

    The "implied gap" here is aligned_start - unit['seed_start'] (the
    static seed position), not a beat-derived one - re-anchoring only
    fires when a unit's gap deviates strongly from the running median of
    its neighbours, and is only accepted if the consensus position scores
    at least as well, so legitimate mid-song jumps stay intact.
    """
    import statistics

    def unit_gap(ui):
        for w in aligned_units[ui]["words"]:
            if w:
                return w["start"] - units[ui]["seed_start"]
        return None

    for ui in range(len(units)):
        gap_i = unit_gap(ui)
        if gap_i is None:
            continue
        history = [unit_gap(j) for j in range(max(0, ui - 10), ui)]
        history = [h for h in history if h is not None]
        if len(history) < 3:
            continue
        med = statistics.median(history)
        if abs(gap_i - med) <= 4.0:
            continue

        unit = units[ui]
        orig_start = unit["seed_start"]
        orig_dur = max(0.5, unit["seed_end"] - unit["seed_start"])
        predicted = med + orig_start
        t1 = max(0.0, predicted - 3.0)
        t2 = min(audio_dur - 0.05, max(t1 + 0.5, predicted + orig_dur + 3.0))
        text = unit["text"]
        words = align_line(model, meta, audio16k, text, t1, t2)
        n = len(unit["words"])
        even_dur = orig_dur / n if n else orig_dur
        orig_word_durs = [max(0.15, even_dur)] * n
        orig_word_starts = [orig_start + i * even_dur for i in range(n)]
        orig_word_ends = [orig_start + (i + 1) * even_dur for i in range(n)]
        cleaned, _, score_sum, score_n = clean_aligned_words(
            words, list(range(n)), orig_word_durs, text, audio_dur, 0.0,
            orig_word_starts, orig_word_ends)
        timed = [c for c in cleaned if c]
        if not timed:
            continue  # consensus position has no match -> keep original
        span = timed[-1]["end"] - timed[0]["start"]
        if span < 0.3 * orig_dur or span > 4.0 * orig_dur + 3.0:
            continue
        new_gap = timed[0]["start"] - orig_start
        if abs(new_gap - med) >= abs(gap_i - med):
            continue  # did not get closer to the consensus
        old_score = aligned_units[ui]["score"] or 0.0
        new_score = (score_sum / score_n) if score_n else 0.0
        if new_score < old_score - 0.05:
            continue  # consensus position matches much worse -> real jump
        print(f"{ULTRASINGER_HEAD} re-anchored lyric line {ui + 1} "
              f"({gap_i - med:+.1f}s off consensus, score "
              f"{old_score:.2f} -> {new_score:.2f})")
        aligned_units[ui]["words"] = cleaned
        aligned_units[ui]["word_orig_starts"] = orig_word_starts
        aligned_units[ui]["word_orig_ends"] = orig_word_ends
        aligned_units[ui]["score"] = new_score or None


def rescue_dropped_lyric_units(units, aligned_units, model, meta, audio16k, audio_dur):
    """Retry lyric lines that got zero timed words, anchored at their
    ORIGINAL seed window (the progressive anchor can be far off after a
    long instrumental gap) - chained like rescue_dropped_units()."""
    prev_end = None
    for ui, (unit, aligned) in enumerate(zip(units, aligned_units)):
        timed = [w for w in aligned["words"] if w]
        if timed:
            prev_end = timed[-1]["end"]
            continue
        orig_start = unit["seed_start"]
        orig_dur = max(0.5, unit["seed_end"] - unit["seed_start"])
        if prev_end is not None:
            t1 = max(0.0, prev_end - 1.5, orig_start - 12.0)
            t2 = max(t1 + 0.5, prev_end + orig_dur + 1.0, orig_start + orig_dur + 6.0)
        else:
            t1 = max(0.0, orig_start - 12.0)
            t2 = max(t1 + 0.5, orig_start + orig_dur + 6.0)
        t2 = min(t2, audio_dur - 0.05)

        text = unit["text"]
        words = align_line(model, meta, audio16k, text, t1, t2)
        n = len(unit["words"])
        even_dur = orig_dur / n if n else orig_dur
        orig_word_durs = [max(0.15, even_dur)] * n
        orig_word_starts = [orig_start + i * even_dur for i in range(n)]
        orig_word_ends = [orig_start + (i + 1) * even_dur for i in range(n)]

        cleaned, _, _, _ = clean_aligned_words(
            words, list(range(n)), orig_word_durs, text, audio_dur, 0.0,
            orig_word_starts, orig_word_ends)
        timed = [c for c in cleaned if c]
        if not timed:
            continue
        span = timed[-1]["end"] - timed[0]["start"]
        if span < 0.3 * orig_dur or span > 4.0 * orig_dur + 3.0:
            continue
        print(f"{ULTRASINGER_HEAD} rescued lyric line {ui + 1} via seed position "
              f"({text[:40]!r})")
        aligned["words"] = cleaned
        aligned["word_orig_starts"] = orig_word_starts
        aligned["word_orig_ends"] = orig_word_ends
        prev_end = timed[-1]["end"]


def build_syllables_from_lyric_units(units, aligned_units, language) -> list:
    """Hyphenate each word and distribute its (aligned or interpolated)
    time evenly across its syllables (reuses UltraSinger's OWN
    hyphenate_each_word/add_hyphen_to_data - the exact same functions a
    brand-new song's whisper transcription goes through).

    Returns one syllable list per lyric UNIT (line):
    [[(word_field, start, end), ...], ...] - word_field already carries
    the trailing-space word-boundary marker - so the caller can rebuild
    line breaks exactly where the lyrics source/file put them.
    """
    from modules.Speech_Recognition.TranscribedData import TranscribedData  # noqa: E402
    from modules.Speech_Recognition.hyphenation import hyphenate_each_word  # noqa: E402
    import UltraSinger as _us  # noqa: E402

    flat_words = []
    word_unit_idx = []
    for ui, (unit, aligned) in enumerate(zip(units, aligned_units)):
        timings = aligned["words"]
        starts = aligned["word_orig_starts"]
        ends = aligned["word_orig_ends"]
        for wi, word in enumerate(unit["words"]):
            timing = timings[wi] if wi < len(timings) else None
            flat_words.append({
                "word": word, "timing": timing,
                "orig_start": starts[wi], "orig_end": ends[wi],
                "orig_dur": max(0.05, ends[wi] - starts[wi]),
            })
            word_unit_idx.append(ui)

    interpolate_word_timings(flat_words)

    transcribed = []
    for w in flat_words:
        if w["timing"]:
            start, end = w["timing"]["start"], w["timing"]["end"]
        else:
            start, end = w["interp"]
        end = max(end, start + 0.05)
        td = TranscribedData()
        td.word = w["word"]
        td.start = start
        td.end = end
        transcribed.append(td)

    _us.remove_unecessary_punctuations(transcribed)

    hyphen_words = None
    if language:
        hyphen_words = hyphenate_each_word(language, transcribed)
    if hyphen_words is not None:
        transcribed = _us.add_hyphen_to_data(transcribed, hyphen_words)
        counts = [len(h) if h else 1 for h in hyphen_words]
    else:
        counts = [1] * len(flat_words)

    syllable_unit_idx = []
    for idx, count in zip(word_unit_idx, counts):
        syllable_unit_idx.extend([idx] * count)

    per_unit_syllables = [[] for _ in units]
    for td, ui in zip(transcribed, syllable_unit_idx):
        word_field = td.word + (" " if td.is_word_end else "")
        per_unit_syllables[ui].append((word_field, td.start, td.end))
    return per_unit_syllables


def write_lyrics_result(txt: Txt, song_dir: str, out_dir: str,
                        per_unit_syllables: list, processing_audio: str):
    """Build a FRESH UltraStar txt from per-unit syllable lists (start/end
    in seconds), re-pitching every syllable with SwiftF0. Unlike
    write_repaired() (which maps new timing onto an EXISTING note
    sequence), this builds an entirely new note sequence - the
    syllable/line count is whatever the lyrics source produced, not the
    original file's. BPM is kept from the scaffold (audio-only, lyrics
    don't change it); header tags are kept verbatim except #GAP."""
    song_name = os.path.basename(song_dir.rstrip("/"))
    out_song_dir = os.path.join(out_dir, song_name)
    os.makedirs(out_song_dir, exist_ok=True)

    for name in os.listdir(song_dir):
        src = os.path.join(song_dir, name)
        if os.path.isfile(src) and not name.lower().endswith(".txt"):
            dst = os.path.join(out_song_dir, name)
            if not os.path.isfile(dst):
                shutil.copy2(src, dst)

    all_syllables = [s for unit in per_unit_syllables for s in unit]
    if not all_syllables:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('Error:')} "
              "no syllables produced from the supplied lyrics")
        return None

    gap_s = max(0.0, all_syllables[0][1])
    real_bpm = txt.real_bpm

    def sec_to_beat(sec):
        return sec * real_bpm / 60.0 if real_bpm else 0.0

    print(f"{ULTRASINGER_HEAD} re-pitching notes with SwiftF0")
    pitched = get_pitch_with_file(processing_audio)
    import librosa

    out_lines = []
    for line in txt.lines:
        raw = line["raw"]
        if "note" in line or not raw.strip().startswith("#"):
            continue  # only header (#TAG:) lines are kept - notes are rebuilt below
        if raw.strip().startswith("#GAP:"):
            out_lines.append(f"#GAP:{round(gap_s * 1000)}")
        else:
            out_lines.append(raw)

    prev_end_beat = None
    for unit_syllables in per_unit_syllables:
        if not unit_syllables:
            continue
        for word_field, start, end in unit_syllables:
            start = max(start, gap_s)
            end = max(end, start + 0.05)
            try:
                seg = create_midi_note_from_pitched_data(
                    start, end, pitched, word_field.strip() or "~", None)
                pitch = int(librosa.note_to_midi(seg.note)) - 48
            except Exception:  # noqa: BLE001
                pitch = 0
            beat = max(0, round(sec_to_beat(start - gap_s)))
            dur = max(1, round(sec_to_beat(end - start)))
            if prev_end_beat is not None:
                beat = max(beat, prev_end_beat)
            prev_end_beat = beat + dur
            out_lines.append(f": {_fmt_num(beat)} {_fmt_num(dur)} "
                             f"{_fmt_num(pitch)} {word_field}")
        out_lines.append(f"- {_fmt_num(prev_end_beat)}")

    if out_lines and out_lines[-1].startswith("- "):
        out_lines.pop()  # no line-break needed after the very last line
    out_lines.append("E")

    out_path = os.path.join(out_song_dir, os.path.basename(txt.path))
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for raw in out_lines:
            f.write(raw.rstrip("\r\n") + "\n")

    print(f"{ULTRASINGER_HEAD} {green_highlighted('wrote')} {out_path}")
    return out_path


def repair_txt_with_lyrics(txt: Txt, lyrics_units: list, song_dir: str, out_dir: str,
                           device: str, work_dir: str, language: str = None,
                           align_model: str = None):
    print(f"{ULTRASINGER_HEAD} {gold_highlighted('Lyrics rebuild')} "
          f"{blue_highlighted(txt.path)} ({len(lyrics_units)} supplied lines)")

    if not lyrics_units:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('Error:')} "
              "supplied lyrics file has no usable lines")
        return None

    os.makedirs(work_dir, exist_ok=True)
    audio_path, err = locate_audio(song_dir, txt, work_dir)
    if err:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('Error:')} {err}")
        return None

    print(f"{ULTRASINGER_HEAD} separating vocals (demucs, device={device})")
    vocals_audio, processing_audio = prepare_processing_audio(
        audio_path, work_dir, device)

    audio16k = load_audio_16k(vocals_audio)
    audio_dur = len(audio16k) / 16000.0

    if align_model:
        import whisperx
        model, meta = whisperx.load_align_model(
            language_code="en", device="cpu", model_name=align_model)
        lang = language or "en"
    else:
        lang = resolve_language(txt, vocals_audio, language)
        model, meta, lang = load_aligner(lang, "cpu")

    scaffold_lines = txt.lyric_lines()
    seed_lyric_windows(lyrics_units, scaffold_lines, txt, audio_dur)

    aligned_units = progressively_align_lyrics(
        lyrics_units, model, meta, audio16k, audio_dur)
    reanchor_outlier_lyric_units(
        lyrics_units, aligned_units, model, meta, audio16k, audio_dur)
    rescue_dropped_lyric_units(
        lyrics_units, aligned_units, model, meta, audio16k, audio_dur)

    n_aligned = sum(1 for a in aligned_units for w in a["words"] if w)
    n_total = sum(len(u["words"]) for u in lyrics_units)
    if n_total and n_aligned / n_total < 0.5:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('warning:')} only "
              f"{n_aligned}/{n_total} words aligned confidently - the "
              "supplied lyrics may not match this audio well; result kept "
              "anyway (lyrics mode never falls back to re-transcription)")

    per_unit_syllables = build_syllables_from_lyric_units(
        lyrics_units, aligned_units, lang)
    out_path = write_lyrics_result(
        txt, song_dir, out_dir, per_unit_syllables, processing_audio)
    if out_path is None:
        return None

    return {"output": out_path, "aligned_words": n_aligned, "total_words": n_total}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--song-dir", required=True,
                        help="folder of the song to repair (contains the txt)")
    parser.add_argument("--out", required=True,
                        help="output folder for repaired songs")
    parser.add_argument("--mode", choices=["gap", "sync", "lyrics"], default="sync")
    parser.add_argument("--device", default="cpu", help="cpu|cuda (demucs)")
    parser.add_argument("--work-dir", default=None,
                        help="cache dir for separation/pitching artifacts")
    parser.add_argument("--language", default=None,
                        help="force language code (e.g. de, en, ru, fi)")
    parser.add_argument("--align-model", default=None,
                        help="force a huggingface wav2vec2 align model")
    parser.add_argument("--lyrics-file", default=None,
                        help="mode=lyrics only: line-delimited trusted lyrics "
                             "(plain text, or lyrics_fetch.py JSON) to force-align "
                             "instead of the txt's own (possibly wrong) lyrics")
    args = parser.parse_args()
    args.language = args.language or os.environ.get("REPAIR_LANGUAGE")

    song_dir = args.song_dir.rstrip("/")
    work_dir = args.work_dir or os.path.join(
        os.environ.get("WORK_DIR", "/data/work"),
        os.path.basename(song_dir))

    if args.mode == "lyrics" and not args.lyrics_file:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('Error:')} "
              "--mode lyrics requires --lyrics-file")
        sys.exit(1)

    txts = find_ultrastar_txts(song_dir)
    if not txts:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('Error:')} no ultrastar txt "
              f"found in {song_dir}")
        sys.exit(1)

    overall_ok = True
    for txt in txts:
        try:
            if args.mode == "lyrics":
                lyrics_source, lines_data = load_lyrics_file(args.lyrics_file)
                lyrics_units = parse_lyrics_lines(lines_data)
                result = repair_txt_with_lyrics(
                    txt, lyrics_units, song_dir, args.out, args.device,
                    work_dir, args.language, args.align_model)
                if result is not None:
                    result["lyrics_source"] = lyrics_source
            else:
                result = repair_txt(txt, song_dir, args.out, args.mode, args.device,
                                    work_dir, args.language, args.align_model)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"{ULTRASINGER_HEAD} {red_highlighted('repair failed:')} {exc}")
            overall_ok = False
            continue
        if result is None:
            overall_ok = False
            continue
        song_name = os.path.basename(song_dir)
        if args.mode == "lyrics":
            print(f"{ULTRASINGER_HEAD} {bright_green_highlighted('Summary')} "
                  f"{cyan_highlighted(song_name)}: lyrics rebuilt from "
                  f"{result['lyrics_source']}, aligned words "
                  f"{result['aligned_words']}/{result['total_words']}")
            print(f"REPAIR_DONE output={result['output']} mode=lyrics "
                  f"aligned={result['aligned_words']}/{result['total_words']} "
                  f"lyrics_source={result['lyrics_source']}")
            continue
        if result["gap_new_ms"] is None:
            gap_info = (f"GAP n/a (re-created), original was {result['gap_old_ms']} ms")
        else:
            delta = result["gap_new_ms"] - result["gap_old_ms"]
            gap_info = (f"GAP {result['gap_old_ms']} -> {result['gap_new_ms']} ms "
                        f"(delta {delta:+.0f} ms)")
        print(f"{ULTRASINGER_HEAD} {bright_green_highlighted('Summary')} "
              f"{cyan_highlighted(song_name)}: {gap_info}, "
              f"aligned words {result['aligned_words']}/{result['total_words']}, "
              f"re-pitched notes {result['repitched']}"
              + (f" [{result['note']}]" if result.get("note") else ""))
        if result["gap_new_ms"] is None:
            print(f"REPAIR_DONE output={result['output']} "
                  f"gap_old_ms={result['gap_old_ms']} "
                  f"mode={result['mode']}")
        else:
            print(f"REPAIR_DONE output={result['output']} "
                  f"gap_old_ms={result['gap_old_ms']} "
                  f"gap_new_ms={result['gap_new_ms']} "
                  f"aligned={result['aligned_words']}/{result['total_words']} "
                  f"repitched={result['repitched']} mode={result['mode']}")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
