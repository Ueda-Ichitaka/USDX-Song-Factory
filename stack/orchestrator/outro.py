"""Where does the song end inside a (longer) music video?

About me: music videos often run past the recording - an outro scene,
talking, credits music. The global lyrics alignment sees voice-like sound
there and pulls the last lyric lines into it. This module finds the end of
the song so the aligner only sees the song itself:

* read_song_end(): an admin's song_end.txt in the song folder (exact end
  time, always wins).
* find_outro_cut(): automatic - only when the audio is at least
  OUTRO_MIN_EXTRA_S longer than the official recording length
  (MusicBrainz), and only at a clear pause (>= OUTRO_MIN_PAUSE_S, both
  stems quiet) that starts within -OUTRO_SEARCH_BEFORE_S /
  +OUTRO_SEARCH_AFTER_S of where the recording should end (first sound +
  recording length). Without such a pause nothing is cut.

Measured (stack/work/bench/bench12_outro.py + an12.py; 37 known-working
songs x 3 real video outros, appended and - as the dangerous case - put in
front): songs with an outro 81.0% -> 87.1% of word starts within 0.3 s
(perfect cut: 88.4%), songs with an intro unharmed (85.8% -> 86.0%, worst
single song -1.7 points).

Needs only numpy (host-testable).
"""

import os
import re

import numpy as np

SONG_END_FILE = "song_end.txt"
OUTRO_MIN_EXTRA_S = float(os.environ.get("OUTRO_MIN_EXTRA_S", "8"))
OUTRO_MIN_PAUSE_S = float(os.environ.get("OUTRO_MIN_PAUSE_S", "1.0"))
OUTRO_SEARCH_BEFORE_S = float(os.environ.get("OUTRO_SEARCH_BEFORE_S", "10"))
OUTRO_SEARCH_AFTER_S = float(os.environ.get("OUTRO_SEARCH_AFTER_S", "15"))
OUTRO_QUIET_BELOW_DB = 30.0   # quiet = this far below the loud (90th percentile) level
OUTRO_CUT_INTO_PAUSE_S = 0.5  # the cut sits this far into the pause
LEVEL_STEP_S = 0.25


def parse_time(text: str) -> float:
    """Seconds from "SS[.fff]", "M:SS[.fff]" or "H:MM:SS[.fff]" (seconds
    and minutes after a colon below 60). Raises ValueError otherwise."""
    text = text.strip()
    parts = text.split(":")
    if not text or len(parts) > 3 or any(p == "" for p in parts):
        raise ValueError(f"not a time: {text!r}")
    if not re.fullmatch(r"\d+(\.\d+)?", parts[-1]) or \
            not all(re.fullmatch(r"\d+", p) for p in parts[:-1]):
        raise ValueError(f"not a time: {text!r}")
    seconds = float(parts[-1])
    if len(parts) > 1 and seconds >= 60:
        raise ValueError(f"seconds must be below 60: {text!r}")
    if len(parts) == 3 and int(parts[1]) >= 60:
        raise ValueError(f"minutes must be below 60: {text!r}")
    for i, p in enumerate(reversed(parts[:-1])):
        seconds += int(p) * 60 ** (i + 1)
    return seconds


def read_song_end(song_dir: str):
    """The end time from song_end.txt in `song_dir`, or None when there is
    no such file or it holds no time line. Lines that are empty or start
    with "#" are skipped; the first other line is the time. Raises
    ValueError (naming the file) when that line is not a time."""
    path = os.path.join(song_dir, SONG_END_FILE)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                return parse_time(line)
            except ValueError as exc:
                raise ValueError(f"{SONG_END_FILE}: {exc}") from None
    return None


def _levels_db(samples, sample_rate: int):
    n = int(sample_rate * LEVEL_STEP_S)
    k = len(samples) // n
    frames = np.asarray(samples[:k * n], dtype=np.float64).reshape(k, n)
    return 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)


def find_outro_cut(vocals, instrumental, recording_seconds, sample_rate: int = 16000):
    """Time (s) where the song ends inside the audio, or None. See the
    module docstring for the rule; `vocals` and `instrumental` are the two
    separated stems (same length and rate)."""
    if not recording_seconds:
        return None
    n = min(len(vocals), len(instrumental))
    duration = n / sample_rate
    if duration - recording_seconds < OUTRO_MIN_EXTRA_S:
        return None
    mix = 10 * np.log10(10 ** (_levels_db(vocals[:n], sample_rate) / 10)
                        + 10 ** (_levels_db(instrumental[:n], sample_rate) / 10))
    loud = mix > np.percentile(mix, 90) - OUTRO_QUIET_BELOW_DB
    expected_end = float(np.argmax(loud)) * LEVEL_STEP_S + recording_seconds
    best = None
    k = 0
    while k < len(mix):
        if loud[k]:
            k += 1
            continue
        j = k
        while j < len(mix) and not loud[j]:
            j += 1
        start = k * LEVEL_STEP_S
        if (j - k) * LEVEL_STEP_S >= OUTRO_MIN_PAUSE_S and \
                expected_end - OUTRO_SEARCH_BEFORE_S <= start <= expected_end + OUTRO_SEARCH_AFTER_S:
            if best is None or abs(start - expected_end) < abs(best - expected_end):
                best = start
        k = j
    return None if best is None else best + OUTRO_CUT_INTO_PAUSE_S
