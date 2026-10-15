"""How far is a song file's timing from the singing - and the #GAP/#BPM that fix it.

About me: a song file timed for another audio version is off by a constant
amount (wrong #GAP) or drifts apart over the song (wrong #BPM, a slightly
faster or slower version). Both are a straight line between the time the
file gives a lyric line and the time it is really sung:

    sung = intercept + slope * file

fit() finds that line robustly (Theil-Sen: the median of the pairwise
slopes, then the median intercept) from (file time, sung time) points, one
per lyric line, as repair.py's whole-song alignment of the file's own lyrics
gives them; misplaced lines do not move it. agree is the share of lines
within AGREE_S of the line.

Because every note time is #GAP + beat * 60 / (4 * #BPM), the same line maps
onto the header: new #GAP = intercept + slope * #GAP, new #BPM = #BPM / slope
- the notes themselves stay untouched. correction() returns that, or only a
new #GAP (gap_only, or no real drift), or None when too few lines agree
(the audio does not fit the file at all).

Measured with the same alignment on the user's library (stack/work/bench/
v09_truthcheck.py, 2026-10-15): 33 of 212 songs off by a constant 0.3-31 s,
19 drifting, both with 80-100% of the lines agreeing once the line is
removed.

Pure standard library (host-testable).
"""

MIN_LINES = 8
AGREE_S = 1.0
MIN_AGREE = 0.8
MIN_DRIFT_S = 0.2
MAX_POINTS = 400


def _median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return None
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def fit(points: list):
    """{"slope", "intercept", "agree", "n", "first", "last"} for points
    [(file_s, sung_s), ...], or None with fewer than MIN_LINES points."""
    if len(points) < MIN_LINES:
        return None
    pts = sorted(points)
    step = max(1, len(pts) // MAX_POINTS)
    sample = pts[::step]
    slopes = [(b[1] - a[1]) / (b[0] - a[0])
              for i, a in enumerate(sample) for b in sample[i + 1:] if b[0] - a[0] > 1.0]
    slope = _median(slopes) if slopes else 1.0
    intercept = _median([y - slope * x for x, y in pts])
    agree = sum(1 for x, y in pts if abs(y - (intercept + slope * x)) <= AGREE_S) / len(pts)
    return {"slope": slope, "intercept": intercept, "agree": agree, "n": len(pts),
            "first": pts[0][0], "last": pts[-1][0],
            "offsets": [y - x for x, y in pts]}


def correction(result, gap_ms: float, bpm_tag: float, gap_only: bool = False,
               min_agree: float = MIN_AGREE, min_drift_s: float = MIN_DRIFT_S):
    """{"gap_ms", "bpm", "offset_s", "drift_s", "agree"} that put every note
    where it is sung, or None when fewer than `min_agree` of the lines
    agree. offset_s is the shift at the first line, drift_s how much it
    changes until the last. With `gap_only` (or a drift below
    `min_drift_s`) only #GAP changes: gap_only puts the first line right,
    otherwise the median shift of all lines is used."""
    if not result or result["agree"] < min_agree:
        return None
    slope, intercept = result["slope"], result["intercept"]
    offset_first = intercept + (slope - 1.0) * result["first"]
    drift_s = (slope - 1.0) * (result["last"] - result["first"])
    out = {"offset_s": offset_first, "drift_s": drift_s, "agree": result["agree"]}
    if gap_only:
        return dict(out, gap_ms=gap_ms + 1000.0 * offset_first, bpm=bpm_tag)
    if abs(drift_s) < min_drift_s:
        return dict(out, gap_ms=gap_ms + 1000.0 * _median(result["offsets"]), bpm=bpm_tag)
    return dict(out, gap_ms=1000.0 * (intercept + slope * gap_ms / 1000.0), bpm=bpm_tag / slope)
