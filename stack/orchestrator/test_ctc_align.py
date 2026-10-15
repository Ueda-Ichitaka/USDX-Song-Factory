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

# --------------------------------------------------------------------------
# end_penalty: the path may end after any word - lyrics never sung at the
# end are left out instead of being squeezed into the audio
# --------------------------------------------------------------------------

# audio: "ab" sung, then nothing; lyrics: "ab c d"
_tail_em = make_emission(40, 6, [(3, 2), (6, 3)])
_tok, _widx = ctc_align.tokenize_words(["ab", "c", "d"], DICT, 0)
_forced = ctc_align.ctc_viterbi(_tail_em, _tok, 0)
check("ctc_viterbi: without end_penalty every token is placed (forced)",
      all(sp is not None for sp in _forced))
_free = ctc_align.ctc_viterbi(_tail_em, _tok, 0, end_penalty=4.0, word_index=_widx)
check(f"ctc_viterbi: with end_penalty the unsung words at the end are left out (got {_free})",
      _free[0] is not None and _free[1] is not None and all(sp is None for sp in _free[2:]))
check("ctc_viterbi: the sung part keeps its frames",
      _free[0][0] == 3 and _free[1][0] == 6)

# everything sung: nothing is left out
_all_em = make_emission(40, 6, [(3, 2), (6, 3), (9, 1), (12, 4), (15, 1), (18, 5)])
_all = ctc_align.ctc_viterbi(_all_em, _tok, 0, end_penalty=4.0, word_index=_widx)
check("ctc_viterbi: with end_penalty nothing is left out when all is sung",
      all(sp is not None for sp in _all))

# "ab cd": only "ab" and "c" sung - "cd" is either placed whole or left out whole
_half_em = make_emission(40, 6, [(3, 2), (6, 3), (9, 1), (12, 4)])
_tok2, _widx2 = ctc_align.tokenize_words(["ab", "cd"], DICT, 0)
for _pen in (1.0, 4.0, 100.0):
    _half = ctc_align.ctc_viterbi(_half_em, _tok2, 0, end_penalty=_pen, word_index=_widx2)
    _word2 = [sp for sp, wi in zip(_half, _widx2) if wi == 1]
    check(f"ctc_viterbi: a word is never cut in half (penalty {_pen}, got {_half})",
          all(sp is None for sp in _word2) or all(sp is not None for sp in _word2))

_words = ctc_align.align_words_globally(["ab", "c", "d"], _tail_em, {"dictionary": DICT},
                                        end_penalty=4.0)
check(f"align_words_globally: left-out words are NOT_SUNG (got {_words})",
      _words[0] is not None and _words[0] != ctc_align.NOT_SUNG
      and _words[1] == ctc_align.NOT_SUNG and _words[2] == ctc_align.NOT_SUNG)
_words = ctc_align.align_words_globally(["ab", "!!", "c", "d"], _tail_em, {"dictionary": DICT},
                                        end_penalty=4.0)
check(f"align_words_globally: a word without letters stays None, also at the end (got {_words})",
      _words[1] is None)
check("align_words_globally: without end_penalty nothing is NOT_SUNG",
      ctc_align.NOT_SUNG not in ctc_align.align_words_globally(
          ["ab", "c", "d"], _tail_em, {"dictionary": DICT}))

# --------------------------------------------------------------------------
# filler: singing between two lyric lines that the lyrics do not have
# --------------------------------------------------------------------------

# "ab" | sung "d d d d d d" (not in the lyrics) | "c"
_fill_em = make_emission(40, 6, [(2, 2), (4, 3), (6, 1), (10, 5), (12, 5), (14, 5),
                                 (16, 5), (18, 5), (20, 5), (30, 1), (34, 4)])
_spans = []
_words = ctc_align.align_words_globally(["ab", "c"], _fill_em, {"dictionary": DICT},
                                        line_lengths=[1, 1], filler_beta=1.0,
                                        filler_enter=5.0, fillers_out=_spans)
check(f"filler: the unknown singing goes to the filler before line 2 (got {_spans})",
      len(_spans) == 1 and _spans[0][2] == 1
      and _spans[0][0] <= 10 * 0.02 + 1e-9 and _spans[0][1] >= 21 * 0.02 - 1e-9)
check(f"filler: the lyric words keep their own places (got {_words})",
      abs(_words[0][0] - 2 * 0.02) < 1e-9 and abs(_words[0][1] - 5 * 0.02) < 1e-9
      and abs(_words[1][0] - 34 * 0.02) < 1e-9)
_spans = []
ctc_align.align_words_globally(["ab", "c"], _fill_em, {"dictionary": DICT},
                               line_lengths=[1, 1], filler_beta=1.0,
                               filler_enter=100.0, fillers_out=_spans)
check(f"filler: not used when entering costs more than it saves (got {_spans})", _spans == [])

# "ab" sung, then "d d d d" (not in the lyrics) after the last line
_trail_em = make_emission(30, 6, [(2, 2), (4, 3), (12, 5), (14, 5), (16, 5), (18, 5)])
_spans = []
_words = ctc_align.align_words_globally(["ab"], _trail_em, {"dictionary": DICT},
                                        line_lengths=[1], filler_beta=1.0,
                                        filler_enter=5.0, fillers_out=_spans)
