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
4. Every repaired song file gets the format version new songs have
   (`#VERSION:1.2.0`, first line) unless it already has one. `#AUDIO` repeats
   `#MP3` (as in new songs), and `#RELATIVE` and a UTF-8 `#ENCODING` are
   removed: USDX refuses to load a versioned file with `#RELATIVE`, even
   `#RELATIVE:No`, which USDB downloads contain. A song using what version 1
   no longer has (`#RELATIVE:yes`, another `#ENCODING`, `#DUETSINGERP1/P2`,
   `#NOTESGAP`) is written without `#VERSION`, and the log says why.

## Repair modes

Set with the environment variable `REPAIR_MODE` (default `sync`); `lyrics` is
chosen automatically, see below.

| mode | what it does | cost |
|------|--------------|------|
| `gap`  | Separates the vocals, aligns the first lyric lines near `#GAP` and, as a second opinion, the song file's whole lyrics over the whole song (one alignment; the times of all lines give the shift, however large - 30 s and more). When both agree (within `GAP_MAX_SPREAD_S`) the opening's finer value is used; when the opening is unsure or contradicts a whole song on which at least 80% of the lines agree, the whole song's value is used (the summary says "start found by the whole song"). Only when neither gives a consistent start is the original `#GAP` kept and the song listed under "Start kept by the gap check". When nothing fits at all, the song file usually belongs to another recording: the job fails with "audio does not match the song file", and the reason names the other audio or video file in the song folder that seems to fit (alignment score at least 0.6 and at least 40% of the first words agreeing), or says that none does. `#MP3`/`#VIDEO` are never changed automatically - set them by hand and run the repair again. A song file without a `#GAP` line gets one. Beats, durations and pitches stay as they are. | ~1-2 min/song (one vocal separation) |
| `sync` | Full repair: separates the vocals, force-aligns **every** lyric line (fixes `#GAP` **and** mid-song drift), rebuilds beats/durations and re-detects every note's pitch. The original lyrics are kept. A song that is clearly shifted (at least `SYNC_PRECORRECT_MIN_S`) or drifts (a slightly faster or slower version, at least `SYNC_PRECORRECT_DRIFT_S` over the song) first gets `#GAP` and `#BPM` from the whole-song alignment, so the line search starts each line in the right place. | ~2-6 min/song (CPU) |
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
the export of the "report a broken song" admin view of
[karaoke-dashboard](https://github.com/Ueda-Ichitaka/karaoke-dashboard),
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

The dashboard export also carries `lyrics_url`, `language` and `cover_url`
(all optional): `lyrics_url` (a link or a lyrics file under `stack/input/`,
as in the song list) is tried first for a `lyrics` report,
`language` (ISO 639-1) pins the aligner language, and `cover_url` replaces
the repaired song's cover (same rules as in the song list, see
[new-songs.md](new-songs.md#optional-columns)):

```csv
band,song name,category,description,lyrics_url,language,cover_url
Metric,Black Sheep,lyrics,second verse is wrong,https://genius.com/Metric-black-sheep-lyrics,en,
```

The optional `genre` and `trait` columns work as in the song list (see
[new-songs.md](new-songs.md#optional-columns)).

| category | what happens |
|----------|--------------|
| `gap` | `gap` mode only - no re-timing of the notes. |
| `async` | `sync` mode - full re-alignment, original lyrics kept. |
| `lyrics` | Force-aligns trusted lyrics, same as a manual `lyrics.txt` (below). A `lyrics_url` column (genius.com, an lrclib.net track page, a direct `.txt`/`.lrc` link or a lyrics file under `stack/input/`) is tried first if given, otherwise an online lookup runs, as for new songs. Nothing found -> the job fails (it never falls back to a blind `sync`, which would only re-time the known-wrong lyrics). |
| `video` / `audio` | Restores the missing file: an unambiguous matching file already in the folder is linked as-is; otherwise, if the folder has a `*.usdb` sync-meta file (written by `usdb_syncer` for songs pulled from USDB), the original YouTube source is re-downloaded. Either way a `gap` re-check runs afterwards. No source found, or two or more ambiguous candidates -> the job fails with a message naming what it found. |
| `cover` | Replaces only the cover image with the one at `cover_url`; the song is copied to `output/repaired/` otherwise unchanged (no alignment, no romanisation). `cover_url` is mandatory here: without it the job is marked `needs_review`; a link that is not reachable or not an image fails the job. |
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

## Song end (`song_end.txt`)

A music video may run on after the song - a closing scene, talking, credits
music, applause. In `lyrics` mode the aligner would otherwise place the last
lyric lines in there. A `song_end.txt` tells it where the song ends; nothing
after that time is used for the lyrics (the audio and video themselves are
not shortened).

Put it next to the song's txt, in the same folder as `lyrics.txt`:

```
stack/input/Lord of the Lost - Six Feet Underground/Lord of the Lost - Six Feet Underground.txt
stack/input/Lord of the Lost - Six Feet Underground/lyrics.txt
stack/input/Lord of the Lost - Six Feet Underground/song_end.txt   <- 4:34.5
```

Format (UTF-8 plain text):

* The first line that is neither empty nor starts with `#` is the end time.
  Empty lines and `#` comment lines are ignored; anything after the time line
  is ignored too.
* The time is measured from the start of the song's audio file, the same
  clock as the time display in USDX's editor or any audio player. Three forms
  are accepted: seconds (`274.5`), minutes:seconds (`4:34.5`) and
  hours:minutes:seconds (`0:04:34.5`). Seconds and minutes after a colon must
  be below 60; a fraction uses a dot, not a comma.
* Pick a time just after the last sung word, inside the pause before the
  outro.

```
# the band keeps talking after the song
4:34.5
```

Rules:

* It is used in `lyrics` mode only (a `lyrics.txt`, a `lyrics` row in
  `broken.csv`, or the online-lyrics step of a new song); `gap` and `sync`
  mode ignore it.
* It wins over the automatic outro detection of new songs (see
  [new-songs.md](new-songs.md#what-happens-to-a-new-song)).
* A time at or after the end of the audio means "no outro" - nothing is cut.
* A line that is not a valid time is reported in the log
  (`warning: song_end.txt: not a time: ...`) and the file is ignored; the
  song is then aligned as if there were none.
* The file is copied into the output folder together with the song, so a
  later repair of that output uses it again.

To fix a finished new song this way, copy its folder from `output/new/` to
`input/`, add `song_end.txt` (and `lyrics.txt` - the one written next to the
song is fine) and run the repair.

## Anchors (`anchors.txt`)

When the aligner drifts in some part of a song (an outro, a choir, a line
sung differently than written), you can pin lyric lines to the time they
are sung - typically the last three lines - and set the song end. The song
is then aligned piece by piece between the anchors, so an error before an
anchor can no longer push the lines after it out of place.

Put `anchors.txt` next to the song's txt and `lyrics.txt`:

```
stack/input/Lord of the Lost - Six Feet Underground/lyrics.txt
stack/input/Lord of the Lost - Six Feet Underground/anchors.txt
```

Format (UTF-8 plain text, one entry per line):

```
# anchors for Six Feet Underground - the last three lines and the song end
4:12.0 | Deep down in the dark
4:18.3 | I'm loving you six feet underground
4:25.0 | Six feet underground
end 4:34.5
```

* **Anchor line:** `<time> | <lyric line>`. The time is when the **first
  word** of that line starts to be sung. The text after `|` is the line as
  it is written in `lyrics.txt`; upper/lower case, punctuation and extra
  spaces do not matter, but the words must be the same.
* **End line:** `end <time>` - where the song ends; nothing after it is used
  for the lyrics (same meaning as [`song_end.txt`](#song-end-song_endtxt), and
  it wins over that file and over the automatic outro detection). Optional;
  at most one, and it must come after the last anchor line.
* **Times:** as in `song_end.txt` - seconds (`265.5`), minutes:seconds
  (`4:25.5`) or hours:minutes:seconds (`0:04:25.5`), measured from the start
  of the song's audio file. Anchor times must increase from line to line.
* Empty lines and lines starting with `#` are ignored.
* **Which occurrence:** anchors are matched from the end of the lyrics - the
  last anchor line means the last line in `lyrics.txt` with that text, each
  earlier anchor the last matching line before it. So with a repeated
  chorus, the anchors mean the latest occurrences - what you want for "the
  last three lines". Any number of anchors is allowed, anywhere in the song.

Rules:

* Used in `lyrics` mode only, like `song_end.txt`.
* If a line of the file breaks the format, or an anchor text is not found in
  `lyrics.txt` (in this order), the log says why
  (`warning: anchors.txt line 3: ...` or `... lyric line not found ...`) and
  the anchors are ignored - the song is aligned as if there were none. A
  broken format also ignores the `end` line; an anchor text that is not found
  keeps it.
* Anchors at or after the song end are ignored.
* The file is copied into the output folder together with the song.

Getting the times: play the song in USDX's editor or any audio player and
note when each of the last three lines starts, and where the singing ends.

## Tuning (rarely needed)

Set them in `stack/.env` (see [configuration.md](configuration.md)).

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
