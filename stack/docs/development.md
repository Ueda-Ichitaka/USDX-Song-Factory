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
* `src/` - UltraSinger itself (transcription, pitch detection, txt writing),
  lightly patched.

`stack/orchestrator/` is mounted into the containers, so changes there apply
to the next `docker compose run` without rebuilding. Changes under `src/` or
to dependencies (`pyproject.toml`) need a rebuild:

```bash
docker compose build ultrasinger                        # CPU image
docker compose --profile rocm build ultrasinger-rocm    # ROCm image
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

# container (from stack/): tests that need the heavy dependencies
docker compose run --rm ultrasinger python /app/orchestrator/test_lyrics_mode.py
docker compose run --rm ultrasinger python /app/orchestrator/test_repair.py
docker compose run --rm ultrasinger python /app/orchestrator/test_ctc_align.py
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
