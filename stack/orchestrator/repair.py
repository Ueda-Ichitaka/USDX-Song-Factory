#!/usr/bin/env python3
"""Repair existing UltraStar songs.

Fixes two common defects of existing UltraStar songs:

  1. wrong #GAP - the initial offset between the start of the audio file
     and the first sung lyrics (mode: "gap")
  2. lyrics drifting out of sync mid-song - text and audio become
     asynchronous over time (mode: "sync", default)

A third mode, "lyrics", force-aligns trusted lyrics from a file and rebuilds
the notes: one note per syllable, plus `~` notes where a held syllable
changes pitch (held_notes.py). Lyrics at the end that are never sung are
left out (drop_unsung_tail()). `--traits` (the song's traits from the song list) switches single
steps: loud_high_instruments lets word ends follow held notes further down
(word_end_drop_db()), harsh_vocals pulls unsure notes towards the melody
(pitch_normalise.normalise_harsh_pitches()).

"gap" and "sync" keep the ORIGINAL lyrics/syllables of the song. Nothing is
re-transcribed; instead the known lyrics are force-aligned to the vocals
of the audio (wav2vec2 alignment via whisperx).

Modes
-----
gap             Separates the vocals, aligns the first lyric lines against
                them and measures where each of the first words really
                starts. #GAP moves to the median only when those values
                agree (decide_gap(), GAP_MAX_SPREAD_S); otherwise the
                original #GAP is kept and REPAIR_DONE says so. When no word
                is found near #GAP at all, the repair fails ("audio does not
                match the song file"); the reason names another recording
                of the song folder that seems to fit
                (describe_other_recordings()). Beats, durations and pitches
                are kept as-is.

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
Every song file written gets the format version new songs have
(song_header.py); one using headers that version lacks stays unversioned
and the log says why.

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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import anchors as anchors_file  # noqa: E402
import ctc_align  # noqa: E402
import held_notes  # noqa: E402
import outro  # noqa: E402
import pitch_normalise  # noqa: E402
from modules.Audio.convert_audio import convert_audio_to_mono_wav  # noqa: E402
from modules.Audio.denoise import denoise_vocal_audio  # noqa: E402
from modules.Audio.separation import DemucsModel  # noqa: E402
from modules.Audio.separation import separate_vocal_from_audio  # noqa: E402
from modules.Audio.silence_processing import get_silence_sections, mute_no_singing_parts  # noqa: E402
from modules.language_file import resolve_language_with_file  # noqa: E402
from modules.Midi.midi_creator import create_midi_note_from_pitched_data, find_nearest_index  # noqa: E402
from modules.Pitcher.pitcher import get_pitch_with_file  # noqa: E402
from modules.Pitcher.pitched_data_helper import CONFIDENCE_THRESHOLD  # noqa: E402
from modules.console_colors import (  # noqa: E402
    ULTRASINGER_HEAD,
    blue_highlighted,
    bright_green_highlighted,
    cyan_highlighted,
    gold_highlighted,
    green_highlighted,
    red_highlighted,
)
from song_header import FORMAT_VERSION, versioned_header  # noqa: E402

NOTE_LINE_TYPES = (":", "F", "R", "G", "*")
# type beat dur pitch [1 separator space] raw-word-field (verbatim)
NOTE_LINE_RE = re.compile(
    r"^\s*([:FRG*])[ \t]+(-?[0-9.,]+)[ \t]+(-?[0-9.,]+)[ \t]+(-?[0-9.,]+)[ \t]?(.*)$")
ALIGN_PAD = float(os.environ.get("REPAIR_ALIGN_PAD", "6.0"))
MIN_WORD_SCORE = float(os.environ.get("REPAIR_MIN_WORD_SCORE", "0.6"))
MAX_LOCAL_DEVIATION = float(os.environ.get("REPAIR_MAX_LOCAL_DEVIATION", "3.0"))
# bounded auto-retry on a poor lyrics-mode alignment (see
# run_alignment_with_retries()) - alignment has real run-to-run variance
# (window/anchor choices), so a poor first attempt is often not the best
# a given audio/text pairing can actually do
REPAIR_MAX_ALIGN_ATTEMPTS = int(os.environ.get("REPAIR_MAX_ALIGN_ATTEMPTS", "3"))
REPAIR_ALIGN_GOOD_ENOUGH_FRACTION = float(
    os.environ.get("REPAIR_ALIGN_GOOD_ENOUGH_FRACTION", "0.5"))
# a lyric line whose mean CTC posterior is below this was barely audible to
# the aligner (wordless vocals, mumbling): its placement is a guess. Only
# used for the log line - measured on 100 hand-synced songs, replacing
# such lines by interpolation made results worse, so nothing acts on it.
GLOBAL_CONFIDENT_LINE_SCORE = 0.25
# a word's letters can end up seconds apart where the aligner heard nothing
# (found live: one word got 25 s); hand-synced words are longer than 3 s
# only ~0.5% of the time, and capping changed no measured end error
MAX_ALIGNED_WORD_SECONDS = float(os.environ.get("MAX_ALIGNED_WORD_SECONDS", "4.0"))
# refinement of the global alignment (see refine_global_alignment()), tuned on
# 104 hand-synced songs: inside one hand-synced lyric line no pause between
# two words exceeds 2.6 s; a held note is still sounding while the vocal stem
# stays within 6 dB of the word's own level; aligned word starts are ~50 ms
# later than hand-synced ones (40 ms lead measured best)
LYRICS_LINE_OUTLIER_GAP_S = float(os.environ.get("LYRICS_LINE_OUTLIER_GAP_S", "3.0"))
WORD_END_EXTEND_DROP_DB = float(os.environ.get("WORD_END_EXTEND_DROP_DB", "6.0"))
WORD_END_EXTEND_MAX_S = float(os.environ.get("WORD_END_EXTEND_MAX_S", "3.0"))
# songs with the trait loud_high_instruments (song list, song_style.py):
# word ends follow the singing further down (bench19_ends.py, 7 hand-synced
# songs: long-note ends within 0.3 s 63.9% at 9 dB vs 61.7% at 6 dB; all
# other songs are best at 6 dB)
LOUD_INSTRUMENTS_WORD_END_DROP_DB = float(
    os.environ.get("LOUD_INSTRUMENTS_WORD_END_DROP_DB", "9.0"))
WORD_START_LEAD_S = float(os.environ.get("WORD_START_LEAD_S", "0.04"))
# lyrics never sung at the end (a video that fades out before the album
# lyrics end) are left out instead of squeezed in: each left-out letter
# costs this much in the global alignment (ctc_align end_penalty; measured
# on 110 hand-synced songs, stack/work/bench/bench26_tail.py: 3 loses sung
# words, 5-6 leaves most unsung words in). 0 = always place every word.
LYRICS_END_SKIP_PENALTY = float(os.environ.get("LYRICS_END_SKIP_PENALTY", "4.0"))
# a note's pitch is picked from at least this much audio around its centre -
# the pitch detector fails most on short notes. Read PITCH_READ_DELAY_S
# after the note: notes are placed like hand-made ones, which sit ~0.1 s
# before the singing (110 hand-made songs, stack/work/bench/
# bench21_syllables.py: right pitch class with the production word timing
# 48.0% -> 49.4%, with `~` notes (held_notes.py) 50.2%; 0.2 s window best
# together with the delay)
PITCH_MIN_WINDOW_S = float(os.environ.get("PITCH_MIN_WINDOW_S", "0.2"))
PITCH_READ_DELAY_S = float(os.environ.get("PITCH_READ_DELAY_S", "0.1"))
# gap check: a new #GAP is accepted only when the per-word implied gaps
# agree - spread between their 10th and 90th percentile at most this many
# seconds (measured on 110 hand-synced songs, stack/work/bench/
# bench14_gap.py: 64 accepted, 3 of them wrong by more than 1 s)
GAP_MAX_SPREAD_S = float(os.environ.get("GAP_MAX_SPREAD_S", "0.3"))
# real pauses for silence-aware seeding/interpolation (see
# seed_lyric_windows()/interpolate_word_timings()) - get_silence_sections()'s
# own default (50ms) flags ordinary inter-syllable micro-pauses WITHIN
# active singing too (verified live 2026-09-16: 83 detected "silences" on
# a real 194s song, most under 0.4s, scattered through a section that
# measured a steady -20dBFS throughout - not actually silent at all).
# Fragmenting a whole-song seeding pass on dozens of these compounds into
# badly wrong line positions; only a genuinely noticeable pause should
# ever be treated as skippable dead time.
LYRICS_MIN_SILENCE_MS = int(os.environ.get("LYRICS_MIN_SILENCE_MS", "1200"))
# distribute_over_non_silent_span()'s scale-to-fill-usable-span behavior
# is correct for a run whose natural total duration roughly matches the
# usable span, but a SPARSE run (1-2 dropped words) inside a wide,
# mostly-non-silent gap (a quiet instrumental passage, or singing Whisper
# just failed to match) got stretched by the same scale, gluing the
# word(s) to the far end of the gap instead of near their natural
# position. Verified live 2026-09-17 on real audio: Therion - Kali Yuga
# III's source line "Mistress of Time" (one line, no break) came out as
# "Mistress of" then "Time" 21s later, right before the next line;
# Letzte Instanz - Rapunzel's whole opening got compressed into the
# wrong span (GAP=5.97s vs real vocal onset ~20s). Capping how far the
# scale can stretch a run keeps sparse runs near their natural size and
# anchored at the near edge of the usable span instead of consuming all
# of it.
INTERP_MAX_STRETCH_FACTOR = float(
    os.environ.get("INTERP_MAX_STRETCH_FACTOR", "3.0"))
# lyrics-mode line-splitting (see split_long_lyric_units()) - a source's
# own line breaks alone can produce a "line" that's way too long on
# screen, or spans a real instrumental/breathing gap
LYRICS_MAX_LINE_CHARS = int(os.environ.get("LYRICS_MAX_LINE_CHARS", "60"))
LYRICS_LINE_BREAK_PAUSE_S = float(os.environ.get("LYRICS_LINE_BREAK_PAUSE_S", "2.5"))
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

def audio_from_video(video: str, work_dir: str):
    """(audio path, error) of a video's sound track, extracted into
    `work_dir` (an existing extraction is reused)."""
    ext = ".m4a" if video.lower().endswith(".mp4") else ".audio"
    audio_out = os.path.join(work_dir, os.path.basename(video) + ext)
    if not os.path.isfile(audio_out):
        import ffmpeg  # ffmpeg-python
        print(f"{ULTRASINGER_HEAD} extracting audio from {video}")
        try:
            # stream copy: fast, no quality loss - works whenever
            # the video's audio codec (usually AAC) is valid inside
            # an m4a/mp4 container. vn is a bare flag: ffmpeg-python
            # writes it without a value only for vn=None (vn=1 gives
            # "-vn 1", which ffmpeg reads as a second output "1")
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
            return audio_from_video(video, work_dir)
    return None, "no audio file found (#MP3/#AUDIO missing and no usable #VIDEO)"


MEDIA_AUDIO_EXTS = (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac")
MEDIA_VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v")


def other_media(song_dir: str, txt: Txt, current_audio: str) -> list:
    """The other recordings in a song folder: one {"stem", "audio",
    "video"} per file name without extension (audio or video may be None),
    sorted by name. The recording the song file references (#MP3/#AUDIO,
    #VIDEO, `current_audio`) is left out."""
    def stem(name):
        return os.path.splitext(os.path.basename(name))[0] if name else None

    referenced = {stem(txt.audio_ref), stem(txt.video_ref), stem(current_audio)} - {None}
    found = {}
    for name in sorted(os.listdir(song_dir)):
        path = os.path.join(song_dir, name)
        ext = os.path.splitext(name)[1].lower()
        kind = "audio" if ext in MEDIA_AUDIO_EXTS else "video" if ext in MEDIA_VIDEO_EXTS else None
        if not kind or not os.path.isfile(path) or stem(name) in referenced:
            continue
        entry = found.setdefault(stem(name), {"stem": stem(name), "audio": None, "video": None})
        entry[kind] = entry[kind] or path
    return [found[k] for k in sorted(found)]


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


def resolve_language(txt: Txt, audio_path: str, forced: str = None,
                     song_dir: str = None, out_dir: str = None) -> str:
    """Resolve the singing language, persisting `<out_dir>/<song_name>/
    language.txt` (the OUTPUT folder - never the original input song_dir,
    matching write_repaired()/write_lyrics_result()'s identical
    out_song_dir pattern) via modules.language_file. A saved file wins
    over both `forced` and the txt's own #LANGUAGE tag, per
    resolve_language_with_file()'s precedence; detect_language() (whisper
    tiny) only ever runs when nothing else is known."""
    input_lang = forced
    if not input_lang and txt.language:
        try:
            import langcodes
            input_lang = langcodes.find(txt.language).language
        except Exception as exc:  # noqa: BLE001
            print(f"{ULTRASINGER_HEAD} {red_highlighted('could not parse language tag')} "
                  f"({txt.language}): {exc}")

    if song_dir and out_dir:
        song_name = os.path.basename(song_dir.rstrip("/"))
        out_song_dir = os.path.join(out_dir, song_name)
        os.makedirs(out_song_dir, exist_ok=True)
        lang = resolve_language_with_file(
            out_song_dir, forced=input_lang,
            detect_fn=lambda: detect_language(audio_path))
    else:
        lang = input_lang or detect_language(audio_path)

    print(f"{ULTRASINGER_HEAD} using language: {blue_highlighted(lang)}")
    return lang


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


# does another recording fit the song file? (gap repair, when no word of the
# first lines is found near #GAP) - bench20_shift.py/an20.py on 110
# hand-synced songs vs. the same with #GAP moved 25 s: mean alignment score
# >= 0.6 and >= 40% of the first-word values in one 0.3 s window find 70%
# of fitting recordings and accept 6% of wrong ones
RECORDING_FIT_MIN_SCORE = 0.6
RECORDING_FIT_MIN_SHARE = 0.4
RECORDING_FIT_WINDOW_S = 0.3


def recording_fit(aligned_units, samples) -> dict:
    """{"score", "share", "n"} of a recording for the first lines: mean
    alignment score of the aligned lines, largest share of the per-word
    values (implied_gap_samples()) inside one RECORDING_FIT_WINDOW_S
    window, number of values."""
    scores = [u.get("score") for u in aligned_units if u.get("score") is not None]
    values = sorted(samples)
    best, j = 0, 0
    for i in range(len(values)):
        while values[i] - values[j] > RECORDING_FIT_WINDOW_S + 1e-9:
            j += 1
        best = max(best, i - j + 1)
    return {"score": sum(scores) / len(scores) if scores else 0.0,
            "share": best / len(values) if values else 0.0,
            "n": len(values)}


def recording_seems_to_fit(fit: dict) -> bool:
    """recording_fit() above the measured limits (at least three values)."""
    return (fit["n"] >= 3 and fit["score"] >= RECORDING_FIT_MIN_SCORE
            and fit["share"] >= RECORDING_FIT_MIN_SHARE)


def _percentile(sorted_values: list, q: float) -> float:
    """q-th percentile (0..1) with linear interpolation between the two
    nearest values (numpy's default)."""
    pos = q * (len(sorted_values) - 1)
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


def gap_spread(samples: list) -> float:
    """How far the per-word implied gaps disagree: 90th minus 10th
    percentile, in seconds."""
    values = sorted(samples)
    return _percentile(values, 0.9) - _percentile(values, 0.1)


def decide_gap(samples: list, old_gap_ms: int, max_spread: float = None) -> dict:
    """The gap check's verdict: the median of the per-word implied gaps
    becomes the new #GAP only when at least two of them agree within
    `max_spread` seconds (GAP_MAX_SPREAD_S); otherwise the original #GAP
    is kept. Returns {"gap_ms", "kept", "spread_s"}."""
    import statistics
    max_spread = GAP_MAX_SPREAD_S if max_spread is None else max_spread
    if len(samples) < 2:
        return {"gap_ms": old_gap_ms, "kept": True, "spread_s": None}
    spread = gap_spread(samples)
    if spread > max_spread:
        return {"gap_ms": old_gap_ms, "kept": True, "spread_s": spread}
    return {"gap_ms": round(statistics.median(samples) * 1000), "kept": False,
            "spread_s": spread}


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


def get_non_silent_subintervals(region_start: float, region_end: float,
                                silence_sections: list) -> list:
    """The parts of [region_start, region_end] NOT covered by any
    (start, end) tuple in `silence_sections`, in chronological order.
    Returns [(region_start, region_end)] unchanged when there is no
    silence to subtract."""
    if not silence_sections:
        return [(region_start, region_end)]
    clipped = []
    for s, e in silence_sections:
        s = max(s, region_start)
        e = min(e, region_end)
        if e > s:
            clipped.append((s, e))
    clipped.sort()
    result = []
    cursor = region_start
    for s, e in clipped:
        if s > cursor:
            result.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < region_end:
        result.append((cursor, region_end))
    return result


def distribute_over_non_silent_span(region_start: float, region_end: float,
                                    durations: list, silence_sections: list = None) -> list:
    """Proportionally distributes `durations` (original/scaffold word
    durations) across [region_start, region_end], packing forward and
    SKIPPING any real silence within the span (see
    get_non_silent_subintervals()) instead of blindly stretching words
    across a silent gap - the words are scaled to exactly fill whatever
    non-silent time actually exists, jumping to the next non-silent
    sub-interval once the current one fills up.

    Falls back to using the whole span (old, silence-blind behavior)
    when there is no usable non-silent time at all - never produces a
    degenerate/empty result."""
    total = sum(durations) or 1.0
    intervals = get_non_silent_subintervals(region_start, region_end, silence_sections)
    usable = sum(e - s for s, e in intervals)
    if usable <= 0:
        intervals = [(region_start, region_end)]
        usable = region_end - region_start

    scale = usable / total if total else 1.0
    scale = min(scale, INTERP_MAX_STRETCH_FACTOR)
    result = []
    interval_i = 0
    cursor = intervals[0][0]
    remaining = intervals[0][1] - intervals[0][0]
    for d in durations:
        need = d * scale
        while need > remaining and interval_i < len(intervals) - 1:
            interval_i += 1
            cursor = intervals[interval_i][0]
            remaining = intervals[interval_i][1] - intervals[interval_i][0]
        start = cursor
        end = min(start + need, intervals[interval_i][1])
        end = max(end, start + 0.01)
        result.append((start, end))
        consumed = end - start
        cursor = end
        remaining -= consumed
    return result


def interpolate_word_timings(flat_words: list, silence_sections: list = None,
                             audio_dur: float = None) -> None:
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

    `silence_sections` (optional [(start, end), ...] in seconds, real
    detected silence - see get_silence_sections()) makes the leading-run
    and middle-run cases skip real pauses within the gap instead of
    blindly stretching words across them - found live 2026-09-16 from
    real manual USDX testing across a full batch regeneration: without
    this, "the lyrics keep going" straight through an actual pause in the
    vocals, the single most commonly reported symptom. Omitting it (the
    default) keeps the old, silence-blind behavior unchanged.

    `audio_dur` (optional, seconds) clamps a TRAILING run (no aligned
    word anywhere after it) so it never ends past the real audio length -
    without this there is NO upper bound at all on a trailing run, so
    several consecutive lines failing alignment at the end of a song
    stack their natural durations one after another straight past the
    real end of the audio. Found live 2026-09-17 on real audio: Lord of
    the Lost - Beyond Beautiful's last lyric line ended at 259.5s on a
    239.45s audio file. Omitting it (the default) keeps the old,
    unclamped behavior unchanged."""
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
            if silence_sections is not None:
                # mirror time around nxt_start so the same forward-packing
                # distributor can anchor the run to END exactly at
                # nxt_start while still skipping real silence - a leading
                # run has the identical blind spot as a middle run
                # (verified live 2026-09-16: a wrong #GAP at the very
                # start of a song came from exactly this)
                mirrored_silence = [(nxt_start - e, nxt_start - s)
                                   for s, e in silence_sections if s < nxt_start]
                mirrored = distribute_over_non_silent_span(
                    0.0, nxt_start, list(reversed(orig_durs)), mirrored_silence)
                positions = [(nxt_start - e, nxt_start - s) for s, e in reversed(mirrored)]
                for w, (s, e) in zip(run, positions):
                    w["interp"] = (s, max(e, s + 0.03))
            else:
                span = min(total, max(0.1, nxt_start))
                # scale down like the middle-run branch below does -
                # without this, a leading run whose ORIGINAL total
                # duration exceeds the real time available before the
                # next aligned word (an ordinary situation: the opening
                # lines of a song, before the aligner locks on) overran
                # straight past that word's own start, corrupting the
                # very beginning of the repaired song
                scale = span / total
                cursor = nxt_start - span
                for w, d in zip(run, orig_durs):
                    scaled_d = d * scale
                    w["interp"] = (cursor, cursor + scaled_d)
                    cursor += scaled_d
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
            if audio_dur is not None:
                for w in run:
                    s, e = w["interp"]
                    e = min(e, audio_dur)
                    s = min(s, max(e - 0.03, 0.0))
                    w["interp"] = (s, e)
        elif silence_sections is not None:
            positions = distribute_over_non_silent_span(
                prv["end"], nxt["start"], orig_durs, silence_sections)
            for w, (s, e) in zip(run, positions):
                w["interp"] = (s, max(e, s + 0.03))
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

    # the folder UltraSinger wrote the song to: the newest one holding a
    # song file (an earlier run can have left folders in us_out, and
    # UltraSinger then writes to "<name> (1)")
    created = [os.path.join(us_out, d) for d in os.listdir(us_out)
               if os.path.isdir(os.path.join(us_out, d))]
    created = [d for d in created if find_ultrastar_txts(d)]
    if not created:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('no output created')}")
        return None
    src_dir = max(created, key=os.path.getmtime)
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        dst = os.path.join(out_song_dir, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.move(src, dst)
        elif os.path.isfile(src):
            os.remove(src)

    songs = find_ultrastar_txts(out_song_dir)
    return songs[0].path if songs else None


# --------------------------------------------------------------------------
# repair pipeline
# --------------------------------------------------------------------------

def describe_other_recordings(txt, song_dir: str, audio_path: str, work_dir: str,
                              device: str, align_first_units) -> str:
    """The second half of the gap repair's failure reason: which other
    recording of the song folder (other_media()) seems to fit the song file
    (recording_seems_to_fit(), best alignment score first), or that none
    does. `align_first_units(audio16k)` -> (aligned units, samples)."""
    tried, best = [], None
    for rec in other_media(song_dir, txt, audio_path):
        name = os.path.basename(rec["audio"] or rec["video"])
        rec_audio = rec["audio"]
        if rec_audio is None:
            rec_audio, err = audio_from_video(rec["video"], work_dir)
            if err:
                print(f"{ULTRASINGER_HEAD} {red_highlighted('skipped:')} {name}: {err}")
                continue
        tried.append(name)
        rec_vocals, _ = prepare_processing_audio(rec_audio, work_dir, device)
        fit = recording_fit(*align_first_units(load_audio_16k(rec_vocals)))
        print(f"{ULTRASINGER_HEAD} other recording {name}: alignment score "
              f"{fit['score']:.2f}, {fit['share']:.0%} of {fit['n']} first-word values agree")
        if recording_seems_to_fit(fit) and (best is None or fit["score"] > best[1]["score"]):
            best = (name, fit)
    if best:
        name, fit = best
        return (f"{name} seems to fit (alignment score {fit['score']:.2f}, "
                f"{fit['share']:.0%} of the first words agree) - set #MP3/#VIDEO to it by hand")
    if tried:
        return f"none of the other recordings in the folder fits ({', '.join(tried)})"
    return "no other recording in the folder"


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

    # raw separated vocals: the best signal for alignment, also for the gap
    # check (on the full mix it moves a correct start by more than 1 s in a
    # third of the songs, on the vocals in 9% - bench14_gap.py)
    print(f"{ULTRASINGER_HEAD} separating vocals (demucs, device={device})")
    vocals_audio, processing_audio = prepare_processing_audio(
        audio_path, work_dir, device)
    align_audio = vocals_audio

    audio16k = load_audio_16k(align_audio)
    audio_dur = len(audio16k) / 16000.0

    if align_model:
        import whisperx
        print(f"{ULTRASINGER_HEAD} loading align model {align_model}")
        model, meta = whisperx.load_align_model(language_code="en", device="cpu",
                                                model_name=align_model)
        lang = language or "en"
    else:
        lang = resolve_language(txt, align_audio, language, song_dir, out_dir)
        model, meta, lang = load_aligner(lang, "cpu")

    if mode == "gap":
        # align the first units to find the real start of the vocals
        first_units = units[:2]

        def align_first_units(audio):
            aligned_ = progressively_align(txt, first_units, model, meta, audio,
                                           len(audio) / 16000.0)
            return aligned_, implied_gap_samples(txt, first_units, aligned_)

        aligned, samples = align_first_units(audio16k)
        if not samples:
            # no word of the first lines near #GAP: the song file may be
            # timed to another recording in the same folder - named in the
            # reason, never switched automatically
            others = describe_other_recordings(txt, song_dir, audio_path, work_dir,
                                               device, align_first_units)
            msg = ("audio does not match the song file: no word of the first lines "
                   f"found near #GAP in {os.path.basename(audio_path)}; {others}")
            print(f"{ULTRASINGER_HEAD} {red_highlighted(msg)}")
            return None
        decision = decide_gap(samples, txt.gap_ms)
        if decision["kept"]:
            spread = decision["spread_s"]
            why = (f"spread {spread:.2f} s" if spread is not None
                   else "too few aligned words")
            print(f"{ULTRASINGER_HEAD} {red_highlighted('gap check unsure')} "
                  f"(the words disagree, {why}) - keeping #GAP {txt.gap_ms} ms")
        new_gap_ms = decision["gap_ms"]
        n_aligned = sum(1 for a in aligned for w in a["words"] if w)
        out_path = write_repaired(txt, song_dir, out_dir, gap_ms=new_gap_ms,
                                  note_times=None, pitches=None)
        return {
            "output": out_path,
            "gap_old_ms": txt.gap_ms, "gap_new_ms": new_gap_ms,
            "gap_kept": decision["kept"], "gap_spread_s": decision["spread_s"],
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


def set_header_tags(header: list, tags: dict) -> list:
    """The header lines (`#TAG:value`) with each of `tags` ({"GAP": "1000"})
    set: an existing line of that tag (any case) is replaced in place, a
    missing one is added after the last header line."""
    out = list(header)
    for tag, value in tags.items():
        prefix = f"#{tag.upper()}:"
        line = f"{prefix}{value}"
        for i, raw in enumerate(out):
            if raw.strip().upper().startswith(prefix):
                out[i] = line
                break
        else:
            out.append(line)
    return out


def _versioned(header: list) -> list:
    """song_header.versioned_header(), with the reason logged when the song
    stays unversioned."""
    out, reason = versioned_header(header)
    if reason:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('note:')} written without "
              f"#VERSION: the song uses {reason}, which format version "
              f"{FORMAT_VERSION} does not have")
    return out


def _with_header_tags(lines: list, tags: dict) -> list:
    """txt.lines-style dicts with set_header_tags() and versioning
    (versioned_header()) applied to the header block (the `#` lines before
    the first note or line break)."""
    end = 0
    while end < len(lines) and lines[end]["raw"].strip().startswith("#") \
            and "note" not in lines[end]:
        end += 1
    header = _versioned(set_header_tags([l["raw"] for l in lines[:end]], tags))
    return [{"raw": raw} for raw in header] + lines[end:]


def write_repaired(txt: Txt, song_dir: str, out_dir: str, gap_ms: int,
                   note_times, pitches):
    """Write the repaired song folder + txt (originals in input stay as-is).
    `#GAP` is set to `gap_ms` (added when the file has none)."""
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

    lines = _with_header_tags(lines, {"GAP": gap_ms})
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
    dropping empty lines / lines with no words. A token without any letter
    or digit (a standalone "-", "\u2013", quote mark, "~") is punctuation,
    not a sung word - it is dropped, and "text" is rebuilt from the kept
    words."""
    units = []
    for entry in lines_data:
        words = [w for w in (entry.get("text") or "").split()
                 if any(ch.isalnum() for ch in w)]
        if not words:
            continue
        units.append({"text": " ".join(words), "words": words, "start": entry.get("start")})
    return units


def seed_lyric_windows(units: list, scaffold_lines: list, txt: Txt,
                       audio_dur: float, silence_sections: list = None) -> None:
    """Fill in unit['seed_start']/unit['seed_end'] in place - the rough
    per-line time estimate progressively_align_lyrics() anchors its search
    windows on.

    Priority: real per-line timing from the lyrics source itself (e.g. a
    synced/LRC lookup) when EVERY unit has one; else map 1:1 onto the
    scaffold's existing lines when the counts match exactly; else
    distribute proportionally (by word count) across the scaffold's total
    vocal-active span (or the whole audio if there is no usable scaffold).

    The proportional tier is the DOMINANT path for real lyrics-mode jobs
    (an exact scaffold-line-count match is rare - editorial line breaks
    from a lyrics source almost never match whisper's own), so its
    blindness to real silence within the span was the true root cause
    behind badly-wrong seed windows even after later rescue attempts
    (verified live 2026-09-16 on real manual USDX testing). When
    `silence_sections` (real detected silence in seconds - see
    get_silence_sections()) is given, this tier packs lines into the
    real non-silent time only, via distribute_over_non_silent_span() -
    exactly the same mechanism interpolate_word_timings() uses for
    individual words."""
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
    total_end = min(audio_dur, total_end)
    word_counts = [len(u["words"]) for u in units]

    if silence_sections is not None:
        positions = distribute_over_non_silent_span(
            total_start, total_end, word_counts, silence_sections)
        for u, (s, e) in zip(units, positions):
            u["seed_start"], u["seed_end"] = s, min(audio_dur, e)
        return

    total_words = sum(word_counts) or 1
    span = max(0.1, total_end - total_start)
    cursor = total_start
    for u, wc in zip(units, word_counts):
        dur = span * (wc / total_words)
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


def build_syllables_from_lyric_units(units, aligned_units, language,
                                     silence_sections: list = None,
                                     audio_dur: float = None) -> list:
    """Hyphenate each word and distribute its (aligned or interpolated)
    time evenly across its syllables (reuses UltraSinger's OWN
    hyphenate_each_word/add_hyphen_to_data - the exact same functions a
    brand-new song's whisper transcription goes through).

    `silence_sections` (optional, real detected silence in seconds - see
    get_silence_sections()) is passed straight through to
    interpolate_word_timings() so unaligned words skip real pauses in the
    vocals instead of being stretched blindly across them.

    `audio_dur` (optional, seconds) is also passed straight through, so a
    trailing run of unaligned words at the very end of the song can never
    be placed past the real end of the audio.

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

    interpolate_word_timings(flat_words, silence_sections=silence_sections,
                             audio_dur=audio_dur)

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


def split_long_lyric_units(per_unit_syllables: list, max_chars: float = None,
                           pause_s: float = None) -> list:
    """Force an extra line break within a unit whenever it's too long on
    screen (`max_chars`) or has a long internal pause (silence gap,
    `pause_s`) - never in the middle of a word. A lyrics source's own
    line breaks (what each unit already is, one per build_syllables_
    from_lyric_units() entry) can easily produce a single "line" that's
    way too long, or that spans a real instrumental/breathing gap that
    should have been its own line break.

    A syllable's word_field ending in a space marks the end of a word
    (see build_syllables_from_lyric_units()) - only that kind of boundary
    is ever split on, so a hyphenated word's own syllables always stay
    together even if the whole word alone exceeds max_chars."""
    max_chars = LYRICS_MAX_LINE_CHARS if max_chars is None else max_chars
    pause_s = LYRICS_LINE_BREAK_PAUSE_S if pause_s is None else pause_s
    result = []
    for unit in per_unit_syllables:
        if not unit:
            result.append(unit)
            continue
        current = [unit[0]]
        current_chars = len(unit[0][0].strip())
        for prev, syl in zip(unit, unit[1:]):
            prev_word, _prev_start, prev_end = prev
            word, start, _end = syl
            at_word_boundary = prev_word.endswith(" ")
            pause = start - prev_end
            would_overflow = current_chars + len(word.strip()) > max_chars
            if at_word_boundary and (pause >= pause_s or would_overflow):
                result.append(current)
                current = []
                current_chars = 0
            current.append(syl)
            current_chars += len(word.strip())
        result.append(current)
    return result


def lyrics_txt_lines(per_unit_syllables: list) -> list:
    """The final, as-used lyric line text (one entry per output line) -
    reconstructed from per_unit_syllables (already split via
    split_long_lyric_units()) the same way write_lyrics_result() builds
    the actual sung syllables, so lyrics.txt reflects the real final
    song, not the raw fetched text before splitting/marker-stripping."""
    return ["".join(w for w, _, _ in unit).strip()
            for unit in per_unit_syllables if unit]


def pitch_window(start: float, end: float, min_len: float = None):
    """The span a note's pitch is picked from: the note itself, widened
    around its centre to at least `min_len` seconds (PITCH_MIN_WINDOW_S),
    never before 0."""
    min_len = PITCH_MIN_WINDOW_S if min_len is None else min_len
    if end - start >= min_len:
        return start, end
    extra = (min_len - (end - start)) / 2
    return max(0.0, start - extra), end + extra


def tilde_word_field(word_field: str, is_tilde: bool, is_last: bool) -> str:
    """The text of one part of a held syllable (held_notes.py): the syllable
    on the first part, `~` on the others; the word end (trailing space)
    goes to the last part."""
    text = "~" if is_tilde else word_field.rstrip(" ")
    if is_last and word_field.endswith(" "):
        text += " "
    return text


def sure_pitch_fraction(start: float, end: float, pitched,
                        threshold: float = CONFIDENCE_THRESHOLD) -> float:
    """Share of the pitch frames of [start, end] whose confidence is above
    `threshold` - the same frames and threshold
    create_midi_note_from_pitched_data() picks the note from."""
    first = find_nearest_index(pitched.times, start)
    last = find_nearest_index(pitched.times, end)
    confidences = (pitched.confidence[first:last] if last > first
                   else [pitched.confidence[first]])
    if len(confidences) == 0:
        return 0.0
    return sum(1 for c in confidences if c > threshold) / len(confidences)


def clamp_beat_to_max(beat: int, dur: int, prev_end_beat, max_beat: int = None):
    """Apply write_lyrics_result()'s monotonic-forward clamp (a note can
    never start before the previous note's own end, so notes render in
    chronological order), then - if `max_beat` is given - keep the note
    inside the audio: a note running over max_beat is shortened to end at
    it, and a note with no room left (it would start at or after max_beat)
    is dropped - returns None. Notes after the end of the audio are not
    sung; squeezing them onto the last beat made them overlap.

    Without the ceiling, a single real but slightly out-of-order timestamp
    anywhere in the song forces every LATER note forward by that same
    offset via the forward clamp alone, with nothing to stop it compounding
    indefinitely. Found live 2026-09-17 on real audio: Lord of the Lost -
    Beyond Beautiful's last note ended at 259.5s on audio only 239.45s long.

    Returns (beat, dur, new_prev_end_beat), or None for a dropped note."""
    if prev_end_beat is not None:
        beat = max(beat, prev_end_beat)
    if max_beat is not None:
        if beat >= max_beat:
            return None
        dur = max(1, min(dur, max_beat - beat))
    return beat, dur, beat + dur


def write_lyrics_result(txt: Txt, song_dir: str, out_dir: str,
                        per_unit_syllables: list, processing_audio: str,
                        audio_dur: float = None, traits=()):
    """Build a FRESH UltraStar txt from per-unit syllable lists (start/end
    in seconds), re-pitching every syllable with SwiftF0. Unlike
    write_repaired() (which maps new timing onto an EXISTING note
    sequence), this builds an entirely new note sequence - the
    syllable/line count is whatever the lyrics source produced, not the
    original file's, EXCEPT a unit too long on screen or spanning a long
    internal pause gets split further (see split_long_lyric_units()). A held
    syllable whose pitch changes level becomes the syllable plus `~` notes
    (held_notes.py). Pitches are read PITCH_READ_DELAY_S after the note, a
    short note's from at least PITCH_MIN_WINDOW_S around it, and the
    detected pitches are normalised for the whole song (octave errors
    folded back by whole octaves - see pitch_normalise.py; with the trait
    harsh_vocals unsure notes are also pulled towards the melody). BPM
    is kept from the scaffold (audio-only, lyrics don't change it);
    header tags are kept verbatim except #GAP."""
    per_unit_syllables = split_long_lyric_units(per_unit_syllables)
    song_name = os.path.basename(song_dir.rstrip("/"))
    out_song_dir = os.path.join(out_dir, song_name)
    os.makedirs(out_song_dir, exist_ok=True)

    for name in os.listdir(song_dir):
        src = os.path.join(song_dir, name)
        if os.path.isfile(src) and not name.lower().endswith(".txt"):
            dst = os.path.join(out_song_dir, name)
            if not os.path.isfile(dst):
                shutil.copy2(src, dst)
    # the admin's song_end.txt / anchors.txt stay with the song, so later
    # re-runs keep them
    for admin_file in (outro.SONG_END_FILE, anchors_file.ANCHORS_FILE):
        src = os.path.join(song_dir, admin_file)
        dst = os.path.join(out_song_dir, admin_file)
        if os.path.isfile(src) and os.path.abspath(src) != os.path.abspath(dst):
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

    # only header (#TAG:) lines are kept - notes are rebuilt below; #GAP is
    # set (added when the file has none, the notes are placed relative to it)
    # and the song is versioned (versioned_header())
    out_lines = _versioned(set_header_tags(
        [line["raw"] for line in txt.lines
         if "note" not in line and line["raw"].strip().startswith("#")],
        {"GAP": round(gap_s * 1000)}))

    max_beat = max(0, round(sec_to_beat(audio_dur - gap_s))) \
        if audio_dur is not None else None

    # first every note's detected pitch, then the whole song's pitches are
    # normalised together (pitch_normalise.py)
    notes, pitches, sure = [], [], []
    for ui, unit_syllables in enumerate(per_unit_syllables):
        for word_field, start, end in unit_syllables:
            start = max(start, gap_s)
            end = max(end, start + 0.05)
            parts = held_notes.split_held_note(
                pitched.times, pitched.frequencies, pitched.confidence,
                start, end, read_delay_s=PITCH_READ_DELAY_S)
            for part_start, part_end, is_tilde in parts:
                # a part of a held syllable is long enough to be read as is
                pitch_start, pitch_end = (
                    (part_start, part_end) if len(parts) > 1
                    else pitch_window(part_start, part_end))
                try:
                    seg = create_midi_note_from_pitched_data(
                        pitch_start + PITCH_READ_DELAY_S,
                        pitch_end + PITCH_READ_DELAY_S,
                        pitched, word_field.strip() or "~", None)
                    pitches.append(int(librosa.note_to_midi(seg.note)) - 48)
                except Exception:  # noqa: BLE001
                    pitches.append(0)
                sure.append(sure_pitch_fraction(part_start + PITCH_READ_DELAY_S,
                                                part_end + PITCH_READ_DELAY_S, pitched))
                notes.append((ui, tilde_word_field(word_field, is_tilde,
                                                   part_end == end),
                              part_start, part_end))
    if "harsh_vocals" in (traits or ()):
        pitches = pitch_normalise.normalise_harsh_pitches(pitches, sure)
    else:
        pitches = pitch_normalise.normalise_pitches(pitches)

    prev_end_beat = None
    current_unit = None
    dropped = 0
    for (ui, word_field, start, end), pitch in zip(notes, pitches):
        beat = max(0, round(sec_to_beat(start - gap_s)))
        dur = max(1, round(sec_to_beat(end - start)))
        placed = clamp_beat_to_max(beat, dur, prev_end_beat, max_beat)
        if placed is None:
            dropped += 1
            continue
        if current_unit is not None and ui != current_unit:
            out_lines.append(f"- {_fmt_num(prev_end_beat)}")
        current_unit = ui
        beat, dur, prev_end_beat = placed
        out_lines.append(f": {_fmt_num(beat)} {_fmt_num(dur)} "
                         f"{_fmt_num(pitch)} {word_field}")
    if dropped:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('warning:')} {dropped} "
              "syllable(s) did not fit before the end of the audio and were "
              "left out (the lyrics are longer than what is sung)")

    out_lines.append("E")

    out_path = os.path.join(out_song_dir, os.path.basename(txt.path))
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for raw in out_lines:
            f.write(raw.rstrip("\r\n") + "\n")

    # persist the final (post-splitting/marker-stripping) lyrics text
    # alongside the song - an admin can hand-correct line breaks/typos
    # here, and any FUTURE repair of this folder already prefers it as
    # the top-priority trusted source (see find_lyrics_file())
    lyrics_txt_path = os.path.join(out_song_dir, "lyrics.txt")
    with open(lyrics_txt_path, "w", encoding="utf-8", newline="\n") as f:
        for line in lyrics_txt_lines(per_unit_syllables):
            f.write(line + "\n")

    print(f"{ULTRASINGER_HEAD} {green_highlighted('wrote')} {out_path}")
    return out_path


