# Configuration

All settings are environment variables. Nothing is required: without any
settings the stack runs on the CPU, generates every song from scratch (no
USDB) and uses only the free lyrics source.

**Where to set them:** `stack/.env` (copy `stack/.env.example`; docker compose
loads it automatically) works only for the variables that `docker-compose.yml`
passes into the container. Variables marked **(compose)** below are not passed
through - add them yourself under `environment:` of the service in
`docker-compose.yml`, for example `ENDCARD_MIN_GAP_S: "10"`.

## Accounts and API keys

| variable | meaning |
|----------|---------|
| `USDB_USERNAME` / `USDB_PASSWORD` | usdb.animux.de account - enables pulling existing songs from USDB ([song-sources.md](song-sources.md#usdb)) |
| `USDB_EU_EMAIL` / `USDB_EU_PASSWORD` | usdb.eu account - second USDB source |
| `GENIUS_API_KEY` | enables the Genius lyrics source ([song-sources.md](song-sources.md#online-lyrics)); get a "Client Access Token" at https://genius.com/api-clients |

## General settings

| variable | default | meaning |
|----------|---------|---------|
| `SONGS_FILE` | `/data/input/song-requests.csv` | the want-list inside the container |
| `BROKEN_CSV` | `/data/input/broken.csv` | broken-song reports ([repairs.md](repairs.md)) |
| `NEW_SONGS_DIR` | `/data/output/new` | where new songs are written |
| `FAILED_DIR` **(compose)** | `/data/output/failed` | where a failed job's partial output goes |
| `MAX_ATTEMPTS` | `3` | attempts per song before it stays failed |
| `JOB_TIMEOUT_MIN` | `90` | hard timeout per song in minutes |
| `REPAIR_MODE` | `sync` | default repair mode, `gap` or `sync` ([repairs.md](repairs.md)) |
| `LYRICS_ENABLED` | `1` | `0` disables the online-lyrics step for new songs |
| `ROMANIZE` | `1` | `0` disables lyrics romanization |
| `USDB_MIN_MATCH_SCORE` | `0.90` | minimum artist+title similarity (0-1) to accept a USDB match |
| `USDB_CATALOG_TTL_HOURS` | `24` | how long the local animux.de catalog cache is reused |
| `BROKEN_MATCH_MIN_SCORE` | `0.85` | minimum #ARTIST/#TITLE similarity (0-1) to attach a `broken.csv` row to a repair folder |
| `ENDCARD_MIN_GAP_S` **(compose)** | `8.0` | silence gap (s) before a trailing blurb counts as a video end-card; `<= 0` disables the filter |
| `ENDCARD_MAX_DUR_S` **(compose)** | `20.0` | a trailing blurb longer than this is kept as real lyrics |
| `ULTRASINGER_ARGS` | - | extra arguments for every UltraSinger run, e.g. `"--disable_hyphenation --format_version 1.1.0"` |
| `WHISPER_MODEL` | auto | force a whisper model (`tiny` ... `large-v2`) instead of the resource-based choice |
| `DEMUCS_MODEL` | auto | force a demucs separation model (`htdemucs`, `htdemucs_ft`, `mdx_extra_q`, ...) |
| `WHISPER_BATCH_SIZE` | auto | force a whisper batch size |

Repair tuning variables: [repairs.md](repairs.md#tuning-rarely-needed).

## Timing and pitch tuning (lyrics mode)

Values measured on 110 hand-synced songs; change only for experiments. All of
them are **(compose)** variables.

| variable | default | meaning |
|----------|---------|---------|
| `MAX_ALIGNED_WORD_SECONDS` | `4.0` | longest duration an aligned word may get |
| `LYRICS_LINE_OUTLIER_GAP_S` | `3.0` | a word further than this from the rest of its line is placed back next to it |
| `WORD_END_EXTEND_DROP_DB` | `6.0` | word ends follow a held note while the vocals stay within this many dB of the word's level |
| `WORD_END_EXTEND_MAX_S` | `3.0` | maximum extension of a word end |
| `WORD_START_LEAD_S` | `0.04` | word starts are moved this much earlier |
| `LYRICS_MAX_LINE_CHARS` | `60` | longer lines are split at a word boundary |
| `LYRICS_LINE_BREAK_PAUSE_S` | `2.5` | a pause this long inside a line starts a new line |
| `PITCH_MIN_WINDOW_S` | `0.4` | a note's pitch is picked from at least this much audio around it |
| `PITCH_FOLD_TOLERANCE` | `7` | a note further (semitones) from its neighbours moves by octaves towards them |
| `PITCH_SONG_TOLERANCE` | `12` | a note further (semitones) from the song's median moves by octaves towards it |
| `PITCH_UNSURE_MIN_FRACTION` | `0.3` | notes with fewer confident pitch frames take the local melody level |

## Resource budget and model choice

Bigger whisper/demucs models give better results but need more memory and
time. Instead of one fixed model for every machine, the stack picks whisper's
model and batch size and demucs' separation model from a declared budget:

| variable | default | meaning |
|----------|---------|---------|
| `STACK_RAM_GB` | `8` | RAM available to the stack |
| `STACK_SWAP_GB` | `250` | available swap (zram etc.) - only a small cushion for spikes, never counted as real capacity |
| `STACK_CPU_CORES` | `16` | CPU threads available to the stack |
| `STACK_VRAM_GB` | `16` (ROCm service only) | GPU memory - only demucs uses the GPU; whisper always runs on the CPU (CTranslate2 has no ROCm support) |
| `STACK_RAM_SAFETY_MARGIN_GB` **(compose)** | `1.5` | subtracted from `STACK_RAM_GB` first |
| `STACK_VRAM_SAFETY_MARGIN_GB` **(compose)** | `2` | subtracted from `STACK_VRAM_GB` first |

The defaults match the machine the stack was developed on - **set them in
`.env` for your hardware**. State your real totals and let the margins cover
what else uses the memory (for example the desktop sharing the GPU); raise the
margins if you still see memory pressure.

Whisper's model is sized from RAM, swap and CPU cores; demucs' model from
VRAM on the ROCm service (where the better `htdemucs_ft` is essentially free)
or from RAM and CPU cores on the CPU service. The choice is printed at the
start of each run:

```
Resource-based auto-selection (RAM=6.5GB swap=250.0GB cpu=16 vram=14.0GB device=cuda):
  whisper model: large-v2 - 7.7 GB effective RAM
  whisper batch size: 16 - 16 CPU core(s)
  demucs model: htdemucs_ft - 14.0 GB VRAM (GPU, best-quality model affordable)
```

Setting `WHISPER_MODEL`/`DEMUCS_MODEL`/`WHISPER_BATCH_SIZE` overrides that one
choice. The thresholds are a documented heuristic
(`stack/orchestrator/resource_profile.py`), not a guarantee for your hardware.

## Memory

The stack processes **one song at a time** and needs about 4-5 GB RAM at its
peak (whisper large-v2 int8 + demucs, measured). No Docker memory limit is set
on purpose - rare spikes go to swap. To be strict, add to the service in
`docker-compose.yml`:

```yaml
    mem_limit: 8g        # RAM only
    memswap_limit: 16g   # RAM + swap
```

If memory gets tight, use a smaller whisper model (`WHISPER_MODEL=medium`).

## AMD GPU (ROCm)

The default service `ultrasinger` runs on the CPU. The GPU service
`ultrasinger-rocm` accelerates vocal separation and alignment (whisper stays
on the CPU either way):

```bash
docker compose --profile rocm up -d ultrasinger-rocm   # builds the ROCm image on first use
```

It passes `/dev/kfd` and `/dev/dri` into the container and needs a working
ROCm kernel driver on the host (`ls /dev/kfd` shows the device). Tested with
an AMD RX 9070 (gfx1201); the PyTorch ROCm 6.4 wheels include RDNA4 kernels.

To use the GPU service permanently, use `ultrasinger-rocm` instead of
`ultrasinger` in every command. Only one of the two services should run at a
time - they share the same state.

## YouTube cookies

Needed for age-restricted videos and when YouTube asks "Sign in to confirm
you're not a bot": [youtube-cookies.md](youtube-cookies.md).
