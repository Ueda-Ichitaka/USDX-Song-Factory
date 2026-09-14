# UltraStar Song Factory - Docker Stack

> **TL;DR: yes, `cd stack` first.** Everything in this guide (`docker
> compose ...`) is run from inside the `stack/` directory, never from the
> repo root - `docker-compose.yml` lives at `stack/docker-compose.yml`.
> ```bash
> cd stack        # once, per terminal session
> docker compose up
> ```
> `docker compose up` again later resumes where it left off - it skips
> everything already done and only processes what's new/pending/failed.

A docker-compose stack that automates [UltraSinger](https://github.com/rakuri255/UltraSinger)
for your UltraStar Deluxe karaoke system:

* **Create new songs in bulk** - put song names / YouTube links (or local
  audio/video file paths) into `input/song-requests.csv` (or `input/songs.txt`) and
  let the stack create complete songs (UltraStar txt, audio, video,
  instrumental, vocals, cover, midi).
* **Lyrics from the internet, not just guessed from audio** - every new song
  tries a real lyrics lookup first (free, no key needed) and only falls back
  to whisper's own (occasionally mis-heard) transcription if nothing is
  found - the finishing report marks which happened per song.
* **Repair existing songs in bulk** - put broken song folders into `input/`.
  The repair tool fixes wrong `#GAP` (initial offset between audio start and
  first lyrics) and lyrics drifting out of sync mid-song, using the original
  lyrics (force-alignment, no re-transcription) - or, when you know the
  existing lyrics are wrong, force-aligns a trusted lyrics file you supply
  instead (see "lyrics" mode below).
* **Romanized lyrics, automatically** - Cyrillic (Russian/Ukrainian), Korean
  and Japanese lyrics are transliterated to Latin script after every song.
* **Spoken video end-cards filtered out** - a short, silence-isolated speech
  blurb near the end of a video ("thanks for watching", ...) is dropped
  before it can pollute the lyrics or get mistranscribed.
* **Progress UI** and a **tabular finishing report** - see how many songs
  were created/repaired, how many are left, ETA, failures; `output/report.md`
  lists exactly which songs were created, repaired, or failed.
* **Failed jobs never pollute your library** - a failed song's partial
  output is quarantined to `output/failed/`, never left in `output/new/`.
* Runs on CPU everywhere; optional **AMD GPU (ROCm)** build for faster
  track separation.

The heavy lifting (PyTorch/demucs, whisper, SwiftF0, ffmpeg, yt-dlp) is fully
containerized - the segfaults/venv troubles you get from running UltraSinger
natively on Arch do not apply here.

---

## Quick start

```bash
cd stack

# 1. put your songs into input/song-requests.csv (already contains the current batch)
# 2. put broken songs into input/ too (one folder per song)

# 3. build + run everything
docker compose up          # foreground, shows all output
# or
docker compose up -d       # detached, watch with: docker compose logs -f
```

**For a real batch (more than a couple of songs), prefer `-d`.** A batch runs
for hours; a foreground `docker compose up`/`run` ties the container to
whatever shell/session launched it - close that session (or have it killed
by anything managing it, e.g. an SSH disconnect or an automation tool's own
process governor) and the container gets SIGTERM'd, stopping the batch
partway through even though nothing was actually wrong with the job itself.
`up -d` (or `run -d`) detaches the container at the Docker daemon level, so
it keeps running independently of the terminal/session that started it -
check on it any time with `docker compose logs -f`, `docker compose exec
ultrasinger progress -w`, or `docker compose attach ultrasinger`.

The container processes all pending jobs (new songs + repairs) and exits when
done. Progress is persisted in `state/state.json`, so you can stop/resume at
any time - jobs that already succeeded are not repeated.

First run only: whisper/demucs/aligner models (~4 GB) are downloaded into
`models/` - this is cached for all later runs.

## Folder layout

```
stack/
├── input/
│   ├── song-requests.csv       # your "want list" for new songs (band,title,url)
│   ├── broken.csv       # optional: broken-song reports (band,song name,category,description)
│   └── <song folder>/  # broken songs to repair (one folder per song)
│       └── lyrics.txt  #   optional: trusted lyrics -> triggers repair "lyrics" mode
├── .env                # optional: GENIUS_API_KEY etc. (copy from .env.example)
├── output/            # results
│   ├── new/<Artist - Title>/  created new songs
│   ├── repaired/<song folder> repaired songs (full copy + fixed txt)
│   ├── failed/<new|repair>/   quarantined partial output of failed jobs
│   └── report.md              tabular finishing report (created/repaired/failed)
├── state/             # progress state (resume support)
├── logs/              # per-job log files
├── models/            # AI model cache (whisper, demucs, aligners)
├── work/              # repair working cache (safe to delete anytime)
├── cookies/           # optional cookies.txt for YouTube (see below)
├── usdb_syncer/       # git submodule - see "USDB integration" below
└── docker-compose.yml
```

## The song list (new songs)

`input/song-requests.csv` (comma or semicolon separated, header line required):

```csv
band,title,url
ASP,Denn Ich bin der Meister,https://www.youtube.com/watch?v=U6bmr2zcCos
ASP,Zaubererbruder,https://www.youtube.com/watch?v=sifM_9DIVbI
Local Band,A Song I Already Have,media/local-band-a-song.mp3
```

* The `url` cell can be a **YouTube link** (must start with `https://` -
  that is the only thing that marks it as a link, nothing is guessed) or a
  **local audio/video file path** (`.mp3`, `.mp4`, ...), resolved relative
  to `input/` when not absolute - just drop the file under `input/` (e.g.
  `input/media/local-band-a-song.mp3`) and reference it by that relative
  path. UltraSinger handles a local file exactly like a download: if it has
  no video track, the song simply has no `#VIDEO` (UltraStar Deluxe falls
  back to its built-in visualization) - nothing special to configure.
* A row with `skip` (or `-` or empty) as url is skipped - e.g. songs you
  already own: `Aequitas,He's a Pirate,skip`
* Songs already marked `done` in `state/` are not re-created.
* **Band/title in the CSV name the output folder and `#ARTIST`/`#TITLE`
  tags.** They are passed through as `--force_artist`/`--force_title` and
  always win over the YouTube channel name and over a MusicBrainz
  alternate-release match (e.g. a live/acoustic/demo version with a
  different title) - MusicBrainz is only used for supplementary metadata
  (cover art, year, genres). Leave a cell empty to fall back to the old
  YouTube/MusicBrainz-derived naming for that row.

Three more columns are optional (append them after `url` - omit entirely for
rows/files that don't need them):

```csv
band,title,url,language,musicbrainz_id,lyrics_url
Lacrimosa,Lichtgestalt,https://www.youtube.com/watch?v=XYZ,de,,
```

* **`language`** - an ISO 639-1 code (`de`, `en`, ...). Passed as
  `--language`, pinning whisper's language detection instead of letting it
  guess - fixes mis-detections on short/ambiguous audio (a purely German
  song was once auto-detected as English at 0.41 confidence).
* **`musicbrainz_id`** - a MusicBrainz recording or release ID. Tried first
  via a direct lookup (recording, falling back to release) instead of the
  fuzzy title/artist search - more reliable supplementary metadata (cover
  art, year, genres). Does **not** affect naming - `band`/`title` above
  still always win.
* **`lyrics_url`** - a link to known-good lyrics for the song, tried first
  (and used directly if found) before the normal online lyrics lookup.
  `genius.com` URLs are scraped directly; anything else must point straight
  at plain text or an `.lrc` file - arbitrary lyrics-site HTML pages are not
  supported (falls back to the normal lyrics search if the URL doesn't
  yield anything usable).

Alternatively `input/songs.txt` (plain lines):

```
https://www.youtube.com/watch?v=U6bmr2zcCos
ASP - Zaubererbruder - https://www.youtube.com/watch?v=sifM_9DIVbI
```

Point the stack to another file with the env var `SONGS_FILE`
(override in `docker-compose.yml` or an `.env` file).

### Re-running: append or overwrite `song-requests.csv`?

For debugging/testing, editing `input/song-requests.csv` by hand (in any way) is
fine - just re-run the stack.

For production (e.g. `song-requests.csv` regenerated by another tool as a table
export - see `karaoke-dashboard`'s `UPSTREAM_REQUESTS.md`), **either
appending new rows or overwriting the whole file with the current full
list works** - jobs are de-duplicated by `url` (new songs) / folder name
(repairs), not by file position, so the file is read fresh every run and
already-`done` rows are simply skipped again, whether they're still
present in the file or not. There's no need to strip already-processed
rows before writing a new export.

**The one thing that does NOT happen automatically:** editing the
`band`/`title`/`language` cell of an **already-`done`** row updates that
job's stored metadata on the next run (useful for fixing a typo after the
fact) but does **not** trigger reprocessing - the song is not
regenerated with the corrected name. If a row needs to be redone (not
just relabeled), reset that job explicitly:

```bash
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset --all
```

(there's currently no per-job reset - `reset --all` re-does every job,
including repairs; see `state/state.json` if you need to hand-edit a
single job's `"status"` back to `"pending"` instead).

## Repairing existing songs

1. Copy a song folder (with its `*.txt`, audio, video, cover) into `input/`:

   ```
   stack/input/ASP - Ich will brennen/ASP - Ich will brennen.txt
   stack/input/ASP - Ich will brennen/ASP - Ich will brennen.mp3
   ...
   ```

2. Start the stack (`docker compose up`). Every folder in `input/` containing
   an UltraStar txt is repaired into `output/repaired/<song folder>/`.

3. Your originals in `input/` are never modified.

Three repair modes (env `REPAIR_MODE`, default `sync`; `lyrics` is chosen
automatically, see below):

| mode | what it does | cost |
|------|--------------|------|
| `gap`  | Aligns only the first lyric lines to the audio, shifts `#GAP` accordingly. Beats/durations/pitches stay as-is. | fast (no separation) |
| `sync` | Full repair: separates vocals, force-aligns **every** lyric line (fixes `#GAP` **and** mid-song drift), rebuilds beats/durations and re-detects every note's pitch (SwiftF0). Original lyrics are kept. | ~2-6 min/song (CPU) |
| `lyrics` | Like `sync`, but REPLACES the lyrics text too, force-aligning a trusted lyrics file you supply instead of the (assumed wrong) existing text. Use when the existing lyrics themselves are the problem, not just their timing. | ~2-6 min/song (CPU) |

### Repairing with `broken.csv` (don't guess what's broken)

Without `broken.csv`, every folder in `input/` gets the same blind
`REPAIR_MODE` (`sync` by default) - correct, but wasteful when you already
know a song's #GAP is just off by a few seconds. Drop `input/broken.csv`
next to your song folders (it's the export from the karaoke-dashboard
project's "report a broken song" admin view, `GET /admin/reports.csv`) and
each folder gets matched to its report by **#ARTIST/#TITLE** (not the
folder name) and repaired with only the fix its category actually calls
for:

```csv
band,song name,category,description
Queen,I Want To Break Free,gap,
ASP,Ich will brennen,async,
Metric,Black Sheep,lyrics,second verse is wrong
Boney M.,Daddy Cool,video,
```

| category | what happens |
|----------|--------------|
| `gap` | `gap` mode only - cheap, no track separation. |
| `async` | `sync` mode - full re-alignment, original lyrics kept. |
| `lyrics` | Force-aligns trusted lyrics, same as a manually-supplied `lyrics.txt` (see below) - a `lyrics_url` column (genius.com, or a direct `.txt`/`.lrc` link) is tried first if given, otherwise an automatic online lookup runs, same as new songs get. Nothing found -> the job fails outright (never silently falls back to a blind `sync` repair, which would just re-time the known-wrong lyrics). |
| `video` / `audio` | Restores the missing file: an unambiguous matching file already sitting in the folder is linked as-is; otherwise, if the folder has a `*.usdb` sync-meta file (written by `usdb_syncer` for anything originally pulled from USDB), its original YouTube source is re-downloaded via yt-dlp. Either way, a cheap `gap` re-check runs afterward (a re-downloaded file may not be padded identically to the original). No source found at all, or two+ ambiguous candidates in the folder -> the job fails with a message naming what it found, rather than guessing. |
| `other`, blank, or unrecognized | **Not run automatically.** Legacy reports predate the category field and are blank; free-text-only ("other") reports can't be turned into an action without guessing. These are marked `needs_review` in the progress/report output instead - fix the category (or the folder) and run `reset` (see below) to pick it up on the next run. |

A folder with **no matching row** in `broken.csv` at all keeps today's
default behavior unchanged (`REPAIR_MODE`, or `lyrics` mode if you dropped
a `lyrics.txt` in yourself - see below) - `broken.csv` is optional
enrichment, never a requirement.

Point the stack to another file with the env var `BROKEN_CSV`
(default `/data/input/broken.csv`); tune the match strictness with
`BROKEN_MATCH_MIN_SCORE` (default `0.85`).

**`needs_review` jobs are not retried automatically** - same reasoning as
the `song-requests.csv` note below: a `docker compose exec ultrasinger ... reset`
un-flags them back to `pending` once you've fixed the report or the
folder.

### Repairing with trusted lyrics ("lyrics" mode)

`sync` mode deliberately never touches the lyrics themselves - re-timing
wrong words just moves the problem, it doesn't fix it. If a song's lyrics
are actually wrong (mis-heard words, missing lines), drop a plain text file
named `lyrics.txt` next to its txt/audio in `input/`:

```
stack/input/ASP - Ich will brennen/ASP - Ich will brennen.txt
stack/input/ASP - Ich will brennen/ASP - Ich will brennen.mp3
stack/input/ASP - Ich will brennen/lyrics.txt      <- one sung line per text line
```

Its presence automatically switches that song's repair to `lyrics` mode -
each line of `lyrics.txt` becomes one line of the rebuilt song (this is
also how you fix "lines were way too long": break them up in `lyrics.txt`
the way they should actually be sung). The pitches are re-detected the same
way `sync` mode does; only the note/syllable structure is rebuilt to match
the new lyrics. This is the same force-alignment engine the "online
lyrics" step (below) uses for new songs - hyphenation + force-alignment +
pitch detection, not a re-transcription.

**Lyrics/audio mismatch fallback:** if the txt lyrics do not match the audio
at all (wrong song version, txt shorter/longer than the audio - detected via
alignment quality), `sync` mode automatically re-creates the song from its
audio with UltraSinger (new transcription). The log clearly marks this:
`Re-creating song from audio with UltraSinger`. Disable with
`REPAIR_FALLBACK=0` (the job then fails instead).

The repair keeps the original syllables, note types (normal/golden/rap/...),
line breaks, video/cover references and header tags. Both word-boundary
conventions found in txt files (trailing spaces and leading spaces) are
supported. A summary line is printed per song:

```
[UltraSinger] Summary ASP - Ich will brennen: GAP 20834 -> 21601 ms
(delta +767 ms), aligned words 207/210, re-pitched notes 183
```

If the song language is missing in the txt (`#LANGUAGE`), it is auto-detected
with a tiny whisper model. You can force it per run by setting
`REPAIR_LANGUAGE` in `docker-compose.yml` if a detection goes wrong.

Advanced tuning env vars (rarely needed):

| variable | default | meaning |
|----------|---------|---------|
| `REPAIR_MODE` | `sync` | `gap` or `sync` |
| `REPAIR_FALLBACK` | `1` | re-create songs whose lyrics don't match the audio |
| `REPAIR_ALIGN_PAD` | `6.0` | search window padding in seconds |
| `REPAIR_MIN_WORD_SCORE` | `0.6` | word alignment score below which a line counts as suspicious |
| `REPAIR_MAX_LOCAL_DEVIATION` | `3.0` | local offset deviation (s) counted as suspicious |

## Lyrics sources (new songs)

Whisper transcribes what it *hears*, and singing is genuinely hard to
transcribe - mis-heard words are the single biggest lyrics-quality problem.
So for every new song, after UltraSinger creates it, the stack tries a real
lyrics lookup before trusting whisper's own transcription:

1. **[syncedlyrics](https://pypi.org/project/syncedlyrics/)** - free, no API
   key, aggregates LRCLIB/NetEase/Musixmatch/Megalobiz. Tried first, and
   prefers a *synced* (line-timed) result when available, which also gives
   the force-aligner a much better starting point than a blind guess.
2. **[Genius](https://genius.com/api-clients)** - only tried if you set
   `GENIUS_API_KEY` (copy `.env.example` to `.env` next to
   `docker-compose.yml` and fill it in). Optional; skipped entirely without
   a key.

When a source is found, its text is force-aligned to the vocals (the exact
same engine as repair.py's `lyrics` mode, see above) and REPLACES whisper's
transcription. When nothing is found anywhere, the song keeps its
whisper-transcribed lyrics - `output/report.md` marks these with ⚠ so you
know which songs are more likely to have mis-heard words. Disable entirely
with `LYRICS_ENABLED=0`.

## USDB integration

Before generating a new song from scratch, the stack first checks whether a
matching upload already exists on USDB instead - there are actually **two**
independent, unrelated UltraStar song databases, and both are checked:

1. **[usdb.animux.de](https://usdb.animux.de/)** - the original, much larger
   community database. Accessed via
   [usdb_syncer](https://github.com/bohning/usdb_syncer) (git submodule at
   `stack/usdb_syncer` - run `git submodule update --init --recursive` after
   cloning, and again after pulling an update to it). Tried first.
2. **[usdb.eu](https://usdb.eu/)** - a separate, newer database with its own
   account system (not supported by usdb_syncer - a small standalone
   scraper, `stack/orchestrator/usdb_eu_lookup.py`, talks to it directly).
   Tried only when animux.de has no match for a song.

A community-created, already-verified upload is almost always higher
quality than a fresh whisper transcription, so it's preferred whenever a
confident match exists on either site.

**Setup:** each site needs its own free account:
- animux.de: set `USDB_USERNAME`/`USDB_PASSWORD` in `.env` (copy from
  `.env.example`).
- usdb.eu: set `USDB_EU_EMAIL`/`USDB_EU_PASSWORD`.

Either site can be configured independently - leave a site's credentials
unset to skip just that source (with both unset, every new song is
generated as before, exactly like today).

**How it works**, per new song:

1. Search animux.de's (locally cached, refreshed every
   `USDB_CATALOG_TTL_HOURS` hours) catalog for an artist+title match; if
   none, search usdb.eu directly (no local cache needed there - its search
   is a live, no-login-required lookup). Either way, a match needs
   `USDB_MIN_MATCH_SCORE` (default 0.90) similarity on *both* artist and
   title to be accepted - a great title match with a wrong artist is still
   the wrong song, so a weak match is never used, and the song just falls
   back to normal generation.
2. Download that song's notes (`.txt`), and cover art when the match came
   from animux.de.
3. Neither site hosts audio/video itself (copyright) - only notes/cover -
   so it's always fetched separately via `yt-dlp`: an animux.de match's own
   comment-linked YouTube video first (if the uploader included one and it
   still resolves), the song's own `song-requests.csv` YouTube link otherwise (a
   usdb.eu match always uses the song-requests.csv link, for now - see
   `knowledge/06-IDEAS.md`).
4. The matched upload's `#GAP` value was tuned for a *different* upload's
   audio (maybe a different rip/trim of the same source), so it's
   re-detected against whatever we actually downloaded - reusing
   `repair.py`'s existing `gap` mode (see "Repairing existing songs"
   above), which only touches `#GAP` and leaves the community-verified
   lyrics/note timing untouched. This also means a usdb-sourced song
   **skips** the online-lyrics step above (see "Lyrics sources") - the
   matched site's own text is already trusted. For an animux.de match,
   any `#GAP` corrections mentioned in the upload's comments are logged
   (informational only, into the txt's `#COMMENT` tag) - they never
   influence the actual re-detection above, which is always trusted over
   a number someone typed in a comment.
5. `output/report.md`'s Lyrics column shows `usdb:animux:<song id>` or
   `usdb:eu:<song id>` for songs sourced this way, so you can tell them
   apart from generated (`transcribed`/`online:<source>`) ones, and from
   each other, at a glance.

Any failure along this path (no confident match on either site, no usable
video source, download failure, a site unreachable) is silent and
non-fatal - the song just falls back to full UltraSinger generation,
exactly as if neither site were configured at all. The two sites are also
independent of each other: a misconfigured/unreachable account for one
never blocks the other.

For animux.de, only usdb_syncer's plain scraping module is reused (login,
search, song details/notes) - not its own downloader, which needs a full
desktop GUI event loop and isn't meant to be driven headlessly. usdb.eu has
no Python client at all, so `usdb_eu_lookup.py` talks to its (undocumented)
JSON search/login/download endpoints directly - reverse-engineered from
the site's own JavaScript and confirmed against the live site end-to-end
(downloading real songs' notes and cover art). See both modules' header
comments and `knowledge/02-DESIGN.md` for the full reasoning.

## Romanized lyrics

Whatever script whisper transcribed - Cyrillic (Russian/Ukrainian), Korean
(Hangul) or Japanese (kana/kanji) - the final txt gets its lyrics
transliterated to Latin script automatically, right after a song is created
or repaired. This is a straight script transliteration (via `cyrtranslit`,
`korean-romanizer`, `pykakasi`), not a lyrics translation - it turns e.g.
"Привет" into "Privet", not into an English word. Song-internal
already-Latin words (loanwords, an English chorus, ...) are left untouched;
only text that is actually Cyrillic/Hangul/Japanese gets converted, word by
word. Disable stack-wide with `ROMANIZE=0`. To romanize a txt manually:

```bash
docker compose run --rm ultrasinger python /app/orchestrator/romanize.py --txt "/data/output/new/Some Song/Some Song.txt"
```

## Finishing report

Every run writes `output/report.md`: a tabular summary of every new song
created (with where its lyrics came from - see "Lyrics sources" above),
every song repaired (with its repair mode), and every job that failed
(with its error and where its quarantined output ended up). Also printed
to the console at the end of a run. Regenerate/view it any time without
running anything:

```bash
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py report
```

## Failed jobs

A job that fails never leaves broken/partial output mixed in with your real
songs: whatever folder it managed to create before failing is moved to
`output/failed/new/` or `output/failed/repaired/` (colliding names get a
` (1)` suffix, nothing is ever overwritten). The failed job itself still
shows up in `progress`/`list`/`report` with its error and the quarantined
path, and `reset` still retries it on the next run - only the *output*
directory is kept clean.

## Daily usage / commands

Everything runs through the orchestrator inside the container. Note that
`docker compose run` needs the full command (arguments after the service
name replace the default command):

```bash
docker compose up                          # run all pending jobs (builds if needed)
docker compose up -d                       # same, detached
docker compose logs -f                     # follow the full verbose output
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py list
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py report
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py progress
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py progress -w   # live view

# single jobs, bypassing the queue:
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py run-one "https://www.youtube.com/watch?v=XYZ"
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py repair-one "/data/input/Some Song"

# state management:
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset          # retry failed jobs
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset --all    # re-do everything

# shell inside the container (for debugging):
docker compose run --rm ultrasinger bash
```

(For the GPU service, use `ultrasinger-rocm` instead of `ultrasinger` -
always with `--profile rocm`.)

While the container is running (service `ultrasinger`):

```bash
docker compose exec ultrasinger progress    # progress table
docker compose exec ultrasinger bash        # second shell inside the container
```

### Verbose output

The orchestrator streams the complete UltraSinger output of every song:

* live: `docker compose logs -f` (or run `docker compose up` in foreground)
* per song: `logs/<song>.log` on the host

### Attaching to the running session (interactive commands)

The service runs with a TTY and open stdin. While a batch is running:

```bash
docker compose attach ultrasinger
```

You are now "inside" the current session and can type commands:

| command | effect |
|---------|--------|
| `s` / `status` | print the progress overview |
| `skip` | abort the current song, mark it failed, continue with the next |
| `stop` / `q` | finish the current song, then exit gracefully (resume later with `docker compose up`) |
| `h` | help |

Detach with `Ctrl-P Ctrl-Q` (do **not** use Ctrl-C, that stops the container).

### Progress UI

`progress -w` gives a self-updating table (clears and redraws every 10s,
same idea as `pacman -Syu`/`flatpak update`'s live progress) - run it in a
second terminal/tab while `docker compose up` itself keeps streaming each
job's verbose output in the first one. Plain `progress` (no `-w`) prints
it once and exits.

```
==========================================================================
  UltraStar Song Factory - Batch Progress        2026-09-10 15:42
==========================================================================
  NEW SONGS:  12 done |  1 failed |  1 running |  47 pending | 2 skipped  (62 total)
  REPAIRS  :   5 done |  0 failed |  0 running | 14 pending   (19 total)
  Pulled from USDB: 4
  Device: GPU (cuda/ROCm)   CPU ~34% | RAM 12.1/30.9 GB (39%) | GPU VRAM 6.2/15.9 GB (39%)
  Elapsed 2h18m | avg 9m41s/song | ETA ~9h
  Current: ASP - Duett (Minnelied der Incubi) - running for 3m (attempt 1)
--------------------------------------------------------------------------
  Failed jobs:
   - Xandria - Nightfall  [new]  exit code 1
==========================================================================
```

- **Pulled from USDB** - how many of the *already-finished* new songs so
  far were sourced from an existing USDB upload (animux.de or usdb.eu -
  see "USDB integration" above) instead of generated from scratch. This
  is a tally of what already happened, not a prediction of how many of
  the still-pending songs will be - that would need an expensive,
  rate-limited search per pending song against usdb.eu.
- **Device / CPU / RAM / GPU VRAM** - live resource usage, read directly
  from `/proc` and (for the ROCm service) the GPU driver's own sysfs
  files - no extra tooling (`rocm-smi`, `nvidia-smi`, `psutil`) needed.
  CPU% is the 1-minute load average normalized by core count (the same
  number `uptime`/`top` show), not a precise instantaneous reading. The
  GPU row is omitted entirely on the CPU service (`DEVICE=cpu`).

## Resource budget & model auto-selection

Transcription/re-pitching/lyrics quality (mis-heard words, garbled repeated
choruses, etc.) is partly a function of which whisper/demucs models get
used - bigger models generally do better, but need more RAM/VRAM and are
slower. Rather than a single fixed `WHISPER_MODEL=large-v2` for every host,
the stack picks whisper's model + batch size and demucs' separation model
from a declared resource budget:

| variable | default | meaning |
|----------|---------|---------|
| `STACK_RAM_GB` | `8` | RAM budget available to the stack |
| `STACK_SWAP_GB` | `250` | available swap (zram etc.) - only a small, capped cushion for transient spikes, never counted as real capacity |
| `STACK_CPU_CORES` | `16` | CPU threads available to the stack |
| `STACK_VRAM_GB` | `16` (ROCm service only) | GPU VRAM available - only demucs separation actually uses the GPU here, whisper always runs on CPU (CTranslate2 has no ROCm support) |
| `STACK_RAM_SAFETY_MARGIN_GB` | `1.5` | subtracted from `STACK_RAM_GB` before it's used - see below |
| `STACK_VRAM_SAFETY_MARGIN_GB` | `2` | subtracted from `STACK_VRAM_GB` before it's used |

The defaults match this stack's actual host (see `05-LESSONS.md` if you're
reading this from `knowledge/`); **change them in `.env` if you move the
stack to different hardware** - the whole point is that the picked models
track the real budget, not a number that happened to be true once.

**A declared budget is rarely all available to the stack alone** - most
concretely, a GPU's VRAM is usually shared with the desktop itself
(multiple monitors, browser, compositor, ...), not dedicated to this
stack. So `STACK_RAM_SAFETY_MARGIN_GB`/`STACK_VRAM_SAFETY_MARGIN_GB` are
subtracted before anything is selected - state your hardware's real
totals in `STACK_RAM_GB`/`STACK_VRAM_GB` and let the margin account for
what else is using it, rather than trying to pre-calculate "16 GB minus
whatever my desktop needs" yourself. Raise the margins if you still see
memory pressure; the defaults are a starting point, not a guarantee.

Whisper's model is sized from RAM+swap+CPU cores (it never touches the
GPU in this stack); demucs' model is sized from VRAM on the ROCm service
(GPU makes the 4x-slower `htdemucs_ft` essentially free, so it's picked
whenever VRAM allows) or from RAM+CPU cores on the CPU service. Every
choice - already margin-adjusted - is printed at the start of each run so
it's never a black box:

```
Resource-based auto-selection (RAM=6.5GB swap=250.0GB cpu=16 vram=14.0GB device=cuda):
  whisper model: large-v2 - 7.7 GB effective RAM
  whisper batch size: 16 - 16 CPU core(s)
  demucs model: htdemucs_ft - 14.0 GB VRAM (GPU, best-quality model affordable)
```

Setting `WHISPER_MODEL`/`DEMUCS_MODEL`/`WHISPER_BATCH_SIZE` directly always
overrides the budget-based choice for that one setting. The thresholds are
a documented heuristic (`stack/orchestrator/resource_profile.py`) built
from published faster-whisper/demucs resource figures plus this project's
own measured peak (~4.3 GB RAM for whisper large-v2 int8 + demucs
together) - not a benchmarked guarantee for your exact hardware.

## Configuration (environment variables)

Set in `docker-compose.yml` (or an `.env` file next to it):

| variable | default | meaning |
|----------|---------|---------|
| `WHISPER_MODEL` | auto-selected | force a specific whisper model (`tiny`,`base`,`small`,`medium`,`large-v2`,...) instead of the resource-based choice (see "Resource budget" below) |
| `DEMUCS_MODEL` | auto-selected | force a specific demucs separation model (`htdemucs`,`htdemucs_ft`,`mdx_extra_q`,...) |
| `WHISPER_BATCH_SIZE` | auto-selected | force a specific whisper batch size |
| `REPAIR_MODE` | `sync` | `gap` or `sync` (see above) |
| `ULTRASINGER_ARGS` | - | extra args for every UltraSinger run, e.g. `"--disable_hyphenation --format_version 1.1.0"` |
| `MAX_ATTEMPTS` | `2` | attempts per song before it stays failed |
| `JOB_TIMEOUT_MIN` | `240` | hard timeout per song in minutes |
| `SONGS_FILE` | `/data/input/song-requests.csv` | the want-list file inside the container |
| `NEW_SONGS_DIR` | `/data/output/new` | where newly created songs are written |
| `FAILED_DIR` | `/data/output/failed` | where a failed job's partial output is quarantined |
| `ROMANIZE` | `1` | set to `0` to disable automatic lyrics romanization |
| `LYRICS_ENABLED` | `1` | set to `0` to disable the online-lyrics-for-new-songs step |
| `GENIUS_API_KEY` | - | enables the Genius lyrics source (see "Lyrics sources" above); unset = skipped |
| `USDB_USERNAME` / `USDB_PASSWORD` | - | enables the usdb.animux.de source (see "USDB integration" above); either unset = skipped |
| `USDB_MIN_MATCH_SCORE` | `0.90` | minimum artist+title similarity (0-1) to accept a USDB match (either site) |
| `USDB_CATALOG_TTL_HOURS` | `24` | how long the local animux.de catalog cache is reused before refreshing |
| `USDB_EU_EMAIL` / `USDB_EU_PASSWORD` | - | enables the usdb.eu source (see "USDB integration" above); either unset = skipped |
| `BROKEN_CSV` | `/data/input/broken.csv` | the broken-song-reports file inside the container (see "Repairing with broken.csv" above) |
| `BROKEN_MATCH_MIN_SCORE` | `0.85` | minimum #ARTIST/#TITLE similarity (0-1) to attach a broken.csv row to a repair folder |
| `ENDCARD_MIN_GAP_S` | `8.0` | silence gap (s) before a trailing blurb is considered a video end-card; `<=0` disables the filter |
| `ENDCARD_MAX_DUR_S` | `20.0` | a trailing blurb longer than this is kept (treated as real lyrics, not an end-card) |

## AMD GPU with ROCm (optional)

The default service (`ultrasinger`) runs on CPU. Whisper always runs on CPU
anyway (its engine CTranslate2 has no ROCm support); the GPU only accelerates
demucs separation and alignment. If that is worth it for you:

```bash
docker compose --profile rocm up ultrasinger-rocm   # builds the ROCm image
```

The ROCm service passes `/dev/kfd` and `/dev/dri` into the container. It was
tested with an AMD RX 9070 (gfx1201) on ROCm; the PyTorch ROCm 6.4 wheels
include RDNA4 kernels. Requirements: a working ROCm kernel driver on the host
(`ls /dev/kfd` shows the device).

To switch the whole batch to the GPU service permanently, just use
`ultrasinger-rocm` instead of `ultrasinger` in the commands above (only one of
them should run at a time - they share the same state).

## Memory

The host has ~8 GB RAM available for the stack plus 250 GB zram0 swap. The
stack processes **one song at a time** and needs ~4-5 GB peak (whisper
large-v2 int8 + demucs, measured). No hard docker memory limit is set on
purpose - the kernel swaps out rare spikes into zram automatically. If you
want to be strict, add to the service:

```yaml
    mem_limit: 8g        # RAM only
    memswap_limit: 16g   # RAM + swap (zram)
```

If memory gets tight, use a smaller whisper model
(`WHISPER_MODEL=medium`).

## YouTube: "Sign in to confirm you're not a bot" / age-restricted videos

yt-dlp occasionally hits YouTube bot protection, and age-restricted videos
always need this. Export your browser's YouTube cookies once, on the host
(not inside the container - the container has no browser):

```bash
# close the browser first - it locks its cookie DB while running, which
# makes yt-dlp fail with "database is locked"
yt-dlp --cookies-from-browser firefox --cookies stack/cookies/cookies.txt \
  --skip-download "https://www.youtube.com/watch?v=<any-video-id>"
```

- `--cookies-from-browser` also accepts `chrome`, `brave`, `edge`, ... - and
  a specific profile if you have more than one: `firefox:default-release`,
  `chrome:Profile 2`.
- The trailing URL is required (yt-dlp refuses to run with none) and is only
  used to trigger the extraction - `--skip-download` means nothing is
  downloaded. Using the actual blocked video's URL here also confirms the
  cookies work for that specific video before you retry the batch.
- The output file must be in Mozilla/Netscape format (first line `# Netscape
  HTTP Cookie File`) - `--cookies-from-browser` + `--cookies <file>` writes
  it in that format automatically, don't hand-edit it.
- Cookies expire - if bot-check errors come back after a while, just re-run
  the command above.

The orchestrator automatically passes `--cookiefile` to UltraSinger when
`cookies/cookies.txt` exists - no further config needed once the file is in
place.

## Troubleshooting

* **A song failed** - check `logs/<song>.log`. `docker compose run --rm
  ultrasinger reset` re-queues failed jobs; `docker compose up` retries them.
  Any partial output it created is in `output/failed/`, not mixed into your
  real library.
* **`local input file not found: ...`** - a `song-requests.csv` row that isn't a
  `https://` link is treated as a local file path under `input/`; check the
  path is spelled correctly and the file is actually there.
* **A `broken.csv`-categorized repair failed with "no existing file to link
  and no usdb sync-meta source"** - `video`/`audio` category and neither an
  unambiguous local file nor a `*.usdb` sidecar was found; add the file (or
  a `.usdb` sidecar), or re-categorize it.
* **"multiple candidate files found"** - two+ unrelated media files sit in
  the folder for a `video`/`audio` repair; rename the one to keep so its
  basename matches the `.txt`, or remove the extra one.
* **A job shows up as `needs_review`** - its `broken.csv` category is
  blank/`other`/unrecognized, or (for `video`/`audio`) the reported defect
  didn't reproduce (the file was already there). See `output/report.md`
  for the description; fix the report or the folder, then `reset` it.
* **Model downloads each run?** - `models/` must be writable by the container
  (it runs as host user uid 1000 by default).
* **Wrong language detected for a repair** - set `#LANGUAGE` in the song txt
  or force the aligner model via env (e.g. `REPAIR_LANGUAGE=de`).
* **Repair quality** - `sync`/`lyrics` mode re-aligns the lyrics; if a line
  could not be aligned at all it keeps the original/estimated timing for
  that line (visible in the log).
* **A song's lyrics still look mis-heard** - check `output/report.md`: if
  it's marked ⚠ ("transcribed"), no online source had that song, so it kept
  whisper's own transcription. Add a `lyrics.txt` next to it in `input/`
  and reset the job to fix it manually (see "lyrics" mode above); adding a
  `GENIUS_API_KEY` may also help for less common songs.
* **A song still ends with a stray spoken line** - the end-card filter
  (`ENDCARD_MIN_GAP_S`/`ENDCARD_MAX_DUR_S`) is a heuristic (isolated by a
  long silence gap, short enough to be a blurb) - a genuinely long spoken
  outro won't be dropped, and a very short one right after the last sung
  line (small gap) won't either. Tune the thresholds or fix the txt by hand.
* **Disk usage** - `work/` (repair cache) and `output/<song>/cache` folders
  can be deleted anytime. Song outputs themselves are usually ~50-100 MB
  (video included).

## What the repair does NOT do

* It does not change the lyrics themselves - if the original txt has wrong
  words, they stay wrong (use new-song creation for that).
* `#VIDEOGAP` is not adjusted - if video and audio have different offsets,
  fix it manually after repair.
* Duet songs (P1/P2 in one txt) are aligned as one continuous singer - line
  breaks are preserved, but interleaved duet timing is not specially modeled.
