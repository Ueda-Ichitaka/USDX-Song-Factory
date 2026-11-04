"""Freestyle lines of an UltraStar song file: reading, marks files, applying.

About me: shared by freestyle_marker.py (the desktop app to mark lines) and
apply_freestyle.py (writes the marked lines into song files). A lyrics line
is freestyle when all its notes have the note type F (not scored; the other
types: ':' normal, '*' golden, 'R' rap, 'G' rap golden). Lines are numbered
from 1 in file order; a line ends at a line break ('-'), a duet singer
switch (P1/P2) or the end ('E'). Its text is its syllables joined, a tab in
a syllable read as a space.

A marks file (stack/input/freestyle/<Band> - <Title>.tsv, UTF-8, no header)
names one song and the lines wanted as freestyle - it is the whole wanted
state of that song:

    band<TAB>Pol1z1stens0hn
    title<TAB>Recht Kommt (K.O. in KA)
    1<TAB>„am Landgericht”
    42<TAB>Ich glaube es ist eine traurige Wahrheit ...

apply() turns every note of a marked line into F and every F note of any
other line back into ':'. It only rewrites the note type character, so the
encoding, a BOM and the line endings stay as they are. A mark whose line
number and text do not fit the song is refused (MarksMismatch).

Pure standard library.
"""

import codecs
import os
import re
from dataclasses import dataclass, field

import songfile

NOTE_RE = re.compile(r"^([:*FRG])[ \t]+(-?\d+)[ \t]+(\d+)[ \t]+(-?\d+)(?:[ \t](.*))?$")
LINE_END_RE = re.compile(r"^(-|E\b|P\s*\d)")
NOTE_TYPES = ":*FRG"


class MarksMismatch(ValueError):
    """Marks that do not fit the song file (line number and text differ)."""


@dataclass
class Line:
    number: int
    text: str = ""
    rows: list = field(default_factory=list)
    types: str = ""
    start_beat: int = 0
    end_beat: int = 0

    @property
    def freestyle(self) -> bool:
        return bool(self.types) and set(self.types) == {"F"}

    @property
    def partly_freestyle(self) -> bool:
        return "F" in self.types and not self.freestyle


@dataclass
class Song:
    tags: dict
    lines: list

    def seconds(self, beat: int):
        """The time of a beat in the audio (#GAP ms + beat * 60 / (#BPM * 4));
        None without #BPM or with #RELATIVE:yes."""
        if self.tags.get("RELATIVE", "").lower() == "yes":
            return None
        try:
            bpm = float(self.tags.get("BPM", "").replace(",", "."))
            gap = float((self.tags.get("GAP") or "0").replace(",", "."))
        except ValueError:
            return None
        if bpm <= 0:
            return None
        return gap / 1000 + beat * 60 / (bpm * 4)


def _text_lines(data: bytes) -> list:
    """The file's lines as text, index for index the same as data.split(b"\\n")."""
    return songfile.decode(data).split("\n")


def parse(data: bytes) -> Song:
    tags, lines = {}, []
    current = None
    for row, line in enumerate(_text_lines(data)):
        line = line.rstrip("\r")
        note = NOTE_RE.match(line)
        if note:
            if current is None:
                current = Line(number=len(lines) + 1, start_beat=int(note.group(2)))
                lines.append(current)
            start, length = int(note.group(2)), int(note.group(3))
            current.rows.append(row)
            current.types += note.group(1)
            current.text += (note.group(5) or "").replace("\t", " ")
            current.end_beat = max(current.end_beat, start + length)
        elif line.startswith("#") and current is None and not lines:
            key, _, value = line[1:].partition(":")
            tags.setdefault(key.strip().upper(), value.strip())
        elif LINE_END_RE.match(line):
            current = None
    for item in lines:
        item.text = item.text.strip()
    return Song(tags=tags, lines=lines)


def check_marks(song: Song, marks: list) -> list:
    """What does not fit: one message per mark whose line is not in the
    song or has a different text."""
    problems = []
    for number, text in marks:
        if not 1 <= number <= len(song.lines):
            problems.append(f"line {number}: the song has {len(song.lines)} lines")
        elif song.lines[number - 1].text != text:
            problems.append(f"line {number}: the song has '{song.lines[number - 1].text}', "
                            f"the marks say '{text}'")
    return problems


def changes(song: Song, marked: set) -> list:
    """(row, old type, new type) of every note apply() rewrites."""
    out = []
    for line in song.lines:
        for row, kind in zip(line.rows, line.types):
            if line.number in marked and kind != "F":
                out.append((row, kind, "F"))
            elif line.number not in marked and kind == "F":
                out.append((row, kind, ":"))
    return out


def apply(data: bytes, marks: list) -> bytes:
    """The song file with the marked lines freestyle and no other F note."""
    song = parse(data)
    problems = check_marks(song, marks)
    if problems:
        raise MarksMismatch("; ".join(problems))
    rows = data.split(b"\n")
    for row, _, new in changes(song, {number for number, _ in marks}):
        at = len(codecs.BOM_UTF8) if row == 0 and rows[0].startswith(codecs.BOM_UTF8) else 0
        rows[row] = rows[row][:at] + new.encode("ascii") + rows[row][at + 1:]
    return b"\n".join(rows)


def safe_name(name: str) -> str:
    """A 'Band - Title' made safe as a file or folder name (the stack's rule:
    a slash or NUL becomes a hyphen)."""
    name = (name or "").replace("\x00", "-").replace("/", "-").strip(". ")
    return name[:200] or "song"


def marks_name(band: str, title: str) -> str:
    return safe_name(f"{band} - {title}") + ".tsv"


def write_marks(path: str, band: str, title: str, marks: list) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    clean = lambda s: s.replace("\t", " ").replace("\r", " ").replace("\n", " ")  # noqa: E731
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"band\t{clean(band)}\ntitle\t{clean(title)}\n")
        for number, text in sorted(marks):
            f.write(f"{number}\t{clean(text)}\n")


def read_marks(path: str) -> tuple:
    """(band, title, [(line number, line text), ...]) of a marks file."""
    band, title, marks = "", "", []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for line in f:
            key, _, value = line.rstrip("\r\n").partition("\t")
            if key == "band":
                band = value
            elif key == "title":
                title = value
            elif key.strip().isdigit():
                marks.append((int(key), value))
    return band, title, sorted(marks)


def _matches(path: str, band: str, title: str) -> bool:
    tags = songfile.read(path).tags
    return tags.get("ARTIST") == band and tags.get("TITLE") == title


def find_song(library: str, band: str, title: str) -> list:
    """The song files in the library with #ARTIST band and #TITLE title: the
    folder 'Band - Title' first, else every folder."""
    first = os.path.join(library, safe_name(f"{band} - {title}"))
    if os.path.isdir(first):
        found = [p for p in songfile.song_txts(first) if _matches(p, band, title)]
        if found:
            return found
    found = []
    for folder, dirs, _ in os.walk(library):
        dirs.sort()
        if os.path.abspath(folder) == os.path.abspath(first):
            continue
        found += [p for p in songfile.song_txts(folder) if _matches(p, band, title)]
    return found


def env_value(key: str, env_file: str) -> str:
    """KEY from a .env file (last line wins, quotes removed), else ''."""
    value = ""
    if os.path.isfile(env_file):
        for line in open(env_file, encoding="utf-8"):
            name, sep, rest = line.strip().partition("=")
            if sep and name.strip() == key:
                value = rest.strip().strip('"').strip("'")
    return value
