#!/usr/bin/env python3
"""Tests for timing_fit.py.

About me: plain assert-based checks (no pytest) for fitting a song file's
line times to the times the lines are really sung, and turning that fit into
a new #GAP / #BPM. Pure standard library, runs on the host:

    python3 test_timing_fit.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import timing_fit  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


# lines every 4 s from 10 s; sung 2.5 s later than the file says
_file = [10.0 + 4 * k for k in range(30)]
_const = [(f, f + 2.5) for f in _file]
fit = timing_fit.fit(_const)
check(f"fit: a constant offset (got {fit})",
      close(fit["slope"], 1.0) and close(fit["intercept"], 2.5) and fit["agree"] == 1.0
      and fit["n"] == 30)

# sung = 1.0 + 1.01 * file (the song drifts 1% - another #BPM)
_drift = [(f, 1.0 + 1.01 * f) for f in _file]
fit = timing_fit.fit(_drift)
check(f"fit: a drift (got {fit})", close(fit["slope"], 1.01) and close(fit["intercept"], 1.0))

# a third of the lines misplaced by the aligner do not move the fit
_noisy = list(_const)
for k in range(0, 30, 3):
    _noisy[k] = (_noisy[k][0], _noisy[k][1] + 17.0)
fit = timing_fit.fit(_noisy)
check(f"fit: robust against misplaced lines (got {fit})",
      close(fit["slope"], 1.0, 1e-3) and close(fit["intercept"], 2.5, 1e-2)
      and close(fit["agree"], 20 / 30))

check("fit: too few lines -> None", timing_fit.fit(_const[:4]) is None)

# correction: #GAP / #BPM so that every note lands where it is sung
gap_ms, bpm = 12000.0, 150.0
beat_s = 60.0 / (4 * bpm)
_beats = [40 * k for k in range(30)]
_pts = [(gap_ms / 1000 + b * beat_s, 1.0 + 1.01 * (gap_ms / 1000 + b * beat_s)) for b in _beats]
corr = timing_fit.correction(timing_fit.fit(_pts), gap_ms, bpm)
new_beat_s = 60.0 / (4 * corr["bpm"])
check(f"correction: with the new #GAP and #BPM every note starts where it is sung (got {corr})",
      all(close(corr["gap_ms"] / 1000 + b * new_beat_s, 1.0 + 1.01 * (gap_ms / 1000 + b * beat_s), 1e-6)
          for b in _beats))
check("correction: reports the offset at the first line and the drift over the song",
      close(corr["offset_s"], 1.0 + 0.01 * 12.0) and close(corr["drift_s"], 0.01 * (_pts[-1][0] - _pts[0][0])))

corr = timing_fit.correction(timing_fit.fit(_const), gap_ms, bpm)
check(f"correction: no drift -> #BPM stays, #GAP moves by the offset (got {corr})",
      corr["bpm"] == bpm and close(corr["gap_ms"], gap_ms + 2500.0))

corr = timing_fit.correction(timing_fit.fit(_drift), gap_ms, bpm, gap_only=True)
check(f"correction: gap only -> #BPM stays, the first line is right (got {corr})",
      corr["bpm"] == bpm and close(corr["gap_ms"], gap_ms + 1000.0 * (1.0 + 0.01 * 10.0)))

_bad = [(f, f + (k % 7) * 5.0) for k, f in enumerate(_file)]
check("correction: lines that do not agree -> None (the audio does not fit)",
      timing_fit.correction(timing_fit.fit(_bad), gap_ms, bpm) is None)
check("correction: no fit -> None", timing_fit.correction(None, gap_ms, bpm) is None)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
