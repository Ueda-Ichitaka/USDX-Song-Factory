#!/usr/bin/env python3
"""Compare newly generated songs against their approved reference versions.

About me: stack/reference/ holds songs whose generated timing was checked by
ear in USDX and approved. After a change to the alignment, this compares the
new generation of each of those songs (by default stack/output/new/<song>/)
with the approved version, word by word, so a regression shows up without
another full listening round. Words are matched by their text (so changed
lyrics still compare on the words both versions share); reported per song
are the share of word starts within 0.1 s / 0.3 s of the reference, the
median signed shift and the largest shifts. Pure standard library - runs on
the host:

    python3 orchestrator/compare_reference.py [--reference DIR] [--new DIR]
"""

import argparse
import difflib
import os
import re
import statistics
import sys

NOTE_RE = re.compile(r"^([:*FRG]) (-?\d+) (\d+) (-?\d+) ?(.*)$")


def _header_number(text: str, tag: str) -> float:
    for line in text.splitlines():
        if line.upper().startswith(f"#{tag}:"):
            return float(line.split(":", 1)[1].strip().replace(",", "."))
    return 0.0


def word_timings(txt_text: str) -> list:
    """(word, start_s, end_s) for every word of an UltraStar txt (single
    track). Syllables are joined until one ends with a space or a line
    break follows."""
    gap_s = _header_number(txt_text, "GAP") / 1000.0
    bpm = _header_number(txt_text, "BPM")
    beat_s = 60.0 / (bpm * 4) if bpm else 0.0
    words = []
    current = None
    for line in txt_text.splitlines():
        match = NOTE_RE.match(line.rstrip("\r\n"))
        if not match:
            if line.startswith("-") and current:
                words.append(tuple(current))
                current = None
            continue
        beat, dur, text = int(match.group(2)), int(match.group(3)), match.group(5)
        start = gap_s + beat * beat_s
        end = gap_s + (beat + dur) * beat_s
        if current is None:
            current = [text.strip(), start, end]
        else:
            current[0] += text.strip()
            current[2] = end
        if text.endswith(" "):
            words.append(tuple(current))
            current = None
    if current:
        words.append(tuple(current))
    return words


def _normalise(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def compare_words(reference: list, candidate: list, worst_n: int = 5) -> dict:
    """Match candidate words to reference words by normalised text and
    compare their start times (candidate minus reference)."""
    matcher = difflib.SequenceMatcher(
        a=[_normalise(w[0]) for w in reference],
        b=[_normalise(w[0]) for w in candidate], autojunk=False)
    shifts = []
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            ref_word = reference[block.a + k]
            cand_word = candidate[block.b + k]
            shifts.append((ref_word[0], ref_word[1], cand_word[1] - ref_word[1]))
    result = {"ref_words": len(reference), "matched": len(shifts),
              "within_01": None, "within_03": None, "median_shift": None,
              "worst": []}
    if shifts:
        deltas = [s for _, _, s in shifts]
        result["within_01"] = sum(abs(d) <= 0.1 + 1e-9 for d in deltas) / len(deltas)
        result["within_03"] = sum(abs(d) <= 0.3 + 1e-9 for d in deltas) / len(deltas)
        result["median_shift"] = round(statistics.median(deltas), 6)
        result["worst"] = [(w, round(t, 3), round(s, 3)) for w, t, s in
                           sorted(shifts, key=lambda x: -abs(x[2]))[:worst_n]]
    return result


def _song_txt(folder: str):
    """The folder's UltraStar txt: the first .txt with a #BPM header
    (language.txt, lyrics.txt and other notes have none)."""
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if name.lower().endswith(".txt") and os.path.isfile(path):
            if re.search(r"^#BPM:", _read(path), re.MULTILINE | re.IGNORECASE):
                return path
    return None


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def compare_folders(reference_dir: str, new_dir: str) -> list:
    """One result row per song folder in `reference_dir`, compared with the
    same-named folder in `new_dir`."""
    rows = []
    for song in sorted(os.listdir(reference_dir)):
        ref_folder = os.path.join(reference_dir, song)
        if not os.path.isdir(ref_folder):
            continue
        ref_txt = _song_txt(ref_folder)
        new_folder = os.path.join(new_dir, song)
        new_txt = _song_txt(new_folder) if os.path.isdir(new_folder) else None
        if not ref_txt or not new_txt:
            rows.append({"song": song, "missing": True})
            continue
        row = compare_words(word_timings(_read(ref_txt)), word_timings(_read(new_txt)))
        row.update(song=song, missing=False)
        rows.append(row)
    return rows


def render_table(rows: list) -> str:
    lines = ["| Song | Matched words | Start within 0.1 s | Start within 0.3 s "
             "| Median shift (s) | Largest shifts (word @ reference s: shift s) |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        if row["missing"]:
            lines.append(f"| {row['song']} | missing | | | | |")
            continue
        if not row["matched"]:
            lines.append(f"| {row['song']} | 0/{row['ref_words']} | | | | |")
            continue
        worst = ", ".join(f"{w} @ {t:.1f}: {s:+.2f}" for w, t, s in row["worst"])
        lines.append(
            f"| {row['song']} | {row['matched']}/{row['ref_words']} "
            f"| {row['within_01'] * 100:.0f}% | {row['within_03'] * 100:.0f}% "
            f"| {row['median_shift']:+.3f} | {worst} |")
    return "\n".join(lines)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", default=os.path.join(here, "reference"),
                        help="approved songs (default: stack/reference)")
    parser.add_argument("--new", default=os.path.join(here, "output", "new"),
                        help="new generation to check (default: stack/output/new)")
    args = parser.parse_args()
    if not os.path.isdir(args.reference):
        print(f"no reference folder: {args.reference}", file=sys.stderr)
        sys.exit(2)
    print(render_table(compare_folders(args.reference, args.new)))


if __name__ == "__main__":
    main()
