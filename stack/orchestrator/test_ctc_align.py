#!/usr/bin/env python3
"""Tests for ctc_align.py.

About me: plain assert-based checks (no pytest) for the global CTC forced
alignment used by repair.py's lyrics mode: tokenizing lyrics words, the
CTC Viterbi itself (also cross-checked against torchaudio's own
forced_align while that still exists), chunked emission stitching and the
word-level result. Real wav2vec2 models are replaced by tiny fakes.
Runs inside the docker image (needs torch):

    docker compose run --rm ultrasinger-rocm python /app/orchestrator/test_ctc_align.py
"""

import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ctc_align  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


DICT = {"-": 0, "|": 1, "a": 2, "b": 3, "c": 4, "d": 5}


def make_emission(n_frames, n_classes, plan):
    """log-probs where blank (class 0) dominates except at the planned
    (frame, class) points."""
    em = torch.full((n_frames, n_classes), -8.0)
    em[:, 0] = 0.0
    for frame, cls in plan:
        em[frame, :] = -8.0
        em[frame, cls] = 0.0
    return em.log_softmax(-1)


# --------------------------------------------------------------------------
# blank_id_from / tokenize_words
# --------------------------------------------------------------------------

check("blank_id_from: uses the [pad]/<pad> entry when present",
      ctc_align.blank_id_from({"<pad>": 7, "a": 1}) == 7)
check("blank_id_from: defaults to 0",
      ctc_align.blank_id_from(DICT) == 0)

tokens, widx = ctc_align.tokenize_words(["ab", "c!"], DICT, 0)
check(f"tokenize_words: letters + a word separator between words, "
      f"punctuation dropped (got {tokens!r})",
      tokens == [2, 3, 1, 4] and widx == [0, 0, -1, 1])

tokens, widx = ctc_align.tokenize_words(["a-b"], DICT, 0)
check(f"tokenize_words: a character that maps to the blank id is skipped "
      f"(got {tokens!r})", tokens == [2, 3])

tokens, widx = ctc_align.tokenize_words(["éa", "7"], DICT, 0)
check(f"tokenize_words: letters/digits missing from the dictionary become "
      f"the wildcard token {ctc_align.WILDCARD} (got {tokens!r})",
      tokens == [ctc_align.WILDCARD, 2, 1, ctc_align.WILDCARD] and
      widx == [0, 0, -1, 1])

tokens, widx = ctc_align.tokenize_words(["a", "...", "b"], DICT, 0)
check(f"tokenize_words: a word with no alignable characters yields no "
      f"tokens and no double separator (got {tokens!r}, {widx!r})",
      tokens == [2, 1, 3] and widx == [0, -1, 2])

tokens, widx = ctc_align.tokenize_words(["A", "B"], DICT, 0)
check("tokenize_words: matching is case-insensitive",
      tokens == [2, 1, 3])

# --------------------------------------------------------------------------
# ctc_viterbi
# --------------------------------------------------------------------------

em = make_emission(20, 6, [(3, 2), (7, 3), (12, 4)])
spans = ctc_align.ctc_viterbi(em, [2, 3, 4], 0)
check(f"ctc_viterbi: peaky emissions put every token exactly on its peak "
      f"frame (got {[(s, e) for s, e, _ in spans]!r})",
      [(s, e) for s, e, _ in spans] == [(3, 4), (7, 8), (12, 13)])
check("ctc_viterbi: token scores are the mean posterior on the token's "
      "frames (about 1.0 for a clean peak)",
      all(0.9 < sc <= 1.0 for _, _, sc in spans))

em = make_emission(20, 6, [(7, 3), (8, 3), (9, 3), (10, 3)])
spans = ctc_align.ctc_viterbi(em, [3], 0)
check(f"ctc_viterbi: a sustained token spans all its frames "
      f"(got {[(s, e) for s, e, _ in spans]!r})",
      [(s, e) for s, e, _ in spans] == [(7, 11)])

em = make_emission(20, 6, [(3, 2), (6, 2)])
spans = ctc_align.ctc_viterbi(em, [2, 2], 0)
check(f"ctc_viterbi: two identical tokens in a row are kept apart "
      f"(got {[(s, e) for s, e, _ in spans]!r})",
      [(s, e) for s, e, _ in spans] == [(3, 4), (6, 7)])

raised = False
try:
    ctc_align.ctc_viterbi(make_emission(2, 6, []), [2, 3, 4, 5, 2], 0)
except ValueError:
    raised = True
check("ctc_viterbi: raises ValueError when there are fewer frames than "
      "tokens", raised)
check("ctc_viterbi: no tokens -> no spans",
      ctc_align.ctc_viterbi(make_emission(5, 6, []), [], 0) == [])

# cross-check against torchaudio's own implementation while it exists
try:
    import warnings

    import torchaudio.functional as TAF
    have_taf = hasattr(TAF, "forced_align")
