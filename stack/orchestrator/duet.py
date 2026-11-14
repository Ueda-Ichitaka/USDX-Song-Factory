#!/usr/bin/env python3
"""The duet core: one model of a duet song file (two singers, P1 and P2).

About me: used by duet generation and duet repair (knowledge/07-DUETS.md,
phase 1). A duet file holds all lines of P1, then all lines of P2; a line
both sing is written in both parts with the same start and text. This
module

- reads such a file (parse_duet()): the header, the singer names
  (#P1:/#P2:, or the older #DUETSINGERP1:/#DUETSINGERP2:), the lines of
  each part with their notes, and problems such as notes going back in
  time inside a line;
- joins a P1 line and a P2 line with the same start beat and the same text
  (spaces and "~" ignored) into one shared line (DuetLine with both
  singers, both copies kept, each with its own notes);
- gives the timeline (timeline()): every line once, sorted by start - what
  an aligner or a whole-song fit has to see (never a line twice, never a
  jump back in time);
- finds overlapping lines (overlap_partners()): a solo line overlapping a
  solo line of the other singer by more than OVERLAP_SHARE of the shorter
  one; the shorter line is left out of the alignment (aligned_timeline())
  and keeps its distance to its partner (follow_partner()); of two equally
  long lines the later one is left out;
- writes a duet file (write_duet()): the header (names set in the tag form
  the file uses - the older #DUETSINGERP form is not converted, see
  song_header.py; a file without names gets #P1/#P2), a P1 and a P2
  section, a line break "- <end of the line>" between lines without a
  stored one, "E". Unchanged notes, breaks and markers keep their raw text
  (also a break after the last line of a part and a marker repeated inside
  a part), so an unchanged file is written back byte for byte (BOM and line
  endings included);
- splits an artist into singer names (split_artist(), duet_names());
- reads an admin's singer mark at the start of a lyrics line ("P1:", "P2:",
  "P1+P2:", split_singer_mark()) and gives the mark of a singer
  (singer_mark());
- tells the tag form a header names the singers in (name_form()).

decode() gives a song file's text and encoding (UTF-8, cp1252 for old
files), so it can be written back in the same encoding.

A #RELATIVE:yes file is refused (DuetError): its beats count from each
line, so starts cannot be compared. Standard library only (plus the
artist split pattern of lyrics_fetch.py).
"""
import re
from dataclasses import dataclass, field

from lyrics_fetch import ARTIST_SPLIT_RE
from song_header import header_tag

PARTS = ("P1", "P2")
OVERLAP_SHARE = 0.25
NOTE_RE = re.compile(r"^\s*([:FRG*])[ \t]+(-?[0-9.,]+)[ \t]+(-?[0-9.,]+)[ \t]+(-?[0-9.,]+)[ \t]?(.*)$")
MARKER_RE = re.compile(r"^\s*P\s*([12])\s*$")
NAME_TAGS = {"P": ("P1", "P2"), "DUETSINGERP": ("DUETSINGERP1", "DUETSINGERP2")}
# an admin's singer mark at the start of a lyrics line: "P1:", "P2:", "P1+P2:"
SINGER_MARK_RE = re.compile(r"^\s*(P\s*1\s*\+\s*P\s*2|P\s*2\s*\+\s*P\s*1|P\s*1|P\s*2)\s*:\s*", re.IGNORECASE)


class DuetError(ValueError):
    """A file the duet core cannot read as a duet."""


def _number(text: str) -> float:
    value = float(text.replace(",", "."))
    return int(value) if value == int(value) else value


def fmt(value) -> str:
    """A beat, length or pitch as UltraStar writes it: whole numbers without
    a decimal point."""
    return str(int(value)) if float(value) == int(value) else f"{value:g}"


@dataclass(eq=False)
class Note:
    kind: str
    beat: float
    length: float
    pitch: int
    text: str
    raw: str = None    # the row as read; None for a new or changed note

    def row(self) -> str:
        return self.raw if self.raw is not None else \
            f"{self.kind} {fmt(self.beat)} {fmt(self.length)} {fmt(self.pitch)} {self.text}"


@dataclass(eq=False)
class Line:
    part: str
    notes: list = field(default_factory=list)
    break_raw: str = None  # the "- <beat>" row after the line as read
    before: list = field(default_factory=list)  # a repeated P marker read before the line

    @property
    def start(self):
        return self.notes[0].beat

    @property
    def end(self):
        return max(n.beat + n.length for n in self.notes)

    @property
    def text(self) -> str:
        return "".join(n.text for n in self.notes).strip()


