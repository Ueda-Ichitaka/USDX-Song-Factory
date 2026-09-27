# UltraStar Song Factory

Turn a list of songs into ready-to-sing UltraStar Deluxe karaoke songs -
unattended, in bulk.

You write down which songs you want (artist, title, YouTube link); the
factory works through the list and delivers complete song folders: UltraStar
txt with synced lyrics and notes, audio, video, cover, separate vocal and
instrumental tracks. It can also repair songs you already have (wrong `#GAP`,
lyrics drifting out of sync, wrong lyrics, missing video).

## What it does

- **Prefers existing community songs.** If a song is already on USDB
  (usdb.animux.de or usdb.eu), its hand-made notes are used and only the video,
  audio and `#GAP` are added.
- **Generates the rest from scratch.** Vocals are separated from the music,
  real lyrics are looked up online (lyrics link, syncedlyrics, Genius) and
  aligned word by word to the singing; note pitches are detected and kept in a
  singable range. Only when no lyrics are found online does a song keep the
  speech-recognition transcription.
- **Repairs existing songs** - from a folder, optionally with a
  `broken.csv` that says what is wrong with each song.
- Romanises Cyrillic, Korean and Japanese lyrics, filters spoken video
  end-cards, shows live progress, writes a finishing report, keeps failed
  songs out of your library, and resumes where it stopped.

## What it uses

- [UltraSinger](https://github.com/rakuri255/UltraSinger) (this repository is
  a fork) - the song-generation engine
- [Demucs](https://github.com/facebookresearch/demucs) - vocal/instrument
  separation
- [WhisperX](https://github.com/m-bain/whisperX) - speech recognition and the
  wav2vec2 models for word-level lyrics alignment
- [SwiftF0](https://github.com/lars76/swift-f0) - pitch detection
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - YouTube downloads
- [usdb_syncer](https://github.com/bohning/usdb_syncer) (git submodule) -
  USDB access
- [syncedlyrics](https://pypi.org/project/syncedlyrics/),
  [Genius](https://genius.com/) - online lyrics
- Docker Compose - everything runs in containers; CPU everywhere, optional AMD
  GPU (ROCm)

## Quickstart

**You need:** Linux (or any system with Docker), Docker with the Compose
plugin (`docker compose version`), git, and free disk space: about 40 GB for
the CPU setup (image ~8 GB, AI models ~4-25 GB depending on languages, plus
your songs), about 60 GB for the AMD GPU setup (image ~24 GB).

```bash
# 1. Get the code, including the USDB submodule
git clone --recurse-submodules https://github.com/Ueda-Ichitaka/USDX-Batch-Stack.git
cd USDX-Batch-Stack/stack          # every command below runs from here

# 2. Create the data folders yourself (the container runs as uid 1000;
#    folders created by Docker would belong to root)
mkdir -p input output state logs models work cookies

# 3. Optional: accounts and keys (USDB, Genius) - works without them
cp .env.example .env               # then edit .env

# 4. Write your song list
cat > input/song-requests.csv <<'EOF'
band,title,url
ASP,Zaubererbruder,https://www.youtube.com/watch?v=sifM_9DIVbI
Hannes Wader,Bella ciao,https://www.youtube.com/watch?v=x6WW4mhGcOw
EOF

# 5. Build and start (first start builds the image and downloads the models)
docker compose up -d                                   # CPU
# docker compose --profile rocm up -d ultrasinger-rocm # or: AMD GPU (ROCm)

# 6. Watch it work
docker compose exec ultrasinger progress -w            # live progress table (Ctrl-C to leave)
docker compose logs -f                                 # full output

# 7. Collect your songs
ls output/new                      # finished songs, one folder each
cat output/report.md               # what was created, from which source, what failed
```

Copy the folders from `output/new/` into your UltraStar Deluxe song folder.
For the GPU service, use `ultrasinger-rocm` instead of `ultrasinger` in the
commands above and below.

If your user id is not 1000 (`id -u`), run
`sudo chown -R 1000:1000 input output state logs models work cookies` after
step 2.

## Everyday commands

All from inside `stack/`:

| What | Command |
|---|---|
| Start / resume the batch | `docker compose up -d` |
| Live progress | `docker compose exec ultrasinger progress -w` |
| Full output | `docker compose logs -f` |
| Stop after the current song | `docker compose attach ultrasinger`, type `stop` (leave with `Ctrl-P Ctrl-Q`) |
| Stop immediately | `docker compose stop` |
| Show the report | `docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py report` |
| Retry failed songs | `docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset`, then `docker compose up -d` |
| Redo one song | `docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py reset --done --match "Zaubererbruder"`, then `docker compose up -d` |
| Update the code | `git pull && git submodule update --init --recursive && docker compose build` (GPU: `docker compose --profile rocm build ultrasinger-rocm`) |

Adding songs later: append rows to `input/song-requests.csv` and start again -
finished songs are skipped. Every command and option:
[stack/docs/orchestrator-manual.md](stack/docs/orchestrator-manual.md).

## Documentation

| Topic | File |
|---|---|
| Running the stack: commands, progress view, report, output | [stack/docs/running.md](stack/docs/running.md) |
| The song list: format, optional columns, choosing which jobs run | [stack/docs/new-songs.md](stack/docs/new-songs.md) |
| Repairing existing songs, `broken.csv`, trusted lyrics | [stack/docs/repairs.md](stack/docs/repairs.md) |
| USDB, online lyrics, romanization | [stack/docs/song-sources.md](stack/docs/song-sources.md) |
| Settings, resource budget, AMD GPU | [stack/docs/configuration.md](stack/docs/configuration.md) |
| YouTube cookies (bot check, age-restricted videos) | [stack/docs/youtube-cookies.md](stack/docs/youtube-cookies.md) |
| `orchestrator.py` manual (man page) | [stack/docs/orchestrator-manual.md](stack/docs/orchestrator-manual.md) |
| Troubleshooting | [stack/docs/troubleshooting.md](stack/docs/troubleshooting.md) |
| Development: tests, regression check | [stack/docs/development.md](stack/docs/development.md) |

## The underlying UltraSinger tool

The song-generation engine (`src/UltraSinger.py`) is the original UltraSinger
CLI, patched locally (see `git log`). For direct CLI use outside the Docker
stack, see the upstream project: https://github.com/rakuri255/UltraSinger.

## License

MIT - see [LICENSE](LICENSE). Original work by
[rakuri255](https://github.com/rakuri255) (Vadim Rangnau).
