#!/usr/bin/env python3
"""Tests for pitch_normalise.py.

About me: plain assert-based checks (same style as the other orchestrator
tests) for the octave folding and unsure-note filling applied to generated
note pitches. Pure standard library, runs on the host:

    python3 test_pitch_normalise.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pitch_normalise as pn  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# rolling_median()
# --------------------------------------------------------------------------

check("rolling_median: window of k neighbours on each side, shrinking at the edges",
      pn.rolling_median([1, 5, 2, 8, 3], 1) == [3.0, 2.0, 5.0, 3.0, 5.5])

# --------------------------------------------------------------------------
# fold_octaves(): a note far from its neighbours moves by whole octaves
# --------------------------------------------------------------------------

melody = [4, 5, 7, 5, 4, 2, 4, 5, 7, 9, 7, 5]
check("fold_octaves: a melody within the tolerance stays unchanged",
      pn.fold_octaves(melody) == melody)

octave_error = [4, 5, 7, 5, 28, 2, 4, 5, 7, 9, 7, 5]
check("fold_octaves: a note two octaves too high comes back down "
      f"(got {pn.fold_octaves(octave_error)})",
      pn.fold_octaves(octave_error)[4] == 4)

too_low = [4, 5, 7, 5, -8, 2, 4, 5, 7, 9, 7, 5]
check("fold_octaves: a note an octave too low comes back up",
      pn.fold_octaves(too_low)[4] == 4)

leap = [4, 5, 7, 5, 11, 12, 11, 9, 7, 5, 4, 5]
check("fold_octaves: a real leap of up to 7 semitones is kept",
      pn.fold_octaves(leap) == leap)

check("fold_octaves: only whole octaves are ever applied (pitch class kept)",
      all((a - b) % 12 == 0 for a, b in zip(pn.fold_octaves(octave_error), octave_error)))

# real pitches of a screamed passage (Cypecore - The Hills Have Eyes)
scattered = [3, 47, 3, -5, -4, 48, -6, 48, 20, 47, 22, 36, 37, -5, 8, 5, 6, 2]
folded = pn.fold_octaves(scattered)
check(f"fold_octaves: a scattered sequence loses its multi-octave jumps - the "
      f"largest neighbour jump shrinks from 53 to under 24 (got {folded})",
      max(abs(a - b) for a, b in zip(folded, folded[1:])) < 24)

check("fold_octaves: an empty list stays empty", pn.fold_octaves([]) == [])

# --------------------------------------------------------------------------
# fold_to_song_range(): nothing further than an octave from the song median
# --------------------------------------------------------------------------

drifted = [3, 11, 3, 7, 8, 24, 18, 24, 20, 23, 22, 24, 25, 19, 20, 17, 18, 14]
bounded = pn.fold_to_song_range(drifted)
check(f"fold_to_song_range: every note within 12 semitones of the song median (got {bounded})",
      all(abs(p - 19) <= 12 for p in bounded))
check("fold_to_song_range: notes already within the range stay",
      bounded[5:] == drifted[5:])
check("fold_to_song_range: only whole octaves are applied",
      all((a - b) % 12 == 0 for a, b in zip(bounded, drifted)))
check("fold_to_song_range: an empty list stays empty", pn.fold_to_song_range([]) == [])

# --------------------------------------------------------------------------
# fill_unsure_notes(): notes without a confident pitch take the local level
# --------------------------------------------------------------------------

pitches = [4, 5, 40, 5, 4, 2, 4]
sure = [1.0, 1.0, 0.0, 0.9, 0.5, 1.0, 0.8]
filled = pn.fill_unsure_notes(pitches, sure)
check(f"fill_unsure_notes: an unsure note takes the median of its sure neighbours "
      f"(got {filled})",
      filled[2] == 4.0 and filled[:2] == [4, 5] and filled[3:] == [5, 4, 2, 4])
check("fill_unsure_notes: notes at or above the threshold are kept",
      pn.fill_unsure_notes([4, 30, 5, 6], [1.0, 0.3, 1.0, 1.0]) == [4, 30, 5, 6])
check("fill_unsure_notes: with fewer than 3 sure notes nothing changes",
      pn.fill_unsure_notes([4, 30, 5], [1.0, 0.0, 1.0]) == [4, 30, 5])

# --------------------------------------------------------------------------
# normalise_pitches(): fill, fold locally, fold to the song range, whole numbers
# --------------------------------------------------------------------------

result = pn.normalise_pitches([4, 5, 7, 5, 28, 2, 4, 5, 40, 9, 7, 5],
                              [1, 1, 1, 1, 1, 1, 1, 1, 0.1, 1, 1, 1])
check(f"normalise_pitches: unsure notes filled, octave errors folded, ints (got {result})",
      result[4] == 4 and abs(result[8] - 5) <= 2 and all(isinstance(p, int) for p in result))
check("normalise_pitches: same length as the input",
      len(result) == 12)
wide = pn.normalise_pitches(scattered, [1.0] * len(scattered))
check(f"normalise_pitches: the screamed passage ends up within an octave of its "
      f"median (got {wide})",
      all(abs(p - sorted(wide)[len(wide) // 2]) <= 12 for p in wide))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
