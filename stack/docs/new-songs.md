# New songs: the song list

The stack creates new songs from a want-list: `stack/input/song-requests.csv`
(or a plain `input/songs.txt`). The CSV is normally the song-request export of
the companion web app
[karaoke-dashboard](https://github.com/Ueda-Ichitaka/karaoke-dashboard)
(`GET /admin/requests.csv`), but it can also be written by hand. Every row becomes one job; finished jobs are
remembered in `state/state.json` and never redone unless you reset them.

All commands on this page are run from inside `stack/`.

## `song-requests.csv`

Comma or semicolon separated, header line required. The dashboard export
looks like this:

```csv
band name,song name,youtube link,language,musicbrainz_id,lyrics_url,cover_url,duet
ASP,Zaubererbruder,https://www.youtube.com/watch?v=sifM_9DIVbI,de,,,,no
```

A hand-written file only needs the first three columns, with either header
spelling (`band name,song name,youtube link` or `band,title,url`):

```csv
band,title,url
ASP,Denn Ich bin der Meister,https://www.youtube.com/watch?v=U6bmr2zcCos
ASP,Zaubererbruder,https://www.youtube.com/watch?v=sifM_9DIVbI
Local Band,A Song I Already Have,media/local-band-a-song.mp3
```

* The `url` cell is either a **YouTube link** (it must start with `https://`
  - that is the only thing that marks it as a link, nothing is guessed) or a
  **local audio/video file path** (`.mp3`, `.mp4`, ...), resolved relative to
  `input/` when not absolute. Drop the file under `input/` (for example
  `input/media/local-band-a-song.mp3`) and reference it by that relative path.
  A local file is handled exactly like a download: if it has no video track,
  the song simply has no `#VIDEO` (UltraStar Deluxe falls back to its built-in
  visualization).
* A row with `skip` (or `-`, or empty) as url is skipped - for example songs
  you already own: `Aequitas,He's a Pirate,skip`. The finishing report lists
  it with the reason.
* **Band/title name the output folder and the `#ARTIST`/`#TITLE` tags.** They
  are passed through as `--force_artist`/`--force_title` and always win over
  the YouTube channel name and over a MusicBrainz alternate-release match (a
  live/acoustic/demo version with a different title). MusicBrainz only
  supplies extra metadata (cover art, year, genres). Leave a cell empty to
  fall back to the YouTube/MusicBrainz-derived naming for that row.

### Optional columns

Append them after `url`; omit them for rows or files that don't need them.

**Best practice: always fill in `language` and `musicbrainz_id`.** Without a
language, whisper guesses it from the audio (and can guess wrong, which
spoils the transcription and every check built on it); without a
MusicBrainz ID, a video outro cannot be cut off automatically (see "What
happens to a new song" below).

```csv
band,title,url,language,musicbrainz_id,lyrics_url,cover_url,duet
Lacrimosa,Lichtgestalt,https://www.youtube.com/watch?v=XYZ,de,,,,
```

* **`language`** - an ISO 639-1 code (`de`, `en`, ...). Pins whisper's
  language detection instead of letting it guess (anything else, like the
  dashboard's `mixed` for songs that switch language, means "detect it") - fixes mis-detections on
  short or ambiguous audio (a purely German song was once detected as English
  at 0.41 confidence). For a language whisper cannot transcribe, write its
  name (for example `Gaulish`): whisper then detects on its own, and the
  [lyrics review](#lyrics-review) lists the song as "not checkable" instead of
  flagging it. The same happens for `mixed`.
* **`musicbrainz_id`** - a MusicBrainz recording or release ID. Looked up
  directly (recording first, then release) instead of the fuzzy title/artist
  search, for more reliable cover art, year and genres. Does **not** affect
  naming - `band`/`title` still win. Its recording length is also used to
  cut off a video outro before the lyrics are aligned (see "What happens to a
  new song" below); a release or release-group ID works too - the track whose
  title matches the song is used. Pick the ID of the version the video
  actually shows (album, single or live) - a different version's length can
  put the cut in the wrong place or prevent it.
* **`lyrics_url`** - known-good lyrics, tried first and used directly if they
  yield anything. `genius.com` URLs are scraped; an lrclib.net track page
  (`https://lrclib.net/tracks/15562042`) is read from lrclib.net's API,
  synced lyrics (with line times) first, plain otherwise - a lrclib.net
  search link names no single song and is not used; any other `http(s)` link
  must point straight at plain text or an `.lrc` file (arbitrary lyrics-site
  HTML pages are not supported). Anything that is not a link is a lyrics file
  (plain text, one sung line per line, or LRC), relative to `stack/input/`
  unless the path is absolute, e.g. `lyrics/Die Funkhausgruppe - Die Physiker.txt`
  for `stack/input/lyrics/Die Funkhausgruppe - Die Physiker.txt`. See
  [song-sources.md](song-sources.md).
* **`cover_url`** - a link to a cover image. After the song is created, the
  image replaces the cover (`<Artist - Title> [CO].jpg`/`.png`; WebP, GIF and
  BMP are converted to JPEG) and `#COVERURL` records the link. A link that
  does not work or is not an image is logged and ignored - it never fails a
  song.
* **`duet`** - `yes`, `no` or empty. Only `yes` asks for a duet version; empty
  means the same as `no`. A song may appear twice, once with `yes` and once
  without - these are two separate jobs (the duet one is labelled
  `Artist - Title (Duet)`). **Duet generation does not exist yet:** a duet
  row is listed as skipped with the reason "duet generation is not available
  yet" (progress view: `2 skipped (1 duet)`), and the plain row is created
  as usual.
* **`genre`** and **`trait`** - set by an admin on the dashboard. Each cell
  may hold several comma-joined values (CSV-quoted:
  `"metal,folk","harsh_vocals,loud_high_instruments"`); their order means
  nothing, and they belong to the song, not the band. Genres: `metal`,
  `medieval`, `folk`, `gothic`, `symphonic`, `punk`, `pop`, `rap`, `kpop`,
  `jpop`, `rock`, `industrial`, `ebm`. Traits: `harsh_vocals`,
  `operatic_vocals`, `loud_high_instruments`, `choir`. Empty, `default` and
  `none` mean no special handling. A value the factory does not know is
  ignored and listed in the finishing report under "Unknown genre or trait
  values". What the traits switch: `loud_high_instruments` lets word ends
  follow a held note down to 9 dB below the word instead of 6 dB
  (`LOUD_INSTRUMENTS_WORD_END_DROP_DB`). `harsh_vocals` keeps growled and
  screamed notes from jumping around: a note the pitch detector is unsure
  about moves halfway towards the melody around it, at most 3 semitones
  from it (`HARSH_MAX_DISTANCE`). The other traits and the genres change
  nothing yet - no handling was measured to beat the default.

### `songs.txt` (plain list)

Instead of the CSV, `input/songs.txt` with one song per line:

```
https://www.youtube.com/watch?v=U6bmr2zcCos
ASP - Zaubererbruder - https://www.youtube.com/watch?v=sifM_9DIVbI
```

Point the stack to another file with the environment variable `SONGS_FILE`
(see [configuration.md](configuration.md)).

## What happens to a new song

1. **USDB first.** If a confident match exists on USDB, its community-made
   notes are used, the fitting video and audio are downloaded, and `#GAP` is
   re-detected for that audio. See [song-sources.md](song-sources.md#usdb).
2. **Otherwise generated from scratch:** vocal separation and
   transcription, then real lyrics from the internet force-aligned to the
   vocals when a source is found (see
   [song-sources.md](song-sources.md#online-lyrics)). In that case the notes
   are rebuilt from those lyrics: one note per syllable, plus `~` notes where
   a held syllable moves to another pitch. Pitches are re-detected slightly
   after each note (the singing lags notes placed like hand-made ones) and
   normalised (octave errors folded back). Lyrics at the end that are not
   found in the audio at all (a video that fades out before the album
   lyrics end) are left out, and the job log says how many words; without
   this they would be squeezed into the last seconds and pull the sung
   lines with them. Without an online source the song keeps the
   transcription; there UltraSinger itself cuts a long note into `~` notes of
   a fixed length and at pauses, each with its own pitch.
   **Video outros:** a music video often runs past the song (a closing scene,
   talking, credits music), and the aligner would pull the last lyric lines
   into it. When the row has a `musicbrainz_id` and the video is at least 8 s
   longer than that recording, the lyrics are aligned only up to a clear
   pause (at least 1 s, both vocals and music quiet) within 10 s before to
   15 s after the point where the recording should end. Without such a pause
   nothing is cut. The log says `song ends at ...s (outro detection)`. For
   songs without an ID, or when the detection misses, put a
   [`song_end.txt`](repairs.md#song-end-song_endtxt) into the song folder and
   repair it. UltraSinger's own MusicBrainz search is not used for this: it
   picked the exact recording for only 15 of 32 list songs with a known ID
   (often a live or music-video recording), which would cut in wrong places.
3. Lyrics in Cyrillic, Korean or Japanese script are romanised.
   **Lyrics review:** when online lyrics replaced the transcription, they are
   compared with what whisper heard in the vocals (UltraSinger's own
   transcription, kept from before the lyrics step). See
   [Lyrics review](#lyrics-review) below.
4. The result lands in `output/new/<Artist - Title>/`.

## Lyrics review

The worst lyrics errors come from a lyrics source that does not match the
video: the album version when the video is a shorter edit, an extra verse, a
different arrangement. The aligner then has to place lines that are never
sung. After a new song gets online lyrics, its `lyrics.txt` is compared with
UltraSinger's transcription of the vocals, word by word in order (a repeated
chorus cannot pair with the wrong repeat; slightly misheard words still
count). A line counts as heard when at least half of its words were found.

The song is listed under **Lyrics to review** in `output/report.md` when

* less than 60% of the lyric words were heard (`LYRICS_REVIEW_MIN_HEARD`), or
* 5 or more lyric lines in a row were not heard
  (`LYRICS_REVIEW_MAX_UNHEARD_RUN`).

The note names the heard and unheard lines and when the singing ends, so you
can check those lines and fix the song: trim `lyrics.txt`, or set the end
with [`song_end.txt`](repairs.md#song-end-song_endtxt) or
[`anchors.txt`](repairs.md#anchors-anchorstxt), then repair it.

It is a hint where to look, not an automatic fix. Measured on 30 songs rated
by ear: it flags 4 of 6 songs with a wrong lyrics source and 6 of 24 correct
songs - mostly singing whisper cannot transcribe well (Gaulish, operatic
vocals). Songs from USDB and songs without online lyrics are not reviewed.
A `language` whisper cannot transcribe (see above) makes the song "not
checkable" instead.

## Re-running: append to or overwrite `song-requests.csv`?

Both work. Jobs are de-duplicated by `url` (new songs) and folder name
(repairs), not by position in the file. The file is read fresh every run and
already-done rows are skipped, whether they are still in the file or not -
there is no need to strip processed rows before writing a new export (for
example from `karaoke-dashboard`).

**What does NOT happen automatically:** editing the `band`/`title`/`language`
cell of an already-done row updates that job's stored metadata on the next
run (useful for fixing a typo), but does **not** regenerate the song. To redo
a song, reset it (see below), for example
`reset --done --match "Rauta"`.

## Choosing which jobs run

`run`, `list`, `report` and `reset` accept the same selection flags (full
reference: [orchestrator-manual.md](orchestrator-manual.md)):

| Flag | Meaning |
|---|---|
| `--only generated` | new songs USDB does **not** have - created from scratch |
| `--only usdb` | new songs USDB has - pulled from USDB (notes, video/audio, corrected `#GAP`) |
| `--only repairs` | songs repaired from `input/` |
| `--only a,b` | any comma-separated combination |
| `--only-new` / `--only-repairs` | older aliases: every new song / repairs only |
| `--match TEXT` | only jobs whose label contains TEXT (case-insensitive) |
| `--no-usdb` | developer override for `run`: never pull from USDB, generate every new song from scratch (to compare alignment quality on a song USDB also has). Contradicts `--only usdb` |

The normal policy stays: without `--no-usdb`, a new song that USDB has is
always pulled from USDB. `--only generated` / `--only usdb` first check each
pending new song against USDB and leave the ones that don't fit pending.

```bash
# only songs to be created from scratch
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py run --only generated
# redo every finished generated song, then run just those
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset --done --only generated
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py run --only generated
# redo one song
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset --done --match "Rauta"
```

`reset` classifies finished songs from `state.json` (a lyrics source starting
with `usdb:` counts as `usdb`); it only flips jobs back to `pending` and never
deletes output folders. Without `--done` it retries only failed jobs of the
selection.

### Leftover jobs

`state/state.json` remembers every job ever planned, so a song you later
remove from `song-requests.csv` (or a folder removed from `input/`) stays
behind as an "orphan" that no run ever queues. The progress view counts only
the jobs the song list and input folder define right now and shows orphans on
a separate "Not counted" line; `reset --prune` deletes them from the state
(backup in `state/backups/`, output folders untouched).