def _anchored_timings(units, emission, meta, anchors):
    """Word timings like ctc_align.align_words_globally(), but aligned piece
    by piece: the lines before the first anchor on the audio before it,
    every anchored line and the lines up to the next anchor on the audio
    between the two anchor times. A piece whose audio is too short for its
    words leaves them untimed (interpolated later)."""
    frame = ctc_align.FRAME_SECONDS
    n_frames = emission.shape[0]
    bounds = [(0, 0.0)] + [(ui, t) for ui, t in anchors]
    timings = []
    for k, (first_unit, start_s) in enumerate(bounds):
        last_unit = bounds[k + 1][0] if k + 1 < len(bounds) else len(units)
        end_s = bounds[k + 1][1] if k + 1 < len(bounds) else n_frames * frame
        words = [w for u in units[first_unit:last_unit] for w in u["words"]]
        if not words:
            continue
        f0, f1 = int(round(start_s / frame)), min(n_frames, int(round(end_s / frame)))
        try:
            piece = ctc_align.align_words_globally(words, emission[f0:f1], meta)
        except ValueError:
            piece = [None] * len(words)
        offset = f0 * frame
        for timing in piece:
            if timing is None:
                timings.append(None)
            else:
                start, end, score = timing
                timings.append((start + offset, end + offset, score))
    return timings