check(f"filler: singing after the last line goes to a trailing filler (got {_words}, {_spans})",
      abs(_words[0][0] - 2 * 0.02) < 1e-9 and abs(_words[0][1] - 5 * 0.02) < 1e-9
      and len(_spans) == 1 and _spans[0][2] == 1 and _spans[0][0] <= 12 * 0.02 + 1e-9)

_plain_em = make_emission(20, 6, [(2, 2), (4, 3), (6, 1), (10, 4)])
_spans = []
_with = ctc_align.align_words_globally(["ab", "c"], _plain_em, {"dictionary": DICT},
                                       line_lengths=[1, 1], filler_beta=1.0,
                                       filler_enter=5.0, fillers_out=_spans)
_without = ctc_align.align_words_globally(["ab", "c"], _plain_em, {"dictionary": DICT})
check(f"filler: nothing unknown sung -> no filler, same timing (got {_spans}, {_with}, {_without})",
      _spans == [] and _with == _without)

_spans = []
_words = ctc_align.align_words_globally(["ab", "!!", "c"], _fill_em, {"dictionary": DICT},
                                        line_lengths=[1, 1, 1], filler_beta=1.0,
                                        filler_enter=5.0, fillers_out=_spans)
check(f"filler: a line without letters is passed over (got {_words}, {_spans})",
      _words[1] is None and abs(_words[2][0] - 34 * 0.02) < 1e-9 and len(_spans) == 1
      and _spans[0][2] == 2)

_spans = []
_free = ctc_align.align_words_globally(["ab", "c", "d"], _tail_em, {"dictionary": DICT},
                                       end_penalty=4.0, line_lengths=[1, 1, 1],
                                       filler_beta=1.0, filler_enter=5.0, fillers_out=_spans)
check(f"filler: works together with the end penalty (got {_free})",
      _free[0] is not None and _free[0] != ctc_align.NOT_SUNG
      and _free[1] == ctc_align.NOT_SUNG and _free[2] == ctc_align.NOT_SUNG)

# --------------------------------------------------------------------------
# line skips: a line may be left out at a cost per letter (optional lines)
# --------------------------------------------------------------------------

# lyrics a | b, plus the candidate line "c d" between them
_opt_sung = make_emission(20, 6, [(2, 2), (4, 1), (6, 4), (8, 1), (10, 5), (12, 1), (15, 3)])
_opt_unsung = make_emission(20, 6, [(2, 2), (4, 1), (15, 3)])
_w = ctc_align.align_words_globally(["a", "c", "d", "b"], _opt_sung, {"dictionary": DICT},
                                    line_lengths=[1, 2, 1], filler_beta=1.0, filler_enter=5.0,
                                    line_skip=[None, 0.5, None])
check(f"line skip: an optional line that is sung is placed (got {_w})",
      _w[1] is not None and _w[1] != ctc_align.NOT_SUNG and abs(_w[1][0] - 6 * 0.02) < 1e-9)
_w = ctc_align.align_words_globally(["a", "c", "d", "b"], _opt_unsung, {"dictionary": DICT},
                                    line_lengths=[1, 2, 1], filler_beta=1.0, filler_enter=5.0,
                                    line_skip=[None, 0.5, None])
check(f"line skip: an optional line that is not sung is left out as NOT_SUNG (got {_w})",
      _w[1] == ctc_align.NOT_SUNG and _w[2] == ctc_align.NOT_SUNG
      and abs(_w[0][0] - 2 * 0.02) < 1e-9 and abs(_w[3][0] - 15 * 0.02) < 1e-9)
_w = ctc_align.align_words_globally(["a", "c", "d", "b"], _opt_unsung, {"dictionary": DICT},
                                    line_lengths=[1, 2, 1], filler_beta=1.0, filler_enter=5.0,
                                    line_skip=[None, None, None])
check(f"line skip: None = the line must be placed (got {_w})",
      all(x is not None and x != ctc_align.NOT_SUNG for x in _w))
_w = ctc_align.align_words_globally(["c", "d", "a", "b"], make_emission(20, 6, [(4, 2), (6, 1), (12, 3)]),
                                    {"dictionary": DICT}, line_lengths=[2, 1, 1],
                                    filler_beta=1.0, filler_enter=5.0, line_skip=[0.5, None, None])
check(f"line skip: also the first line can be left out (got {_w})",
      _w[0] == ctc_align.NOT_SUNG and _w[1] == ctc_align.NOT_SUNG and abs(_w[2][0] - 4 * 0.02) < 1e-9)

# an optional line that is left out must not change the other words: the
# jump over it still passes one word separator, like the path without it
_sep_em = make_emission(12, 6, [(2, 2), (3, 3)])  # "a" then "b" right after, no separator sung
_alone = ctc_align.align_words_globally(["a", "b"], _sep_em, {"dictionary": DICT})
_with = ctc_align.align_words_globally(["a", "c", "b"], _sep_em, {"dictionary": DICT},
                                       line_lengths=[1, 1, 1], line_skip=[None, 2.0, None])
check(f"line skip: a left-out line leaves the others as without it (got {_with} vs {_alone})",
      _with[1] == ctc_align.NOT_SUNG and _with[0] == _alone[0] and _with[2] == _alone[1])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
