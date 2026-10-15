"""Lyrics that are sung but missing from the lyrics text, found by whisper.

About me: online lyrics often write a repeated chorus line fewer times than
it is sung (Die Funkhausgruppe - Die Physiker: "Technik, Chemie, Atomkraft,
Physik" sung four times, written three times). The global alignment then
spreads one written line over two sung ones. This module compares the
lyrics with whisper's transcript of the same song (for a new song: the
txt UltraSinger made) and proposes the missing lines, in text order:

  1. the lyric words are matched to the transcript words (affine_pairs():
     monotonic, fuzzy word equality from lyrics_divergence, and every run
     of unmatched words costs once - so the unmatched words of an extra
     repeat stay together instead of being spread over all repeats)
  2. a run of at least `min_run` unmatched transcript words between the
     matches of two different lyric lines is singing the text lacks; it
     becomes copies of lyric lines it matches (segment_run()), else
     whisper's own words (at least `min_whisper_words`; not before the
     first or after the last lyric line - there they are a video's spoken
     intro or outro), else a span for review (user decision 2026-10-14:
     fill automatically, mark for review only as the last resort)
  3. limits against whisper's own errors: at most `max_copies` copies of
     one line per gap, and words in a whisper loop (`loop_min` or more
     identical words in a row, e.g. eighty "hello") are left out of the
     comparison

repair.py aligns the proposed lines as OPTIONAL lines (ctc_align line
skips): a proposal that is not really sung is left out by the alignment.
Measured on 93 hand-synced songs (stack/work/bench/, with the filler and
LYRICS_FILL_OPTIONAL_COST 2): lyric lines left out on purpose
(v09_fillbench.py) - 42-53% of the left-out words come back, 94-100% of
them within 1 s; words within 1 s +0.1 (complete lyrics) to +0.5 points
(four lines missing). With the real online lyrics a new song gets
(v09_realbench.py): words within 1 s 86.1% -> 86.3%, 5 songs better (up to
+11 points), 2 worse (a misheard whisper line), 86 unchanged.

Pure standard library (host-testable).
"""

import lyrics_divergence as ld


def affine_pairs(a: list, b: list, gap_open: float = 2.0) -> list:
    """Monotonic matching of two folded word lists: one point per matched
    pair (lyrics_divergence.similar_words), minus `gap_open` for every run
    of skipped words in a or in b. Returns [(i, j), ...] in order.

    V[s][i][j] is the best score from (i, j) on, s = 0 not skipping,
    1 skipping in a, 2 skipping in b."""
    n, m = len(a), len(b)
    if not n or not m:
        return []
    sim = [[ld.similar_words(x, y) for y in b] for x in a]
    neg = float("-inf")
    V = [[[0.0] * (m + 1) for _ in range(n + 1)] for _ in range(3)]
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i == n and j == m:
                continue
            match = 1.0 + V[0][i + 1][j + 1] if i < n and j < m and sim[i][j] else neg
            skip_a = V[1][i + 1][j] if i < n else neg
            skip_b = V[2][i][j + 1] if j < m else neg
            V[0][i][j] = max(match, skip_a - gap_open, skip_b - gap_open)
            V[1][i][j] = max(match, skip_a, skip_b - gap_open)
            V[2][i][j] = max(match, skip_a - gap_open, skip_b)
    pairs, i, j, s = [], 0, 0, 0
    while i < n or j < m:
        here = V[s][i][j]
        if i < n and j < m and sim[i][j] and abs(1.0 + V[0][i + 1][j + 1] - here) < 1e-9:
            pairs.append((i, j))
            i, j, s = i + 1, j + 1, 0
        elif i < n and abs(V[1][i + 1][j] - (0.0 if s == 1 else gap_open) - here) < 1e-9:
            i, s = i + 1, 1
        else:
            j, s = j + 1, 2
    return pairs


def _share_in(line_words: list, run_words: list):
    """(share of the line's words found in order in run_words, index after
    the last matched run word, number of DIFFERENT line words matched)."""
    a = [ld.fold_word(w) for w in line_words if ld.fold_word(w)]
    b = [ld.fold_word(w) for w in run_words]
    if not a:
        return 0.0, 0, 0
    pairs = ld._lcs_pairs(a, b)
    distinct = len({a[i] for i, _ in pairs})
    return len(pairs) / len(a), (pairs[-1][1] + 1 if pairs else 0), distinct


