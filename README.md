# UltraStar Song Factory

A Docker-based batch pipeline for bulk-creating and repairing UltraStar
Deluxe karaoke songs, built on top of
[UltraSinger](https://github.com/rakuri255/UltraSinger).

## What this is

This repository is a fork of
[rakuri255/UltraSinger](https://github.com/rakuri255/UltraSinger) - a tool
that automatically generates UltraStar Deluxe karaoke files (lyrics, pitch,
midi, vocal/instrumental separation) from music - extended with `stack/`, a
Docker Compose batch-processing stack that turns UltraSinger into a
hands-off song factory: point it at a list of songs or a folder of broken
ones, and it works through the whole batch unattended.

## What it does

- **Bulk-creates new songs** from a CSV/text list of song names, YouTube
  links, or local audio/video files.
- **Checks USDB first** for an existing, community-verified upload before
  generating a new song from scratch, via a
  [usdb_syncer](https://github.com/bohning/usdb_syncer) submodule.
- **Bulk-repairs existing songs** - fixes GAP/timing drift, or, in "lyrics"
  mode, replaces wrong lyrics entirely using a trusted lyrics file you
  supply.
- **Looks up real lyrics online** before falling back to audio
  transcription, recording which source was used per song.
- Also handles lyrics romanization, spoken video end-card filtering,
  progress tracking, and a finishing report - see `stack/README.md` for
  the full feature list.
- Runs on CPU everywhere, with an optional AMD GPU (ROCm) build.

## Quick start

```bash
cd stack
docker compose up
```

Full setup, configuration, and usage docs: **[stack/README.md](stack/README.md)**.

## The underlying UltraSinger tool

The core song-generation engine (`src/UltraSinger.py`) is still the
original UltraSinger CLI, only lightly patched locally (see `git log`).
For manual/direct CLI use outside the Docker stack, refer to the upstream
project itself: https://github.com/rakuri255/UltraSinger.

## License

MIT - see [LICENSE](LICENSE). Original work by
[rakuri255](https://github.com/rakuri255) (Vadim Rangnau).
