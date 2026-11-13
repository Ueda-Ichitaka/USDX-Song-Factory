# Note types

Every note of an UltraStar song has a note type, the first character of its
line in the song file. The stack only writes normal notes; the note type
editor sets the others by hand - for whole lines or single notes - and a
second tool writes them into a copy of the song file. Commands run from the
repository root, on the host (not in the stack's container).

```sh
python3 utilities/note_type_editor.py                  # 1. mark: a file picker opens
python3 utilities/apply_note_types.py                  # 2. dry run: what would change
python3 utilities/apply_note_types.py --apply          #    writes stack/output/note-types/
```

Then copy each `stack/output/note-types/<folder>/<song file>` into the library
folder of the same name (only the song file changes; overwrite it).

| Type | Key | Name | The format says |
|---|---|---|---|
| `:` | n | normal | the normal note |
| `*` | g | golden | twice the points of a normal note |
| `F` | f | freestyle | not scored - for spoken samples, news clips, quotes |
| `R` | r | rap | a rap note |
| `G` | Shift+R | rap golden | twice the points of a rap note |

The format reference: [usdx.eu/format](https://usdx.eu/format/) (note types,
version 1.1.0 of the format).

## The editor

```sh
python3 utilities/note_type_editor.py [SONG.txt] [--marks DIR]
```

Opens the song file given, or a file picker (in `LIBRARY_DIR` from
`stack/.env`). The window lists the lyrics lines with their type and start
time; below the list the syllables of the line under the cursor, each in the
colour of its type, the picked note underlined.

| Key | Does |
|---|---|
| Up/Down, k/j | previous/next line (PgUp/PgDn: 10 lines, Home/End) |
| Left/Right, h/l | pick a note of the line; left of the first note: the whole line again |
| n, g, f, r, Shift+R | set the type of the whole line, or of the picked note |
| Space | freestyle on/off (all `F` becomes normal, anything else `F`) |
| Enter | freestyle on/off and the next line |
| p | play the line, or the picked note (0.3 s before to 0.3 s after it) |
| Esc | stop playing |
| a | autoplay on/off: play each line or note you move to |
| v | play the song's `#VOCALS` file instead of `#AUDIO`/`#MP3` (on/off) |
| Ctrl+S | save the marks file |
| Ctrl+O | open another song file (asks to save first) |
| q | quit (asks to save first) |

- The Type column: empty for normal, the type character when all notes of
  the line have it, `mixed` otherwise.
- Opening a song shows the types of its marks file. Without one, or when it
  does not fit the song any more (the status line says what does not fit),
  it shows the song file's own types - so golden notes a song already has
  are kept unless you change them.
- Playing needs `ffplay` (ffmpeg), `#BPM` and no `#RELATIVE:yes`; without
  them everything but the sound works.
- The song file is only read.

## The marks file

Ctrl+S writes `stack/input/note-types/<Band> - <Title>.tsv` (from `#ARTIST`
and `#TITLE`; a `/` becomes `-`; `--marks DIR` for another folder). UTF-8, no
header, TAB separated. It holds the whole wanted state of the song's note
types:

```text
band	Pol1z1stens0hn
title	Recht Kommt (K.O. in KA)
1	F	„am Landgericht”
27	mixed	Guck ma' hoch
27.3	*	 hoch
45	R	Tick tack du Primat Recht kommt langsam und hart
```

- `band` and `title` name the song (its `#ARTIST` and `#TITLE`).
- A row per line whose notes are not all normal: the line number, its type
  when all its notes have the same one, and the line's text.
- A line with several types: `mixed`, then a row `<line>.<note>` per note of
  it that is not normal, with its syllable.
- A line not listed is all normal.
- Lines are numbered from 1 in file order; a line ends at a line break
  (`-`), a duet singer switch (`P1`/`P2`) or the end (`E`). Notes are
  numbered from 1 within their line. A line's text is its syllables joined,
  as the editor shows it. The line numbers tell lines with the same text
  apart (a chorus); the texts show when the song changed since.

## Applying

```sh
python3 utilities/apply_note_types.py [--library DIR] [--marks DIR] [--out DIR] [--apply]
```

- Reads every `.tsv` in `stack/input/note-types/` (`--marks DIR`) and finds
  its song in the library (`--library DIR`, default `LIBRARY_DIR` from the
  environment or `stack/.env`) by `#ARTIST` and `#TITLE`: the folder
  `<Band> - <Title>` first, then the whole library.
- Writes a copy with every note set to its wanted type into
  `stack/output/note-types/<library folder>/<song file>` (`--out DIR`). Only
  the note type character changes; the encoding, a byte order mark and the
  line endings stay. Only songs that change are written.
- Lines not in the marks file become all normal: a note you set back to
  normal in the editor is written as normal.
- Left out and listed: a marks file whose line numbers, texts or syllables
  do not fit the song (`mismatch`), a song not in the library (`missing`), a
  band and title found in several song files (`several`), a `.tsv` without
  band and title rows.
- The library is only read. Without `--apply` nothing is written.

Example output:

```text
write    Pol1z1stens0hn - Recht Kommt (K.O. in KA)/Pol1z1stens0hn - Recht Kommt (K.O. in KA).txt: F 1-10, 42-44 (158 notes change)

1 to write into stack/output/note-types/, 0 already as marked, 0 left out
dry run - nothing written, use --apply
```

The score UltraSinger writes into `#COMMENT` ("Score: total ...") stays as it
is; it no longer fits once lines are freestyle.
