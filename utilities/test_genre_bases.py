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
      b("Mittelalter") == ["Mittelalter"] and b("Mittelalter-Rock") == ["Mittelalter", "Rock"])
check(f"bases: a base keeps its apostrophe as written (got {b('Pirate’s Folk')})",
      b("Pirate’s Folk") == ["Pirate’s", "Folk"])
check(f"bases: Grog’n Roll is Pirate (got {b('Grog’n Roll')}, {b(chr(71) + 'rog' + chr(39) + 'n Roll')})",
      b("Grog’n Roll") == b("Grog'n Roll") == ["Pirate"])
check(f"bases: Electro Hop -> Electronic, Hip-Hop (got {b('Electro Hop')}, {b('Electrohop')})",
      b("Electro Hop") == b("Electrohop") == b("electro-hop") == ["Electronic", "Hip-Hop"]
      and "synonym electro hop -> electro, hip-hop" in lib.explain("Electro Hop")[1])

# --------------------------------------------------------------------------
# MERGE: bases that are another base (or none); whole names in PHRASES
# --------------------------------------------------------------------------

_merge = {"Electro": ["Electronic"], "Electro Pop": ["Electronic", "Pop"], "Metalcore": ["Metal"],
          "Christmas": ["Pop"], "Rapcore": ["Rap"], "Garage Rock": ["Rock"], "Gypsy jazz": ["Jazz"],
          "Gypsy": [], "Folklore": ["Folk"], "Musical": ["Soundtrack"], "8bit": ["Electronic"], "8-bit": ["Electronic"], "8 Bit": ["Electronic"],
          "Eurodance": ["Dance"], "Euro Dance": ["Dance"], "Synthpop": ["Pop"], "Synth Pop": ["Pop"],
          "Ragga": ["Reggae"], "Ragga hip-Hop": ["Reggae", "Hip-Hop"], "Shanty": ["Folk"],
          "Tanzmetal": ["Dance", "Metal"], "Vocal": ["Pop"], "Vocal Pop": ["Pop"],
          "Freak Metal": ["Metal"], "Medieval": ["Mittelalter"], "Medieval Rock": ["Mittelalter", "Rock"],
          "Celtic Punk": ["Pagan", "Punk"], "Darkwave": ["Gothic"], "Dark Wave": ["Gothic"],
          "US Metal": ["Metal"], "Skate Punk": ["Punk"], "Pony": ["Children"],
          "Pop, Vocal, Acapella": None}
_got = {k: b(k) for k in _merge if _merge[k] is not None}
check(f"bases: merged and dropped bases (got {_got})",
      all(_got[k] == _merge[k] for k in _got))
check(f"value: merged bases dedup (got {lib.value('Pop, Vocal, Acapella')}, {lib.value('Electro Pop, Electronic')})",
      lib.value("Pop, Vocal, Acapella") == ["Pop", "A cappella"]
      and lib.value("Electro Pop, Electronic") == ["Electronic", "Pop"])
check(f"explain: merges are named (got {lib.explain('Metalcore')[1]}, {lib.explain('Gypsy jazz')[1]})",
      "merged metalcore -> metal" in lib.explain("Metalcore")[1]
      and "dropped: gypsy" in lib.explain("Gypsy jazz")[1])
check(f"bases: slashes split (got {b('Gothic/Doom Metal')})",
      b("Gothic/Doom Metal") == ["Gothic", "Doom", "Metal"])

# --------------------------------------------------------------------------
# names kept whole
# --------------------------------------------------------------------------

check(f"bases: Hip-Hop in any spelling stays one base (got {b('Hip Hop')}, {b('hiphop')})",
      b("Hip Hop") == b("hip-hop") == b("Hiphop") == ["Hip-Hop"]
      and b("Alternative hip-Hop") == ["Alternative", "Hip-Hop"])
check(f"bases: Rock and Roll in its spellings is Rock (got {b('Rock’n’Roll')}, {b('rock & roll')})",
      b("Rock'n'Roll") == b("Rock’n’Roll") == b("rock & roll") == b("Rock and Roll") == ["Rock"])
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

check(f"bases: halves that are genres split (got {b('Popfunk')}, {b('Electropop')})",
      b("Popfunk") == ["Pop", "Funk"] and b("Electropop") == ["Electronic", "Pop"])
check(f"bases: a half that is no genre keeps the word (got {b('Metalcore')}, {b('Hardcore')}, {b('Electronic')})",
      b("Hardcore") == ["Hardcore"] and b("Electronic") == ["Electronic"] and b("Rapcore") == ["Rap"])
_keep = gb.Splitter(MB, COUNTS, keep_one_word={"electropop"})
check(f"bases: the keep list stops a split (got {b('Electropop', _keep)})",
      b("Electropop", _keep) == ["Electropop"] and b("Popfunk", _keep) == ["Pop", "Funk"])

# --------------------------------------------------------------------------
# how a base is written
# --------------------------------------------------------------------------

check(f"bases: the spelling most library songs use (got {b('folk metal')})",
      b("folk metal") == ["Folk", "Metal"])
_lower = gb.Splitter(MB, {"Viking metal": 2})
check(f"bases: a base the library writes only in lower case starts upper case (got {b('Viking metal', _lower)})",
      b("Viking metal", _lower) == ["Viking", "Metal"])
_bare = gb.Splitter(MB)
check(f"bases: without a library words start upper case (got {b('power metal', _bare)}, {b('italo disco', _bare)})",
      b("power metal", _bare) == ["Power", "Metal"] and b("italo disco", _bare) == ["Italo", "Disco"]
      and b("hardcore punk", _bare) == ["Hardcore", "Punk"] and b("uk garage", _bare) == ["UK", "Garage"])

# --------------------------------------------------------------------------
# value(): a whole #GENRE value, explain(): why
# --------------------------------------------------------------------------

check(f"value: bases in order of appearance, duplicates dropped (got {lib.value('Gothic Rock, Symphonic Black Metal, Gothic Metal')})",
      lib.value("Gothic Rock, Symphonic Black Metal, Gothic Metal")
      == ["Gothic", "Rock", "Symphonic", "Black", "Metal"])
_why = lib.explain("Metal Alternativo")[1]
check(f"explain: the fixes are named (got {_why}, {lib.explain('Popfunk')[1]}, {lib.explain('Electronic Music')[1]})",
      "translation alternativo -> alternative" in _why
      and "compound popfunk -> pop + funk" in lib.explain("Popfunk")[1]
      and "filler dropped: music" in lib.explain("Electronic Music")[1]
      and "typo? ghotic -> gothic" in lib.explain("Ghotic Metal")[1]
      and "kept whole: Hip-Hop" in lib.explain("Hip Hop")[1])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
