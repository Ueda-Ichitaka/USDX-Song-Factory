"""Official recording length for a song list's musicbrainz_id.

About me: outro.find_outro_cut() needs to know how long the recording really
is, to spot a video that runs past it. The length comes only from the
musicbrainz_id the song list author chose - UltraSinger's own MusicBrainz
search picks the exact recording for only 15 of 32 list songs with a known
id (live and music-video recordings are common hits), and even a filtered
"studio version" pick gets the length wrong for 7 of 32, which could cut
real lyrics. The list's id can be a recording, a release or a release-group
id; for the latter two, the track whose title matches the song is used.

Every problem (unknown id, no length, network) gives None - the song is
then simply aligned without an outro cut.
"""

import difflib
import re

MIN_TITLE_SIMILARITY = 0.8
MAX_GROUP_RELEASES = 3


def _clean_title(title: str) -> str:
    title = re.sub(r"\(.*?\)|\[.*?\]", "", title or "")
    return re.sub(r"[^\w]", "", title.lower())


def _seconds(length) -> float:
    return int(length) / 1000.0 if length else None


def _track_length(releases: list, title: str):
    wanted = _clean_title(title)
    best, best_score = None, MIN_TITLE_SIMILARITY
    for rel in releases:
        for medium in rel.get("medium-list", []):
            for track in medium.get("track-list", []):
                recording = track.get("recording", {})
                score = difflib.SequenceMatcher(
                    None, _clean_title(recording.get("title", "")), wanted).ratio()
                if score >= best_score:
                    best_score = score
                    best = _seconds(recording.get("length") or track.get("length"))
    return best


def recording_seconds(mbid: str, title: str, client=None):
    """Length in seconds of the recording behind `mbid`, or None."""
    mbid = (mbid or "").strip()
    if not mbid:
        return None
    if client is None:
        import musicbrainzngs as client  # noqa: N813
        client.set_useragent("USDX-Song-Factory", "1.0",
                             "https://github.com/rakuri255/UltraSinger")
    try:
        try:
            recording = client.get_recording_by_id(mbid)["recording"]
            return _seconds(recording.get("length"))
        except client.ResponseError:
            pass
        includes = ["recordings"]
        try:
            releases = [client.get_release_by_id(mbid, includes=includes)["release"]]
        except client.ResponseError:
            group = client.get_release_group_by_id(mbid, includes=["releases"])["release-group"]
            releases = [client.get_release_by_id(r["id"], includes=includes)["release"]
                        for r in group.get("release-list", [])[:MAX_GROUP_RELEASES]]
        return _track_length(releases, title)
    except client.WebServiceError:
        return None
