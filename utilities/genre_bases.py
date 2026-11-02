#!/usr/bin/env python3
"""Breaks genre names down into their bases.

About me: the one place that decides how a genre name becomes bases, shared
by genre_clusters.py (the library's genres) and genre_lookup.py (genres
found online), so both write the same bases:

  "Alternative Metal"      -> Alternative, Metal
  "Symphonic Black Metal"  -> Symphonic, Black, Metal
  "Mittelalter-Rock"       -> Mittelalter, Rock

Rules, in this order:
- split at spaces, hyphens, underscores and slashes;
- names in KEEP_WHOLE stay one base in any spelling ("Hip Hop", "hiphop" ->
  Hip-Hop; "Rock'n'Roll" -> Rock and Roll; "A capella" -> A cappella);
- names in DROPPED are no genre for this library and are dropped (Easy
  Listening, Singer-Songwriter); PHRASES are other bases (Video Game
  Music -> Soundtrack, Tanzmetal -> Dance, Metal, Garage Rock -> Rock);
- FILLER words are dropped ("Electronic Music" -> Electronic);
- TRANSLATIONS and SYNONYMS replace a word (Alternativo -> Alternative,
  Alt. -> Alternative, BSO -> Soundtrack); Mittelalter stays German;
- a one-word genre splits into two halves when both are genres themselves
  (a one-word MusicBrainz genre or a base in the library: "Synthpop" ->
  Synth, Pop when the library has "Synth Pop"), unless it is in
  KEEP_ONE_WORD;
- a typo is a word that is not in the MusicBrainz genre list (as a word or
  as a whole genre without spaces) with exactly one close word (one letter
  off up to 5 letters, two for longer ones) that is in the list and used in
  the library ("Ghotic" -> Gothic);
- last, MERGE turns a base into other bases or none, wherever it came from
  (Electro -> Electronic, also the half of "Electropop"; Medieval ->
  Mittelalter; Gypsy -> dropped). Single songs no rule gets right go into
  genre-song-fixes.tsv (genre_clusters.py, normalize_genres.sh).
A base is written the way most library songs write it (only spellings
that needed no fix count), a base the library does not have as written in
the genre (DISPLAY for acronyms); the first letter is always upper case.
The bases of a genre keep their order, duplicates dropped.
"""

import re

# names that stay one base: written form -> other spellings (lower case,
# spaces and hyphens removed)
KEEP_WHOLE = {
    "Hip-Hop": ["hiphop"],
    "Trip Hop": ["triphop"],
    "R&B": ["r&b", "rnb", "r'n'b", "rhythmandblues", "rhythm&blues"],
    "K-Pop": ["kpop"], "J-Pop": ["jpop"], "J-Rock": ["jrock"], "C-Pop": ["cpop"],
    "Rock and Roll": ["rockandroll", "rock'n'roll", "rock&roll", "rocknroll", "rock'n'roll"],
    "Drum and Bass": ["drumandbass", "drum&bass", "drum'n'bass", "drumnbass", "dnb"],
    "A cappella": ["acappella", "acapella", "acapela"],
    "Neue Deutsche Härte": ["neuedeutschehärte", "neuedeutschehaerte", "ndh"],
    "Neue Deutsche Welle": ["neuedeutschewelle", "ndw"],
    "New Wave": ["newwave"], "New Age": ["newage"],
    "Lo-Fi": ["lofi"], "Hi-NRG": ["hinrg"], "Doo-Wop": ["doowop"],
    "Bossa Nova": ["bossanova"], "Big Band": ["bigband"],
    "EBM": ["ebm", "electronicbodymusic"],
}
# names that are no genre for this library: dropped wherever they appear
# (name -> spellings, lower case, spaces and hyphens removed)
DROPPED = {"Easy Listening": ["easylistening"], "Singer-Songwriter": ["singersongwriter"]}
# names that are other bases (spellings as in KEEP_WHOLE -> bases)
PHRASES = {"videogamemusic": ["soundtrack"], "videogamesoundtrack": ["soundtrack"],
           "videogame": ["soundtrack"], "gamemusic": ["soundtrack"],
           "electrohop": ["electro", "hip-hop"], "grog'nroll": ["pirate"],
           "garagerock": ["rock"], "usmetal": ["metal"], "skatepunk": ["punk"],
           "synthpop": ["pop"], "eurodance": ["dance"], "tanzmetal": ["dance", "metal"],
           "darkwave": ["gothic"], "8bit": ["electronic"]}
