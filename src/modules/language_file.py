"""Persisted, admin-editable language.txt handling.

About me: shared between repair.py's realignment path and UltraSinger's
own new-song transcription, so both apply the identical precedence rules
for which language to use - the same admin-editable-persisted-file
pattern already used for lyrics.txt (see stack/orchestrator/repair.py's
find_lyrics_file()/lyrics_txt_lines()).
"""

import os

LANGUAGE_FILE_NAME = "language.txt"


def resolve_language_with_file(song_dir: str, forced: str = None,
                                detect_fn=None, log=print) -> str:
    """Resolve the language code to use for `song_dir`, checking/writing
    `<song_dir>/language.txt`:

      1. file exists + forced given -> prefer the FILE (log a warning if
         they disagree - edit/delete the file to actually change it)
      2. no file + forced given -> write the file from forced, use forced
      3. file exists + no forced -> use the file's value
      4. no file + no forced -> call detect_fn() to auto-detect, write
         the result, use it

    `detect_fn` (no arguments, returns a language code) is only ever
    called in case 4 - never when the language is already known from a
    file or a forced value. Returns whatever `detect_fn()` returns
    (falsy included) when there is nothing else to go on and no
    `detect_fn` was given.
    """
    path = os.path.join(song_dir, LANGUAGE_FILE_NAME)
    file_lang = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                file_lang = f.read().strip() or None
        except OSError:
            file_lang = None

    forced = (forced or "").strip() or None

    if file_lang:
        if forced and forced.lower() != file_lang.lower():
            log(f"language.txt says {file_lang!r} but {forced!r} was also "
                "given - using the saved file (edit/delete it to override)")
        return file_lang

    if forced:
        _write_language_file(path, forced)
        return forced

    detected = detect_fn() if detect_fn else None
    if detected:
        _write_language_file(path, detected)
    return detected


def _write_language_file(path: str, language: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(language.strip() + "\n")
