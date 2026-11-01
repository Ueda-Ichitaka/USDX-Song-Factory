#!/usr/bin/env python3
"""The UltraStar format version of a song file's header.

About me: repaired songs get the same `#VERSION` as new songs
(FORMAT_VERSION, the version UltraSinger writes - test_repair.py checks
that the two agree). A song file without `#VERSION` follows the
unversioned format; version 1 drops its relative mode, encodings other
than UTF-8, `#DUETSINGERP1/P2` and `#NOTESGAP`, so a song using one of them
stays unversioned and the reason is returned. USDX refuses to load a
versioned file that has a `#RELATIVE` line at all (even "no"), so that line
is removed; `#AUDIO` repeats `#MP3` as in new songs.

Also the header rules both need: is_duet() (P1/P2 voices - the stack does
not repair duets), shift_play_range() (#START and #END move with a #GAP
shift; a #START the first note would come before is removed, since USDX
plays silence until #START) and set_tag() (one tag of a finished song
file, e.g. #VIDEOGAP).

Used by repair.py (every repaired song file it writes) and orchestrator.py
(the cover repair, which copies a song without repair.py, duet folders,
#VIDEOGAP). No heavy imports, so both can use it.
"""

import re

FORMAT_VERSION = "1.2.0"
# USDX starts a song at least this long before its first note
# (MINIMUM_START_TIME in UScreenSingController.pas)
USDX_LEAD_IN_S = 3.0

# headers of the unversioned format that version 1 has no equivalent for
_UNVERSIONABLE = ("DUETSINGERP1", "DUETSINGERP2", "NOTESGAP")


def header_tag(raw: str):
    """(TAG upper-cased, value) of a `#TAG:value` line, else (None, None)."""
    stripped = raw.strip()
    if not stripped.startswith("#") or ":" not in stripped:
        return None, None
    tag, value = stripped[1:].split(":", 1)
    return tag.strip().upper(), value.strip()


def versioned_header(header: list) -> tuple:
    """(header lines, None) with `#VERSION:FORMAT_VERSION` first, or the
    header unchanged and the reason it stays unversioned. A header that has
    a #VERSION is left alone."""
    tags = {}
    for raw in header:
        tag, value = header_tag(raw)
        if tag and value:
            tags.setdefault(tag, value)
    if "VERSION" in tags:
        return list(header), None
    if tags.get("RELATIVE", "").lower() == "yes":
        return list(header), f"#RELATIVE:{tags['RELATIVE']} (relative mode)"
    encoding = tags.get("ENCODING")
    if encoding and encoding.upper().replace("-", "") != "UTF8":
        return list(header), f"#ENCODING:{encoding}"
    for tag in _UNVERSIONABLE:
        if tag in tags:
            return list(header), f"#{tag}"

    out = [f"#VERSION:{FORMAT_VERSION}"]
    for raw in header:
        tag, value = header_tag(raw)
        if tag in ("RELATIVE", "ENCODING"):
            continue
        out.append(raw)
        if tag == "MP3" and value and "AUDIO" not in tags:
            out.append(f"#AUDIO:{value}")
    return out, None


def versioned_text(text: str) -> tuple:
    """versioned_header() for a whole song file's text: (text, reason).
    Only the header lines (the `#` lines at the top) change; a byte order
    mark and the file's line endings stay."""
    bom = "﻿" if text.startswith("﻿") else ""
    lines = text[len(bom):].splitlines(keepends=True)
    end = 0
    while end < len(lines) and lines[end].strip().startswith("#"):
        end += 1
    header = [line.rstrip("\r\n") for line in lines[:end]]
    if not header:
        return text, None
    new_header, reason = versioned_header(header)
    if reason or new_header == header:
        return text, reason
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    return bom + "".join(raw + newline for raw in new_header) + "".join(lines[end:]), None


def is_duet(text: str) -> bool:
    """A duet song file: P1/P2 voice lines or a #DUETSINGERP1/#P1 header
    (the rule of the library utilities' songfile.py)."""
    for raw in text.splitlines():
        tag, _ = header_tag(raw)
        if tag in ("DUETSINGERP1", "P1"):
            return True
        if re.match(r"^P\s*\d", raw):
            return True
    return False


def _number(value: str):
    try:
        return float(value.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def _fmt(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def shift_play_range(header: list, delta_ms: float, first_note_s: float,
                     last_note_end_s: float) -> list:
    """The header lines with #START (seconds) and #END (ms) moved by
    `delta_ms`, the shift of #GAP (a #START of 0, "from the beginning",
    stays). #START is removed when it would end up
    below 0 or later than USDX_LEAD_IN_S before the first note (USDX then
    plays silence until #START while the lyrics run), #END when it would
    end before the last note does. Other lines stay."""
    out = []
    for raw in header:
        tag, value = header_tag(raw)
        if tag == "START" and _number(value):
            start = _number(value) + delta_ms / 1000.0
            if start < 0 or start > first_note_s - USDX_LEAD_IN_S:
                continue
            raw = f"#START:{_fmt(start)}"
        elif tag == "END" and _number(value) is not None:
            end = _number(value) + delta_ms
            if end < last_note_end_s * 1000.0:
                continue
            raw = f"#END:{int(round(end))}"
        out.append(raw)
    return out


def set_tag(text: str, tag: str, value: str) -> str:
    """A whole song file's text with one header tag set: an existing line of
    that tag (any case) replaced in place, else added after the last header
    line. The file's line endings stay."""
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    end = 0
    while end < len(lines) and lines[end].strip().startswith("#"):
        end += 1
    line = f"#{tag.upper()}:{value}{newline}"
    for i in range(end):
        if header_tag(lines[i])[0] == tag.upper():
            lines[i] = line
            return "".join(lines)
    return "".join(lines[:end] + [line] + lines[end:])