# bases that are other bases ([] drops the base), applied last
MERGE = {"electro": ["electronic"], "metalcore": ["metal"],
         "rapcore": ["rap"], "rock and roll": ["rock"], "christmas": ["pop"],
         "vocal": ["pop"], "folklore": ["folk"], "shanty": ["folk"],
         "musical": ["soundtrack"], "ragga": ["reggae"], "medieval": ["mittelalter"],
         "celtic": ["pagan"], "pony": ["children"], "gypsy": [], "freak": []}
# one-word genres that are never split into halves (lower case)
KEEP_ONE_WORD = set()
# words that are no genre on their own
FILLER = {"music", "general", "and", "n", "'n'", "the", "of", "style", "&", "'"}
# words of other languages -> English (Mittelalter stays German)
TRANSLATIONS = {"alternativo": "alternative", "progresivo": "progressive",
                "sinfonico": "symphonic", "sinfónico": "symphonic", "bso": "soundtrack"}
# short forms and other spellings
SYNONYMS = {"alt.": "alternative", "alt": "alternative", "goth": "gothic",
            "soundtracks": "soundtrack"}
# how a base without a library spelling is written, when not just upper case first
DISPLAY = {"ebm": "EBM", "us": "US", "uk": "UK", "edm": "EDM", "idm": "IDM", "nwobhm": "NWOBHM"}
APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})

_KEPT, _DROPPED = {}, {}
for _table, _out in ((KEEP_WHOLE, _KEPT), (DROPPED, _DROPPED)):
    for _name, _others in _table.items():
        for _key in [re.sub(r"[\s\-]+", "", _name.lower())] + _others:
            _out[_key] = _name


def tokens(text: str) -> list:
    """The words of a genre name as written."""
    return [t for t in re.split(r"[\s\-_/]+", (text or "").strip()) if t]


def word_key(word: str) -> str:
    """A word as compared: lower case, every apostrophe as '."""
    return word.lower().translate(APOSTROPHES)


def distance(a: str, b: str) -> int:
    """Edit distance; swapping two neighbouring letters counts as one."""
    d = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _upper_first(word: str) -> str:
    return word[:1].upper() + word[1:]


