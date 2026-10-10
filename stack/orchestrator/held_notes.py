"""Held syllables: the syllable plus `~` notes where its pitch changes level.

About me: a generated syllable is one note with one pitch. Hand-made
UltraStar files split a held syllable that moves through the melody into
the syllable and `~` continuation notes, one per pitch level (7% of their
notes). split_held_note() finds those levels in the pitch track: the
confident frames of the note, folded to within 6 semitones of their median
(octave errors of the detector), are cut into the parts with the least
squared distance to their own mean, plus `penalty` per extra part.

Measured on 110 hand-made songs (stack/work/bench/bench21_syllables.py,
knowledge/03-PROGRESS.md "Syllables and `~` notes measured"): with the
production word timing, sung time on the right pitch class 48.0% -> 50.2%
together with the 0.2 s pitch window and the 0.1 s read delay (repair.py);
7-8% of the notes become `~` notes (hand-made 7.0%). Penalty 40, parts of
at least 0.12 s, only notes of at least 0.3 s.

numpy only (host-testable).
"""

import os

import numpy as np

TILDE_PENALTY = float(os.environ.get("TILDE_PENALTY", "40"))
TILDE_MIN_NOTE_S = float(os.environ.get("TILDE_MIN_NOTE_S", "0.3"))
TILDE_MIN_PART_S = float(os.environ.get("TILDE_MIN_PART_S", "0.12"))
TILDE_MAX_PARTS = 6
# same as modules.Pitcher.pitched_data_helper.CONFIDENCE_THRESHOLD (not
# imported: this module stays free of UltraSinger's dependencies)
CONFIDENCE_THRESHOLD = 0.2
MIN_CONFIDENT_FRAMES = 4


def _nearest(times, value) -> int:
    i = int(np.searchsorted(times, value, side="left"))
    if i > 0 and (i == len(times) or abs(value - times[i - 1]) < abs(value - times[i])):
        return i - 1
    return min(i, len(times) - 1)


def _segment_costs(x):
    """cost[i, j]: squared distance of the confident frames i..j-1 to their
    mean (unconfident frames, NaN, weigh nothing)."""
    w = (~np.isnan(x)).astype(float)
    v = np.nan_to_num(x)
    cw, cv, cvv = (np.concatenate([[0.0], np.cumsum(a)]) for a in (w, w * v, w * v * v))
    i = np.arange(len(x) + 1)[:, None]
    j = np.arange(len(x) + 1)[None, :]
    weight = cw[j] - cw[i]
    total = cv[j] - cv[i]
    safe = np.where(weight > 0, weight, 1.0)
    return np.where(weight > 0, (cvv[j] - cvv[i]) - total * total / safe, 0.0)


def _best_cuts(cost, parts: int, min_len: int):
    """Inner cut positions of the split into `parts` parts of at least
    `min_len` frames with the least summed cost, or None if none fits."""
    n = cost.shape[0] - 1
    if n < parts * min_len:
        return None
    best = np.full((parts + 1, n + 1), np.inf)
    back = np.zeros((parts + 1, n + 1), dtype=int)
    best[0, 0] = 0.0
    for m in range(1, parts + 1):
        for j in range(m * min_len, n - (parts - m) * min_len + 1):
            lo, hi = (m - 1) * min_len, j - min_len
            cand = best[m - 1, lo:hi + 1] + cost[lo:hi + 1, j]
            k = int(np.argmin(cand))
            best[m, j], back[m, j] = cand[k], lo + k
    cuts, j = [], n
    for m in range(parts, 1, -1):
        j = back[m, j]
        cuts.append(j)
    return sorted(cuts), best[parts, n]


def split_held_note(times, freqs, confs, start: float, end: float,
                    penalty: float = None, min_note_s: float = None,
                    min_part_s: float = None, read_delay_s: float = 0.0,
                    threshold: float = CONFIDENCE_THRESHOLD) -> list:
    """[(start, end, is_tilde), ...] covering [start, end]: the note itself
    first, then one `~` note per further pitch level. The track is read
    `read_delay_s` later than the note (the singing lags the note, see
    repair.PITCH_READ_DELAY_S); the returned times are note times."""
    penalty = TILDE_PENALTY if penalty is None else penalty
    min_note_s = TILDE_MIN_NOTE_S if min_note_s is None else min_note_s
    min_part_s = TILDE_MIN_PART_S if min_part_s is None else min_part_s
    whole = [(start, end, False)]
    if end - start < min_note_s or len(times) == 0:
        return whole
    times = np.asarray(times, dtype=float)
    a = _nearest(times, start + read_delay_s)
    b = max(_nearest(times, end + read_delay_s), a + 1)
    f = np.asarray(freqs[a:b], dtype=float)
    c = np.asarray(confs[a:b], dtype=float)
    ok = (c > threshold) & (f > 0)
    if ok.sum() < MIN_CONFIDENT_FRAMES:
        return whole
    x = np.full(len(f), np.nan)
    x[ok] = 12 * np.log2(f[ok] / 440.0) + 69
    ref = np.median(x[ok])
    x = x - 12 * np.round((x - ref) / 12)
    cost = _segment_costs(x)
    n = len(x)
    hop = max(1e-9, float(np.median(np.diff(times)))) if len(times) > 1 else 0.016
    min_len = max(1, int(np.ceil(min_part_s / hop - 1e-6)))
    best_total, best_cuts = cost[0, n], []
    for parts in range(2, min(TILDE_MAX_PARTS, n // min_len) + 1):
        found = _best_cuts(cost, parts, min_len)
        if found is None:
            break
        cuts, total = found
        total += penalty * (parts - 1)
        if total < best_total:
            best_total, best_cuts = total, cuts
    if not best_cuts:
        return whole
    edges = ([start] + [float(times[a + k]) - read_delay_s for k in best_cuts] + [end])
    return [(edges[i], edges[i + 1], i > 0) for i in range(len(edges) - 1)]