def pin_anchor_words(aligned_units, units, anchors) -> None:
    """The first word of every anchored line starts exactly at its anchor
    time (an untimed one gets its natural length from there). Mutates
    `aligned_units` in place."""
    for ui, seconds in anchors or []:
        words = aligned_units[ui]["words"]
        if not words:
            continue
        if words[0]:
            words[0]["start"] = seconds
            words[0]["end"] = max(words[0]["end"], seconds + 0.03)
        else:
            words[0] = {"start": seconds, "score": 0.0,
                        "end": seconds + _natural_word_seconds(units[ui]["words"][0])}


def drop_unsung_tail(units: list, aligned_units: list):
    """Cut the lyrics after the last sung word: every word from the first
    ctc_align.NOT_SUNG on is left out (the path only leaves out an end, so
    they all follow it). Returns (units, aligned_units, words left out);
    a line sung only in part keeps its sung words."""
    for ui, aligned in enumerate(aligned_units):
        if ctc_align.NOT_SUNG not in aligned["words"]:
            continue
        wi = aligned["words"].index(ctc_align.NOT_SUNG)
        left_out = sum(len(u["words"]) for u in units[ui:]) - wi
        kept_units, kept_aligned = list(units[:ui]), list(aligned_units[:ui])
        if wi:
            words = units[ui]["words"][:wi]
            kept_units.append(dict(units[ui], words=words, text=" ".join(words)))
            timed = aligned["words"][:wi]
            scores = [w["score"] for w in timed if w]
            kept_aligned.append(dict(
                aligned, words=timed, text=" ".join(words),
                score=sum(scores) / len(scores) if scores else None,
                word_orig_starts=aligned["word_orig_starts"][:wi],
                word_orig_ends=aligned["word_orig_ends"][:wi]))
        return kept_units, kept_aligned, left_out
    return units, aligned_units, 0


