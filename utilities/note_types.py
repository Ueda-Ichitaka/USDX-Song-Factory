"""Note types of an UltraStar song file: reading, marks files, applying.

About me: shared by note_type_editor.py (the desktop app to set the note
types of lines and single notes) and apply_note_types.py (writes them into
song files). The note types: ':' normal, '*' golden, 'F' freestyle (not
scored), 'R' rap, 'G' rap golden. Lines are numbered from 1 in file order;
a line ends at a line break ('-'), a duet singer switch (P1/P2) or the end
('E'). Its notes are numbered from 1 within the line. A line's text is its
syllables joined (a syllable without a leading space continues the word),
a tab in a syllable read as a space.

A marks file (stack/input/note-types/<Band> - <Title>.tsv, UTF-8, no header)
names one song and holds the whole wanted state of its note types: a row
per line whose notes are not all normal - its type when all its notes have
the same one, else "mixed" and a row "<line>.<note>" per note of it that is
not normal. A line not listed is all normal.

    band<TAB>Pol1z1stens0hn
    title<TAB>Recht Kommt (K.O. in KA)
    1<TAB>F<TAB>„am Landgericht”
    27<TAB>mixed<TAB>Guck ma' hoch
    27.3<TAB>*<TAB> hoch
    45<TAB>R<TAB>Tick tack du Primat Recht kommt langsam und hart

apply() gives every note its wanted type. It only rewrites the note type
character, so the encoding, a BOM and the line endings stay. Marks that do
not fit the song (another line text or syllable, a line or note the song
does not have) are refused (MarksMismatch).

Pure standard library.
"""

import codecs
import os
import re
from dataclasses import dataclass, field

import songfile

NOTE_RE = re.compile(r"^([:*FRG])[ \t]+(-?\d+)[ \t]+(\d+)[ \t]+(-?\d+)(?:[ \t](.*))?$")
LINE_END_RE = re.compile(r"^(-|E\b|P\s*\d)")
NOTE_ROW_RE = re.compile(r"^(\d+)\.(\d+)$")
TYPES = {":": "normal", "*": "golden", "F": "freestyle", "R": "rap", "G": "rap golden"}
MIXED = "mixed"


class MarksMismatch(ValueError):
    """Marks that do not fit the song file."""


@dataclass
class Note:
    row: int
    kind: str
    start: int
    length: int
    text: str


@dataclass
class Line:
    number: int
    notes: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(n.text for n in self.notes).strip()

    @property
    def types(self) -> str:
        return "".join(n.kind for n in self.notes)

    @property
    def kind(self) -> str:
        """The type of all its notes, or 'mixed'."""
        return self.types[0] if len(set(self.types)) == 1 else MIXED

    @property
    def start_beat(self) -> int:
        return self.notes[0].start

    @property
    def end_beat(self) -> int:
        return max(n.start + n.length for n in self.notes)


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


@dataclass
class Marks:
    band: str = ""
    title: str = ""
    lines: dict = field(default_factory=dict)   # line number -> (type or "mixed", line text)
    notes: dict = field(default_factory=dict)   # (line, note) -> (type, syllable)


def parse(data: bytes) -> Song:
    """The header tags and the lyrics lines; a note's row is its index in
    data.split(b"\\n") (the text lines are decoded index for index)."""
    tags, lines = {}, []
    current = None
    for row, line in enumerate(songfile.decode(data).split("\n")):
        line = line.rstrip("\r")
        note = NOTE_RE.match(line)
        if note:
            if current is None:
                current = Line(number=len(lines) + 1)
                lines.append(current)
            current.notes.append(Note(row=row, kind=note.group(1), start=int(note.group(2)),
                                      length=int(note.group(3)),
                                      text=(note.group(5) or "").replace("\t", " ")))
        elif line.startswith("#") and not lines:
            key, _, value = line[1:].partition(":")
            tags.setdefault(key.strip().upper(), value.strip())
        elif LINE_END_RE.match(line):
            current = None
    return Song(tags=tags, lines=lines)


def state(song: Song) -> dict:
    """The note types of every line as the song file has them."""
    return {line.number: line.types for line in song.lines}


