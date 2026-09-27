"""Pitched data helper module

About me: the confidence threshold that separates reliable pitch frames from
guesses, shared by note picking (midi_creator), plotting (pitcher) and the
stack's note normalisation (repair.py). 0.2 measured best for SwiftF0 0.3.0
on 110 hand-made songs (stack/work/bench/an11.py)."""

CONFIDENCE_THRESHOLD = 0.2


def get_frequencies_with_high_confidence(
    frequencies: list[float], confidences: list[float], threshold=CONFIDENCE_THRESHOLD
) -> list[float]:
    """Get frequency with high confidence"""
    conf_f = []
    for i, conf in enumerate(confidences):
        if conf > threshold:
            conf_f.append(frequencies[i])
    if not conf_f:
        conf_f = frequencies
    return conf_f