def align_units_globally(units, model, meta, audio16k, device: str = "cpu",
                         anchors: list = None):
    """Force-align EVERY lyric word in one global, monotonic CTC pass (see
    ctc_align.py) and return (aligned_units, n_aligned) in the per-unit
    structure build_syllables_from_lyric_units() consumes:
    {"words": [timing-dict | None, ...], "text", "score", "word_orig_starts",
    "word_orig_ends"} - positional per unit. A word with nothing alignable
    stays None and is placed by interpolate_word_timings(); its natural
    duration (from its length) is only used to share out the gap between
    its timed neighbours.

    Replaces the per-line window search (seed_lyric_windows() ->
    progressively_align_lyrics() -> reanchor/rescue), whose windows let a
    repeated line re-match the previous occurrence and chained every later
    window off that mistake. If the GPU run fails (e.g. out of memory) the
    emissions are recomputed on the CPU.

    `anchors` ([(unit_index, seconds), ...] in lyric order, from
    anchors.txt) splits the alignment into pieces between the anchor times
    and pins each anchored line's first word to its time. Without anchors
    the words at the end that are never sung are left out
    (LYRICS_END_SKIP_PENALTY) and marked ctc_align.NOT_SUNG - see
    drop_unsung_tail()."""
    words = [w for u in units for w in u["words"]]
    try:
        emission = ctc_align.emissions_for_audio(model, meta, audio16k, device)
    except RuntimeError as exc:
        if device == "cpu":
            raise
        print(f"{ULTRASINGER_HEAD} {red_highlighted('aligner failed on')} "
              f"{device} ({exc}) - retrying on cpu")
        emission = ctc_align.emissions_for_audio(model, meta, audio16k, "cpu")
    if anchors:
        timings = _anchored_timings(units, emission, meta, anchors)
    else:
        timings = ctc_align.align_words_globally(
            words, emission, meta,
            end_penalty=LYRICS_END_SKIP_PENALTY if LYRICS_END_SKIP_PENALTY > 0 else None)

    aligned_units, n_aligned, k = [], 0, 0
    for unit in units:
        unit_words, unit_scores, starts, ends = [], [], [], []
        for word in unit["words"]:
            timing = timings[k]
            k += 1
            if timing == ctc_align.NOT_SUNG:
                unit_words.append(ctc_align.NOT_SUNG)
                starts.append(0.0)
                ends.append(_natural_word_seconds(word))
            elif timing:
                start, end, score = timing
                end = min(max(end, start + 0.03), start + MAX_ALIGNED_WORD_SECONDS)
                unit_words.append({"start": start, "end": end, "score": score})
                unit_scores.append(score)
                starts.append(start)
                ends.append(end)
                n_aligned += 1
            else:
                unit_words.append(None)
                starts.append(0.0)
                ends.append(_natural_word_seconds(word))
        aligned_units.append({
            "words": unit_words, "text": unit["text"],
            "score": sum(unit_scores) / len(unit_scores) if unit_scores else None,
            "word_orig_starts": starts, "word_orig_ends": ends,
        })
    pin_anchor_words(aligned_units, units, anchors)
    return aligned_units, n_aligned