class Splitter:
    """Breaks genre names into bases against the MusicBrainz genre list and
    the library's own genres ({spelling: songs})."""

    def __init__(self, mb_genres, counts=None, keep_one_word=None):
        counts = counts or {}
        self.keep_one_word = set(KEEP_ONE_WORD if keep_one_word is None else keep_one_word)
        self.mb_words = {word_key(t) for g in mb_genres for t in tokens(g)}
        self.mb_compacts = {"".join(word_key(t) for t in tokens(g)) for g in mb_genres}
        mb_single = {g.strip().lower() for g in mb_genres if len(tokens(g)) == 1}
        library = {k for s in counts for k, written, _f in self._first(s) if written}
        self.known = {w for w in mb_single | library if len(w) >= 3}
        self.library_words = {w for w in library if w in self.mb_words}
        self.written = {}
        for spelling, songs in counts.items():
            for key, written, _fix in self._items(spelling):
                if written:
                    forms = self.written.setdefault(key, {})
                    forms[written] = forms.get(written, 0) + songs

    def _first(self, spelling: str) -> list:
        """(key, written, fix) per word: names kept whole, filler words
        (key None), translations and short forms; written is the word as
        in the spelling when nothing replaced it."""
        words = tokens(spelling)
        lower = [word_key(w) for w in words]
        out, i = [], 0
        while i < len(words):
            for j in range(len(words), i, -1):
                compact = "".join(lower[i:j])
                if compact in _KEPT:
                    out.append((_KEPT[compact].lower(), None, f"kept whole: {_KEPT[compact]}"))
                elif compact in _DROPPED:
                    out.append((None, None, f"dropped: {_DROPPED[compact]}"))
                elif compact in PHRASES:
                    phrase, bases = " ".join(lower[i:j]), PHRASES[compact]
                    out += [(base, None, None) for base in bases]
                    out.append((None, None, f"synonym {phrase} -> {', '.join(bases)}"))
                else:
                    continue
                i = j
                break
            else:
                word = lower[i]
                if word in FILLER:
                    out.append((None, None, f"filler dropped: {word}"))
                elif word in TRANSLATIONS:
                    out.append((TRANSLATIONS[word], None, f"translation {word} -> {TRANSLATIONS[word]}"))
                elif word in SYNONYMS:
                    out.append((SYNONYMS[word], None, f"synonym {word} -> {SYNONYMS[word]}"))
                else:
                    out.append((word, words[i], None))
                i += 1
        return out

    def _halves(self, word: str):
        if word in self.keep_one_word:
            return None
        found = [(word[:i], word[i:]) for i in range(3, len(word) - 2)
                 if word[:i] in self.known and word[i:] in self.known]
        return found[0] if len(found) == 1 else None

    def _typo(self, word: str) -> str:
        if (len(word) < 4 or not word.isalpha() or word in self.mb_words
                or word in self.mb_compacts):
            return ""
        limit = 1 if len(word) <= 5 else 2
        close = [w for w in self.library_words
                 if abs(len(w) - len(word)) <= limit and distance(word, w) <= limit]
        return close[0] if len(close) == 1 else ""

    def _items(self, spelling: str) -> list:
        """_first() plus one-word splits and typo fixes."""
        out = []
        for key, written, fix in self._first(spelling):
            if written and key not in self.keep_one_word:
                halves = self._halves(key)
                if halves:
                    out += [(h, None, None) for h in halves]
                    out.append((None, None, f"compound {key} -> {halves[0]} + {halves[1]}"))
                    continue
                typo = self._typo(key)
                if typo:
                    out.append((typo, None, f"typo? {key} -> {typo}"))
                    continue
            out.append((key, written, fix))
        return out

    def display(self, key: str, written: str = "") -> str:
        """How a base is written: a name kept whole as listed, else the way
        most library songs write it, else DISPLAY, else as written here
        (or the key); the first letter always upper case."""
        kept = _KEPT.get(re.sub(r"[\s\-]+", "", key))
        if kept:
            return kept
        forms = self.written.get(key)
        if forms:
            return _upper_first(min(forms, key=lambda w: (-forms[w], not w[:1].isupper(), w)))
        return DISPLAY.get(key) or _upper_first(written or key)

    def explain(self, spelling: str) -> tuple:
        """(bases, fixes) of one genre name."""
        keys, fixes = {}, []
        for key, written, fix in self._items(spelling):
            if fix:
                fixes.append(fix)
            if key in MERGE:
                targets = MERGE[key]
                fixes.append(f"merged {key} -> {', '.join(targets)}" if targets else f"dropped: {key}")
                for target in targets:
                    keys.setdefault(target, "")
            elif key:
                keys.setdefault(key, written or "")
        return [self.display(k, w) for k, w in keys.items()], fixes

    def bases(self, spelling: str) -> list:
        return self.explain(spelling)[0]

    def value(self, value: str) -> list:
        """The bases of a whole #GENRE value (comma-separated genres), in
        order, duplicates dropped."""
        out = []
        for part in (value or "").split(","):
            for base in self.bases(part):
                if base.lower() not in (o.lower() for o in out):
                    out.append(base)
        return out
