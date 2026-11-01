"""#VIDEOGAP: how far a song's video runs ahead of or behind its audio file.

About me: USDX plays the video at `audio time + #VIDEOGAP` (seconds,
UScreenSingController.pas), independent of #GAP. A video from another
source than the audio file (another intro, a music video edit) needs its
own #VIDEOGAP. offset() compares the audio file with the video's own
soundtrack: both become an onset envelope (rise of the log energy per
FRAME_S, robust to another master or loudness), the envelopes are
cross-correlated within MAX_LAG_S, and the best lag is the #VIDEOGAP;
`score` is the Pearson correlation of the envelopes at that lag (the same
recording scores high, another song near 0). measure() decodes both files
with ffmpeg first; new_videogap() decides whether a measurement replaces
the song's #VIDEOGAP (MIN_SCORE, MIN_CHANGE_S).

numpy only (host-testable); measure() needs ffmpeg.
"""

import subprocess

import numpy as np

FRAME_S = 0.01
MAX_LAG_S = 60.0
DECODE_RATE = 8000
# measured on 284 repaired songs: a video whose soundtrack is the same
# recording as the audio file scores 0.47-1.00, another recording 0.13-0.15
MIN_SCORE = 0.4
# a smaller difference to the song's #VIDEOGAP is not worth a change
MIN_CHANGE_S = 0.1


def envelope(samples: np.ndarray, rate: int) -> np.ndarray:
    """The onset envelope: rise of the log energy per FRAME_S frame."""
    frame = max(1, int(round(FRAME_S * rate)))
    n = len(samples) // frame
    if n < 2:
        return np.zeros(0)
    energy = (samples[:n * frame].reshape(n, frame) ** 2).mean(axis=1)
    return np.maximum(0.0, np.diff(np.log(energy + 1e-10)))


def offset(audio: np.ndarray, video_audio: np.ndarray, rate: int, max_lag_s: float = MAX_LAG_S):
    """{"videogap_s", "score"} for two mono signals of the same rate - the
    video plays what the audio plays at t at t + videogap_s - or None when
    one of them has no onsets."""
    a, v = envelope(audio, rate), envelope(video_audio, rate)
    if len(a) < 2 or len(v) < 2 or a.std() == 0 or v.std() == 0:
        return None
    a = (a - a.mean()) / a.std()
    v = (v - v.mean()) / v.std()
    size = 1 << int(np.ceil(np.log2(len(a) + len(v))))
    corr = np.fft.irfft(np.conj(np.fft.rfft(a, size)) * np.fft.rfft(v, size), size)
    max_lag = int(max_lag_s / FRAME_S)
    lags = np.r_[0:min(max_lag, len(v) - 1) + 1, -min(max_lag, len(a) - 1):0]
    best = int(lags[np.argmax(corr[lags])])
    # parabolic interpolation around the peak: sub-frame precision
    left, mid, right = corr[(best - 1) % size], corr[best % size], corr[(best + 1) % size]
    denom = left - 2 * mid + right
    fine = best + (0.5 * (left - right) / denom if denom < 0 else 0.0)
    overlap_a = a[max(0, -best):len(a) - max(0, best + len(a) - len(v))]
    overlap_v = v[max(0, best):max(0, best) + len(overlap_a)]
    score = float(np.corrcoef(overlap_a, overlap_v)[0, 1]) if len(overlap_a) > 1 else 0.0
    return {"videogap_s": float(fine * FRAME_S), "score": score}


def new_videogap(measurement, existing):
    """The #VIDEOGAP value to write for a measure() result, or None to keep
    the song's tag `existing` (its text, None when it has none): only a
    measurement of at least MIN_SCORE that differs by more than
    MIN_CHANGE_S."""
    if not measurement or measurement["score"] < MIN_SCORE:
        return None
    try:
        current = float((existing or "0").replace(",", "."))
    except ValueError:
        current = 0.0
    value = round(measurement["videogap_s"], 2)
    if abs(value - current) <= MIN_CHANGE_S:
        return None
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def decode(path: str, rate: int = DECODE_RATE) -> np.ndarray:
    """The first audio stream of a media file as mono float samples."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0


def measure(audio_path: str, video_path: str):
    """offset() of an audio file and a video file's soundtrack, or None
    (a file without audio, undecodable, or without onsets)."""
    try:
        audio, video = decode(audio_path), decode(video_path)
    except (OSError, subprocess.CalledProcessError):
        return None
    return offset(audio, video, DECODE_RATE)
