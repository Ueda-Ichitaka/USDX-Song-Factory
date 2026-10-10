"""Does a song's lyrics text match what is actually sung?

About me: the worst lyrics errors come from a lyrics source that does not
match the video - a longer album version, a different arrangement, a wrong
song. The aligner then has to place text that is never sung. This compares
the lyric lines with an independent transcript of the vocals (whisper words
with times) and, when too little of the lyrics is heard, writes a "needs
review" note for the finishing report: how much was heard, which lines were
and were not heard, the longest run of unheard lines, and when the singing
ends. A song whose language whisper cannot transcribe (see
language_checkable()) is not judged at all.

Matching: longest common subsequence over words (dynamic programming,
monotonic, so a repeated refrain cannot pair with the wrong repeat); two
words count as the same when their folded letters are at least
WORD_SIMILARITY alike (whisper mishears). A line counts as heard when at
least half of its words were found.

Measured (stack/work/check_divergence.py + divergence_eval.py, 18 songs
labelled from the round 3 notes) with the pipeline's own transcript
(UltraSinger's whisperX, check_divergence_us.py - it hears clearly more than
a separate faster-whisper/Silero run): a note when less than
REVIEW_MIN_HEARD of the lyric words are heard OR REVIEW_MAX_UNHEARD_RUN or
more consecutive lines are not heard flags 4 of 6 lyrics-source problems
and 6 of 24 correct songs (mostly Gaulish and operatic singing whisper
cannot transcribe) - a hint where to look, not an automatic fix.

Pure standard library (host-testable); Cyrillic text is compared with
Cyrillic, and a Latin/Cyrillic mix is transliterated when cyrtranslit is
installed (the image has it).
"""

import difflib
import os
import re
import unicodedata

REVIEW_MIN_HEARD = float(os.environ.get("LYRICS_REVIEW_MIN_HEARD", "0.6"))
REVIEW_MAX_UNHEARD_RUN = int(os.environ.get("LYRICS_REVIEW_MAX_UNHEARD_RUN", "5"))
# the languages whisper can transcribe (faster-whisper 1.2.1
# tokenizer._LANGUAGE_CODES, the version in the image)
WHISPER_LANGUAGES = frozenset(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo "
    "fr gl gu ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo "
    "lt lv mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si "
    "sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo yue zh".split())
WORD_SIMILARITY = 0.7
LINE_HEARD_SHARE = 0.5
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def fold_word(word: str, to_latin: bool = False) -> str:
    """Lower case, accents and punctuation removed, letters of any script
    kept; `to_latin` transliterates Cyrillic first (needs cyrtranslit)."""
    if to_latin and _CYRILLIC.search(word):
        import cyrtranslit
        word = cyrtranslit.to_latin(word, "ru")
    word = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in word if c.isalpha() and not unicodedata.combining(c))


def similar_words(a: str, b: str) -> bool:
    if a == b:
        return True
    if not a or not b or abs(len(a) - len(b)) > max(len(a), len(b)) // 2:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= WORD_SIMILARITY


