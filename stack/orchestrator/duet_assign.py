#!/usr/bin/env python3
"""Who sings which line of a duet (knowledge/07-DUETS.md, phase 3).

About me: per lyrics line, after the alignment has timed it, the singer
comes from, in this order:

1. an admin's mark in the lyrics ("P1:", "P2:", "P1+P2:" - the unit's
   "singer", repair.parse_lyrics_lines()); the only source of lines both
   sing;
2. a Genius section header naming exactly one singer (the unit's
   "singers", lyrics_fetch.attach_singers()), mapped to P1/P2 through the
   singer names;
3. the voice fingerprint: a wespeaker embedding of the vocal stem over the
   line (embed_lines(), lines of at least MIN_EMBED_S), two groups
   (two_groups()) anchored on the lines 1. and 2. assigned; a line without
   a fingerprint takes the group of the nearest line in time
   (fill_short_lines()).

Singer names (song_names()): the song list / broken report columns
singer_p1/singer_p2, else the two names the Genius headers use most, else
the artist split into exactly two (duet.duet_names()), else "P1"/"P2". P1 is
the first to sing (the parts are swapped if needed) - unless singer_p1
names P1 or the admin marked lines (then P1 is what the admin said). Names
are only written when something ties a name to a voice: the columns, or a
line whose Genius header names its singer; otherwise (the voice or marks
alone - marks say which lines are P1, not who P1 is) the parts are named
"P1"/"P2" (user decision 2026-11-14: an artist name on the wrong voice is
worse than none).

The summary says how many lines each source assigned, how far apart the two
voice groups are (separation) and whether the song is unsure: most lines
were assigned by the voice and the groups are close (separation below
UNSURE_SEPARATION, calibrated on the duet corpus) - such a duet is written
and marked for review.

Before the fingerprint model is loaded the free RAM is checked
(enough_ram(), DUET_EMBED_MIN_FREE_GB); too little fails with a reason
instead of risking an out-of-memory kill. numpy at import; torch and
pyannote.audio only inside embed_lines().
"""
import os
from collections import Counter

import numpy as np

import duet
import resource_monitor

MIN_EMBED_S = 1.0
# 1.6: on the 42 corpus songs 15 of the 16 assigned below 70% fall under it,
# the songs above average 92% (an18_duet_assign.py, the user's choice
# 2026-11-13 - no fingerprint signal separates good and bad songs better)
UNSURE_SEPARATION = float(os.environ.get("DUET_UNSURE_SEPARATION", "1.6"))
UNSURE_VOICE_SHARE = 0.5
EMBED_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"
EMBED_MIN_FREE_GB = float(os.environ.get("DUET_EMBED_MIN_FREE_GB", "2.0"))
SOURCES = ("marks", "genius", "voice")


class DuetAssignError(RuntimeError):
    """The singer assignment cannot run (e.g. not enough free RAM)."""


# --- names

def _name_words(name: str) -> set:
    return {w for w in "".join(c if c.isalnum() else " " for c in (name or "").lower()).split() if len(w) >= 3}


def same_person(a: str, b: str) -> bool:
    """Two spellings of one singer: they share a name word of 3+ letters."""
    return bool(_name_words(a) & _name_words(b))


def song_names(singer_p1: str, singer_p2: str, header_names: list, artist: str) -> tuple:
    """(name 1, name 2, source): the columns (singer_p1 is name 1), else the
    two names the Genius headers use most, else the artist split into two,
    else ("P1", "P2", "default"). A missing name is filled from the next
    source (a different person)."""
    counts = Counter()
    order = []
    for n in header_names:
        key = next((k for k in order if same_person(k, n)), None)
        if key is None:
            order.append(n)
            key = n
        counts[key] += 1
    genius = sorted(order, key=lambda n: (-counts[n], order.index(n)))
    artist_names = list(duet.duet_names(artist) or duet.split_artist(artist)[:1])
    sources = [("columns", [n for n in (singer_p1, singer_p2) if n]), ("genius", genius),
               ("artist", artist_names if duet.duet_names(artist) else [])]
    names, source = [], None
    if singer_p1 or singer_p2:
        names, source = [singer_p1 or "", singer_p2 or ""], "columns"
    for label, candidates in sources:
        for n in candidates:
            if len([x for x in names if x]) >= 2:
                break
            if any(same_person(n, x) for x in names if x):
                continue
            if source is None:
                source = label
            if "" in names:
                names[names.index("")] = n
            else:
                names.append(n)
    names = [n for n in names if n]
    if len(names) < 2:
        if source == "genius" and len(names) == 1:
            other = next((n for n in duet.split_artist(artist) if not same_person(n, names[0])), "")
            if other:
                return names[0], other, source
        if not names or source is None:
            return "P1", "P2", "default"
        return names[0], "P2", source
    return names[0], names[1], source