except Exception:  # noqa: BLE001
    have_taf = False

if have_taf:
    torch.manual_seed(0)
    agree = 0
    trials = 8
    for _ in range(trials):
        n_tok = 15
        toks = torch.randint(2, 6, (n_tok,)).tolist()
        emr = (torch.randn(200, 6) * 3).log_softmax(-1)
        mine = ctc_align.ctc_viterbi(emr, toks, 0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ali, sc = TAF.forced_align(
                emr.unsqueeze(0), torch.tensor([toks], dtype=torch.int32), blank=0)
            theirs = TAF.merge_tokens(ali[0], sc[0].exp())
        same = len(mine) == len(theirs) and all(
            m[0] == t.start and m[1] == t.end for m, t in zip(mine, theirs))
        agree += same
    check(f"ctc_viterbi: matches torchaudio.forced_align on random "
          f"emissions ({agree}/{trials})", agree == trials)
else:
    print("[SKIP] torchaudio.forced_align not available - cross-check skipped")

# --------------------------------------------------------------------------
# emissions_for_audio: chunked inference must equal one big pass
# --------------------------------------------------------------------------

class FakeTorchaudioModel:
    """Purely local per-frame 'model': frame f depends only on samples
    [f*320, (f+1)*320). Returns (emissions, lengths) like a torchaudio
    pipeline model."""

    def __call__(self, wav, lengths=None):
        n = wav.shape[-1] // 320
        frames = wav[0, :n * 320].reshape(n, 320)
        logits = torch.stack([frames.mean(1), frames.std(1), frames.max(1).values], dim=1)
        return logits.unsqueeze(0), None

    def to(self, device):
        return self


class FakeHFModel(FakeTorchaudioModel):
    def __call__(self, wav):
        class Out:
            pass
        out = Out()
        out.logits = FakeTorchaudioModel.__call__(self, wav)[0]
        return out


torch.manual_seed(1)
audio = torch.randn(320 * 47 + 130).numpy()
one_pass = ctc_align.emissions_for_audio(
    FakeTorchaudioModel(), {"type": "torchaudio"}, audio, chunk_s=1000.0, ctx_s=0.0)
chunked = ctc_align.emissions_for_audio(
    FakeTorchaudioModel(), {"type": "torchaudio"}, audio, chunk_s=0.1, ctx_s=0.04)
n_common = min(one_pass.shape[0], chunked.shape[0])
check(f"emissions_for_audio: chunked stitching equals a single pass "
      f"({chunked.shape[0]} vs {one_pass.shape[0]} frames)",
      abs(chunked.shape[0] - one_pass.shape[0]) <= 1 and
      torch.allclose(one_pass[:n_common], chunked[:n_common], atol=1e-5))
check("emissions_for_audio: output is log-softmax normalised",
      torch.allclose(chunked.exp().sum(-1), torch.ones(chunked.shape[0]), atol=1e-4))
hf = ctc_align.emissions_for_audio(
    FakeHFModel(), {"type": "huggingface"}, audio, chunk_s=0.1, ctx_s=0.04)
check("emissions_for_audio: huggingface-type models (.logits) work too",
      hf.shape == chunked.shape and torch.allclose(hf, chunked, atol=1e-5))

# --------------------------------------------------------------------------
# align_words_globally
# --------------------------------------------------------------------------

meta = {"dictionary": DICT, "type": "torchaudio"}
# words "ab" "c": tokens a b | c  planned at frames 10, 14, 17, 20
em = make_emission(40, 6, [(10, 2), (14, 3), (17, 1), (20, 4)])
res = ctc_align.align_words_globally(["ab", "c"], em, meta)
check(f"align_words_globally: word start/end come from its first/last "
      f"token frame (20 ms frames) (got {res!r})",
      abs(res[0][0] - 0.20) < 1e-9 and abs(res[0][1] - 0.30) < 1e-9 and
      abs(res[1][0] - 0.40) < 1e-9 and abs(res[1][1] - 0.42) < 1e-9)
check("align_words_globally: every aligned word carries a 0..1 score",
      all(0.0 < r[2] <= 1.0 for r in res))

res = ctc_align.align_words_globally(["a", "...", "b"], make_emission(
    40, 6, [(5, 2), (9, 1), (14, 3)]), meta)
check(f"align_words_globally: a word with nothing alignable gets None while "
      f"its neighbours are aligned (got {res!r})",
      res[1] is None and res[0] is not None and res[2] is not None)

res = ctc_align.align_words_globally(["éb"], make_emission(
    40, 6, [(5, 3)]), meta)
check(f"align_words_globally: a wildcard character (not in the dictionary) "
      f"still yields a timed word (got {res!r})", res[0] is not None)

check("align_words_globally: no words -> empty result",
      ctc_align.align_words_globally([], make_emission(10, 6, []), meta) == [])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
