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

With `end_penalty` the path may end after any word: lyrics that are never
sung at the end (a video that fades out before the album lyrics end) are
left out (NOT_SUNG) instead of being squeezed into the audio, each token
left out costing `end_penalty`. Measured on 110 hand-synced songs
(stack/work/bench/bench26_tail.py): audio cut at 70% of the song - sung
words within 0.3 s 52.5% -> 80.4%, 96% of the unsung words left out; whole
songs unchanged (77.5% -> 77.6%), 0.6% of the sung words left out, mostly
wordless "ah ah" endings the forced path misplaced anyway.

Singing that the lyrics do not have (a chorus the lyrics source writes
once but the singer repeats) goes to a filler between two lines instead of
stretching the neighbouring words over it (ctc_viterbi()). Measured on 93
hand-synced songs with four lyric lines left out (stack/work/bench/
v09_mismatch.py): the word before the gap ended 4.6 s late (median) without
and 0.2 s with the filler; words within 1 s 88.1% -> 90.6%, and with the
complete lyrics 93.6% -> 94.0%.

Everything here is plain torch: the Viterbi is implemented locally because
torchaudio.functional.forced_align is deprecated and removed in torchaudio 2.9.
"""

import math

import torch

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320  # wav2vec2 stride: one emission frame per 20 ms
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
WILDCARD = -1  # token for a letter/digit the model's dictionary does not know
NOT_SUNG = "not sung"  # a word the path left out at the end (end_penalty)


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


def _end_state(alpha, word_index, end_penalty) -> int:
    """The state the best path ends in. Forced (end_penalty None): the last
    token or the blank after it. Otherwise the end of any word (its last
    token or the blank after it), less end_penalty per token left out."""
    return _end_state_value(alpha, word_index, end_penalty)[0]


def _end_state_value(alpha, word_index, end_penalty):
    """(_end_state(), the path's score when it ends there)."""
    n_tok = len(word_index)
    last = 2 * n_tok
    if end_penalty is None:
        state = last if alpha[last] >= alpha[last - 1] else last - 1
        return state, float(alpha[state])
    best, best_state = -math.inf, last
    for k in range(n_tok):
        if word_index[k] < 0 or (k + 1 < n_tok and word_index[k + 1] >= 0):
            continue  # a separator, or the next token belongs to the same word
        cost = end_penalty * (n_tok - 1 - k)
        for state in (2 * k + 1, 2 * k + 2):
            value = float(alpha[state]) - cost
            if value > best:
                best, best_state = value, state
    return best_state, best


def ctc_viterbi(log_probs: torch.Tensor, tokens: list, blank_id: int,
                end_penalty: float = None, word_index: list = None,
                line_ranges: list = None, filler_beta: float = None,
                filler_enter: float = None, fillers_out: list = None,
                line_skip: list = None, skip_max: int = 4):
    """Best CTC path forcing `tokens` (in order) through `log_probs` (T, C).

    Returns one (start_frame, end_frame_exclusive, mean_posterior) per token.
    Standard CTC: a token may span several frames, blanks may sit anywhere,
    and two identical neighbouring tokens need a blank between them.
    With `end_penalty` (and `word_index` from tokenize_words()) the path may
    end after any word; the tokens after it get None.

    Filler (`line_ranges` = [(first_token, last_token), ...] per lyric line
    in order, plus `filler_beta` and `filler_enter`): before every line and
    after the last one an extra state may take frames between the end of
    the previous line and the start of the next - singing the lyrics do not
    have (the path may end in the one after the last line). Entering it
    costs filler_enter once; in it a blank frame costs nothing extra and a
    letter frame scores its best letter less filler_beta. Without it the
    path covers such singing by spreading the neighbouring words' letters
    over it (one word "very slow", then the next lines rushed). Each used
    filler is appended to `fillers_out` as (first_frame, end_frame_exclusive,
    position of the line it precedes in line_ranges; len(line_ranges) for
    the one after the last line).

    Line skips (`line_ranges` plus `line_skip` = per line a cost per token,
    or None for a line that must be placed): the path may jump from the end
    of one line over up to `skip_max` lines to the start of a later one
    (also at the very start), paying each skipped line's cost per token;
    the skipped tokens get None. Cheap costs make optional lines -
    candidate text that is used only where it is sung.
    Raises ValueError when there are too few frames for the tokens."""
    n_tok = len(tokens)
    if n_tok == 0:
        return []
    n_frames, n_cls = log_probs.shape
    repeats = sum(1 for i in range(1, n_tok) if tokens[i] == tokens[i - 1])
    if n_frames < n_tok + repeats:
        raise ValueError(f"{n_frames} frames cannot hold {n_tok} tokens")
    if end_penalty is not None and word_index is None:
        raise ValueError("end_penalty needs word_index")

    n_states = 2 * n_tok + 1
    labels = torch.full((n_states,), blank_id, dtype=torch.long)
    labels[1::2] = torch.tensor(tokens, dtype=torch.long)
    allow_skip = torch.zeros(n_states, dtype=torch.bool)
    allow_skip[3::2] = labels[3::2] != labels[1:-2:2]
    neg_inf = float("-inf")

    use_fill = bool(line_ranges) and filler_beta is not None and filler_enter is not None
    use_skip = bool(line_ranges) and line_skip is not None and any(c is not None for c in line_skip)
    n_lines = len(line_ranges) if line_ranges else 0
    n_fill = n_lines + 1 if use_fill else 0
    if use_fill or use_skip:
        first_tok = torch.tensor([r[0] for r in line_ranges], dtype=torch.long)
        last_tok = torch.tensor([r[1] for r in line_ranges], dtype=torch.long)
        start_state = 2 * first_tok           # blank before the line's first token
        end_state = 2 * last_tok + 2          # blank after the line's last token
        lastchar_state = 2 * last_tok + 1
    if use_skip:
        # leaving out line k costs line_cost[k]; a jump to line j comes from the end of line j-m-1
        line_cost = [math.inf if c is None else c * (r[1] - r[0] + 1)
                     for c, r in zip(line_skip, line_ranges)]
        sk_tgt, sk_src, sk_cost, sk_code = [], [], [], []
        for j in range(1, n_lines):
            for m in range(1, skip_max + 1):
                i = j - m - 1
                if i < 0:
                    break
                cost = sum(line_cost[i + 1:j])
                if math.isfinite(cost):
                    sk_tgt.append(j)
                    sk_src.append(int(end_state[i]))
                    sk_cost.append(cost)
                    sk_code.append(3 + m)
        sk_tgt = torch.tensor(sk_tgt, dtype=torch.long)
        sk_src = torch.tensor(sk_src, dtype=torch.long)
        sk_cost = torch.tensor(sk_cost, dtype=torch.float)
        sk_code = torch.tensor(sk_code, dtype=torch.int8)
        # a jump to line j lands on the blank after line j-1, so the word
        # separator before line j is still passed - a left-out line must not
        # make the junction of its neighbours cheaper than without it
        has_sep = [j > 0 and word_index is not None and word_index[int(first_tok[j]) - 1] < 0
                   for j in range(n_lines)]
        jump_state = torch.tensor([int(end_state[j - 1]) if has_sep[j] else int(start_state[j])
                                   for j in range(n_lines)], dtype=torch.long)
        line_at_jump = {int(jump_state[j]): j for j in range(1, n_lines)}
    if use_fill:
        others = torch.ones(n_cls, dtype=torch.bool)
        others[blank_id] = False
        filler_em = torch.maximum(log_probs[:, blank_id],
                                  log_probs[:, others].max(dim=1).values - filler_beta)
        head = torch.full((1,), neg_inf)

    alpha = torch.full((n_states + n_fill,), neg_inf)
    alpha[0] = log_probs[0, blank_id]
    alpha[1] = log_probs[0, tokens[0]]
    if use_fill:
        alpha[n_states] = filler_em[0] - filler_enter  # filler before the first line
    if use_skip:  # start at line j, leaving out lines 0..j-1
        for j in range(1, min(n_lines, skip_max + 1)):
            cost = sum(line_cost[:j])
            if not math.isfinite(cost):
                break
            alpha[int(start_state[j])] = log_probs[0, blank_id] - cost
    back = torch.zeros((n_frames, n_states + n_fill), dtype=torch.int8)
    pad1 = torch.full((1,), neg_inf)
    pad2 = torch.full((2,), neg_inf)
    for t in range(1, n_frames):
        base = alpha[:n_states]
        step1 = torch.cat([pad1, base[:-1]])
        step2 = torch.where(allow_skip, torch.cat([pad2, base[:-2]]), neg_inf)
        best, choice = torch.stack([base, step1, step2]).max(0)
        new = torch.empty_like(alpha)
        choice = choice.to(torch.int8)
        if use_skip and len(sk_tgt):
            # best jump per target line; equal-valued jumps are all valid back pointers
            vals = alpha[sk_src] - sk_cost
            top = torch.full((n_lines,), neg_inf).scatter_reduce(0, sk_tgt, vals, "amax")
            better = top > best[jump_state]
            if bool(better.any()):
                sel = (vals == top[sk_tgt]) & better[sk_tgt]
                best[jump_state[sk_tgt[sel]]] = vals[sel]
                choice[jump_state[sk_tgt[sel]]] = sk_code[sel]
        if use_fill:
            # filler b < n_lines hands over to the start of line b (choice 3)
            from_fill = alpha[n_states:]
            better = from_fill[:n_lines] > best[start_state]
            if bool(better.any()):
                best[start_state[better]] = from_fill[:n_lines][better]
                choice[start_state[better]] = 3
            # filler b: stay, or enter from the end of line b-1 (blank after / last letter)
            enter_blank = torch.cat([head, alpha[end_state]]) - filler_enter
            enter_char = torch.cat([head, alpha[lastchar_state]]) - filler_enter
            fbest, fchoice = torch.stack([from_fill, enter_blank, enter_char]).max(0)
            new[n_states:] = fbest + filler_em[t]
            back[t, n_states:] = fchoice.to(torch.int8)
        new[:n_states] = best + log_probs[t][labels]
        back[t, :n_states] = choice.to(torch.int8)
        alpha = new

    state, value = _end_state_value(
        alpha[:n_states], word_index if end_penalty is not None else [0] * n_tok, end_penalty)
    if use_fill and float(alpha[n_states + n_lines]) > value:
        state = n_states + n_lines  # ends in the filler after the last line
    line_at_start = ({int(s): b for b, s in enumerate(start_state.tolist())}
                     if use_fill or use_skip else {})
    states = [0] * n_frames
    for t in range(n_frames - 1, -1, -1):
        states[t] = state
        if t == 0:
            break
        c = int(back[t, state])
        if state >= n_states:  # in filler b
            b = state - n_states
            if c == 1:
                state = int(end_state[b - 1])
            elif c == 2:
                state = int(lastchar_state[b - 1])
        elif c == 3:
            state = n_states + line_at_start[state]
        elif c > 3:  # jumped over c - 3 lines
            state = int(end_state[line_at_jump[state] - (c - 3) - 1])
        else:
            state -= c

    first = [None] * n_tok
    last = [None] * n_tok
    total = [0.0] * n_tok
    fills = {}
    for t, s in enumerate(states):
        if s >= n_states:
            fills.setdefault(s - n_states, [t, t + 1])[1] = t + 1
        elif s % 2 == 1:
            k = (s - 1) // 2
            if first[k] is None:
                first[k] = t
            last[k] = t
            total[k] += math.exp(float(log_probs[t, tokens[k]]))
    if fillers_out is not None:
        fillers_out.extend((f0, f1, b) for b, (f0, f1) in sorted(fills.items()))
    return [(first[k], last[k] + 1, total[k] / (last[k] + 1 - first[k]))
            if first[k] is not None else None for k in range(n_tok)]


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


def _line_token_ranges(line_lengths: list, word_index: list):
    """[(first_token, last_token), ...] and the line number of each, for
    the lines (given as word counts) that have at least one token."""
    owner = []
    for li, n in enumerate(line_lengths):
        owner.extend([li] * n)
    ranges = {}
    for ti, wi in enumerate(word_index):
        if wi >= 0:
            li = owner[wi]
            ranges.setdefault(li, [ti, ti])[1] = ti
    lines = sorted(ranges)
    return [tuple(ranges[li]) for li in lines], lines


def align_words_globally(words: list, emission: torch.Tensor, meta: dict,
                         end_penalty: float = None, line_lengths: list = None,
                         filler_beta: float = None, filler_enter: float = None,
                         fillers_out: list = None, line_skip: list = None) -> list:
    """Word timings from one global alignment of `words` (in order) over
    `emission` (T, C).

    Returns one (start_s, end_s, score) per word, None for a word with no
    alignable character, or NOT_SUNG for a word the path left out at the
    end (only with `end_penalty`, see ctc_viterbi()). score is the mean
    token posterior (0..1): low scores mark stretches the model could not
    really hear (wordless vocals, mumbling) where the placement is a guess.

    With `line_lengths` (words per lyric line, summing to len(words)),
    `filler_beta` and `filler_enter`, singing between lines that the lyrics
    do not have goes to a filler (see ctc_viterbi()); each used filler is
    appended to `fillers_out` as (start_s, end_s, number of the line it
    precedes - len(line_lengths) after the last line). `line_skip` (per
    line a cost per letter, or None) lets the path leave lines out (see
    ctc_viterbi()); their words come back as NOT_SUNG."""
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
    line_ranges = line_numbers = skips = None
    use_fill = filler_beta is not None and filler_enter is not None
    if line_lengths and (use_fill or line_skip):
        line_ranges, line_numbers = _line_token_ranges(line_lengths, word_index)
        if line_skip:
            skips = [line_skip[li] for li in line_numbers]
    frames = []
    spans = ctc_viterbi(emission, tokens, blank_id, end_penalty=end_penalty,
                        word_index=word_index, line_ranges=line_ranges,
                        filler_beta=filler_beta if use_fill else None,
                        filler_enter=filler_enter if use_fill else None,
                        fillers_out=frames, line_skip=skips)
    if fillers_out is not None:
        fillers_out.extend((f0 * FRAME_SECONDS, f1 * FRAME_SECONDS,
                            line_numbers[b] if b < len(line_numbers) else len(line_lengths))
                           for f0, f1, b in frames)

    scores = {}
    for span, wi in zip(spans, word_index):
        if wi < 0:
            continue
        if span is None:
            result[wi] = NOT_SUNG
            continue
        start, end, score = span
        if result[wi] is None:
            result[wi] = [start * FRAME_SECONDS, end * FRAME_SECONDS]
            scores[wi] = [score]
        else:
            result[wi][1] = end * FRAME_SECONDS
            scores[wi].append(score)
    return [r if r is None or r == NOT_SUNG else (r[0], r[1], sum(scores[i]) / len(scores[i]))
            for i, r in enumerate(result)]
