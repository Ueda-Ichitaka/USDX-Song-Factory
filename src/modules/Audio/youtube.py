"""YouTube Downloader"""

import os
import re

import yt_dlp

from modules.os_helper import sanitize_filename, get_unused_song_output_dir
from modules import os_helper
from modules.ProcessData import MediaInfo
from modules.Audio.bpm import get_bpm_from_file
from modules.console_colors import ULTRASINGER_HEAD
from modules.Image.image_helper import save_image
from modules.musicbrainz_client import get_song_info
from modules.ffmpeg_helper import separate_audio_video


def get_youtube_title(url: str, cookiefile: str = None) -> tuple[str | None, str]:
    """Get the title of the YouTube video"""

    ydl_opts = {
        "cookiefile": cookiefile,
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(
            url, download=False  # We just want to extract the info
        )

    if result.get("artist") and result.get("track"):
        return result["artist"].strip(), result["track"].strip()

    title = result["title"].strip()
    # drop trailing channel/label suffixes like "... | Napalm Records"
    if "|" in title:
        title = title.split("|")[0].strip()
    # strip bracketed additions like "[Album Name]" or "(Official Video)",
    # repeatedly so nested brackets are removed completely
    cleaned = title
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r"[\[\(\{][^\[\]\(\)\}]*[\]\)\}]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # split "Artist - Title" at the first dash ("-", "--" and unicode dashes);
    # skip empty parts so "Artist -- Title" does not yield an empty title
    parts = [p.strip() for p in re.split(r"\s*[-–—]\s*", cleaned) if p.strip()]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    if result.get("channel"):
        return result["channel"].strip(), cleaned
    # no artist known at all - None (not "") so the eventual fuzzy search
    # (search_musicbrainz(), inside get_song_info()) takes the title-only
    # __single_line_search path instead of a doomed __multi_line_search("", cleaned)
    return None, cleaned


def __download_youtube_video_with_audio(url: str, clear_filename: str, output_path: str, cookiefile: str = None) -> str:
    """Download video with audio from YouTube and return the file extension"""

    print(f"{ULTRASINGER_HEAD} Downloading Video with Audio")
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio/best",
        "outtmpl": output_path + "/" + clear_filename + ".%(ext)s",
        "merge_output_format": "mp4",
        "cookiefile": cookiefile,
        "socket_timeout": 30,
    }
    __start_download(ydl_opts, url)
    return "mp4"


def __download_youtube_thumbnail(url: str, clear_filename: str, output_path: str, cookiefile: str = None) -> str:
    """Download thumbnail from YouTube"""

    print(f"{ULTRASINGER_HEAD} Downloading thumbnail")
    ydl_opts = {
        "skip_download": True,
        "writethumbnail": True,
        "cookiefile": cookiefile,
        "socket_timeout": 30,
    }

    thumbnail_url = download_and_convert_thumbnail(ydl_opts, url, clear_filename, output_path)
    return thumbnail_url


def download_and_convert_thumbnail(ydl_opts, url: str, clear_filename: str, output_path: str) -> str:
    """Download and convert thumbnail from YouTube"""

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        thumbnail_url = info_dict.get("thumbnail")
        if thumbnail_url:
            response = ydl.urlopen(thumbnail_url)
            image_data = response.read()
            save_image(image_data, clear_filename, output_path)
            return thumbnail_url
        else:
            return ""




def __start_download(ydl_opts, url: str) -> None:
    """Start the download the ydl_opts"""

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        errors = ydl.download(url)
        if errors:
            raise Exception("Download failed with error: " + str(errors))


def should_use_musicbrainz_cover(song_info) -> bool:
    """Whether to use the MusicBrainz-sourced cover (real image bytes,
    already fetched) instead of falling back to a YouTube thumbnail.

    Only `cover_image_data` (the actual bytes save_image() writes to
    disk) matters here - `cover_url` is purely informational (the
    #COVERURL tag). A release whose Cover Art Archive listing has no
    image explicitly marked front=True can legitimately return real
    cover_image_data with cover_url still None (see musicbrainz_client.py
    __get_image()) - requiring BOTH to be set would silently discard an
    already-fetched, usually higher-quality cover in favor of a YouTube
    thumbnail just because the informational url happened to be missing.
    """
    return song_info.cover_image_data is not None


def resolve_song_identity(mb_artist: str, mb_title: str,
                          forced_artist: str = None,
                          forced_title: str = None) -> tuple[str, str]:
    """Decide the final (artist, title) used to name a downloaded song.

    forced_artist/forced_title (typically the caller's own trusted input,
    e.g. a hand-curated song list) always win when given - a YouTube
    channel name or a MusicBrainz alternate-release match must never
    rename a song away from data the caller already knows is correct.
    """
    forced_artist = (forced_artist or "").strip() or None
    forced_title = (forced_title or "").strip() or None
    return forced_artist or mb_artist, forced_title or mb_title


def download_from_youtube(input_url: str, output_folder_path: str, cookiefile: str = None,
                          forced_artist: str = None, forced_title: str = None,
                          musicbrainz_id: str = None) -> tuple[str, str, str, MediaInfo]:
    """Download from YouTube"""
    (artist, title) = get_youtube_title(input_url, cookiefile)

    # Get additional data for song (cover art, year, genres) - a given
    # musicbrainz_id (csv column) is tried first for more reliable
    # metadata, falling back to the fuzzy search when not given/not found
    song_info = get_song_info(title, artist, musicbrainz_id)
    song_info.artist, song_info.title = resolve_song_identity(
        song_info.artist, song_info.title, forced_artist, forced_title)

    basename_without_ext = sanitize_filename(f"{song_info.artist} - {song_info.title}")
    song_output = os.path.join(output_folder_path, basename_without_ext)
    song_output = get_unused_song_output_dir(song_output)
    os_helper.create_folder(song_output)

    print(f"{ULTRASINGER_HEAD} Downloading from YouTube")
    video_ext = __download_youtube_video_with_audio(
        input_url, basename_without_ext, song_output, cookiefile
    )
    video_with_audio_path = os.path.join(song_output, f"{basename_without_ext}.{video_ext}")

    # Separate audio and video
    audio_file_path, final_video_path, audio_ext, video_ext = separate_audio_video(
        video_with_audio_path, basename_without_ext, song_output
    )

    if should_use_musicbrainz_cover(song_info):
        cover_url = song_info.cover_url
        save_image(song_info.cover_image_data, basename_without_ext, song_output)
    else:
        cover_url = __download_youtube_thumbnail(
            input_url, basename_without_ext, song_output, cookiefile
        )

    return (
        basename_without_ext,
        song_output,
        audio_file_path,
        MediaInfo(
            artist=song_info.artist,
            title=song_info.title,
            year=song_info.year,
            genre=song_info.genres,
            cover_url=cover_url,
            video_url=input_url,
            audio_extension=audio_ext,
            video_extension=video_ext
        ),
    )
