#!/usr/bin/env python3
"""Tests for song_header.py.

About me: plain assert-based checks (no pytest, matching the other
stack-level test scripts). Host-runnable: `python3 test_song_header.py`.
That FORMAT_VERSION is the version UltraSinger writes into new songs is
checked in test_repair.py, which runs where UltraSinger's Settings can be
imported.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import song_header  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# versioned_header(): #VERSION first; USDX refuses a versioned file with any
# #RELATIVE line, so "#RELATIVE:No" goes; #AUDIO repeats #MP3 as in new
# songs. Headers version 1 can't express keep the song unversioned.
# --------------------------------------------------------------------------

_v = song_header.FORMAT_VERSION
check("versioned_header: #VERSION first, #AUDIO after #MP3",
      song_header.versioned_header(["#TITLE:B", "#MP3:a.mp3", "#BPM:300"])
      == ([f"#VERSION:{_v}", "#TITLE:B", "#MP3:a.mp3", "#AUDIO:a.mp3", "#BPM:300"], None))
check("versioned_header: #RELATIVE:No and a UTF-8 #ENCODING are removed",
      song_header.versioned_header(["#TITLE:B", "#ENCODING:UTF8", "#MP3:a.mp3", "#RELATIVE:No"])
      == ([f"#VERSION:{_v}", "#TITLE:B", "#MP3:a.mp3", "#AUDIO:a.mp3"], None))
check("versioned_header: an existing #AUDIO is kept as it is",
      song_header.versioned_header(["#MP3:a.mp3", "#AUDIO:b.opus"])
      == ([f"#VERSION:{_v}", "#MP3:a.mp3", "#AUDIO:b.opus"], None))
check("versioned_header: a song that has a #VERSION is left alone",
      song_header.versioned_header(["#VERSION:1.0.0", "#MP3:a.mp3", "#RELATIVE:no"])
      == (["#VERSION:1.0.0", "#MP3:a.mp3", "#RELATIVE:no"], None))
for _blocker, _reason in (("#RELATIVE:yes", "#RELATIVE:yes"),
                          ("#ENCODING:CP1252", "#ENCODING:CP1252"),
                          ("#DUETSINGERP1:Anna", "#DUETSINGERP1"),
                          ("#NOTESGAP:100", "#NOTESGAP")):
    _hdr = ["#TITLE:B", "#MP3:a.mp3", _blocker]
    _out, _why = song_header.versioned_header(_hdr)
    check(f"versioned_header: {_blocker} keeps the song unversioned and says why "
          f"(got {_out!r}, {_why!r})", _out == _hdr and _why and _reason in _why)

# --------------------------------------------------------------------------
# versioned_text(): the same for a whole song file - only the header lines
# change, the notes and the file's own line endings stay
# --------------------------------------------------------------------------

_song = "#TITLE:B\r\n#MP3:a.mp3\r\n#RELATIVE:No\r\n: 0 4 5 La\r\n- 6\r\n: 8 4 5 #la\r\nE\r\n"
check("versioned_text: header versioned, notes and CRLF line endings kept",
      song_header.versioned_text(_song) == (
          f"#VERSION:{_v}\r\n#TITLE:B\r\n#MP3:a.mp3\r\n#AUDIO:a.mp3\r\n"
          ": 0 4 5 La\r\n- 6\r\n: 8 4 5 #la\r\nE\r\n", None))
check("versioned_text: a byte order mark stays at the very start",
      song_header.versioned_text("﻿#TITLE:B\n#MP3:a.mp3\n: 0 4 5 La\nE\n")[0]
      .startswith(f"﻿#VERSION:{_v}\n#TITLE:B\n"))
_blocked = "#TITLE:B\n#RELATIVE:yes\n: 0 4 5 La\n- 6 6\nE\n"
check("versioned_text: an unversionable song comes back unchanged with the reason",
      song_header.versioned_text(_blocked)[0] == _blocked
      and "#RELATIVE:yes" in song_header.versioned_text(_blocked)[1])

check("versioned_text: a text without header lines is left alone",
      song_header.versioned_text("") == ("", None)
      and song_header.versioned_text("La la\n") == ("La la\n", None))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
