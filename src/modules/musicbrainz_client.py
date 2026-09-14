import musicbrainzngs
import re
import string
import time
from urllib.error import URLError
from Levenshtein import ratio
from dataclasses import dataclass
from typing import Optional
from Settings import Settings

from modules.console_colors import ULTRASINGER_HEAD, blue_highlighted, red_highlighted


@dataclass
class SongInfo:
    title: str
    artist: str
    year: Optional[str] = None
    genres: Optional[str] = None
    cover_image_data: Optional[bytes] = None
    cover_url: Optional[str] = None


title_filter = [
    "official video",
    "official music video",
    "Offizielles Musikvideo",
]


MAX_RETRIES = 3


def __clean_string(s: str) -> str:
    return s.translate(str.maketrans('', '', string.punctuation)).lower().strip()


def __strip_bracketed(s: str) -> str:
    """Remove bracketed/parenthetical annotations, e.g. '(Official Video)'
    or '[Full HD]', repeatedly so nested brackets are removed completely.

    Used before Levenshtein-ratio comparisons: raw video/release titles
    routinely carry such annotations, and a strict length-sensitive ratio
    would otherwise reject an otherwise-correct match.
    """
    cleaned = s
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r"[\[\(\{][^\[\]\(\)\}]*[\]\)\}]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def __musicbrainz_request(func):
    for i in range(MAX_RETRIES):
        try:
            return func()
        except musicbrainzngs.musicbrainz.NetworkError:
            time.sleep(1)
    return None


def search_musicbrainz(title: str, artist) -> SongInfo:
    # Musicbrainz API documentation
    # https://python-musicbrainzngs.readthedocs.io/en/latest/api/

    musicbrainzngs.set_useragent("UltraSinger", Settings.APP_VERSION, "https://github.com/rakuri255/UltraSinger")

    # remove from search_string "official video"
    # todo: do we need filter?
    origin_title = title
    origin_artist = artist
    for filter in title_filter:
        title = title.lower().replace(filter.lower(), "").strip()
        if artist is not None:
            artist = artist.lower().replace(filter.lower(), "").strip()

    if not __clean_string(title or ""):
        # nothing to search for - keep the given (e.g. youtube) names
        return SongInfo(title=origin_title, artist=origin_artist or "Unknown Artist")

    if artist is None:
        recording = __single_line_search(title)
    else:
        recording = __multi_line_search(artist, title)

    if recording is None:
        print(f"{ULTRASINGER_HEAD} {red_highlighted('No match found')} "
              f"- keeping the given artist/title")
        return SongInfo(title=origin_title, artist=origin_artist or "Unknown Artist")

    artist = recording['artist-credit-phrase']
    title = recording['title']
    print(
        f"{ULTRASINGER_HEAD} Found data on Musicbrainz: Artist={blue_highlighted(artist)} Title={blue_highlighted(title)}")

    year = __get_year(recording)
    genres = __get_genres(recording)
    image_data, image_url = __get_image(recording)

    return SongInfo(title=title, artist=artist, year=year, genres=genres, cover_image_data=image_data, cover_url=image_url)


def __single_line_search(search_string):
    search_string = __clean_string(search_string)
    search_string = __filter_words(search_string)

    artists = __musicbrainz_request(lambda: musicbrainzngs.search_artists(search_string, limit=10, artist=search_string))
    recordings = __musicbrainz_request(lambda: musicbrainzngs.search_recordings(search_string, limit=100, artistname=search_string))

    if artists is None or recordings is None:
        return None

    found_artist = None

    for record in recordings['recording-list']:
        if found_artist is not None:
            break

        for artist_credit in record['artist-credit']:
            if found_artist is not None:
                break
            if isinstance(artist_credit, str):
                continue
            # todo: there is also an "alias-list". Maybe search also there?

            for artist in artists['artist-list']:
                if artist_credit['artist'] and artist_credit['artist']['id'] == artist['id']:
                    found_artist = record['artist-credit-phrase']
                    break

    if found_artist is None:
        return None

    recordings = [x for x in recordings['recording-list'] if
                  __clean_string(x['artist-credit-phrase']) == __clean_string(found_artist)]

    recording = None

    for record in recordings:
        if __clean_string(record['title']) in __clean_string(search_string):
            recording = record

    return recording


def __filter_words(search_string):
    for filter in title_filter:
        search_string = search_string.lower().replace(filter.lower(), "").strip()
    return search_string


