"""Global CTC forced alignment of a whole lyrics text against a whole vocal track.

About me: repair.py's lyrics mode used to align lyric line by line inside
search windows, which let a repeated line re-match the previous occurrence
and let every later window chain from that wrong position (measured on
110 hand-synced songs: only ~17% of lines within 1 s, and after the first
wrong line ~85% of the following lines stayed wrong). This module instead
aligns ALL words in ONE monotonic pass: wav2vec2 emissions for the whole
track (computed in overlapping chunks), then a single CTC Viterbi over the
concatenated lyrics. The order of the lyrics is enforced by construction,
so repeated lines cannot swap places and an error stays local instead of
cascading.

Everything here is plain torch: the Viterbi is implemented locally because
torchaudio.functional.forced_align is deprecated and removed in torchaudio 2.9.
"""

import math

import torch

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320  # wav2vec2 stride: one emission frame per 20 ms
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
WILDCARD = -1  # token for a letter/digit the model's dictionary does not know


def blank_id_from(dictionary: dict) -> int:
    """The CTC blank id of an aligner dictionary: its [pad]/<pad> entry, else 0."""
    for symbol, code in dictionary.items():
        if symbol in ("[pad]", "<pad>"):
            return code
    return 0


def tokenize_words(words: list, dictionary: dict, blank_id: int):
    """Turn lyric words into CTC tokens, whisperx-style.

    Returns (tokens, word_index): one entry per token; word_index is the
    index into `words`, or -1 for the word-separator token that is placed
    between two words that both have tokens. Characters mapped to the
    blank id and punctuation are skipped; letters/digits the dictionary
    does not know become WILDCARD. A word without any alignable character
    contributes nothing."""
    separator = dictionary.get("|")
    tokens, word_index = [], []
    for wi, word in enumerate(words):
        word_tokens = []
        for ch in word.lower():
            if ch in dictionary:
                if dictionary[ch] != blank_id:
                    word_tokens.append(dictionary[ch])
            elif ch.isalnum():
                word_tokens.append(WILDCARD)
        if not word_tokens:
            continue
        if tokens and separator is not None:
            tokens.append(separator)
            word_index.append(-1)
        tokens.extend(word_tokens)
        word_index.extend([wi] * len(word_tokens))
    return tokens, word_index


def ctc_viterbi(log_probs: torch.Tensor, tokens: list, blank_id: int):
    """Best CTC path forcing `tokens` (in order) through `log_probs` (T, C).

    Returns one (start_frame, end_frame_exclusive, mean_posterior) per token.
    Standard CTC: a token may span several frames, blanks may sit anywhere,
    and two identical neighbouring tokens need a blank between them.
    Raises ValueError when there are too few frames for the tokens."""
    n_tok = len(tokens)
    if n_tok == 0:
        return []
    n_frames = log_probs.shape[0]
    repeats = sum(1 for i in range(1, n_tok) if tokens[i] == tokens[i - 1])
    if n_frames < n_tok + repeats:
        raise ValueError(f"{n_frames} frames cannot hold {n_tok} tokens")

    n_states = 2 * n_tok + 1
    labels = torch.full((n_states,), blank_id, dtype=torch.long)
    labels[1::2] = torch.tensor(tokens, dtype=torch.long)
    allow_skip = torch.zeros(n_states, dtype=torch.bool)
    allow_skip[3::2] = labels[3::2] != labels[1:-2:2]

    neg_inf = float("-inf")
    alpha = torch.full((n_states,), neg_inf)
    alpha[0] = log_probs[0, blank_id]
    alpha[1] = log_probs[0, tokens[0]]
    back = torch.zeros((n_frames, n_states), dtype=torch.int8)
    pad1 = torch.full((1,), neg_inf)
    pad2 = torch.full((2,), neg_inf)
    for t in range(1, n_frames):
        step1 = torch.cat([pad1, alpha[:-1]])
        step2 = torch.where(allow_skip, torch.cat([pad2, alpha[:-2]]), neg_inf)
        best, choice = torch.stack([alpha, step1, step2]).max(0)
        alpha = best + log_probs[t][labels]
        back[t] = choice.to(torch.int8)

    state = n_states - 1 if alpha[n_states - 1] >= alpha[n_states - 2] else n_states - 2
    states = [0] * n_frames
    for t in range(n_frames - 1, -1, -1):
        states[t] = state
        state -= int(back[t, state])

    first = [None] * n_tok
    last = [None] * n_tok
    total = [0.0] * n_tok
    for t, s in enumerate(states):
        if s % 2 == 1:
            k = (s - 1) // 2
            if first[k] is None:
                first[k] = t
            last[k] = t
            total[k] += math.exp(float(log_probs[t, tokens[k]]))
    return [(first[k], last[k] + 1, total[k] / (last[k] + 1 - first[k]))
            for k in range(n_tok)]


