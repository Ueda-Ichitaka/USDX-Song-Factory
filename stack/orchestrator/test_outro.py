#!/usr/bin/env python3
"""Tests for outro.py.

About me: plain assert-based checks (same style as the other orchestrator
tests) for the video outro detection (pause near the official recording
end) and for reading an admin's song_end.txt. Needs only numpy, runs on
the host:

    python3 test_outro.py
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import outro  # noqa: E402

failures = []
SR = 16000


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


def track(parts):
    """Concatenate (seconds, amplitude) parts of steady noise (0 = silence)."""
    rng = np.random.default_rng(0)
    return np.concatenate([rng.uniform(-1, 1, int(s * SR)).astype(np.float32) * a
                           for s, a in parts])


# --------------------------------------------------------------------------
# parse_time()
# --------------------------------------------------------------------------

check("parse_time: plain seconds", outro.parse_time("274.5") == 274.5)
check("parse_time: minutes:seconds", outro.parse_time("4:34") == 274.0)
check("parse_time: minutes:seconds.fraction", outro.parse_time("4:34.5") == 274.5)
check("parse_time: hours:minutes:seconds", outro.parse_time("1:02:03.5") == 3723.5)
check("parse_time: surrounding spaces are ignored", outro.parse_time("  4:34 ") == 274.0)
for bad in ("", "abc", "4:75", "-3", "4:34:", "4,5"):
    try:
        outro.parse_time(bad)
        check(f"parse_time: {bad!r} is rejected", False)
    except ValueError:
        check(f"parse_time: {bad!r} is rejected", True)

# --------------------------------------------------------------------------
# read_song_end(): optional admin file in the song folder
# --------------------------------------------------------------------------

folder = tempfile.mkdtemp()
check("read_song_end: no file -> None", outro.read_song_end(folder) is None)
with open(os.path.join(folder, outro.SONG_END_FILE), "w", encoding="utf-8") as f:
    f.write("# the band keeps talking after the song\n\n4:34.5\n")
check("read_song_end: comment and blank lines skipped, first time line used",
      outro.read_song_end(folder) == 274.5)
with open(os.path.join(folder, outro.SONG_END_FILE), "w", encoding="utf-8") as f:
    f.write("﻿4:34\n")
check("read_song_end: a byte order mark (Windows editors) is ignored",
      outro.read_song_end(folder) == 274.0)
with open(os.path.join(folder, outro.SONG_END_FILE), "w", encoding="utf-8") as f:
    f.write("soon\n")
try:
    outro.read_song_end(folder)
    check("read_song_end: an unreadable time raises ValueError naming the file", False)
except ValueError as exc:
    check("read_song_end: an unreadable time raises ValueError naming the file",
          outro.SONG_END_FILE in str(exc))
with open(os.path.join(folder, outro.SONG_END_FILE), "w", encoding="utf-8") as f:
    f.write("# only a comment\n")
check("read_song_end: a file without a time line -> None", outro.read_song_end(folder) is None)

# --------------------------------------------------------------------------
# find_outro_cut(): pause near the official end of the recording
# --------------------------------------------------------------------------

# song 100 s, 2 s pause, 30 s outro (voice-like level in the vocal stem)
voc = track([(100, 0.3), (2, 0.0), (30, 0.05)])
ins = track([(100, 0.3), (2, 0.0), (30, 0.3)])
check(f"find_outro_cut: cuts 0.5 s into the pause at the official end (got "
      f"{outro.find_outro_cut(voc, ins, 100.0)})",
      outro.find_outro_cut(voc, ins, 100.0) == 100.5)
check("find_outro_cut: nothing when the video is less than 8 s longer",
      outro.find_outro_cut(voc, ins, 126.0) is None)
check("find_outro_cut: nothing without a recording length",
      outro.find_outro_cut(voc, ins, None) is None)

no_pause_v = track([(132, 0.3)])
check("find_outro_cut: nothing when there is no pause near the official end",
      outro.find_outro_cut(no_pause_v, no_pause_v, 100.0) is None)

far_v = track([(60, 0.3), (2, 0.0), (70, 0.3)])
check("find_outro_cut: a pause far before the official end is not used",
      outro.find_outro_cut(far_v, far_v, 100.0) is None)

short_v = track([(100, 0.3), (0.5, 0.0), (31.5, 0.3)])
check("find_outro_cut: a pause shorter than 1 s is not used",
      outro.find_outro_cut(short_v, short_v, 100.0) is None)

two_v = track([(95, 0.3), (2, 0.0), (9, 0.3), (2, 0.0), (24, 0.3)])
check(f"find_outro_cut: of two pauses the one nearest the official end wins "
      f"(got {outro.find_outro_cut(two_v, two_v, 100.0)})",
      outro.find_outro_cut(two_v, two_v, 100.0) == 95.5)

late_v = track([(5, 0.0), (100, 0.3), (2, 0.0), (25, 0.3)])
check(f"find_outro_cut: the official end counts from the first sound "
      f"(got {outro.find_outro_cut(late_v, late_v, 100.0)})",
      outro.find_outro_cut(late_v, late_v, 100.0) == 105.5)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
