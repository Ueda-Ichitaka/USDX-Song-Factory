"""Pitcher module

About me: pitch detection for UltraSinger with SwiftF0 (>= 0.3.0) - one
pitch track (times, frequencies, confidence) per audio file. SwiftF0 0.3.0
reports digital silence (the muted no-singing parts of the processing audio)
with confidence 0 instead of random voiced frames."""
import numpy as np

from scipy.io import wavfile
from swift_f0 import SwiftF0

from modules.console_colors import ULTRASINGER_HEAD, blue_highlighted
from modules.Pitcher.pitched_data import PitchedData
from modules.Pitcher.pitched_data_helper import CONFIDENCE_THRESHOLD

# SwiftF0's full model range (general music, not just speech)
SWIFT_F0_FMIN = 46.875
SWIFT_F0_FMAX = 2093.75

_swift_f0_detector = None

def _get_detector():
    """Lazy initialize SwiftF0 detector"""
    global _swift_f0_detector
    if _swift_f0_detector is None:
        _swift_f0_detector = SwiftF0()
    return _swift_f0_detector


def get_pitch_with_file(
    filename: str
) -> PitchedData:
    """Pitch detection using SwiftF0"""

    print(
        f"{ULTRASINGER_HEAD} Pitching with {blue_highlighted('SwiftF0')}"
    )
    sample_rate, audio = wavfile.read(filename)

    # Convert stereo to mono if needed
    if len(audio.shape) > 1:
        audio = np.mean(audio, axis=1)

    # Normalize audio to float32 based on dtype
    if audio.dtype == np.uint8:
        # uint8: range [0, 255] -> subtract 128 and divide by 128
        audio = (audio.astype(np.float32) - 128.0) / 128.0
    elif audio.dtype in [np.int16, np.int32, np.int64]:
        # Signed integers: use iinfo to get max value and normalize
        dtype_info = np.iinfo(audio.dtype)
        max_val = max(abs(dtype_info.min), abs(dtype_info.max))
        audio = audio.astype(np.float32) / float(max_val)
    elif audio.dtype == np.float64:
        # float64: cast to float32
        audio = audio.astype(np.float32)
    elif audio.dtype != np.float32:
        # Fallback for other types: assume int16 range
        audio = audio.astype(np.float32) / 32768.0

    return get_pitch_with_swift_f0(audio, sample_rate)


def get_pitch_with_swift_f0(
    audio: np.ndarray, sample_rate: int
) -> PitchedData:
    """Pitch detection using SwiftF0

    SwiftF0 resamples to 16 kHz internally and returns one frame per 256
    samples (16 ms).
    """
    detector = _get_detector()

    # Detect pitch
    result = detector.detect(audio, sample_rate, fmin=SWIFT_F0_FMIN, fmax=SWIFT_F0_FMAX)

    # Convert to PitchedData format
    times = [float(t) for t in result.timestamps]
    frequencies = [float(f) for f in result.pitch_hz]
    confidence = [float(c) for c in result.confidence]

    return PitchedData(times, frequencies, confidence)


def get_pitched_data_with_high_confidence(
    pitched_data: PitchedData, threshold=CONFIDENCE_THRESHOLD
) -> PitchedData:
    """Get frequency with high confidence"""
    new_pitched_data = PitchedData([], [], [])
    for i, conf in enumerate(pitched_data.confidence):
        if conf > threshold:
            new_pitched_data.times.append(pitched_data.times[i])
            new_pitched_data.frequencies.append(pitched_data.frequencies[i])
            new_pitched_data.confidence.append(pitched_data.confidence[i])

    return new_pitched_data


class Pitcher:
    """Docstring"""
