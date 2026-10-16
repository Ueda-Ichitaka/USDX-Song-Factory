"""Keep generated note pitches at bay: octave folding.

About me: pitch detection on separated vocals often lands an octave or two
off (harmonics, screams, growls), so generated songs spread over ~28
semitones (5th-95th percentile) where hand-made songs span ~12, and 15% of
neighbouring notes jump by more than an octave (hand-made: 0.3%). Two steps
fix that without touching what USDX scores:

1. fold_octaves(): a note more than `tolerance` semitones away from the
   rolling median of its neighbours moves by whole octaves towards it.
2. fold_to_song_range(): a note still more than PITCH_SONG_TOLERANCE
   semitones (18) away from the song's median moves by whole octaves
   towards it.

Every note keeps its own pitch class, also where the detector was unsure -
a note is never replaced by the level of its neighbours, which would put
whole growled lines on one pitch (stack/work/bench/bench16_flat_out.py).

The strength follows the song's profile (normalise_for_traits()). Default
songs keep a wide song-wide limit: hand-made non-metal songs have 1.8% of
their notes more than an octave from the song's middle (a chorus sung
higher), a limit of 12 squashed that to 0.1%, 18 leaves 2.4% and matches
the hand-made intervals slightly better (+-1 semitone 56.8% vs 56.3%,
2026-10-16, knowledge/03-PROGRESS.md "Octave folding per profile").
Songs with the trait harsh_vocals get the tight limit of 12
(PITCH_SONG_TOLERANCE_HARSH) and one step more (normalise_harsh_pitches()):
an unsure note moves halfway towards the rolling median of its neighbours,
at most 3 semitones from it. Octave
folding alone left 16.5% of neighbouring notes in the generated harsh songs
more than 5 semitones apart (hand-made: 4-5%); with the pull 7.2%, while
neighbours on the same pitch stay below the hand-made share
(stack/work/bench/bench22_harsh.py, an23_harsh_out.py). This step moves
notes by semitones, so it changes what USDX scores.

USDX moves the singer's tone into the note's octave before comparing
(UNote.pas: "move players tone to proper octave"), so whole-octave moves
never change scoring - only where the note bars are drawn. Measured on 110
hand-made songs (stack/work/bench/an9.py, limit 12): range 28 -> 13
semitones, jumps > 12 semitones 15.2% -> 0.2%, notes > 12 semitones from
the song median 13.4% -> 0.3%.

Used by UltraSinger for its own notes (UltraSinger.py, profile from
--traits) and by the stack's lyrics step (stack/orchestrator/repair.py).
Pure standard library (host-testable).
"""

import os
import statistics

PITCH_FOLD_WINDOW = int(os.environ.get("PITCH_FOLD_WINDOW", "5"))
PITCH_FOLD_TOLERANCE = float(os.environ.get("PITCH_FOLD_TOLERANCE", "7"))
PITCH_SONG_TOLERANCE = float(os.environ.get("PITCH_SONG_TOLERANCE", "18"))
PITCH_SONG_TOLERANCE_HARSH = float(os.environ.get("PITCH_SONG_TOLERANCE_HARSH", "12"))
FOLD_ITERATIONS = 3
# profile harsh_vocals: an unsure note (fewer confident pitch frames than
# this share) moves halfway towards the local melody, ending at most
# HARSH_MAX_DISTANCE semitones from it
HARSH_UNSURE_BELOW = float(os.environ.get("HARSH_UNSURE_BELOW", "0.3"))
HARSH_MAX_DISTANCE = float(os.environ.get("HARSH_MAX_DISTANCE", "3"))


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


def normalise_pitches(pitches: list, song_tolerance: float = None) -> list:
    """fold_octaves(), fold_to_song_range() with `song_tolerance`
    (default PITCH_SONG_TOLERANCE), as whole semitones."""
    return [int(round(p)) for p in fold_to_song_range(fold_octaves(pitches), song_tolerance)]


def pull_unsure_notes(pitches: list, sure: list, max_distance: float = None,
                      unsure_below: float = None, k: int = None) -> list:
    """Every note with a confident-frame share (`sure`) below
    `unsure_below` moves halfway towards the rolling median of its
    neighbours and ends at most `max_distance` semitones from it; the other
    notes stay. Whole semitones."""
    max_distance = HARSH_MAX_DISTANCE if max_distance is None else max_distance
    unsure_below = HARSH_UNSURE_BELOW if unsure_below is None else unsure_below
    k = PITCH_FOLD_WINDOW if k is None else k
    reference = rolling_median(pitches, k)
    result = []
    for p, share, ref in zip(pitches, sure, reference):
        if share < unsure_below:
            halfway = (p + ref) / 2
            p = min(max(halfway, ref - max_distance), ref + max_distance)
        result.append(int(round(p)))
    return result


def normalise_harsh_pitches(pitches: list, sure: list) -> list:
    """normalise_pitches() for songs with harsh vocals: octaves folded with
    the song-wide limit PITCH_SONG_TOLERANCE_HARSH, then unsure (growled,
    screamed) notes pulled towards the melody (pull_unsure_notes()), then
    folded again."""
    tight = PITCH_SONG_TOLERANCE_HARSH
    return normalise_pitches(pull_unsure_notes(normalise_pitches(pitches, tight), sure), tight)


def normalise_for_traits(pitches: list, sure: list, traits) -> list:
    """The song's pitches normalised for its profile: the trait
    harsh_vocals -> normalise_harsh_pitches(), every other song ->
    normalise_pitches(). `sure` is each note's confident-frame share."""
    if "harsh_vocals" in (traits or ()):
        return normalise_harsh_pitches(pitches, sure)
    return normalise_pitches(pitches)