@dataclass(eq=False)
class DuetLine:
    """A line of the timeline: one singer's line, or a shared line with a
    copy for each singer."""
    singers: frozenset
    copies: dict

    @property
    def main(self) -> Line:
        return self.copies.get("P1") or self.copies["P2"]

    @property
    def start(self):
        return min(c.start for c in self.copies.values())

    @property
    def end(self):
        return max(c.end for c in self.copies.values())

    @property
    def text(self) -> str:
        return self.main.text


@dataclass(eq=False)
class DuetFile:
    header: list
    names: dict
    name_form: str
    parts: dict
    lines: list = None
    problems: list = field(default_factory=list)
    markers: dict = field(default_factory=dict)
    end_raw: str = "E"
    tail: list = field(default_factory=list)
    newline: str = "\n"
    bom: bool = False
    final_newline: bool = True

    def __post_init__(self):
        if self.lines is None:
            self.lines = join_shared(self.parts)


def norm(text: str) -> str:
    return re.sub(r"[\s~]+", " ", text).strip()


def join_shared(parts: dict) -> list:
    """All lines once, sorted by start: a P1 and a P2 line with the same
    start beat and text are one shared line."""
    p2_by_key = {}
    for line in parts["P2"]:
        p2_by_key.setdefault((line.start, norm(line.text)), []).append(line)
    out, used = [], set()
    for line in parts["P1"]:
        twins = [m for m in p2_by_key.get((line.start, norm(line.text)), []) if id(m) not in used]
        if twins:
            used.add(id(twins[0]))
            out.append(DuetLine(frozenset(PARTS), {"P1": line, "P2": twins[0]}))
        else:
            out.append(DuetLine(frozenset({"P1"}), {"P1": line}))
    out += [DuetLine(frozenset({"P2"}), {"P2": m}) for m in parts["P2"] if id(m) not in used]
    return sorted(out, key=lambda l: (l.start, "P1" not in l.singers))


def decode(raw: bytes) -> tuple:
    """(text, encoding) of a song file's bytes: UTF-8 (a BOM stays in the
    text, parse_duet() keeps it), else cp1252 (old song files), else
    latin-1. Writing the text back in that encoding gives the same bytes."""
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def name_form(header: list):
    """The tag form a header names the singers in: "P" (#P1/#P2),
    "DUETSINGERP" (#DUETSINGERP1/2) or None."""
    tags = {header_tag(raw)[0] for raw in header}
    if tags & {"P1", "P2"}:
        return "P"
    if tags & {"DUETSINGERP1", "DUETSINGERP2"}:
        return "DUETSINGERP"
    return None


def parse_duet(text: str) -> DuetFile:
    bom = text.startswith("﻿")
    body_text = text[1:] if bom else text
    newline = "\r\n" if "\r\n" in body_text else "\n"
    final_newline = body_text.endswith(("\n", "\r"))
    rows = body_text.replace("\r\n", "\n").split("\n")
    if final_newline:
        rows = rows[:-1]

    header, i = [], 0
    while i < len(rows) and (rows[i].strip().startswith("#") or not rows[i].strip()):
        header.append(rows[i])
        i += 1
    tags = {}
    for raw in header:
        tag, value = header_tag(raw)
        if tag:
            tags.setdefault(tag, value)
    if tags.get("RELATIVE", "").lower() == "yes":
        raise DuetError("#RELATIVE:yes - relative beats are not supported for duets")

    parts = {p: [] for p in PARTS}
    markers, problems = {}, []
    part, current, end_raw, tail, pending = "P1", None, "E", [], []

    def close(break_raw=None):
        nonlocal current
        if current is not None and current.notes:
            current.break_raw = break_raw
            parts[current.part].append(current)
        current = None

    while i < len(rows):
        raw = rows[i]
        stripped = raw.strip()
        i += 1
        marker = MARKER_RE.match(raw)
        if marker:
            close()
            part = f"P{marker.group(1)}"
            if part in markers:
                pending.append(raw)
            else:
                markers[part] = raw
            continue
        note = NOTE_RE.match(raw)
        if note:
            if current is None:
                current = Line(part=part, before=pending)
                pending = []
            n = Note(note.group(1), _number(note.group(2)), _number(note.group(3)),
                     int(_number(note.group(4))), note.group(5) or "", raw)
            if current.notes and n.beat < current.notes[-1].beat:
                problems.append(f"{part} line {len(parts[part]) + 1}: notes go back in time "
                                f"(beat {fmt(current.notes[-1].beat)} -> {fmt(n.beat)})")
            current.notes.append(n)
        elif stripped.startswith("-"):
            close(raw)
        elif stripped == "E" or stripped.startswith("E "):
            close()
            end_raw, tail = raw, rows[i:]
            break
    close()
    for p in PARTS:
        if not parts[p]:
            raise DuetError(f"no {p} part - not a duet")

    form = name_form(header)
    names = {p: tags.get(NAME_TAGS[form][k], "") if form else "" for k, p in enumerate(PARTS)}
    return DuetFile(header=header, names=names, name_form=form, parts=parts, problems=problems,
                    markers=markers, end_raw=end_raw, tail=tail, newline=newline, bom=bom,
                    final_newline=final_newline)