# --- grouping

def _normalise(vectors: list):
    idx = [i for i, v in enumerate(vectors) if v is not None]
    if not idx:
        return idx, None
    X = np.array([np.asarray(vectors[i], dtype=float) for i in idx])
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)
    return idx, X


def two_groups(vectors: list, anchors: dict, iters: int = 50) -> tuple:
    """(label 0/1 per line - None without a vector -, separation): spherical
    k-means with two groups. Anchored lines ({line: label}) keep their
    label and decide which group is which; without anchors the groups start
    from the first line and the line least like it. separation = distance
    of the group centres / mean distance of the lines to their own centre
    (cosine)."""
    idx, X = _normalise(vectors)
    labels = [None] * len(vectors)
    if X is None or len(idx) < 2:
        return labels, 0.0
    fixed = {k: anchors[i] for k, i in enumerate(idx) if i in anchors}
    centres = np.zeros((2, X.shape[1]))
    for g in (0, 1):
        members = [k for k, lab in fixed.items() if lab == g]
        if members:
            centres[g] = X[members].mean(0)
    have = [g for g in (0, 1) if np.any(centres[g])]
    if not have:
        centres[0] = X[0]
        have = [0]
    if len(have) == 1:
        g = have[0]
        centres[1 - g] = X[int(np.argmin(X @ (centres[g] / np.linalg.norm(centres[g]))))]
    centres /= np.maximum(np.linalg.norm(centres, axis=1, keepdims=True), 1e-12)
    lab = np.zeros(len(idx), int)
    for _ in range(iters):
        lab = np.argmax(X @ centres.T, axis=1)
        for k, g in fixed.items():
            lab[k] = g
        for g in (0, 1):
            if np.any(lab == g):
                m = X[lab == g].mean(0)
                centres[g] = m / max(np.linalg.norm(m), 1e-12)
    for k, i in enumerate(idx):
        labels[i] = int(lab[k])
    if not (np.any(lab == 0) and np.any(lab == 1)):
        return labels, 0.0
    between = 1.0 - float(centres[0] @ centres[1])
    within = float(np.mean([1.0 - X[k] @ centres[lab[k]] for k in range(len(idx))]))
    return labels, between / max(within, 1e-6)


def fill_short_lines(labels: list, centres: list) -> list:
    """A line without a label takes the label of the nearest labelled line
    (by the time of the line's middle)."""
    known = [(c, lab) for c, lab in zip(centres, labels) if lab is not None]
    if not known:
        return list(labels)
    return [lab if lab is not None else min(known, key=lambda k: abs(k[0] - c))[1]
            for c, lab in zip(centres, labels)]


# --- assignment

