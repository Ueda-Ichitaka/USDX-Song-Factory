# UltraStar Song Factory - Docker stack

This folder is the batch stack: `docker-compose.yml`, the orchestrator code,
and the data folders (`input/`, `output/`, `state/`, ...). **Every
`docker compose` command runs from inside this folder.**

New here? Start with the quickstart in the [main README](../README.md).

## Documentation

| Topic | File |
|---|---|
| Running the stack: commands, progress view, report, output, folder layout | [docs/running.md](docs/running.md) |
| The song list: format, optional columns, choosing which jobs run | [docs/new-songs.md](docs/new-songs.md) |
| Repairing existing songs, `broken.csv`, trusted lyrics | [docs/repairs.md](docs/repairs.md) |
| USDB, online lyrics, romanization, end-card filter | [docs/song-sources.md](docs/song-sources.md) |
| Settings (environment variables), resource budget, memory, AMD GPU | [docs/configuration.md](docs/configuration.md) |
| YouTube cookies (bot check, age-restricted videos) | [docs/youtube-cookies.md](docs/youtube-cookies.md) |
| `orchestrator.py` manual (man page) | [docs/orchestrator-manual.md](docs/orchestrator-manual.md) |
| Troubleshooting | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Development: code layout, tests, regression check | [docs/development.md](docs/development.md) |
