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

Used by repair.py (every repaired song file it writes) and orchestrator.py
(the cover repair, which copies a song without repair.py). No heavy
imports, so both can use it.
"""

FORMAT_VERSION = "1.2.0"

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
