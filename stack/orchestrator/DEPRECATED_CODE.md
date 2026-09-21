# Code superseded by global CTC alignment (to be removed once proven)

Lyrics mode (`repair.py --mode lyrics`, used for every new song whose lyrics
were fetched online) now aligns all words in ONE global CTC pass
(`ctc_align.py`, wired in through `repair.align_units_globally()`). The
per-line window machinery below is no longer called by any production code
path. It is kept for now because the benchmark harness compares against it.

Remove it when the new aligner has been confirmed on real songs (listening
test) and the follow-up experiments (tighter windows, whisper fallback) are
finished.

## repair.py

| Item | Why it is obsolete |
|---|---|
| `seed_lyric_windows()` | Per-line seed windows (scaffold spans / proportional by word count). The global pass needs no seeds. |
| `progressively_align_lyrics()` | Per-line window alignment chained through an `expected` position; the source of the cascade. |
| `reanchor_outlier_lyric_units()` | Patches drift produced by the chained windows. |
| `rescue_dropped_lyric_units()` | Retries lines the windows failed to place. |
| `run_alignment_with_retries()` | Retries a deterministic algorithm; the global pass is deterministic too. |
| `REPAIR_MAX_ALIGN_ATTEMPTS`, `REPAIR_ALIGN_GOOD_ENOUGH_FRACTION` | Only used by `run_alignment_with_retries()`. |

Still used, keep: `interpolate_word_timings()` (places words the aligner
could not time), `get_non_silent_subintervals()` /
`distribute_over_non_silent_span()`, `LYRICS_MIN_SILENCE_MS`,
`INTERP_MAX_STRETCH_FACTOR`, `clamp_beat_to_max()`,
`build_syllables_from_lyric_units()`, `split_long_lyric_units()`,
`write_lyrics_result()`. The scaffold txt is still needed for BPM and header
tags. Sync and gap repair modes have their own per-line machinery
(`progressively_align`, `reanchor_outlier_units`, `rescue_dropped_units`,
`refine_first_units`) that is NOT part of this list.

## test_lyrics_mode.py

Remove the check blocks for `seed_lyric_windows`, `run_alignment_with_retries`
and any other test that only exercises the functions above.

## Benchmark dependency

`stack/work/bench/bench2.py` (baseline column) calls the old functions.
Delete this dependency together with the code, or freeze the baseline
numbers first.
