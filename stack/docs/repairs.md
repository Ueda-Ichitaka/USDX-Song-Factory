# Repairing existing songs

Songs you already have but that are broken (wrong `#GAP`, lyrics drifting out
of sync, wrong lyrics, missing video/audio) can be repaired in bulk.

All commands on this page are run from inside `stack/`.

## How it works

1. Copy a song folder (its `*.txt`, audio, video, cover) into `input/`:

   ```
   stack/input/ASP - Ich will brennen/ASP - Ich will brennen.txt
   stack/input/ASP - Ich will brennen/ASP - Ich will brennen.mp3
   ...
   ```

2. Start the stack (`docker compose up -d`). Every folder in `input/` that
   contains an UltraStar txt is repaired into `output/repaired/<song folder>/`.
3. Your originals in `input/` are never modified.

## Repair modes

Set with the environment variable `REPAIR_MODE` (default `sync`); `lyrics` is
chosen automatically, see below.

| mode | what it does | cost |
|------|--------------|------|
| `gap`  | Aligns only the first lyric lines to the audio and shifts `#GAP` accordingly. Beats, durations and pitches stay as they are. | fast (no separation) |
| `sync` | Full repair: separates the vocals, force-aligns **every** lyric line (fixes `#GAP` **and** mid-song drift), rebuilds beats/durations and re-detects every note's pitch. The original lyrics are kept. | ~2-6 min/song (CPU) |
| `lyrics` | Like `sync`, but REPLACES the lyrics text too, force-aligning a trusted lyrics file instead of the (assumed wrong) existing text. | ~2-6 min/song (CPU) |

The repair keeps the original syllables, note types (normal/golden/rap/...),
line breaks, video/cover references and header tags (except in `lyrics`
mode, which rebuilds the notes from the new text). Both word-boundary
conventions found in txt files (trailing and leading spaces) are supported.
A summary line is printed per song:

```
[UltraSinger] Summary ASP - Ich will brennen: GAP 20834 -> 21601 ms
(delta +767 ms), aligned words 207/210, re-pitched notes 183
```

If the song language is missing in the txt (`#LANGUAGE`), it is detected with
a tiny whisper model. Force it by setting `#LANGUAGE` in the txt, or with
`REPAIR_LANGUAGE` (see "Tuning" below) if a detection goes wrong.

## `broken.csv`: tell the stack what is broken

Without `broken.csv`, every folder in `input/` gets the same blind
`REPAIR_MODE` - correct, but wasteful when you know a song's `#GAP` is just
off by a few seconds. Put `input/broken.csv` next to the song folders (it is
the export of the karaoke-dashboard's "report a broken song" admin view,
`GET /admin/reports.csv`). Each folder is matched to its report by
**#ARTIST/#TITLE** (not the folder name) and gets only the fix its category
calls for:

```csv
band,song name,category,description
Queen,I Want To Break Free,gap,
ASP,Ich will brennen,async,
Metric,Black Sheep,lyrics,second verse is wrong
Boney M.,Daddy Cool,video,
```

An optional `language` column (ISO 639-1) pins the aligner language, like the
column of the same name in the song list.

| category | what happens |
|----------|--------------|
| `gap` | `gap` mode only - cheap, no separation. |
| `async` | `sync` mode - full re-alignment, original lyrics kept. |
| `lyrics` | Force-aligns trusted lyrics, same as a manual `lyrics.txt` (below). A `lyrics_url` column (genius.com, or a direct `.txt`/`.lrc` link) is tried first if given, otherwise an online lookup runs, as for new songs. Nothing found -> the job fails (it never falls back to a blind `sync`, which would only re-time the known-wrong lyrics). |
| `video` / `audio` | Restores the missing file: an unambiguous matching file already in the folder is linked as-is; otherwise, if the folder has a `*.usdb` sync-meta file (written by `usdb_syncer` for songs pulled from USDB), the original YouTube source is re-downloaded. Either way a cheap `gap` re-check runs afterwards. No source found, or two or more ambiguous candidates -> the job fails with a message naming what it found. |
| `other`, blank, or unknown | **Not run automatically.** Marked `needs_review` in the progress view and report instead - fix the category (or the folder) and `reset` to pick it up. |

A folder with **no matching row** keeps the default behaviour (`REPAIR_MODE`,
or `lyrics` mode if a `lyrics.txt` is present) - `broken.csv` is optional.

Point the stack to another file with `BROKEN_CSV` (default
`/data/input/broken.csv`); tune the match strictness with
`BROKEN_MATCH_MIN_SCORE` (default `0.85`).

**`needs_review` jobs are not retried automatically** - a `reset` flips them
back to `pending` once you have fixed the report or the folder.

## Trusted lyrics ("lyrics" mode)

`sync` mode never touches the lyrics themselves - re-timing wrong words only
moves the problem. If a song's lyrics are actually wrong (mis-heard words,
missing lines), put a plain text file named `lyrics.txt` next to its txt and
audio in `input/`:

```
stack/input/ASP - Ich will brennen/ASP - Ich will brennen.txt
stack/input/ASP - Ich will brennen/ASP - Ich will brennen.mp3
stack/input/ASP - Ich will brennen/lyrics.txt      <- one sung line per text line
```

Its presence switches that song's repair to `lyrics` mode. Each line of
`lyrics.txt` becomes one line of the rebuilt song - this is also how you fix
"lines are way too long": break them up in `lyrics.txt` the way they are
sung. Pitches are re-detected the same way `sync` mode does. This is the same
engine the online-lyrics step uses for new songs (force-alignment, no
re-transcription). Every song created or repaired this way also gets its final
`lyrics.txt` written next to it, so a later repair can start from it.

**Lyrics/audio mismatch fallback:** if the txt lyrics do not match the audio
at all (wrong song version, txt much shorter or longer than the audio -
detected via alignment quality), `sync` mode re-creates the song from its
audio with UltraSinger. The log marks this with
`Re-creating song from audio with UltraSinger`. Disable with
`REPAIR_FALLBACK=0` (the job then fails instead).

## Tuning (rarely needed)

`REPAIR_MODE` can be set in `stack/.env`; the others are not passed through
from `.env` - add them under `environment:` in `docker-compose.yml` (see
[configuration.md](configuration.md)).

| variable | default | meaning |
|----------|---------|---------|
| `REPAIR_MODE` | `sync` | `gap` or `sync` |
| `REPAIR_FALLBACK` | `1` | re-create songs whose lyrics don't match the audio |
| `REPAIR_LANGUAGE` | - | force the aligner language (e.g. `de`) |
| `REPAIR_ALIGN_PAD` | `6.0` | search window padding in seconds (`sync` mode) |
| `REPAIR_MIN_WORD_SCORE` | `0.6` | word alignment score below which a line counts as suspicious |
| `REPAIR_MAX_LOCAL_DEVIATION` | `3.0` | local offset deviation (s) counted as suspicious |

## Limits

* `gap` and `sync` mode never change the lyrics - use `lyrics` mode for that.
* `#VIDEOGAP` is not adjusted - if video and audio have different offsets,
  fix it manually after the repair.
* Duet songs (P1/P2 in one txt) are aligned as one continuous singer - line
  breaks are preserved, but interleaved duet timing is not modelled.