def emissions_for_audio(model, meta: dict, audio16k, device: str = "cpu",
                        chunk_s: float = 30.0, ctx_s: float = 3.0) -> torch.Tensor:
    """Log-softmax emissions (T, C) of an aligner model for a whole 16 kHz
    track, computed in `chunk_s` chunks that each see `ctx_s` of context on
    both sides (only the chunk's own frames are kept), so long songs need
    no more memory than a short one."""
    wav = torch.as_tensor(audio16k, dtype=torch.float32)
    if wav.dim() > 1:
        wav = wav[0]
    n = wav.shape[0]
    chunk = max(FRAME_SAMPLES, int(chunk_s * SAMPLE_RATE) // FRAME_SAMPLES * FRAME_SAMPLES)
    ctx = int(ctx_s * SAMPLE_RATE) // FRAME_SAMPLES * FRAME_SAMPLES
    model = model.to(device)
    pieces = []
    try:
        start = 0
        while start < n:
            end = min(n, start + chunk)
            lo, hi = max(0, start - ctx), min(n, end + ctx)
            segment = wav[lo:hi].unsqueeze(0).to(device)
            if segment.shape[-1] < 400:
                segment = torch.nn.functional.pad(segment, (0, 400 - segment.shape[-1]))
            with torch.inference_mode():
                if meta["type"] == "torchaudio":
                    logits, _ = model(segment)
                else:
                    logits = model(segment).logits
                emission = torch.log_softmax(logits, dim=-1)[0].cpu()
            skip = (start - lo) // FRAME_SAMPLES
            keep = math.ceil((end - start) / FRAME_SAMPLES)
            pieces.append(emission[skip:skip + keep])
            start = end
    finally:
        model.to("cpu")
    return torch.cat(pieces, 0)


def align_words_globally(words: list, emission: torch.Tensor, meta: dict) -> list:
    """Word timings from one global alignment of `words` (in order) over
    `emission` (T, C).

    Returns one (start_s, end_s, score) per word, or None for a word with no
    alignable character. score is the mean token posterior (0..1): low
    scores mark stretches the model could not really hear (wordless
    vocals, mumbling) where the placement is a guess."""
    dictionary = meta["dictionary"]
    blank_id = blank_id_from(dictionary)
    tokens, word_index = tokenize_words(words, dictionary, blank_id)
    result = [None] * len(words)
    if not tokens:
        return result
    if WILDCARD in tokens:
        others = torch.ones(emission.shape[1], dtype=torch.bool)
        others[blank_id] = False
        wildcard_col = emission[:, others].max(dim=1).values.unsqueeze(1)
        wildcard_id = emission.shape[1]
        emission = torch.cat([emission, wildcard_col], dim=1)
        tokens = [wildcard_id if t == WILDCARD else t for t in tokens]
    spans = ctc_viterbi(emission, tokens, blank_id)

    scores = {}
    for (start, end, score), wi in zip(spans, word_index):
        if wi < 0:
            continue
        if result[wi] is None:
            result[wi] = [start * FRAME_SECONDS, end * FRAME_SECONDS]
            scores[wi] = [score]
        else:
            result[wi][1] = end * FRAME_SECONDS
            scores[wi].append(score)
    return [(r[0], r[1], sum(scores[i]) / len(scores[i])) if r else None
            for i, r in enumerate(result)]
