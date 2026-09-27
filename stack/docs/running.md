# Running the stack

Everything here is run from inside `stack/` (that is where
`docker-compose.yml` lives). The examples use the CPU service `ultrasinger`;
for the AMD GPU service use `ultrasinger-rocm` instead (see
[configuration.md](configuration.md#amd-gpu-rocm)). Only one of the two
should run at a time - they share the same state.

## Start, stop, resume

```bash
docker compose up -d          # run all pending jobs in the background (builds the image if needed)
docker compose logs -f        # follow the full output
docker compose up             # same, in the foreground
```

**For a real batch, use `-d`.** A batch runs for hours; a foreground
`docker compose up`/`run` is tied to the terminal that started it - close it
(or lose the SSH connection) and the container gets stopped part-way through.
`-d` detaches the container from the terminal.

The container processes all pending jobs (new songs + repairs) one at a time
and exits when done. Progress is stored in `state/state.json`: stop at any
time and `docker compose up -d` again later - finished jobs are skipped,
pending and failed ones are continued.

The first run downloads the AI models (~4 GB) into `models/`; later runs reuse
them.

## Commands

The orchestrator runs inside the container. `docker compose run` needs the
full command (anything after the service name replaces the default command):

```bash
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py list       # planned jobs and status
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py progress   # progress table once
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py progress -w  # live progress table
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py report     # finishing report

# one job, bypassing the queue
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py run-one "https://www.youtube.com/watch?v=XYZ"
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py repair-one "/data/input/Some Song"

# state
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset          # retry failed jobs
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset --all    # redo everything
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset --prune  # drop jobs no longer in the song list

# shell inside the container (debugging)
docker compose run --rm ultrasinger bash
```

Selecting which jobs run (`--only generated|usdb|repairs`, `--match TEXT`):
[new-songs.md](new-songs.md#choosing-which-jobs-run). Every command and
option: [orchestrator-manual.md](orchestrator-manual.md).

While a batch is running:

```bash
docker compose exec ultrasinger progress -w   # live progress table
docker compose exec ultrasinger bash          # second shell in the running container
```

## Progress view

`progress -w` redraws a table every 10 s - run it in a second terminal while
the batch runs. Plain `progress` prints it once.

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

- **Pulled from USDB** - how many of the finished new songs came from an
  existing USDB upload instead of being generated. A tally of what happened,
  not a prediction for the pending songs.
- **Elapsed / avg / ETA** - measured since the current run started.
- **Device / CPU / RAM / GPU VRAM** - live usage read from `/proc` and (ROCm
  service) the GPU driver's sysfs files. CPU% is the 1-minute load average
  per core, like `uptime`/`top`. The GPU part is left out on the CPU service.
- Jobs still stored in `state.json` but no longer in the song list or input
  folder are shown on a separate "Not counted" line (`reset --prune` removes
  them).

## Interactive commands (attached session)

The service runs with a TTY and open stdin. While a batch runs:

```bash
docker compose attach ultrasinger
```

| command | effect |
|---------|--------|
| `s` / `status` | print the progress overview |
| `skip` | abort the current song, mark it failed, continue with the next |
| `stop` / `q` | finish the current song, then exit (resume later with `docker compose up -d`) |
| `h` | help |

Detach with `Ctrl-P Ctrl-Q` - **not** Ctrl-C, which stops the container.

## Output

```
stack/output/
├── new/<Artist - Title>/     created songs (txt, audio, video, vocals, instrumental, cover, midi, lyrics.txt)
├── repaired/<song folder>/   repaired songs (full copy + fixed txt)
├── failed/<new|repair>/      partial output of failed jobs
└── report.md                 finishing report
```

Copy the song folders from `output/new/` and `output/repaired/` into your
UltraStar Deluxe song folder.

### Finishing report

Every run writes `output/report.md` (and prints it at the end): every new song
created, with where its lyrics came from (`online:<source>`,
`usdb:<site>:<id>` or `transcribed`); every song repaired, with its repair
mode; every failed job, with its error and where its partial output went; and
every skipped song with the reason ("no link in the song list", or "no longer
in the song list" for a stale record that `reset --prune` removes). Regenerate
it without running anything:

```bash
docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py report
```

### Failed jobs

A failed job never leaves partial output among your real songs: whatever it
created is moved to `output/failed/new/` or `output/failed/repaired/`
(colliding names get a ` (1)` suffix, nothing is overwritten). The job shows
up in `progress`, `list` and `report` with its error, and `reset` retries it
on the next run. Each song is tried up to `MAX_ATTEMPTS` times (default 3).

### Logs

The full output of every song is streamed live (`docker compose logs -f`) and
kept per song in `logs/<job>.log`.

## Folder layout

```
stack/
├── input/
│   ├── song-requests.csv   want-list for new songs (band,title,url,...)
│   ├── broken.csv          optional: broken-song reports (band,song name,category,description)
│   └── <song folder>/      broken songs to repair (one folder per song)
│       └── lyrics.txt      optional: trusted lyrics -> repair "lyrics" mode
├── .env                    optional: accounts, API keys, tuning (copy from .env.example)
├── output/                 results (see above)
├── state/                  job state (resume support)
├── logs/                   per-job log files
├── models/                 AI model cache (whisper, demucs, aligners)
├── work/                   working cache (safe to delete)
├── cookies/                optional cookies.txt for YouTube
├── reference/              approved songs for the regression check (not in git)
├── docs/                   this documentation
├── usdb_syncer/            git submodule (USDB access)
└── docker-compose.yml
```
