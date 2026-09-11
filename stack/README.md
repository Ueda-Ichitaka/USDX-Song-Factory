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
  audio/video file paths) into `input/songs.csv` (or `input/songs.txt`) and
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

# 1. put your songs into input/songs.csv (already contains the current batch)
# 2. put broken songs into input/ too (one folder per song)

# 3. build + run everything
docker compose up          # foreground, shows all output
# or
docker compose up -d       # detached, watch with: docker compose logs -f
```

The container processes all pending jobs (new songs + repairs) and exits when
done. Progress is persisted in `state/state.json`, so you can stop/resume at
any time - jobs that already succeeded are not repeated.

First run only: whisper/demucs/aligner models (~4 GB) are downloaded into
`models/` - this is cached for all later runs.

## Folder layout

```
stack/
├── input/
│   ├── songs.csv       # your "want list" for new songs (band,title,url)
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
└── docker-compose.yml
```

## The song list (new songs)

`input/songs.csv` (comma or semicolon separated, header line required):

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

Alternatively `input/songs.txt` (plain lines):

```
https://www.youtube.com/watch?v=U6bmr2zcCos
ASP - Zaubererbruder - https://www.youtube.com/watch?v=sifM_9DIVbI
```

Point the stack to another file with the env var `SONGS_FILE`
(override in `docker-compose.yml` or an `.env` file).

### Re-running: append or overwrite `songs.csv`?

For debugging/testing, editing `input/songs.csv` by hand (in any way) is
fine - just re-run the stack.

For production (e.g. `songs.csv` regenerated by another tool as a table
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

`progress` (or `progress -w` for a live view) renders:

```
==========================================================================
  UltraStar Song Factory - Batch Progress        2026-09-10 15:42
==========================================================================
  NEW SONGS:  12 done |  1 failed |  1 running |  47 pending | 2 skipped  (62 total)
  REPAIRS  :   5 done |  0 failed |  0 running | 14 pending   (19 total)
  Elapsed 2h18m | avg 9m41s/song | ETA ~9h
  Current: ASP - Duett (Minnelied der Incubi) - running for 3m (attempt 1)
--------------------------------------------------------------------------
  Failed jobs:
   - Xandria - Nightfall  [new]  exit code 1
==========================================================================
```

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
| `SONGS_FILE` | `/data/input/songs.csv` | the want-list file inside the container |
| `NEW_SONGS_DIR` | `/data/output/new` | where newly created songs are written |
| `FAILED_DIR` | `/data/output/failed` | where a failed job's partial output is quarantined |
| `ROMANIZE` | `1` | set to `0` to disable automatic lyrics romanization |
| `LYRICS_ENABLED` | `1` | set to `0` to disable the online-lyrics-for-new-songs step |
| `GENIUS_API_KEY` | - | enables the Genius lyrics source (see "Lyrics sources" above); unset = skipped |
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

## YouTube: "Sign in to confirm you're not a bot"

yt-dlp occasionally hits YouTube bot protection. If downloads fail with that
error, export your browser cookies once:

```bash
yt-dlp --cookies ~/cookies.txt --cookies-from-browser firefox
cp ~/cookies.txt stack/cookies/cookies.txt
```

The orchestrator automatically passes `--cookiefile` to UltraSinger when
`cookies/cookies.txt` exists.

## Troubleshooting

* **A song failed** - check `logs/<song>.log`. `docker compose run --rm
  ultrasinger reset` re-queues failed jobs; `docker compose up` retries them.
  Any partial output it created is in `output/failed/`, not mixed into your
  real library.
* **`local input file not found: ...`** - a `songs.csv` row that isn't a
  `https://` link is treated as a local file path under `input/`; check the
  path is spelled correctly and the file is actually there.
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