def check_marks(song: Song, marks: Marks) -> list:
    """What does not fit the song, one message each."""
    problems = []
    for number, (kind, text) in marks.lines.items():
        if not 1 <= number <= len(song.lines):
            problems.append(f"line {number}: the song has {len(song.lines)} lines")
        elif kind != MIXED and kind not in TYPES:
            problems.append(f"line {number}: unknown note type '{kind}'")
        elif song.lines[number - 1].text != text:
            problems.append(f"line {number}: the song has '{song.lines[number - 1].text}', "
                            f"the marks say '{text}'")
    for (number, index), (kind, text) in marks.notes.items():
        name = f"note {number}.{index}"
        if marks.lines.get(number, ("", ""))[0] != MIXED:
            problems.append(f"{name}: line {number} is not marked mixed")
        elif not 1 <= number <= len(song.lines):
            continue
        elif not 1 <= index <= len(song.lines[number - 1].notes):
            problems.append(f"{name}: line {number} has {len(song.lines[number - 1].notes)} notes")
        elif kind not in TYPES:
            problems.append(f"{name}: unknown note type '{kind}'")
        elif song.lines[number - 1].notes[index - 1].text.strip() != text.strip():
            problems.append(f"{name}: the song has '{song.lines[number - 1].notes[index - 1].text.strip()}', "
                            f"the marks say '{text.strip()}'")
    return problems


def wanted(song: Song, marks: Marks) -> dict:
    """The note types the marks want for every line (marks checked first)."""
    out = {}
    for line in song.lines:
        kind = marks.lines.get(line.number, (":", ""))[0]
        if kind == MIXED:
            types = [":"] * len(line.notes)
            for (number, index), (note_kind, _) in marks.notes.items():
                if number == line.number and 1 <= index <= len(types):
                    types[index - 1] = note_kind
            out[line.number] = "".join(types)
        else:
            out[line.number] = kind * len(line.notes)
    return out


def changes(song: Song, want: dict) -> list:
    """(row, old type, new type) of every note whose type changes."""
    out = []
    for line in song.lines:
        types = want.get(line.number, ":" * len(line.notes))
        for note, new in zip(line.notes, types):
            if note.kind != new:
                out.append((note.row, note.kind, new))
    return out


def apply(data: bytes, marks: Marks) -> bytes:
    """The song file with the note types the marks want."""
    song = parse(data)
    problems = check_marks(song, marks)
    if problems:
        raise MarksMismatch("; ".join(problems))
    rows = data.split(b"\n")
    for row, _, new in changes(song, wanted(song, marks)):
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


def _cell(text: str) -> str:
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def write_marks(path: str, song: Song, want: dict) -> None:
    """The marks file of `song` for the note types `want` (line -> types)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = [f"band\t{_cell(song.tags.get('ARTIST', ''))}", f"title\t{_cell(song.tags.get('TITLE', ''))}"]
    for line in song.lines:
        types = want.get(line.number, line.types)
        if set(types) == {":"}:
            continue
        if len(set(types)) == 1:
            rows.append(f"{line.number}\t{types[0]}\t{_cell(line.text)}")
            continue
        rows.append(f"{line.number}\t{MIXED}\t{_cell(line.text)}")
        for index, (note, kind) in enumerate(zip(line.notes, types), 1):
            if kind != ":":
                rows.append(f"{line.number}.{index}\t{kind}\t{_cell(note.text)}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(rows) + "\n")


def read_marks(path: str) -> Marks:
    marks = Marks()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in f:
            cells = row.rstrip("\r\n").split("\t", 2)
            key = cells[0].strip()
            if key == "band" and len(cells) > 1:
                marks.band = "\t".join(cells[1:])
            elif key == "title" and len(cells) > 1:
                marks.title = "\t".join(cells[1:])
            elif len(cells) == 3 and key.isdigit():
                marks.lines[int(key)] = (cells[1].strip(), cells[2])
            elif len(cells) == 3 and NOTE_ROW_RE.match(key):
                number, index = NOTE_ROW_RE.match(key).groups()
                marks.notes[(int(number), int(index))] = (cells[1].strip(), cells[2])
    return marks


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