def _natural_word_seconds(word: str) -> float:
    """Length given to a word placed without its own timing."""
    return 0.1 + 0.06 * len(word)


def place_line_outliers(aligned_units, units, max_gap: float) -> None:
    """Inside each lyric line, split the timed words wherever two
    neighbours are more than `max_gap` seconds apart and keep the largest
    cluster (ties: the better mean score). A word that far from the rest of
    its own line was matched to some other sound (an intro chant, a backing
    vocal); it is placed back next to its line at natural length: words
    before the kept cluster end where it starts, words after it start where
    it ends, in lyric order, with score 0.0. Mutates `aligned_units` in
    place."""
    for unit, lyric_unit in zip(aligned_units, units):
        words = unit["words"]
        timed = [i for i, w in enumerate(words) if w]
        if len(timed) < 2:
            continue
        clusters = [[timed[0]]]
        for a, b in zip(timed, timed[1:]):
            if words[b]["start"] - words[a]["end"] > max_gap:
                clusters.append([])
            clusters[-1].append(b)
        if len(clusters) == 1:
            continue
        keep = max(clusters, key=lambda c: (
            len(c), sum(words[i]["score"] for i in c) / len(c)))
        outliers = [i for c in clusters if c is not keep for i in c]
        cursor = words[keep[0]]["start"]
        for i in sorted((i for i in outliers if i < keep[0]), reverse=True):
            length = _natural_word_seconds(lyric_unit["words"][i])
            words[i] = {"start": cursor - length, "end": cursor, "score": 0.0}
            cursor -= length
        cursor = words[keep[-1]]["end"]
        for i in sorted(i for i in outliers if i > keep[-1]):
            length = _natural_word_seconds(lyric_unit["words"][i])
            words[i] = {"start": cursor, "end": cursor + length, "score": 0.0}
            cursor += length


