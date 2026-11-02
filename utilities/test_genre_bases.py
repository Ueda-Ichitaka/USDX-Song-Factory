#!/usr/bin/env python3
"""Tests for genre_bases.py.

About me: plain assert-based checks like the other utility tests; the
MusicBrainz genre list is a small fake, no network. Run:
python3 test_genre_bases.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import genre_bases as gb  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


MB = ["alternative metal", "black metal", "symphonic black metal", "gothic rock", "medieval rock",
      "metal", "rock", "pop", "punk", "funk", "wave", "electro", "dance", "synth-pop", "hip hop",
      "thrash metal", "gothic metal", "electropop", "metalcore", "hardcore punk", "electronic",
      "italo disco", "disco", "k-pop", "r&b", "heavy metal", "hard rock", "folk metal"]
COUNTS = {"Alternative Metal": 2, "Mittelalter-Rock": 4, "Symphonic Black Metal": 1, "Folk metal": 3,
          "Dark Rock": 1, "Synth Pop": 1, "Ghotic Metal": 1, "Trash Metal": 1, "Thrash Metal": 11,
          "Gothic Metal": 11, "US Metal": 1}
lib = gb.Splitter(MB, COUNTS)


def b(spelling, splitter=lib):
    return splitter.bases(spelling)


# --------------------------------------------------------------------------
# splitting at spaces, hyphens and slashes
# --------------------------------------------------------------------------

check(f"bases: words become bases (got {b('Alternative Metal')}, {b('Symphonic Black Metal')})",
      b("Alternative Metal") == ["Alternative", "Metal"]
      and b("Symphonic Black Metal") == ["Symphonic", "Black", "Metal"]
      and b("black metal") == ["Black", "Metal"])
check(f"bases: Mittelalter stays German (got {b('Mittelalter')}, {b('Mittelalter-Rock')})",
      b("Mittelalter") == ["Mittelalter"] and b("Mittelalter-Rock") == ["Mittelalter", "Rock"]
      and b("Medieval Rock") == ["Medieval", "Rock"])
check(f"bases: a base keeps its apostrophe as written (got {b('Grog’n Roll')})",
      b("Grog’n Roll") == ["Grog’n", "Roll"])
check(f"bases: slashes split (got {b('Gothic/Doom Metal')})",
      b("Gothic/Doom Metal") == ["Gothic", "Doom", "Metal"])

# --------------------------------------------------------------------------
# names kept whole
# --------------------------------------------------------------------------

check(f"bases: Hip-Hop in any spelling stays one base (got {b('Hip Hop')}, {b('hiphop')})",
      b("Hip Hop") == b("hip-hop") == b("Hiphop") == ["Hip-Hop"]
      and b("Ragga hip-Hop") == ["Ragga", "Hip-Hop"])
check(f"bases: Rock and Roll in its spellings (got {b('Rock’n’Roll')}, {b('rock & roll')})",
      b("Rock'n'Roll") == b("Rock’n’Roll") == b("rock & roll") == b("Rock and Roll") == ["Rock and Roll"])
check(f"bases: A cappella, also misspelled (got {b('A capella')}, {b('Acapella')})",
      b("A capella") == b("Acapella") == ["A cappella"])
check(f"bases: K-Pop, R&B, Hi-NRG, EBM (got {b('k-pop')}, {b('R&B')}, {b('Hi-NRG')}, {b('electronic body music')})",
      b("k-pop") == ["K-Pop"] and b("R&B") == ["R&B"] and b("Hi-NRG") == ["Hi-NRG"]
      and b("electronic body music") == ["EBM"] and b("ebm") == ["EBM"])

check(f"bases: Easy Listening and Singer-Songwriter are dropped (got {b('Easy Listening')}, {b('singer/songwriter')})",
      b("Easy Listening") == [] and b("easy-listening") == [] and b("Singer-Songwriter") == []
      and b("singer/songwriter") == [] and lib.value("Pop, Easy Listening") == ["Pop"]
      and "dropped: Easy Listening" in lib.explain("Easy Listening")[1])
check(f"bases: video game music is Soundtrack (got {b('Video Game Music')})",
      b("Video Game Music") == ["Soundtrack"] and b("video game soundtrack") == ["Soundtrack"]
      and "synonym video game music -> soundtrack" in lib.explain("Video Game Music")[1])

# --------------------------------------------------------------------------
# translations, short forms, filler words, typos
# --------------------------------------------------------------------------

check(f"bases: translations and short forms (got {b('Metal Alternativo')}, {b('Alt. Rock')}, {b('BSO')})",
      b("Metal Alternativo") == ["Metal", "Alternative"] and b("Alt. Rock") == ["Alternative", "Rock"]
      and b("BSO") == ["Soundtrack"] and b("Goth rock") == ["Gothic", "Rock"]
      and b("Power Metal Sinfonico") == ["Power", "Metal", "Symphonic"])
check(f"bases: filler words are dropped (got {b('Electronic Music')}, {b('General Heavy Metal')}, {b('Hard Rock & Metal')})",
      b("Electronic Music") == ["Electronic"] and b("General Heavy Metal") == ["Heavy", "Metal"]
      and b("Hard Rock & Metal") == ["Hard", "Rock", "Metal"])
check(f"bases: typos fixed with a close library word (got {b('Ghotic Metal')}, {b('Trash Metal')})",
      b("Ghotic Metal") == ["Gothic", "Metal"] and b("Trash Metal") == ["Thrash", "Metal"])

# --------------------------------------------------------------------------
# one-word genres: split when both halves are genres themselves
# --------------------------------------------------------------------------

check(f"bases: halves that are genres split (got {b('Synthpop')}, {b('Darkwave')}, {b('Electropop')})",
      b("Synthpop") == ["Synth", "Pop"] and b("Darkwave") == ["Dark", "Wave"]
      and b("Electropop") == ["Electro", "Pop"])
check(f"bases: a half that is no genre keeps the word (got {b('Metalcore')}, {b('Hardcore')}, {b('Electronic')})",
      b("Metalcore") == ["Metalcore"] and b("Hardcore") == ["Hardcore"] and b("Electronic") == ["Electronic"]
      and b("Tanzmetal") == ["Tanzmetal"])
_keep = gb.Splitter(MB, COUNTS, keep_one_word={"electropop"})
check(f"bases: the keep list stops a split (got {b('Electropop', _keep)})",
      b("Electropop", _keep) == ["Electropop"] and b("Synthpop", _keep) == ["Synth", "Pop"])

# --------------------------------------------------------------------------
# how a base is written
# --------------------------------------------------------------------------

check(f"bases: the spelling most library songs use (got {b('folk metal')}, {b('US Metal')})",
      b("folk metal") == ["Folk", "Metal"] and b("US Metal") == ["US", "Metal"])
_lower = gb.Splitter(MB, {"Gypsy jazz": 2})
check(f"bases: a base the library writes only in lower case starts upper case (got {b('Gypsy jazz', _lower)})",
      b("Gypsy jazz", _lower) == ["Gypsy", "Jazz"])
_bare = gb.Splitter(MB)
check(f"bases: without a library words start upper case (got {b('power metal', _bare)}, {b('italo disco', _bare)})",
      b("power metal", _bare) == ["Power", "Metal"] and b("italo disco", _bare) == ["Italo", "Disco"]
      and b("hardcore punk", _bare) == ["Hardcore", "Punk"] and b("us metal", _bare) == ["US", "Metal"])

# --------------------------------------------------------------------------
# value(): a whole #GENRE value, explain(): why
# --------------------------------------------------------------------------

check(f"value: bases in order of appearance, duplicates dropped (got {lib.value('Gothic Rock, Symphonic Black Metal, Gothic Metal')})",
      lib.value("Gothic Rock, Symphonic Black Metal, Gothic Metal")
      == ["Gothic", "Rock", "Symphonic", "Black", "Metal"])
_why = lib.explain("Metal Alternativo")[1]
check(f"explain: the fixes are named (got {_why}, {lib.explain('Synthpop')[1]}, {lib.explain('Electronic Music')[1]})",
      "translation alternativo -> alternative" in _why
      and "compound synthpop -> synth + pop" in lib.explain("Synthpop")[1]
      and "filler dropped: music" in lib.explain("Electronic Music")[1]
      and "typo? ghotic -> gothic" in lib.explain("Ghotic Metal")[1]
      and "kept whole: Hip-Hop" in lib.explain("Hip Hop")[1])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
