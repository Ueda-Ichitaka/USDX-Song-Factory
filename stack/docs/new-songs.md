# New songs: the song list

The stack creates new songs from a want-list: `stack/input/song-requests.csv`
(or a plain `input/songs.txt`). Every row becomes one job; finished jobs are
remembered in `state/state.json` and never redone unless you reset them.

All commands on this page are run from inside `stack/`.

## `song-requests.csv`

Comma or semicolon separated, header line required:

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

Append them after `url`; omit them for rows or files that don't need them:

```csv
band,title,url,language,musicbrainz_id,lyrics_url
Lacrimosa,Lichtgestalt,https://www.youtube.com/watch?v=XYZ,de,,
```

* **`language`** - an ISO 639-1 code (`de`, `en`, ...). Pins whisper's
  language detection instead of letting it guess - fixes mis-detections on
  short or ambiguous audio (a purely German song was once detected as English
  at 0.41 confidence).
* **`musicbrainz_id`** - a MusicBrainz recording or release ID. Looked up
  directly (recording first, then release) instead of the fuzzy title/artist
  search, for more reliable cover art, year and genres. Does **not** affect
  naming - `band`/`title` still win.
* **`lyrics_url`** - a link to known-good lyrics, tried first and used
  directly if it yields anything. `genius.com` URLs are scraped; anything else
  must point straight at plain text or an `.lrc` file (arbitrary lyrics-site
  HTML pages are not supported). See [song-sources.md](song-sources.md).

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
   are rebuilt from those lyrics and their pitches are re-detected and
   normalised (octave errors folded back). Without an online source the song
   keeps the transcription.
3. Lyrics in Cyrillic, Korean or Japanese script are romanised.
4. The result lands in `output/new/<Artist - Title>/`.

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
