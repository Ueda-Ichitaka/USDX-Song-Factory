# Development

Notes for changing the stack itself.

## Code layout

* `stack/orchestrator/orchestrator.py` - job planning, queue, state, progress,
  report ([orchestrator-manual.md](orchestrator-manual.md)).
* `stack/orchestrator/repair.py` - repairs and the lyrics alignment used for
  new songs (global CTC alignment in `ctc_align.py`, note pitch normalisation
  in `pitch_normalise.py`).
* `stack/orchestrator/lyrics_fetch.py`, `usdb_lookup.py`,
  `usdb_eu_lookup.py`, `romanize.py` - lyrics and USDB sources.
* `stack/orchestrator/outro.py`, `recording_length.py`, `anchors.py` - where
  the song ends inside a video (outro cut, `song_end.txt`, `anchors.txt`).
* `stack/orchestrator/lyrics_divergence.py` - the lyrics review (does the
  lyrics text match what is sung?).
* `stack/orchestrator/timing_fit.py` - how far a song file is shifted or
  drifts against the singing, and the `#GAP`/`#BPM` that fix it.
* `stack/orchestrator/lyrics_fill.py` - lines whisper heard that the lyrics
  text lacks (a chorus repeat written once too few), proposed for the
  lyrics step.
* `src/` - UltraSinger itself (transcription, pitch detection, txt writing),
  lightly patched.

## Building the images

There are two images, both built from `stack/Dockerfile` with the repository
root as build context (see the `build:` sections in `docker-compose.yml`):

| service | image | PyTorch wheels |
|---------|-------|----------------|
| `ultrasinger` | `ultrasinger-stack:cpu` | `https://download.pytorch.org/whl/cpu` |
| `ultrasinger-rocm` | `ultrasinger-stack:rocm` | `https://download.pytorch.org/whl/rocm6.4` |

What needs a rebuild:

* **No rebuild:** anything under `stack/orchestrator/`. The folder is mounted
  into the containers (`./orchestrator:/app/orchestrator`), so a change
  applies to the next process that starts - the next `docker compose run`,
  and for a running batch the next song (every song starts `repair.py`,
  `lyrics_fetch.py` and `romanize.py` fresh). `orchestrator.py` itself is
  only read when the batch starts, so restart the batch after changing it.
* **Rebuild:** anything under `src/` (UltraSinger itself), `pyproject.toml`
  (dependencies), `stack/Dockerfile`, or the `stack/usdb_syncer` submodule.
  These are copied into the image at build time.

Commands (from inside `stack/`):

```bash
# normal rebuild after a change (reuses unchanged layers - fast when only src/ changed)
docker compose build ultrasinger                          # CPU image
docker compose --profile rocm build ultrasinger-rocm      # ROCm image

# full rebuild from scratch (new base image, fresh pip installs - e.g. to pick up
# a newer dependency release); takes much longer
docker compose build --pull --no-cache ultrasinger
docker compose --profile rocm build --pull --no-cache ultrasinger-rocm

# keep a build log
docker compose --profile rocm build ultrasinger-rocm 2>&1 | tee work/build_rocm.log
```

A warning `buildx Docker CLI plugin not found: falling back to the classic
builder` is harmless - the Dockerfile uses no BuildKit-only feature.

After a rebuild, a running batch still uses the old image: stop it (`stop`
in the attached session, see [running.md](running.md)) and start it again
with `docker compose up -d` (CPU) or
`docker compose --profile rocm up -d ultrasinger-rocm` (ROCm).

Check whether an image contains your current `src/` (prints nothing but
`image src identical` when it does; from the repository root):

```bash
(cd src && find . -name "*.py" -exec md5sum {} + | LC_ALL=C sort -k2) > /tmp/host.md5
(cd stack && docker compose run --rm -T --no-deps --entrypoint bash ultrasinger-rocm \
  -c "cd /app/UltraSinger/src && find . -name '*.py' -exec md5sum {} + | LC_ALL=C sort -k2") > /tmp/image.md5
diff /tmp/host.md5 /tmp/image.md5 && echo "image src identical"
```

## Tests

```bash
# host (from the repository root): UltraSinger's pytest suite
.venv/bin/python -m pytest pytest

# host (from stack/orchestrator/): pure-Python orchestrator tests
python3 test_orchestrator.py
python3 test_lyrics_fetch.py
python3 test_pitch_normalise.py
python3 test_compare_reference.py
python3 test_outro.py
python3 test_recording_length.py
python3 test_anchors.py
python3 test_lyrics_divergence.py
python3 test_lyrics_fill.py
python3 test_timing_fit.py
python3 test_song_header.py

# container (from stack/): tests that need the heavy dependencies
docker compose run --rm ultrasinger python /app/orchestrator/test_lyrics_mode.py
docker compose run --rm ultrasinger python /app/orchestrator/test_repair.py
docker compose run --rm ultrasinger python /app/orchestrator/test_ctc_align.py
docker compose run --rm ultrasinger python /app/orchestrator/test_romanize.py
```

## Reference songs (regression check)

`stack/reference/` holds generated songs whose timing was checked by ear in
USDX and approved (one folder per song, copied unchanged from `output/new/`).
After a change to the alignment and a new run, compare the new generation of
those songs with the approved versions (from inside `stack/`):

```bash
python3 orchestrator/compare_reference.py
# other folders: --reference DIR --new DIR
```

It prints one table row per reference song: how many words both versions
share, the share of word starts within 0.1 s and 0.3 s of the approved
version, the median shift (new minus approved, in seconds) and the largest
shifts. Words are matched by their text, so changed lyrics still compare on
the words both versions have. A song without a new generation shows up as
`missing`. The folder is in `.gitignore` (audio and video do not belong in
git); to add a song, copy its folder from `output/new/`.

## Benchmarks

`stack/work/bench/` (not in git) holds the measurement scripts used to tune
the alignment and pitch detection against the hand-synced songs in
`test data/known working/`; results and decisions are recorded in
`knowledge/03-PROGRESS.md`.
