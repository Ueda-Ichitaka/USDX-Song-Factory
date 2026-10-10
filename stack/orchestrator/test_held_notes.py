#!/usr/bin/env python3
"""Tests for held_notes.py.

About me: plain assert-based checks (same style as the other orchestrator
tests) for splitting a held syllable into the syllable plus `~` notes where
its pitch changes level. numpy only, runs on the host:

    python3 test_held_notes.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import held_notes  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


HOP = 0.016


def track(segments, conf=0.9):
    """Pitch track from [(seconds, midi), ...] pieces, starting at 0."""
    times, freqs = [], []
    t = 0.0
    for dur, midi in segments:
        for _ in range(int(round(dur / HOP))):
            times.append(t)
            freqs.append(440.0 * 2 ** ((midi - 69) / 12))
            t += HOP
    return np.array(times), np.array(freqs), np.full(len(times), conf)


def split(tr, start, end, **kw):
    times, freqs, confs = tr
    return held_notes.split_held_note(times, freqs, confs, start, end, **kw)


# --------------------------------------------------------------------------
# split_held_note()
# --------------------------------------------------------------------------

steady = track([(1.0, 60)])
check("a note shorter than min_note_s stays one note",
      split(steady, 0.0, 0.25) == [(0.0, 0.25, False)])
check("a long note on one pitch stays one note",
      split(steady, 0.0, 1.0) == [(0.0, 1.0, False)])

two = track([(0.5, 60), (0.5, 64)])
parts = split(two, 0.0, 1.0)
check("a long note that moves up 4 semitones becomes the note plus a ~ note",
      len(parts) == 2 and parts[0][2] is False and parts[1][2] is True)
check("the ~ note starts where the pitch changes (within one frame)",
      len(parts) == 2 and abs(parts[1][0] - 0.5) <= HOP + 1e-9)
check("the parts cover the note without gaps",
      len(parts) == 2 and parts[0][0] == 0.0 and parts[-1][1] == 1.0
      and parts[0][1] == parts[1][0])

vibrato = track([(0.1, 60.3), (0.1, 59.7)] * 5)
check("vibrato around one pitch stays one note",
      len(split(vibrato, 0.0, 1.0)) == 1)

octave = track([(0.3, 60), (0.1, 72), (0.3, 60), (0.1, 72), (0.2, 60)])
check("octave errors of the detector do not split a steady note",
      len(split(octave, 0.0, 1.0)) == 1)

unsure = track([(1.0, 60)], conf=0.0)
check("a note without confident frames stays one note",
      split(unsure, 0.0, 1.0) == [(0.0, 1.0, False)])

short_step = track([(0.45, 60), (0.08, 64), (0.47, 60)])
check("no part is shorter than min_part_s",
      all(e - s >= 0.12 - 1e-9 for s, e, _ in split(short_step, 0.0, 1.0)))

three = track([(0.4, 60), (0.4, 63), (0.4, 67)])
check("a note through three levels becomes the note plus two ~ notes",
      [p[2] for p in split(three, 0.0, 1.2)] == [False, True, True])
check("a high penalty keeps the note whole",
      len(split(three, 0.0, 1.2, penalty=1e6)) == 1)

shifted = track([(0.6, 60), (0.6, 64)])
parts = split(shifted, 0.0, 1.0, read_delay_s=0.1)
check("read_delay_s reads the track later but returns note times",
      len(parts) == 2 and abs(parts[1][0] - 0.5) <= HOP + 1e-9
      and parts[0][0] == 0.0 and parts[-1][1] == 1.0)

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("all passed")