def timeline(d: DuetFile) -> list:
    """Every line once (a shared line once for both singers), by start."""
    return list(d.lines)


def overlap_partners(d: DuetFile) -> dict:
    """{left-out line: partner line} for solo lines overlapping a solo line
    of the other singer by more than OVERLAP_SHARE of the shorter one; the
    longer line stays (of two equally long ones the earlier), the shorter
    is left out. In timeline order."""
    kept = [l for l in d.lines if len(l.singers) == 2]
    solo = sorted((l for l in d.lines if len(l.singers) == 1),
                  key=lambda l: (-(l.end - l.start), l.start, "P1" not in l.singers))
    partners = {}
    for line in solo:
        best, best_overlap = None, 0.0
        for other in kept:
            if len(other.singers) == 2 or other.singers == line.singers:
                continue
            overlap = min(line.end, other.end) - max(line.start, other.start)
            shorter = max(1e-9, min(line.end - line.start, other.end - other.start))
            if overlap / shorter > OVERLAP_SHARE and overlap > best_overlap:
                best, best_overlap = other, overlap
        if best is None:
            kept.append(line)
        else:
            partners[line] = best
    return {l: partners[l] for l in d.lines if l in partners}


def aligned_timeline(d: DuetFile) -> list:
    """The timeline without the overlapping lines left out of the alignment."""
    left_out = overlap_partners(d)
    return [l for l in d.lines if l not in left_out]


def follow_partner(old_start, old_partner_start, new_partner_start):
    """The new start of a left-out line: its old distance to its partner."""
    return old_start + (new_partner_start - old_partner_start)


def set_names(header: list, names: dict, form: str) -> list:
    """The header with the singer names: replaced in place in the tag form
    the file uses, else added as #P1/#P2 after the last header line."""
    if form:
        tags = NAME_TAGS[form]
        out = []
        for raw in header:
            tag, _ = header_tag(raw)
            if tag in tags:
                raw = f"#{tag}:{names[PARTS[tags.index(tag)]]}"
            out.append(raw)
        return out
    last = max((i for i, raw in enumerate(header) if raw.strip()), default=-1)
    return header[:last + 1] + [f"#P1:{names['P1']}", f"#P2:{names['P2']}"] + header[last + 1:]


def write_duet(d: DuetFile, names: dict = None) -> str:
    rows = set_names(d.header, names, d.name_form) if names else list(d.header)
    for part in PARTS:
        rows.append(d.markers.get(part, part))
        lines = d.parts[part]
        for k, line in enumerate(lines):
            rows += line.before
            rows += [n.row() for n in line.notes]
            if line.break_raw is not None:
                rows.append(line.break_raw)
            elif k < len(lines) - 1:
                rows.append(f"- {fmt(line.end)}")
    rows.append(d.end_raw)
    rows += d.tail
    text = d.newline.join(rows) + (d.newline if d.final_newline else "")
    return ("﻿" if d.bom else "") + text


def split_singer_mark(text: str) -> tuple:
    """(singer, sung text) of a lyrics line with an admin's mark: "P1:" ->
    "P1", "P2:" -> "P2", "P1+P2:" (or "P2+P1:") -> "both"; (None, text)
    without a mark."""
    m = SINGER_MARK_RE.match(text or "")
    if not m:
        return None, text
    mark = re.sub(r"\s", "", m.group(1)).upper()
    return ("both" if "+" in mark else mark), text[m.end():]


def singer_mark(singer: str) -> str:
    """The mark a lyrics line of `singer` (P1, P2, both) starts with -
    what split_singer_mark() reads."""
    return {"P1": "P1: ", "P2": "P2: ", "both": "P1+P2: "}[singer]


def split_artist(artist: str) -> list:
    """The singers of an artist credit: split at feat./ft./featuring/with/
    and/x/&/,/+ ("Eminem feat. Rihanna" -> ["Eminem", "Rihanna"])."""
    return [a.strip() for a in ARTIST_SPLIT_RE.split(artist or "") if a.strip()]


def duet_names(artist: str):
    """(P1 name, P2 name) when the artist credit names exactly two singers,
    else None (the order is the credit's; who is P1 is decided later)."""
    names = split_artist(artist)
    return (names[0], names[1]) if len(names) == 2 else None
