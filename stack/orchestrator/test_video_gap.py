#!/usr/bin/env python3
"""Tests for video_gap.py.

About me: plain assert-based checks (no pytest), numpy only, host-testable
with synthetic signals (no ffmpeg, no media files):

    python3 test_video_gap.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import video_gap  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


RATE = 8000
rng = np.random.default_rng(7)


def music(seconds):
    """Noise bursts of random length and loudness - something with onsets."""
    out = np.zeros(int(seconds * RATE))
    t = 0.0
    while t < seconds - 1:
        length = rng.uniform(0.05, 0.4)
        start = int(t * RATE)
        out[start:start + int(length * RATE)] = rng.normal(0, rng.uniform(0.1, 1.0), int(length * RATE))
        t += length + rng.uniform(0.05, 0.5)
    return out


song = music(90)
# the video has 2.5 s more intro than the audio file: what the audio plays at
# t, the video plays at t + 2.5 -> USDX's video position = audio time + 2.5
video = np.concatenate([np.zeros(int(2.5 * RATE)), song * 0.3])
result = video_gap.offset(song, video, RATE)
check(f"offset: a video with 2.5 s more intro -> #VIDEOGAP +2.5 (got {result})",
      result is not None and abs(result["videogap_s"] - 2.5) <= 0.02)
check("offset: the same content scores high", result is not None and result["score"] > 0.8)

# the video starts 1.2 s into the song (shorter intro): #VIDEOGAP -1.2
result = video_gap.offset(song, song[int(1.2 * RATE):] * 2.0, RATE)
check(f"offset: a video with 1.2 s less intro -> #VIDEOGAP -1.2 (got {result})",
      result is not None and abs(result["videogap_s"] + 1.2) <= 0.02)

# a little noise on the video's soundtrack (another master) still works
noisy = np.concatenate([np.zeros(int(0.7 * RATE)), song]) + rng.normal(0, 0.05, len(song) + int(0.7 * RATE))
result = video_gap.offset(song, noisy, RATE)
check(f"offset: noise on the video soundtrack (got {result})",
      result is not None and abs(result["videogap_s"] - 0.7) <= 0.02)

# another song entirely: low score
result = video_gap.offset(song, music(90), RATE)
check(f"offset: unrelated audio scores low (got {result})",
      result is not None and result["score"] < 0.3)

check("offset: silence -> None", video_gap.offset(np.zeros(RATE * 30), song, RATE) is None)

# new_videogap(): the value to write, or None to keep the song's tag.
# Measured on 284 repaired songs: the same recording scores 0.47-1.00,
# another recording 0.13-0.15
check("new_videogap: a confident measurement that differs replaces the tag",
      video_gap.new_videogap({"videogap_s": 0.0004, "score": 0.99}, "15") == "0"
      and video_gap.new_videogap({"videogap_s": 2.504, "score": 0.6}, None) == "2.5")
check("new_videogap: within 0.1 s of the tag -> keep",
      video_gap.new_videogap({"videogap_s": 2.03, "score": 0.99}, "2,0") is None
      and video_gap.new_videogap({"videogap_s": 0.03, "score": 0.99}, None) is None)
check("new_videogap: a low score (another recording) -> keep",
      video_gap.new_videogap({"videogap_s": 4.5, "score": 0.13}, None) is None)
check("new_videogap: negative values keep their sign",
      video_gap.new_videogap({"videogap_s": -0.5, "score": 0.9}, None) == "-0.5"
      and video_gap.new_videogap({"videogap_s": -1.214, "score": 0.9}, None) == "-1.21")
check("new_videogap: no measurement -> keep", video_gap.new_videogap(None, "7") is None)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