def place_leading_words(aligned_units, units, silence_sections) -> None:
    """Give the untimed words before the song's first timed word a timing:
    packed backwards from that word at their natural length (0.1 s + 0.06 s
    per character), inside the nearest non-silent stretch before it that
    offers at least 0.15 s per word, compressed when that stretch is
    shorter than their natural length. With no such stretch they end at the
    first timed word. Placed words get score 0.0. Mutates `aligned_units`
    in place."""
    leading = []
    first_start = None
    for ui, unit in enumerate(aligned_units):
        for wi, word in enumerate(unit["words"]):
            if word:
                first_start = word["start"]
                break
            leading.append((ui, wi))
        if first_start is not None:
            break
    if first_start is None or not leading:
        return
    natural = [_natural_word_seconds(units[ui]["words"][wi]) for ui, wi in leading]
    need = 0.15 * len(leading)
    lo, hi = 0.0, first_start
    for start, end in reversed(get_non_silent_subintervals(
            0.0, first_start, silence_sections)):
        if end - start >= need:
            lo, hi = start, end
            break
    total = sum(natural)
    span = min(total, hi - lo)
    scale = span / total
    cursor = hi - span
    for (ui, wi), dur in zip(leading, natural):
        aligned_units[ui]["words"][wi] = {
            "start": cursor, "end": cursor + dur * scale, "score": 0.0}
        cursor += dur * scale


