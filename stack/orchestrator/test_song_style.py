#!/usr/bin/env python3
"""Tests for song_style.py.

About me: plain assert-based regression checks (same style as
test_orchestrator.py - no pytest dependency). Run with
`python3 test_song_style.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import song_style  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# parse_genres(): the dashboard's comma-joined genre cell
# --------------------------------------------------------------------------

check("parse_genres: blank means no genre",
      song_style.parse_genres("") == ([], []))
check("parse_genres: None means no genre",
      song_style.parse_genres(None) == ([], []))
check("parse_genres: 'default' means no genre",
      song_style.parse_genres("default") == ([], []))
check("parse_genres: one genre",
      song_style.parse_genres("metal") == (["metal"], []))
check("parse_genres: several genres, the order of the cell means nothing",
      song_style.parse_genres("metal,gothic") == song_style.parse_genres("gothic,metal")
      == (["gothic", "metal"], []))
check("parse_genres: case and spaces do not matter",
      song_style.parse_genres(" Industrial, EBM ,Metal") == (["ebm", "industrial", "metal"], []))
check("parse_genres: 'default' next to a real genre is dropped",
      song_style.parse_genres("default,folk") == (["folk"], []))
check("parse_genres: a genre given twice counts once",
      song_style.parse_genres("folk,folk") == (["folk"], []))
check("parse_genres: an unknown value is returned separately, not used",
      song_style.parse_genres("metal,polka") == (["metal"], ["polka"]))
for code in ("default", "metal", "medieval", "folk", "gothic", "symphonic", "punk",
             "pop", "rap", "kpop", "jpop", "rock", "industrial", "ebm"):
    check(f"GENRES knows {code!r}", code in song_style.GENRES or code == "default")

# --------------------------------------------------------------------------
# parse_traits(): the dashboard's comma-joined trait cell
# --------------------------------------------------------------------------

check("parse_traits: blank means no trait",
      song_style.parse_traits("") == ([], []))
check("parse_traits: 'none' means no trait",
      song_style.parse_traits("none") == ([], []))
check("parse_traits: several traits, sorted",
      song_style.parse_traits("loud_high_instruments,harsh_vocals")
      == (["harsh_vocals", "loud_high_instruments"], []))
check("parse_traits: the written-out form with spaces is the same trait",
      song_style.parse_traits("Harsh vocals, choir") == (["choir", "harsh_vocals"], []))
check("parse_traits: unknown value returned separately",
      song_style.parse_traits("choir,yodel") == (["choir"], ["yodel"]))
check("TRAITS are the four of the dashboard",
      song_style.TRAITS == frozenset({"harsh_vocals", "operatic_vocals",
                                      "loud_high_instruments", "choir"}))

# --------------------------------------------------------------------------
# style_fields(): what a parsed CSV row carries
# --------------------------------------------------------------------------

check("style_fields: genres, traits and the ignored values of a row",
      song_style.style_fields({"genre": "metal,polka", "trait": "choir"})
      == {"genres": ["metal"], "traits": ["choir"], "style_unknown": ["genre:polka"]})
check("style_fields: a row without the columns has no style",
      song_style.style_fields({}) == {"genres": [], "traits": [], "style_unknown": []})
check("style_fields: unknown trait is named as a trait",
      song_style.style_fields({"trait": "yodel"})["style_unknown"] == ["trait:yodel"])

# --------------------------------------------------------------------------
# describe(): one short text for logs and the report
# --------------------------------------------------------------------------

check("describe: genres and traits",
      song_style.describe({"genres": ["folk", "metal"], "traits": ["harsh_vocals"]})
      == "folk, metal | harsh_vocals")
check("describe: nothing set",
      song_style.describe({"genres": [], "traits": []}) == "default | none")
check("describe: a job from before the columns",
      song_style.describe({}) == "default | none")


print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
