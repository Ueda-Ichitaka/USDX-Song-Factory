"""An admin's anchors.txt: pinned lyric lines and the song end.

About me: the global lyrics alignment can drift where the audio is unclear
(outros, choirs, lyrics that differ from what is sung). anchors.txt lets an
admin pin the start time of some lyric lines - typically the last three -
and the end of the song. repair.py then aligns the song piece by piece
between the anchors, so an error cannot run across an anchor, and nothing
after the end time is used for the lyrics. See stack/docs/repairs.md,
"Anchors (anchors.txt)", for the file format.
"""

import os
import re

from outro import parse_time

ANCHORS_FILE = "anchors.txt"


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def read_anchors(song_dir: str):
    """{"lines": [(seconds, text), ...], "end": seconds or None} from
    anchors.txt in `song_dir`, or None when there is no such file or it
    holds no anchor and no end line. Raises ValueError naming the file and
    the line number for any line that does not follow the format."""
    path = os.path.join(song_dir, ANCHORS_FILE)
    if not os.path.isfile(path):
        return None
    lines, end = [], None
    with open(path, encoding="utf-8-sig") as f:
        for number, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            where = f"{ANCHORS_FILE} line {number}"
            try:
                if line.lower().startswith("end ") or line.lower() == "end":
                    if end is not None:
                        raise ValueError("a second end line")
                    end = parse_time(line[3:])
                    continue
                if "|" not in line:
                    raise ValueError("expected '<time> | <lyric line>' or 'end <time>'")
                time_text, text = line.split("|", 1)
                seconds, text = parse_time(time_text), text.strip()
                if not text:
                    raise ValueError("no lyric line after '|'")
                if lines and seconds <= lines[-1][0]:
                    raise ValueError("anchor times must increase from line to line")
                if end is not None:
                    raise ValueError("anchor lines must come before the end line")
                lines.append((seconds, text))
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from None
    if end is not None and lines and end <= lines[-1][0]:
        raise ValueError(f"{ANCHORS_FILE}: the end must come after the last anchor")
    if not lines and end is None:
        return None
    return {"lines": lines, "end": end}


def match_anchor_units(anchor_lines: list, unit_texts: list) -> list:
    """[(unit_index, seconds), ...] in lyric order for anchor lines
    [(seconds, text), ...]. Text comparison ignores case, punctuation and
    spacing. Matching runs from the end: the last anchor pins the last
    lyric line with that text, each earlier anchor the last matching line
    before the one pinned after it - so a repeated chorus line means its
    latest occurrence. Raises ValueError for a line that is not found."""
    normalised = [_normalise(t) for t in unit_texts]
    result = []
    limit = len(unit_texts)
    for seconds, text in reversed(anchor_lines):
        wanted = _normalise(text)
        index = next((i for i in range(limit - 1, -1, -1) if normalised[i] == wanted), None)
        if index is None:
            raise ValueError(f"{ANCHORS_FILE}: lyric line not found (in this order): {text!r}")
        result.append((index, seconds))
        limit = index
    return list(reversed(result))
