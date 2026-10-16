"""Read the header of an UltraStar song file, and the dashboard codes for it.

About me: shared by the library utilities (library_check.py,
import_broken_songs.py, fill_lyrics_links.py, copy_reported_songs.py).
read() gives the header tags (first value of each tag), the number of notes
and whether the file is a duet (P1/P2) or uses relative timing - the same
rule as USDX (USong.pas): only #RELATIVE:yes, the second number of a line
break ("- 30 31") is ignored without it. Song files in other encodings than
UTF-8 (cp1252 is common in old libraries) are read too. song_txts() counts
a .txt file as a song file only with #TITLE and #ARTIST, which USDX needs
too - license.txt, README.txt and notes files are left out.
language_code() and genre_code() turn the free text of #LANGUAGE and #GENRE
into the karaoke-dashboard's codes (its app/languages.py and app/genres.py).

Pure standard library.
"""

import os
import re
from dataclasses import dataclass, field

ADMIN_FILES = ("lyrics.txt", "language.txt", "song_end.txt", "anchors.txt")
LANGUAGES = {"english": "en", "englisch": "en", "german": "de", "deutsch": "de",
             "french": "fr", "français": "fr", "spanish": "es", "español": "es",
             "italian": "it", "portuguese": "pt", "dutch": "nl", "polish": "pl",
             "russian": "ru", "swedish": "sv", "danish": "da", "norwegian": "no",
             "finnish": "fi", "turkish": "tr", "japanese": "ja", "korean": "ko",
             "chinese": "zh"}
GENRES = ("metal", "medieval", "folk", "gothic", "symphonic", "punk", "pop", "rap",
          "kpop", "jpop", "rock", "industrial", "ebm")
NOTE_RE = re.compile(r"^[:*FRG]\s")


@dataclass
class SongFile:
    path: str
    text: str
    tags: dict = field(default_factory=dict)
    notes: int = 0
    duet: bool = False
    relative: bool = False


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def read(path: str) -> SongFile:
    with open(path, "rb") as f:
        text = decode(f.read())
    song = SongFile(path=path, text=text)
    for line in text.splitlines():
        if line.startswith("#"):
            key, _, value = line[1:].partition(":")
            song.tags.setdefault(key.strip().upper(), value.strip())
        elif NOTE_RE.match(line):
            song.notes += 1
        elif re.match(r"^P\s*\d", line):
            song.duet = True
    if "DUETSINGERP1" in song.tags or "P1" in song.tags:
        song.duet = True
    if song.tags.get("RELATIVE", "").lower() == "yes":
        song.relative = True
    return song


def language_code(value: str) -> str:
    """The dashboard language code of a #LANGUAGE value (its first entry);
    empty for romanized lyrics ("Japanese (romanized)") and unknown values."""
    first = (value or "").split(",")[0].strip().lower()
    return LANGUAGES.get(first, "")


def genre_code(value: str) -> str:
    """#GENRE as a dashboard genre code when it is exactly one, else 'default'."""
    code = (value or "").strip().lower()
    return code if code in GENRES else "default"


def generated_by_stack(text: str) -> bool:
    """A song file this stack (UltraSinger) wrote - no truth for a check."""
    return "ultrasinger" in text[:4000].lower()


def song_txts(folder: str) -> list:
    """The song files of a folder (sorted): .txt files with #TITLE and
    #ARTIST, admin files left out."""
    out = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not name.lower().endswith(".txt") or name.lower() in ADMIN_FILES or not os.path.isfile(path):
            continue
        tags = read(path).tags
        if tags.get("TITLE") and tags.get("ARTIST"):
            out.append(path)
    return out