def assign(units: list, vectors: list, singer_p1: str = "", singer_p2: str = "", artist: str = "") -> tuple:
    """([{"singer": P1|P2|both, "source": marks|genius|voice}] per unit,
    summary). units: dicts with "start"/"end" (seconds) and optionally
    "singer" (an admin's mark) and "singers" (Genius names); vectors: one
    fingerprint (or None) per unit."""
    header_names = [s[0] for u in units for s in [u.get("singers") or []] if len(s) == 1]
    n1, n2, names_source = song_names(singer_p1, singer_p2, header_names, artist)
    marks = [u.get("singer") if u.get("singer") in ("P1", "P2", "both") else None for u in units]
    explicit = bool(singer_p1 or singer_p2) or any(marks)

    def by_name(u, first, second):
        s = u.get("singers") or []
        if len(s) != 1:
            return None
        if same_person(s[0], first):
            return "P1"
        if same_person(s[0], second):
            return "P2"
        return None

    # which name is P1: the columns say it; else marks on Genius lines decide; else name 1
    first, second = n1, n2
    if not (singer_p1 or singer_p2) and any(marks):
        agree = sum(1 for u, m in zip(units, marks) if m in ("P1", "P2") and by_name(u, n1, n2) == m)
        disagree = sum(1 for u, m in zip(units, marks) if m in ("P1", "P2") and by_name(u, n1, n2) not in (None, m))
        if disagree > agree:
            first, second = n2, n1
    genius = [by_name(u, first, second) for u in units]

    anchors = {}
    for i, (m, g) in enumerate(zip(marks, genius)):
        solo = m if m in ("P1", "P2") else (g if m is None else None)
        if solo:
            anchors[i] = 0 if solo == "P1" else 1
    labels, separation = two_groups(vectors, anchors)
    centres = [(u["start"] + u["end"]) / 2 for u in units]
    labels = fill_short_lines(labels, centres)

    out = []
    for m, g, lab in zip(marks, genius, labels):
        if m:
            out.append({"singer": m, "source": "marks"})
        elif g:
            out.append({"singer": g, "source": "genius"})
        else:
            out.append({"singer": "P2" if lab == 1 else "P1", "source": "voice"})

    if not explicit:
        first_solo = next((r["singer"] for r, u in sorted(zip(out, units), key=lambda x: x[1]["start"])
                           if r["singer"] in ("P1", "P2")), "P1")
        if first_solo == "P2":
            swap = {"P1": "P2", "P2": "P1", "both": "both"}
            out = [{"singer": swap[r["singer"]], "source": r["source"]} for r in out]
            first, second = second, first

    # a name belongs to a voice only through the song list's names or a Genius
    # header naming the singer of a line; else the parts are just P1 / P2
    if not (singer_p1 or singer_p2) and not any(genius):
        first, second, names_source = "P1", "P2", "default"

    sources = Counter(r["source"] for r in out)
    voice_share = sources["voice"] / len(out) if out else 0.0
    no_vectors = all(v is None for v in vectors)
    unsure = voice_share >= UNSURE_VOICE_SHARE and (no_vectors or separation < UNSURE_SEPARATION)
    summary = {"names": (first, second), "names_source": names_source,
               "sources": {s: sources.get(s, 0) for s in SOURCES},
               "separation": separation, "unsure": bool(unsure)}
    return out, summary


# --- the fingerprint model

def enough_ram(need_gb: float, read=resource_monitor.read_ram_usage) -> tuple:
    """(enough, free GB): free RAM against what a step needs; a system whose
    RAM cannot be read counts as enough (free None)."""
    usage = read()
    if not usage:
        return True, None
    free = usage["total_gb"] - usage["used_gb"]
    return free >= need_gb, free


_MODEL = {}


def embed_lines(audio16k, spans: list, device: str = "cpu") -> list:
    """A wespeaker fingerprint (numpy vector) of the vocal stem over each
    (start, end) span in seconds, None for spans under MIN_EMBED_S. The
    model is loaded once per process, after the free-RAM check."""
    if "inference" not in _MODEL:
        ok, free = enough_ram(EMBED_MIN_FREE_GB)
        if not ok:
            raise DuetAssignError(f"not enough free RAM for the voice fingerprint model: "
                                  f"{free:.1f} GB free, {EMBED_MIN_FREE_GB:.1f} GB needed")
        import torch
        from pyannote.audio import Inference, Model
        model = Model.from_pretrained(EMBED_MODEL)
        _MODEL["inference"] = Inference(model, window="whole", device=torch.device(device))
        _MODEL["torch"] = torch
    torch, inference = _MODEL["torch"], _MODEL["inference"]
    out = []
    for start, end in spans:
        if end - start < MIN_EMBED_S:
            out.append(None)
            continue
        seg = torch.tensor(np.asarray(audio16k[int(start * 16000):int(end * 16000)]),
                           dtype=torch.float32).unsqueeze(0)
        out.append(np.asarray(inference({"waveform": seg, "sample_rate": 16000}), dtype=float).ravel())
    return out
