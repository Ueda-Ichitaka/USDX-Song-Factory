#!/usr/bin/env python3
"""Genre labels and vocal/instrument traits of a song.

About me: reads the `genre` and `trait` columns of the karaoke-dashboard's
song-requests.csv and broken.csv (that project's app/genres.py and
app/traits.py). The admin sets them per song; the factory switches single
pipeline steps on the traits, because the vocal style cannot be detected
reliably from the audio (see knowledge/06-IDEAS.md "Style profiles").

Meaning the factory relies on (agreed in karaoke-dashboard's
UPSTREAM_REQUESTS.md):

- a cell holds several comma-joined values; their order means nothing, so
  the parsed lists are sorted;
- values are per song, not per band;
- blank or `default` genre and blank or `none` trait mean no special
  handling;
- a value this file does not know is returned separately and never used,
  so the dashboard can add labels at any time.
"""

GENRES = frozenset({
    "metal", "medieval", "folk", "gothic", "symphonic", "punk", "pop", "rap",
    "kpop", "jpop", "rock", "industrial", "ebm",
})
NEUTRAL_GENRE = "default"

TRAITS = frozenset({
    "harsh_vocals", "operatic_vocals", "loud_high_instruments", "choir",
})
NEUTRAL_TRAIT = "none"


def _parse(value, known: frozenset, neutral: str):
    """(known values, unknown values) of one comma-joined cell, each sorted
    and without duplicates. Case does not matter; spaces inside a value
    stand for underscores ("harsh vocals" = "harsh_vocals")."""
    known_found, unknown = set(), set()
    for part in (value or "").split(","):
        code = "_".join(part.strip().lower().split())
        if not code or code == neutral:
            continue
        (known_found if code in known else unknown).add(code)
    return sorted(known_found), sorted(unknown)


def parse_genres(value):
    """(genres, unknown values) of a `genre` cell."""
    return _parse(value, GENRES, NEUTRAL_GENRE)


def parse_traits(value):
    """(traits, unknown values) of a `trait` cell."""
    return _parse(value, TRAITS, NEUTRAL_TRAIT)


def style_fields(row: dict) -> dict:
    """The style of one CSV row ({lower-cased header: value}):
    {"genres", "traits", "style_unknown"}; style_unknown names each ignored
    value with its column ("genre:polka")."""
    genres, unknown_genres = parse_genres(row.get("genre"))
    traits, unknown_traits = parse_traits(row.get("trait"))
    return {
        "genres": genres,
        "traits": traits,
        "style_unknown": [f"genre:{v}" for v in unknown_genres]
                         + [f"trait:{v}" for v in unknown_traits],
    }


def describe(job: dict) -> str:
    """"genres | traits" for logs and the report, with the dashboard's
    neutral words when nothing is set."""
    genres = ", ".join(job.get("genres") or []) or NEUTRAL_GENRE
    traits = ", ".join(job.get("traits") or []) or NEUTRAL_TRAIT
    return f"{genres} | {traits}"
