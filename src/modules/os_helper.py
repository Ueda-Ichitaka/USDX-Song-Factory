"""OS helper functions.

About me: small file and folder helpers of UltraSinger - creating, moving
and copying files, sanitizing file names, and preparing a song's output
folder: an existing folder of the same name is replaced by the new song
(replace_song_output_dir()), unless it holds the run's own input file.
"""

import os
import shutil

from modules.console_colors import ULTRASINGER_HEAD


def create_folder(folder_name: str) -> None:
    """Creates a folder if it doesn't exist."""
    print(f"{ULTRASINGER_HEAD} Creating output folder. -> {folder_name}")
    is_exist = os.path.exists(folder_name)
    if not is_exist:
        os.makedirs(folder_name)


def move(original: str, target: str) -> None:
    """Moves a file from one location to another."""
    shutil.move(original, target)


def copy(original: str, target: str) -> None:
    """Copies a file from one location to another."""
    shutil.copy(original, target)


def rename(original: str, target: str) -> None:
    """Renames a file."""
    os.rename(original, target)


def current_executor_path() -> str:
    """Current executor path"""
    return os.getcwd()


def path_join(path1: str, path2: str) -> str:
    """Joins two paths together"""
    return os.path.join(path1, path2)


def check_file_exists(file_path: str) -> bool:
    """Checks if a file exists."""
    return os.path.isfile(file_path)


def check_if_folder_exists(song_output: str) -> bool:
    """Checks if a folder exists."""
    return os.path.isdir(song_output)


def remove_folder(cache_path):
    """Removes a folder."""
    shutil.rmtree(cache_path)


FILENAME_REPLACEMENTS = (('?:"', ""), ("<", "("), (">", ")"), ("/\\|*", "-"))


def sanitize_filename(fname: str) -> str:
    """Sanitize filename"""
    for old, new in FILENAME_REPLACEMENTS:
        for char in old:
            fname = fname.replace(char, new)
    if fname.endswith("."):
        fname = fname.rstrip(" .")  # Windows does not like trailing periods
    return fname


def replace_song_output_dir(path: str, keep=()) -> str:
    """The song output folder `path`, emptied for the new song: an existing
    folder is removed, so the new song replaces the old one with no stale
    files left. A folder that holds one of the `keep` files (the run's own
    input) is kept, and the new song's files overwrite the old ones in it."""
    if not check_if_folder_exists(path):
        return path
    folder = os.path.realpath(path)
    for item in keep:
        if item and os.path.realpath(item).startswith(folder + os.sep):
            print(f"{ULTRASINGER_HEAD} Output folder holds the input file, writing into it -> {path}")
            return path
    print(f"{ULTRASINGER_HEAD} Replacing the existing output folder -> {path}")
    shutil.rmtree(path)
    return path
