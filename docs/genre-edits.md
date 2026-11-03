# Genre edits

The karaoke-dashboard lets an admin set the genres of a song and exports
the edits as `genre-edits.tsv`. The stack writes them into the song files;
nothing else in the song changes. Commands run from the repository root
(full reference: [stack-manual.md](stack-manual.md)).

```sh
# 1. save the export as stack/input/genre-edits.tsv, then:
./factory.sh pull-genres            # dry run: which song files would be copied
./factory.sh pull-genres --apply    # copies them from the library into stack/input/genres/
./factory.sh genres                 # writes the genres -> stack/output/genres/
```

Then copy each `stack/output/genres/<folder>/<song file>` into the library
folder of the same name (only the song file changes; overwrite it).

## The export

No header line; per line the song file relative to the library, a TAB, and
the wanted genres:

```text
AC-DC - Hells Bells/HellsBells-638.txt	Rock, Hard
AC_DC - Thunderstruck/AC_DC - Thunderstruck.txt	Rock, Hard
```

- The genres are written exactly as given: in their order, trimmed,
  duplicates (any case) dropped, joined with `, `. They do not go through the
  genre bases of `utilities/genre_bases.py`.
- A line naming a file that is no song file (`language.txt` and the other
  files the stack writes next to a song) is left out: `pull-genres` lists it
  as "no song", the report as skipped.
- `LIBRARY_DIR` in `stack/.env` says where the library is (`--library DIR`
  for one call).

## What happens

- `pull-genres` copies only the song files, no audio, video or cover, into
  `stack/input/genres/<folder>/`. A copy already there is replaced by the
  library's current file. The library is only read.
- `genres` (= `./factory.sh run --only genres`) runs one job per line of the
  export: the song file from `stack/input/genres/` gets the wanted `#GENRE`
  (the first `#GENRE` line replaced, further ones dropped, else added after
  the header) and goes to `stack/output/genres/<folder>/`. Byte order mark,
  line endings and the encoding (UTF-8 or cp1252) stay; the input copy is not
  changed. Seconds per song, no GPU.
- A line whose song file is not in `stack/input/genres/` yet waits: it is
  listed as skipped ("pull-genres copies it") and runs once the file is
  there.
- A done edit runs again when a later export wants other genres for that
  song; with the same genres it stays done.
- `output/report.md` lists every written song under "Genres updated" with
  the genre before and now.
