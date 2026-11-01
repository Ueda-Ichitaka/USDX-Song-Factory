# Library utilities

Tools to find songs with a wrong `#GAP` or drifting timing in an UltraStar
library and hand them to the stack for repair. Three steps, each its own
tool - run them one after another or only the ones you need - plus a tool
that copies the songs of a dashboard export into the stack input. The
repair itself is a normal stack run; step 4 covers getting the repaired
songs back into the library.

All commands below run from the repository root (not from `stack/`).

| Step | Tool | Runs on | Changes |
|---|---|---|---|
| 1. Scan | `run_library_check.sh` (starts `library_check.py`) | the stack's container | nothing in the library; writes results + report |
| 2. Import | `import_broken_songs.py` | host | copies song folders into the stack input, adds `broken.csv` rows |
| 3. Lyrics links | `fill_lyrics_links.py` | host | fills empty `lyrics_url` cells of `broken.csv` |
| 4. Repair, copy back | the stack, a file manager | stack container, host | writes `stack/output/repaired/<repair>/`; you copy the songs into the library |
| Dashboard export | `copy_reported_songs.py` | host | copies the reported song folders into the stack input, merges the rows into `broken.csv` |

`songfile.py` is no tool of its own: the other tools use it to read song
file headers (the same rules as USDX) and to turn `#LANGUAGE`/`#GENRE` into
dashboard codes.

Requirements:

- the scan: the stack's ROCm image (`ultrasinger-rocm`, built as described
  in `stack/docs/development.md`) and an AMD GPU - the check runs the vocal
  separation and the aligner on the GPU (the service maps `/dev/kfd` and
  `/dev/dri`); Docker with the Compose plugin
- the host tools: Python 3 with `requests`, and `ffprobe` (ffmpeg) for the
  audio length checks

## 1. Scan a library

```sh
utilities/run_library_check.sh /path/to/library [output folder]
```

Every usable song file is checked the way the stack's gap/sync repair
measures a song: its own lyrics are aligned to its separated vocals, which
gives how far the lyrics are off at the first line, how much that grows
over the song (drift), how many lines agree, and the `#GAP` (and `#BPM`)
that would fix it. Song files are `.txt` files with `#TITLE` and `#ARTIST`
(as in USDX; `license.txt`, `README.txt` and notes are not songs). Left out:
duets, relative timing (`#RELATIVE:yes` - as in USDX, a line break like
`- 30 31` alone is absolute timing), song files without `#BPM` (USDX does
not load them), songs without audio or with fewer than 50 notes, songs this
stack generated, audio longer than 15 minutes.

- The library is mounted read-only - nothing in it is changed.
- Output folder (default `library analysis/` in the repository):
  `results.jsonl` (one line per song file) and `library-check.txt` (the
  report: `#GAP off`, `drifts`, `unclear`, `good`, left out), rewritten
  after every 10 songs.
- About 20-45 s per song; a whole library takes many hours. Songs are
  checked in a random order, so a partial run is already a fair sample.