def frame_levels_db(audio16k):
    """Level (dBFS) of every 20 ms frame of a 16 kHz track - the same frame
    grid as the aligner's emissions."""
    import numpy as np
    samples = np.asarray(audio16k, dtype=np.float32)
    n_frames = len(samples) // ctc_align.FRAME_SAMPLES
    frames = samples[:n_frames * ctc_align.FRAME_SAMPLES].reshape(
        n_frames, ctc_align.FRAME_SAMPLES)
    return 10.0 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)


def extend_word_ends(aligned_units, levels_db, drop_db: float,
                     max_ext: float) -> None:
    """The aligner ends a word at its last recognised letter, so a held
    note ends too early. Extend each timed word's end while the vocal stem
    stays within `drop_db` of the word's own mean level, by at most
    `max_ext` seconds, never closer than 40 ms to the next timed word and
    never beyond MAX_ALIGNED_WORD_SECONDS. A word is never shortened.
    Mutates `aligned_units` in place."""
    frame_s = ctc_align.FRAME_SECONDS
    timed = [w for unit in aligned_units for w in unit["words"] if w]
    n_levels = len(levels_db)
    for i, word in enumerate(timed):
        first = int(word["start"] / frame_s)
        last = max(first + 1, int(word["end"] / frame_s))
        own = (sum(levels_db[first:last]) / (last - first)
               if last <= n_levels else -120.0)
        threshold = own - drop_db
        if i + 1 < len(timed):
            next_start = timed[i + 1]["start"] - 0.04
        else:
            next_start = word["end"] + max_ext
        limit = min(next_start, word["start"] + MAX_ALIGNED_WORD_SECONDS,
                    word["end"] + max_ext)
        frame = int(word["end"] / frame_s)
        while (frame < n_levels and (frame + 1) * frame_s <= limit + 1e-9
               and levels_db[frame] > threshold):
            frame += 1
        word["end"] = max(word["end"], min(limit, frame * frame_s))


def lead_word_starts(aligned_units, lead_s: float) -> None:
    """Move every timed word's start `lead_s` earlier (never below 0); the
    aligner places starts slightly later than a hand-synced song does.
    Mutates `aligned_units` in place."""
    for unit in aligned_units:
        for word in unit["words"]:
            if word:
                word["start"] = max(0.0, word["start"] - lead_s)


def word_end_drop_db(traits) -> float:
    """How far below a word's level its held note may fall before the word
    ends (extend_word_ends()), for a song with these traits."""
    if "loud_high_instruments" in (traits or ()):
        return LOUD_INSTRUMENTS_WORD_END_DROP_DB
    return WORD_END_EXTEND_DROP_DB


def refine_global_alignment(aligned_units, units, audio16k,
                            silence_sections, traits=()) -> None:
    """Precision pass over align_units_globally()'s result, in the order it
    was measured in: line outliers back next to their line, leading words
    directly before the first timed word, word ends follow held notes
    (word_end_drop_db() of the song's traits), starts lead by
    WORD_START_LEAD_S. Mutates `aligned_units` in place."""
    place_line_outliers(aligned_units, units, LYRICS_LINE_OUTLIER_GAP_S)
    place_leading_words(aligned_units, units, silence_sections)
    extend_word_ends(aligned_units, frame_levels_db(audio16k),
                     word_end_drop_db(traits), WORD_END_EXTEND_MAX_S)
    lead_word_starts(aligned_units, WORD_START_LEAD_S)


def run_alignment_with_retries(align_attempt_fn, max_attempts: int = None):
    """Run `align_attempt_fn()` (no args - performs ONE full alignment
    attempt, returns (units, aligned_units, n_aligned, n_total)) up to
    `max_attempts` times. Alignment has real run-to-run variance (window/
    anchor choices) - a poor first attempt is often not the best a given
    audio/text pairing can actually do. Stops early the first time an
    attempt's aligned-word fraction reaches REPAIR_ALIGN_GOOD_ENOUGH_
    FRACTION; otherwise always returns whichever attempt scored best,
    even if none reached it - never loops unboundedly, and never discards
    a decent-but-imperfect attempt just because a later one was worse."""
    max_attempts = REPAIR_MAX_ALIGN_ATTEMPTS if max_attempts is None else max_attempts
    best = None
    best_fraction = -1.0
    for attempt in range(1, max_attempts + 1):
        result = align_attempt_fn()
        _units, _aligned, n_aligned, n_total = result
        fraction = (n_aligned / n_total) if n_total else 0.0
        print(f"{ULTRASINGER_HEAD} lyrics alignment attempt {attempt}/{max_attempts}: "
              f"{n_aligned}/{n_total} words aligned ({fraction:.0%})")
        if fraction > best_fraction:
            best_fraction = fraction
            best = result
        if fraction >= REPAIR_ALIGN_GOOD_ENOUGH_FRACTION or attempt == max_attempts:
            break
    return best