def _lcs_pairs(a: list, b: list) -> list:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, below = dp[i], dp[i + 1]
        for j in range(m - 1, -1, -1):
            row[j] = below[j + 1] + 1 if similar_words(a[i], b[j]) else max(below[j], row[j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if similar_words(a[i], b[j]) and dp[i][j] == dp[i + 1][j + 1] + 1:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _needs_latin(lines: list, words: list) -> bool:
    lyrics_cyr = any(_CYRILLIC.search(line) for line in lines)
    heard_cyr = any(_CYRILLIC.search(w[2]) for w in words)
    return lyrics_cyr != heard_cyr


def compare_lyrics(lines: list, transcript_words: list) -> dict:
    """Compare lyric lines with transcript words [(start, end, text), ...].

    Returns {"heard": share of lyric words found, "heard_lines": [line
    numbers, 1-based], "unheard_lines": [...], "line_times": [start of the
    first matched word per line or None], "singing_ends": end of the last
    transcript word or None}."""
    to_latin = _needs_latin(lines, transcript_words)
    lyric_words, owner = [], []
    for k, line in enumerate(lines):
        for w in line.split():
            folded = fold_word(w, to_latin)
            if folded:
                lyric_words.append(folded)
                owner.append(k)
    heard_words, heard_index = [], []
    for idx, (_, _, text) in enumerate(transcript_words):
        folded = fold_word(text, to_latin)
        if folded:
            heard_words.append(folded)
            heard_index.append(idx)
    pairs = _lcs_pairs(lyric_words, heard_words)
    hits = [0] * len(lines)
    total = [0] * len(lines)
    times = [None] * len(lines)
    for k in owner:
        total[k] += 1
    for i, j in pairs:
        k = owner[i]
        hits[k] += 1
        start = transcript_words[heard_index[j]][0]
        if times[k] is None:
            times[k] = start
    heard_lines = [k + 1 for k in range(len(lines)) if total[k] and hits[k] / total[k] >= LINE_HEARD_SHARE]
    unheard_lines = [k + 1 for k in range(len(lines)) if total[k] and k + 1 not in heard_lines]
    return {
        "heard": len(pairs) / len(lyric_words) if lyric_words else 0.0,
        "heard_lines": heard_lines,
        "unheard_lines": unheard_lines,
        "longest_unheard_run": _longest_run(unheard_lines),
        "line_times": times,
        "singing_ends": transcript_words[-1][1] if transcript_words else None,
    }


def _longest_run(numbers: list):
    """(first, last) of the longest run of consecutive numbers, or None."""
    best = None
    start = prev = None
    for n in numbers + [None]:
        if n is not None and prev is not None and n == prev + 1:
            prev = n
            continue
        if start is not None and (best is None or prev - start > best[1] - best[0]):
            best = (start, prev)
        start = prev = n
    return best


def language_checkable(raw_language):
    """(True, None) when whisper can transcribe the song list's language
    value (empty means whisper detects it), else (False, reason): a code or
    name whisper does not know (e.g. "Gaulish"), or "mixed" for songs that
    switch language."""
    value = (raw_language or "").strip()
    if not value or value.lower() in WHISPER_LANGUAGES:
        return True, None
    if value.lower() == "mixed":
        return False, "the song list marks the language as 'mixed'"
    return False, f"whisper cannot transcribe the language '{value}'"


def line_ranges(numbers: list) -> str:
    """[1, 2, 3, 7] -> "1-3, 7"; empty -> "-"."""
    if not numbers:
        return "-"
    parts, start, prev = [], numbers[0], numbers[0]
    for n in numbers[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if prev != start else f"{start}")
        if n is not None:
            start = prev = n
    return ", ".join(parts)


def _clock(seconds: float) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:04.1f}"


def review_note(result, min_heard: float = None, max_unheard_run: int = None):
    """The finishing-report note for a compare_lyrics() result, or None when
    enough of the lyrics was heard and no run of max_unheard_run or more
    consecutive lines went unheard (or there is nothing to judge)."""
    min_heard = REVIEW_MIN_HEARD if min_heard is None else min_heard
    max_unheard_run = REVIEW_MAX_UNHEARD_RUN if max_unheard_run is None else max_unheard_run
    if not result:
        return None
    run = result.get("longest_unheard_run")
    long_run = run is not None and run[1] - run[0] + 1 >= max_unheard_run
    if result["heard"] >= min_heard and not long_run:
        return None
    note = (f"lyrics may not match the audio: {result['heard']:.0%} of the lyric words "
            f"were heard (heard: lines {line_ranges(result['heard_lines'])}; "
            f"not heard: lines {line_ranges(result['unheard_lines'])})")
    if long_run:
        note += f"; lines {run[0]}-{run[1]} in a row not heard"
    if result["singing_ends"] is not None:
        note += f"; singing ends at {_clock(result['singing_ends'])}"
    return note