- Stop with `docker stop library-check`, continue by running the same
  command again (finished songs are skipped; a result that no longer fits
  the song file - e.g. a song left out for a reason that no longer holds -
  is redone, and the report only lists the library's current song files). The script refuses to start
  while a check is running.
- Follow the progress: `docker logs -f library-check`.
- Resources: the container is capped (`MEMORY`, `CPUS` below) and waits
  before each song until enough RAM is free and the load is low enough
  (`MIN_AVAIL_GB`, `MAX_LOAD`). Do not run it at the same time as a stack
  batch: both use the GPU and the CPU-heavy aligner.

Settings (environment variables set when calling the script - there is
no settings file):

| Variable | Default | Meaning |
|---|---|---|
| `MEMORY` | `10g` | memory cap of the container - an overrun only stops the check, not your other programs |
| `MEMORY_SWAP` | `12g` | memory + swap cap |
| `CPUS` | `12` | CPU cap of the container |
| `THREADS` | `4` | CPU threads of the aligner |
| `MIN_AVAIL_GB` | `8` | before each song, wait until this much RAM is free |
| `MAX_LOAD` | `14` | ... and the 1-minute load is below this |
| `WORKER_SONGS` | `10` | songs per fresh worker process (memory is given back after each) |
| `MAX_AUDIO_MIN` | `15` | leave out songs with longer audio (live sets, medleys need more memory than the cap) |
| `RETRY_ERRORS` | unset | `1`: check songs marked as errors again (each once per run; a song that fails again stays marked) |
| `CHECK_NAME` | `library-check` | container name (for a second check at the same time) |
| `ONLY` | unset | only songs whose name contains this text - for tests of `library_check.py` in the container; `run_library_check.sh` does not pass it on |

Example: `MEMORY=8g CPUS=6 utilities/run_library_check.sh /music/ultrastar`

Verdicts in the report:

- **#GAP off** - shifted by a constant amount; the given `#GAP` fixes it.
- **drifts** - the shift grows over the song: `#BPM` is wrong or the audio
  is another version; the given `#GAP` and `#BPM` fix it.
- **unclear** - fewer than 60% of the lines fit: a different audio version,
  or singing the aligner cannot follow. Listen before changing anything.
- **damaged audio** - the audio decodes to less than half of its stated
  length (a zero-filled or broken file); replace the file.
- **good** - the lyrics sit on the singing; nothing to do.

A song is named by its folder; when the song file's name differs from the
folder, it follows in brackets (`ABBA - Chiquitita [Chiquitita-402]`). The
left-out list at the end of the report gives every left-out song with its
reason - a song whose check crashed is listed there as `error: ...`
(`RETRY_ERRORS=1` checks those again).

`results.jsonl` has one JSON line per checked song file (the newest line
per name counts). A checked song has `name`, `lang`, `lines`, `gap_ms`,
`bpm`, `offset_s` (song file minus singing, negative = the lyrics come too
late), `drift_s`, `agree`, `gap_fix_ms`, `bpm_fix`, `gap_fix_with_bpm_ms`
and `verdict` (plus `note` when there is one); a left-out song has `name`
and `skip` (the reason). Step 2 reads this file.

## 2. Import the songs into the stack

```sh
python3 utilities/import_broken_songs.py --library /path/to/library            # dry run
python3 utilities/import_broken_songs.py --library /path/to/library --apply
```

For every song with the verdict `#GAP off` (category `gap`) or `drifts`
(category `async`) of the scan's `results.jsonl`:

- its folder is copied from the library into `stack/input/` (a folder
  already there is left as it is);
- a `broken.csv` row is added in the karaoke-dashboard's export format:
  band and song name from the song file's `#ARTIST`/`#TITLE` (the stack
  matches rows to folders by them), the measured values and the fix as
  description (e.g. `library check: lyrics 9.76 s too late; fix #GAP 30270
  -> 20513`), trait `none` (set traits in the dashboard), no `cover_url`
  (library songs have their cover). Language: the dashboard code of the
  first `#LANGUAGE` entry; empty for romanized lyrics ("Japanese
  (romanized)") and unknown values, so the stack detects it. Genre: `#GENRE`
  when it is exactly a dashboard genre code, else `default`. A song that already has
  a row gets no second one; an older `broken.csv` is rewritten in the
  current format (a backup `broken.csv.bak-<time>` is kept);
- then step 3 runs for the rows without a lyrics link (`--no-lyrics` skips it).

Options: `--results FILE` (default `library analysis/results.jsonl`),
`--input DIR` (default `stack/input`), `--categories gap`, `drift` or
`gap,drift` (default both).

Note: a `broken.csv` exported from the dashboard later does not contain
these rows unless the songs are added there too.

## 3. Lyrics links

```sh
python3 utilities/fill_lyrics_links.py            # dry run
python3 utilities/fill_lyrics_links.py --apply
```

Looks up a link for every `broken.csv` row with an empty `lyrics_url`
(rows with a link are never touched):

- **lrclib.net** first: artist and title must match closely, and the
  hit's length must be within 4 s of the song's own audio in the stack
  input (same version); synced lyrics are preferred.
  Link: `https://lrclib.net/tracks/<id>`.
- **Genius** when lrclib.net has nothing and a Genius API key is set: the
  environment variable `GENIUS_API_KEY`, else the same key in the stack's
  own `stack/.env` (the key its lyrics search uses, see
  `stack/.env.example`). The utilities have no settings file of their own.
- **lrclib.net free-text search** when both find nothing (the artist is
  often named differently there, e.g. "Der König der Löwen" for "Disney's
  Der König der Löwen"): by artist and title, then by the title alone. A
  hit counts when its title matches, its length is within 4 s of the song's
  own audio and a word of the band (not "the", "and", "feat") is in the
  hit's artist or title - so "Queen - Jealousy" is not taken for "Marina
  And The Diamonds - Jealousy". Without the song's audio nothing is taken.

Options: `--csv FILE` (default `stack/input/broken.csv`), `--input DIR`
(the stack input folder, default: next to the csv). The requests identify
the stack (User-Agent) and wait 1 s between songs; when lrclib.net is
overloaded (429/503) a request is tried again after 5, 10 and 20 s.

## 4. Repair and copy back into the library

Start the stack on the imported songs (from `stack/`, see
`stack/docs/running.md`); to run only the repairs:

```sh
cd stack
docker compose --profile rocm run -d --name repairs-run ultrasinger-rocm \
  python /app/orchestrator/orchestrator.py run --only repairs
docker exec -it repairs-run progress -w      # live progress
```

The repaired songs land in `stack/output/repaired/<repair>/<song folder>/`,
one folder per repair actually done: `gap/`, `sync/`, `recreated/` (a sync
repair that had to re-create the song from the audio), `lyrics/`, `media/`,
`cover/`. Each is a full copy of the song folder with the fixed song file.

Copying them into the library (the library is not written by any tool
here): the media files keep the modification time of the library's
files (the copies into the input and the repair preserve it), the fixed
song file is newer. Folders that came into the input another way may have
newer media times; those files are copied again, which does no harm. In Dolphin (KDE), drop the song
folders onto the library, choose "Write Into" for the folders, tick "Apply
to All" and pick "Overwrite older files" from the Overwrite button's menu:
only files newer than the library's are copied, the unchanged audio and
video are skipped. Files the library does not have are copied anyway:

- `language.txt` in every repaired folder (the language the repair used;
  USDX ignores it);
- in `recreated/`: `[Vocals].mp3`, `[Instrumental].mp3`, a `.mid`, and in
  some folders a second song file next to the old one - USDX would list
  that song twice, so check these folders by hand;
- a folder whose name is not in the library is added as a new song.

## Songs reported in the dashboard

```sh
python3 utilities/copy_reported_songs.py --library /path/to/library --csv ~/Downloads/broken.csv            # dry run
python3 utilities/copy_reported_songs.py --library /path/to/library --csv ~/Downloads/broken.csv --apply
```

For every row of a karaoke-dashboard export ("Reported" page, `broken.csv`,
with or without the header line; `--csv` can be given more than once):

- the song folder is found in the library the way the dashboard names it:
  `<band> - <song name>` (just `<song name>` without a band), ignoring case;
- the folder is copied into `stack/input/` (a folder already there, in any
  case, is left as it is); rows without a library folder are listed;
- the row is merged into `stack/input/broken.csv` (dashboard format, a
  backup `broken.csv.bak-<time>` is kept); a song that already has a row
  gets no second one. Rows without a library folder are merged too (the
  stack ignores a row without a folder). Empty genre and trait cells become
  `default` and `none`.

Option: `--input DIR` (default `stack/input`).

## Tests

```sh
cd utilities
python3 test_songfile.py
python3 test_library_check.py
python3 test_import_broken_songs.py
python3 test_fill_lyrics_links.py
python3 test_copy_reported_songs.py
```