def determine_song_end(song_dir: str, vocals_path: str, audio16k,
                       recording_seconds: float = None, anchor_end: float = None):
    """(end_seconds, source) of the song inside the audio, or (None, None)
    when the whole audio belongs to the song. An admin's song_end.txt in
    `song_dir` wins (an end at or after the audio end means: no outro) -
    unless anchors.txt has an end line (`anchor_end`), which wins over it; an
    unreadable one is reported and ignored. Otherwise, with a known
    recording length, outro.find_outro_cut() looks for a pause near the
    official end, using the instrumental stem next to `vocals_path`."""
    duration = len(audio16k) / 16000.0
    if anchor_end is not None:
        return (anchor_end, anchors_file.ANCHORS_FILE) if anchor_end < duration else (None, None)
    try:
        end = outro.read_song_end(song_dir)
    except ValueError as exc:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('warning:')} {exc} - ignored")
        end = None
    if end is not None:
        return (end, outro.SONG_END_FILE) if end < duration else (None, None)
    if recording_seconds:
        instrumental = load_audio_16k(
            os.path.join(os.path.dirname(vocals_path), "no_vocals.wav"))
        cut = outro.find_outro_cut(audio16k, instrumental, recording_seconds)
        if cut is not None and cut < duration:
            return cut, "outro detection"
    return None, None


def repair_txt_with_lyrics(txt: Txt, lyrics_units: list, song_dir: str, out_dir: str,
                           device: str, work_dir: str, language: str = None,
                           align_model: str = None, recording_seconds: float = None,
                           traits=None):
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
    # real detected silence (processing_audio is already muted wherever
    # this found silence, so it's cheap and reliable to scan here) - lets
    # the interpolation of words the aligner could not place skip real
    # pauses instead of stretching them across (see interpolate_word_timings())
    silence_sections = get_silence_sections(
        processing_audio, min_silence_len=LYRICS_MIN_SILENCE_MS)

    audio16k = load_audio_16k(vocals_audio)
    audio_dur = len(audio16k) / 16000.0
    # a video outro (scene, talking, credits music) is cut off before the
    # alignment - otherwise the last lyric lines get pulled into it
    try:
        anchor_data = anchors_file.read_anchors(song_dir)
    except ValueError as exc:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('warning:')} {exc} - "
              f"{anchors_file.ANCHORS_FILE} ignored")
        anchor_data = None
    song_end, end_source = determine_song_end(
        song_dir, vocals_audio, audio16k, recording_seconds,
        anchor_end=anchor_data["end"] if anchor_data else None)
    if song_end is not None:
        print(f"{ULTRASINGER_HEAD} song ends at {song_end:.1f}s ({end_source}) - "
              f"the last {audio_dur - song_end:.1f}s are not used for the lyrics")
        audio16k = audio16k[:int(song_end * 16000)]
        audio_dur = song_end

    if align_model:
        import whisperx
        model, meta = whisperx.load_align_model(
            language_code="en", device="cpu", model_name=align_model)
        lang = language or "en"
    else:
        lang = resolve_language(txt, vocals_audio, language, song_dir, out_dir)
        model, meta, lang = load_aligner(lang, "cpu")

    anchored = None
    if anchor_data and anchor_data["lines"]:
        try:
            anchored = anchors_file.match_anchor_units(
                anchor_data["lines"], [u["text"] for u in lyrics_units])
            anchored = [(ui, t) for ui, t in anchored if t < audio_dur] or None
            print(f"{ULTRASINGER_HEAD} {len(anchored or [])} anchored line(s) from "
                  f"{anchors_file.ANCHORS_FILE}")
        except ValueError as exc:
            print(f"{ULTRASINGER_HEAD} {red_highlighted('warning:')} {exc} - "
                  "anchors ignored")
            anchored = None
    aligned_units, n_aligned = align_units_globally(
        lyrics_units, model, meta, audio16k, device, anchors=anchored)
    n_total = sum(len(u["words"]) for u in lyrics_units)
    confident = sum(1 for a in aligned_units
                    if a["score"] is not None and a["score"] >= GLOBAL_CONFIDENT_LINE_SCORE)
    print(f"{ULTRASINGER_HEAD} global lyrics alignment: {n_aligned}/{n_total} "
          f"words placed, {confident}/{len(aligned_units)} lines confident")

    lyrics_units, aligned_units, n_unsung = drop_unsung_tail(lyrics_units, aligned_units)
    if n_unsung:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('warning:')} the last "
              f"{n_unsung} lyric word(s) were not found in the audio and are "
              "left out (lyrics longer than the recording)")

    if n_total and n_aligned / n_total < 0.5:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('warning:')} only "
              f"{n_aligned}/{n_total} words aligned confidently - the "
              "supplied lyrics may not match this audio well; result kept "
              "anyway (lyrics mode never falls back to re-transcription)")

    refine_global_alignment(aligned_units, lyrics_units, audio16k, silence_sections,
                            traits=traits or ())
    if anchored:
        pin_anchor_words(aligned_units, lyrics_units, anchored)

    per_unit_syllables = build_syllables_from_lyric_units(
        lyrics_units, aligned_units, lang, silence_sections=silence_sections,
        audio_dur=audio_dur)
    out_path = write_lyrics_result(
        txt, song_dir, out_dir, per_unit_syllables, processing_audio,
        audio_dur=audio_dur, traits=traits or ())
    if out_path is None:
        return None

    return {"output": out_path, "aligned_words": n_aligned, "total_words": n_total}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def parse_traits_arg(value) -> list:
    """The song's traits from `--traits a,b` (codes of
    orchestrator/song_style.py, already checked by the orchestrator)."""
    return [t.strip() for t in (value or "").split(",") if t.strip()]


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
    parser.add_argument("--recording-seconds", type=float, default=None,
                        help="mode=lyrics only: official length of the recording "
                             "(MusicBrainz); a video that runs longer gets its "
                             "outro cut off at a pause near that length "
                             "(see outro.py; a song_end.txt in the song folder wins)")
    parser.add_argument("--traits", default=None,
                        help="the song's vocal/instrument traits from the song "
                             "list, comma-joined (harsh_vocals, operatic_vocals, "
                             "loud_high_instruments, choir); "
                             "loud_high_instruments lets word ends follow held "
                             "notes further down (word_end_drop_db())")
    args = parser.parse_args()
    args.language = args.language or os.environ.get("REPAIR_LANGUAGE")
    traits = parse_traits_arg(args.traits)
    if traits:
        print(f"{ULTRASINGER_HEAD} song traits: {', '.join(traits)}")

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
                    work_dir, args.language, args.align_model,
                    recording_seconds=args.recording_seconds, traits=traits)
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
            gap_check = ""
            if "gap_kept" in result:
                spread = result.get("gap_spread_s")
                gap_check = (f"gap_check={'kept' if result['gap_kept'] else 'moved'} "
                             f"gap_spread_s={'none' if spread is None else f'{spread:.2f}'} ")
            print(f"REPAIR_DONE output={result['output']} "
                  f"gap_old_ms={result['gap_old_ms']} "
                  f"gap_new_ms={result['gap_new_ms']} "
                  f"{gap_check}"
                  f"aligned={result['aligned_words']}/{result['total_words']} "
                  f"repitched={result['repitched']} mode={result['mode']}")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