def __multi_line_search(artist: str, title: str):
    # Try both combinations since we don't know which one is the artist and which one is the title
    artist1, title1 = artist, title
    artist2, title2 = title, artist

    result1 = __musicbrainz_request(lambda: musicbrainzngs.search_recordings(recording=title1, limit=10, artist=artist1, artistname=artist1))
    result2 = __musicbrainz_request(lambda: musicbrainzngs.search_recordings(recording=title2, limit=10, artist=artist2, artistname=artist2))

    if result1 is None:
        result1 = {'recording-count': 0, 'recording-list': []}
    if result2 is None:
        result2 = {'recording-count': 0, 'recording-list': []}

    # Only accept recordings whose title is similar enough to the searched
    # title AND whose artist matches - musicbrainz fulltext search returns
    # very loose fuzzy hits that must not be used blindly.
    min_similarity = 0.6

    def candidates(result, searched_title, searched_artist):
        found = []
        clean_searched_title = __strip_bracketed(searched_title)
        for record in result['recording-list']:
            if ratio(__clean_string(record['title']),
                     __clean_string(clean_searched_title)) < min_similarity:
                continue
            if __clean_string(record['artist-credit-phrase']) == \
                    __clean_string(searched_artist):
                found.append(record)
        return found

    record1 = candidates(result1, title1, artist1)
    record2 = candidates(result2, title2, artist2)

    def best(records, searched_title):
        clean_searched_title = __strip_bracketed(searched_title)
        return max(records, key=lambda x: ratio(
            __clean_string(x['title']), __clean_string(clean_searched_title)))

    def match_ratio(records, searched_title):
        clean_searched_title = __strip_bracketed(searched_title)
        return ratio(__clean_string(clean_searched_title),
                     __clean_string(best(records, searched_title)['title']))

    recording = None
    if len(record1) > 0 and len(record2) > 0:
        if match_ratio(record1, title1) >= match_ratio(record2, title2):
            recording = best(record1, title1)
        else:
            recording = best(record2, title2)
    elif len(record1) > 0:
        recording = best(record1, title1)
    elif len(record2) > 0:
        recording = best(record2, title2)

    return recording


def __safe_lookup(func):
    """Like __musicbrainz_request, but treats a not-found (ResponseError,
    e.g. a 404 for an ID that doesn't exist as this entity type) as a
    clean None instead of letting it propagate - callers use this to try
    "is this ID a recording?" without an explicit try/except at each
    call site."""
    try:
        return __musicbrainz_request(func)
    except musicbrainzngs.ResponseError:
        return None


MBID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def lookup_musicbrainz_by_id(musicbrainz_id: str):
    """Direct-by-ID lookup (csv musicbrainz_id column) - skips the fuzzy
    title/artist search entirely for more reliable metadata. Accepts
    either a recording MBID or a release MBID: a recording lookup is
    tried first, a release lookup is the fallback (auto-detected, since
    a csv author may reasonably supply either and the stack has no way
    to know which without trying). Returns None if neither resolves -
    the caller then falls back to the fuzzy search_musicbrainz(), same as
    "no match found" there.

    The id is validated as a well-formed MBID (a UUID) BEFORE it ever
    reaches the network. A clean 400 (garbage id, no special characters)
    already comes back fast and is handled by __safe_lookup below - but a
    malformed id containing certain characters (a raw space, in
    particular) breaks the request in a way musicbrainzngs' own retry
    logic (_safe_read: up to 8 attempts with escalating backoff) treats
    as transient, hanging for ~1-2 minutes before giving up rather than
    failing fast (confirmed live, not guessed). A csv value is unverified
    user input - a copy-paste losing a character or gaining a stray space
    must never cost a whole song's generation that much dead time.
    """
    musicbrainz_id = (musicbrainz_id or "").strip()
    if not musicbrainz_id:
        return None
    if not MBID_RE.match(musicbrainz_id):
        print(f"{ULTRASINGER_HEAD} {red_highlighted('Invalid musicbrainz_id')} "
              f"(not a well-formed MBID): {musicbrainz_id!r} - ignoring it, "
              "falling back to the normal search")
        return None

    musicbrainzngs.set_useragent("UltraSinger", Settings.APP_VERSION, "https://github.com/rakuri255/UltraSinger")

    # "release-groups" is NOT a valid include for a recording lookup (only
    # for a release lookup - musicbrainzngs.VALID_INCLUDES); __get_year()
    # fetches the release-group itself via a separate get_release_by_id()
    # call when it isn't already embedded in the release data.
    recording_response = __safe_lookup(lambda: musicbrainzngs.get_recording_by_id(
        musicbrainz_id, includes=["artist-credits", "releases", "tags"]))
    if recording_response is not None and "recording" in recording_response:
        recording = recording_response["recording"]
        year = __get_year(recording) if "release-list" in recording else None
        genres = __get_genres(recording)
        image_data, image_url = (
            __get_image(recording) if "release-list" in recording else (None, None))
        print(f"{ULTRASINGER_HEAD} Found data on Musicbrainz by recording ID: "
              f"Artist={blue_highlighted(recording.get('artist-credit-phrase', ''))} "
              f"Title={blue_highlighted(recording.get('title', ''))}")
        return SongInfo(title=recording.get("title", ""),
                        artist=recording.get("artist-credit-phrase", ""),
                        year=year, genres=genres,
                        cover_image_data=image_data, cover_url=image_url)

    release_response = __safe_lookup(lambda: musicbrainzngs.get_release_by_id(
        musicbrainz_id, includes=["artist-credits", "tags"]))
    if release_response is not None and "release" in release_response:
        release = release_response["release"]
        year = None
        if "date" in release and release["date"]:
            year = release["date"].strip().split("-")[0] or None
        genres = __get_genres(release)
        image_data = __safe_lookup(lambda: musicbrainzngs.get_image_front(musicbrainz_id))
        image_url = None
        if image_data is not None:
            image_list = __safe_lookup(lambda: musicbrainzngs.get_image_list(musicbrainz_id))
            if image_list is not None:
                for image in image_list["images"]:
                    if image["front"]:
                        image_url = image["image"]
                        break
        print(f"{ULTRASINGER_HEAD} Found data on Musicbrainz by release ID: "
              f"Artist={blue_highlighted(release.get('artist-credit-phrase', ''))} "
              f"Title={blue_highlighted(release.get('title', ''))}")
        return SongInfo(title=release.get("title", ""),
                        artist=release.get("artist-credit-phrase", ""),
                        year=year, genres=genres,
                        cover_image_data=image_data, cover_url=image_url)

    return None