def segment_run(run_words: list, lines_words: list, min_share: float = 0.6) -> list:
    """Cover a run of transcript words with copies of lyric lines, greedily
    from the left: [("repeat", line index) | ("whisper", [words]), ...]. A
    copy must match at least `min_share` of the line's words, at least 3 of
    its different words (all of them for a line with fewer) and start at
    the run's first or second word."""
    out, pos, loose = [], 0, []
    while pos < len(run_words):
        best, best_i, best_end = 0.0, None, 0
        head = [ld.fold_word(w) for w in run_words[pos:pos + 2] if ld.fold_word(w)]
        for i, line in enumerate(lines_words):
            share, end, distinct = _share_in(line, run_words[pos:pos + len(line) + 2])
            # enough DIFFERENT words of the line: whisper looping on one word
            # ("hello hello hello ...") is no copy of "Hello, hello, hello, how low"
            needed = min(3, len({ld.fold_word(w) for w in line if ld.fold_word(w)}))
            if share < min_share or distinct < needed:
                continue
            first = [ld.fold_word(w) for w in line[:2] if ld.fold_word(w)]
            if not any(ld.similar_words(h, f) for h in head for f in first):
                continue
            if share * len(line) > best:
                best, best_i, best_end = share * len(line), i, end
        if best_i is None:
            loose.append(run_words[pos])
            pos += 1
            continue
        if loose:
            out.append(("whisper", loose))
            loose = []
        out.append(("repeat", best_i))
        pos += max(1, best_end)
    if loose:
        out.append(("whisper", loose))
    return out


def _loop_words(folded: list, loop_min: int) -> set:
    """Indices of words inside a run of at least `loop_min` identical words
    - whisper repeating itself (dozens of "hello" in a row), not singing."""
    out, start = set(), 0
    for k in range(1, len(folded) + 1):
        if k == len(folded) or folded[k] != folded[start]:
            if folded[start] and k - start >= loop_min:
                out.update(range(start, k))
            start = k
    return out


def candidate_lines(lines_words: list, transcript: list, min_run: int = 3,
                    min_whisper_words: int = 3, gap_open: float = 2.0,
                    loop_min: int = 6, max_copies: int = 2):
    """The lyrics (word lists per line) with the proposed missing lines put
    in, from `transcript` [(start_s, end_s, word), ...]. Returns (lines,
    origin per line: the original line index, ("repeat", line index) or
    ("whisper", text), review spans [(start_s, end_s), ...] that could not
    be filled)."""
    origin = list(range(len(lines_words)))
    lines = [list(line) for line in lines_words]
    if not transcript:
        return lines, origin, []
    flat, owner = [], []
    for li, line in enumerate(lines_words):
        for w in line:
            folded = ld.fold_word(w)
            if folded:
                flat.append(folded)
                owner.append(li)
    folded_all = [ld.fold_word(text) for _, _, text in transcript]
    looping = _loop_words(folded_all, loop_min)
    heard, heard_index = [], []
    for k, folded in enumerate(folded_all):
        if folded and k not in looping:
            heard.append(folded)
            heard_index.append(k)
    line_of_heard = {j: owner[i] for i, j in affine_pairs(flat, heard, gap_open)}
    inserts, review, run, prev_line = {}, [], [], None
    for j in range(len(heard) + 1):
        if j < len(heard) and j not in line_of_heard:
            run.append(j)
            continue
        next_line = line_of_heard.get(j) if j < len(heard) else None
        # a run inside one line's matches is mishearing, not missing text
        between = prev_line is None or next_line is None or next_line > prev_line
        if len(run) >= min_run and between:
            before = next_line if next_line is not None else len(lines_words)
            run_words = [transcript[heard_index[k]][2].strip() for k in run]
            # before the first / after the last lyric line whisper's own words
            # are a video's spoken intro or outro, not missing lyrics
            outside = prev_line is None or next_line is None
            entries, copies = [], {}
            for kind, value in segment_run(run_words, lines_words):
                if kind == "repeat":
                    copies[value] = copies.get(value, 0) + 1
                    if copies[value] <= max_copies:
                        entries.append((list(lines_words[value]), ("repeat", value)))
                elif len(value) >= min_whisper_words and not outside:
                    entries.append((list(value), ("whisper", " ".join(value))))
            if entries:
                inserts.setdefault(before, []).extend(entries)
            else:
                review.append((transcript[heard_index[run[0]]][0],
                               transcript[heard_index[run[-1]]][1]))
        run = []
        if next_line is not None:
            prev_line = next_line
    new_lines, new_origin = [], []
    for b in range(len(lines_words) + 1):
        for words, org in inserts.get(b, []):
            new_lines.append(words)
            new_origin.append(org)
        if b < len(lines_words):
            new_lines.append(lines[b])
            new_origin.append(b)
    return new_lines, new_origin, review
