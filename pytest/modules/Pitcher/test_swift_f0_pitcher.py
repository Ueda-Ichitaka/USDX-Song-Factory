"""Tests for pitcher.py's SwiftF0 (>= 0.3.0) pitch detection.

About me: checks get_pitch_with_swift_f0() against synthetic audio - a pure
tone must come back at its frequency with high confidence, and digital
silence (the muted no-singing parts of the processing audio) must never be
reported as a confident pitch - plus the shared confidence threshold."""

import numpy as np

import src.modules.Pitcher.pitcher as pitcher
import src.modules.Pitcher.pitched_data_helper as helper

SR = 16000


def _tone(hz, seconds, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def test_pure_tone_is_detected_at_its_frequency():
    result = pitcher.get_pitch_with_swift_f0(_tone(220.0, 2.0), SR)
    freqs = np.array(result.frequencies)
    conf = np.array(result.confidence)
    confident = freqs[conf > helper.CONFIDENCE_THRESHOLD]
    assert len(confident) > 0.8 * len(freqs)
    assert abs(np.median(confident) - 220.0) < 220.0 * 0.03


def test_timestamps_increase_and_match_the_other_lists():
    result = pitcher.get_pitch_with_swift_f0(_tone(330.0, 1.0), SR)
    assert len(result.times) == len(result.frequencies) == len(result.confidence)
    assert all(b > a for a, b in zip(result.times, result.times[1:]))


def test_digital_silence_is_never_a_confident_pitch():
    audio = np.concatenate([np.zeros(SR, dtype=np.float32), _tone(220.0, 1.0)])
    result = pitcher.get_pitch_with_swift_f0(audio, SR)
    times = np.array(result.times)
    conf = np.array(result.confidence)
    silent = conf[times < 0.9]
    assert len(silent) > 0
    assert np.all(silent <= helper.CONFIDENCE_THRESHOLD)


def test_other_sample_rates_are_accepted():
    result = pitcher.get_pitch_with_swift_f0(_tone(220.0, 1.0, sr=44100), 44100)
    conf = np.array(result.confidence)
    freqs = np.array(result.frequencies)
    assert abs(np.median(freqs[conf > helper.CONFIDENCE_THRESHOLD]) - 220.0) < 220.0 * 0.03


def test_confidence_threshold_is_the_value_measured_best_for_swift_f0_0_3():
    # stack/work/bench/an11.py: 0.2 is the best threshold for SwiftF0 0.3.0
    assert helper.CONFIDENCE_THRESHOLD == 0.2


def test_high_confidence_helpers_use_the_shared_threshold():
    freqs = [100.0, 200.0, 300.0]
    confs = [0.1, 0.25, 0.9]
    assert helper.get_frequencies_with_high_confidence(freqs, confs) == [200.0, 300.0]
    data = pitcher.PitchedData([0.0, 0.1, 0.2], freqs, confs)
    assert pitcher.get_pitched_data_with_high_confidence(data).frequencies == [200.0, 300.0]
