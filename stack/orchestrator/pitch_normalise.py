"""Keep generated note pitches at bay: octave folding and unsure-note filling.

About me: pitch detection on separated vocals often lands an octave or two
off (harmonics, screams, growls) or guesses on unpitched sound, so generated
songs spread over ~28 semitones (5th-95th percentile) where hand-made songs
span ~12, and 15% of neighbouring notes jump by more than an octave (hand-
made: 0.3%). Three steps fix that without touching what USDX scores:

1. fill_unsure_notes(): a note with too few confident pitch frames takes the
   local melody level (rolling median of the confident notes near it).
2. fold_octaves(): a note more than `tolerance` semitones away from the
   rolling median of its neighbours moves by whole octaves towards it.
3. fold_to_song_range(): a note still more than an octave away from the
   song's median moves by whole octaves towards it.

USDX moves the singer's tone into the note's octave before comparing
(UNote.pas: "move players tone to proper octave"), so whole-octave moves
never change scoring - only where the note bars are drawn. Measured on 110
hand-made songs (stack/work/bench/an9.py): range 28 -> 13 semitones, jumps
> 12 semitones 15.2% -> 0.2%, notes > 12 semitones from the song median
13.4% -> 0.3%, neighbouring intervals within 1 semitone of the hand-made
ones 47.6% -> 55.1%.

Pure standard library (host-testable).
"""

import os
import statistics

PITCH_FOLD_WINDOW = int(os.environ.get("PITCH_FOLD_WINDOW", "5"))
PITCH_FOLD_TOLERANCE = float(os.environ.get("PITCH_FOLD_TOLERANCE", "7"))
PITCH_SONG_TOLERANCE = float(os.environ.get("PITCH_SONG_TOLERANCE", "12"))
PITCH_UNSURE_MIN_FRACTION = float(os.environ.get("PITCH_UNSURE_MIN_FRACTION", "0.3"))
FOLD_ITERATIONS = 3


def rolling_median(values: list, k: int) -> list:
    """Median of each value and its k neighbours on each side (fewer at the
    edges)."""
    n = len(values)
    return [float(statistics.median(values[max(0, i - k):min(n, i + k + 1)]))
            for i in range(n)]


def fold_octaves(pitches: list, k: int = None, tolerance: float = None,
                 iterations: int = FOLD_ITERATIONS) -> list:
    """Move every note that is more than `tolerance` semitones away from
    the rolling median of its neighbours by the whole number of octaves
    that brings it closest; repeated `iterations` times so a cluster of
    wrong notes cannot hold the median. Only whole octaves are applied."""
    k = PITCH_FOLD_WINDOW if k is None else k
    tolerance = PITCH_FOLD_TOLERANCE if tolerance is None else tolerance
    result = list(pitches)
    for _ in range(iterations):
        reference = rolling_median(result, k)
        result = [p - 12 * round((p - ref) / 12) if abs(p - ref) > tolerance else p
                  for p, ref in zip(result, reference)]
    return result


def fold_to_song_range(pitches: list, tolerance: float = None) -> list:
    """Move every note more than `tolerance` semitones away from the song's
    median pitch by whole octaves towards it - fold_octaves() alone lets a
    long wrong stretch drift, this bounds the whole song."""
    tolerance = PITCH_SONG_TOLERANCE if tolerance is None else tolerance
    if not pitches:
        return []
    median = statistics.median(pitches)
    return [p - 12 * round((p - median) / 12) if abs(p - median) > tolerance else p
            for p in pitches]


def fill_unsure_notes(pitches: list, sure_fractions: list,
                      min_fraction: float = None, k: int = None) -> list:
    """A note whose share of confident pitch frames is below
    `min_fraction` takes the rolling median (over the confident notes only)
    at the nearest confident note. With fewer than 3 confident notes there
    is no melody to lean on and nothing changes."""
    min_fraction = PITCH_UNSURE_MIN_FRACTION if min_fraction is None else min_fraction
    k = PITCH_FOLD_WINDOW if k is None else k
    sure_idx = [i for i, f in enumerate(sure_fractions) if f >= min_fraction]
    if len(sure_idx) < 3:
        return list(pitches)
    reference = rolling_median([pitches[i] for i in sure_idx], k)
    result = list(pitches)
    for i, fraction in enumerate(sure_fractions):
        if fraction < min_fraction:
            nearest = min(range(len(sure_idx)), key=lambda j: abs(sure_idx[j] - i))
            result[i] = reference[nearest]
    return result


def normalise_pitches(pitches: list, sure_fractions: list) -> list:
    """fill_unsure_notes(), fold_octaves(), fold_to_song_range(), as whole
    semitones."""
    return [int(round(p)) for p in fold_to_song_range(
        fold_octaves(fill_unsure_notes(pitches, sure_fractions)))]