def get_song_info(title: str, artist, musicbrainz_id: str = None) -> SongInfo:
    """Single entry point for song metadata lookup. A given musicbrainz_id
    (csv column) is tried first via lookup_musicbrainz_by_id() - skipping
    the fuzzy search entirely for more reliable metadata - and only falls
    back to the normal fuzzy search_musicbrainz() when no id was given or
    the id didn't resolve to anything."""
    if musicbrainz_id:
        info = lookup_musicbrainz_by_id(musicbrainz_id)
        if info is not None:
            return info
    return search_musicbrainz(title, artist)


def __get_image(recording) -> (bytes, str):
    image_data = None
    image_url = None
    if 'release-list' in recording:
        for release in recording['release-list']:
            try:
                image_data = __musicbrainz_request(lambda: musicbrainzngs.get_image_front(release['id']))
                if image_data is None:
                    continue

                image_list = __musicbrainz_request(lambda: musicbrainzngs.get_image_list(release['id']))
                if image_list is None:
                    continue

                for image in image_list['images']:
                    if image['front']:
                        image_url = image['image']
                        break
                break
            except musicbrainzngs.ResponseError:
                continue
    if image_data is not None:
        print(f"{ULTRASINGER_HEAD} Found cover image")

    return image_data, image_url


def __get_year(recording):
    year = None

    if 'release-list' not in recording or not recording['release-list']:
        return year

    release = recording['release-list'][0]
    if 'release-group' in release:
        # search_recordings() results already embed this - no extra call
        release_group_id = release['release-group']['id']
    else:
        # get_recording_by_id() results don't: "release-groups" isn't a
        # valid include for a recording lookup (musicbrainzngs
        # VALID_INCLUDES has it only for "release", not "recording") -
        # fetch the release itself, which DOES support that include, to
        # get its release-group id first
        release_lookup = __musicbrainz_request(lambda: musicbrainzngs.get_release_by_id(
            release['id'], includes=["release-groups"]))
        if release_lookup is None or 'release-group' not in release_lookup.get('release', {}):
            return year
        release_group_id = release_lookup['release']['release-group']['id']

    release_group = __musicbrainz_request(lambda: musicbrainzngs.get_release_group_by_id(release_group_id))

    if release_group is None:
        return year

    if 'first-release-date' not in release_group['release-group']:
        return year

    year = release_group['release-group']['first-release-date'].strip()
    year = year.split('-')[0]

    if year is not None:
        print(f"{ULTRASINGER_HEAD} Found year: {blue_highlighted(year)}")

    return year


def __get_genres(recording) -> str:
    # todo secondary-type-list ??
    genres = None
    if 'tag-list' in recording:
        genres = ""
        for tag in recording['tag-list']:
            genres += f"{tag['name'].strip()},"
    if genres is not None:
        print(f"{ULTRASINGER_HEAD} Found genres: {blue_highlighted(genres)}")
    return genres
