#!/usr/bin/env python3
"""Tests for anchors.py.

About me: plain assert-based checks (same style as the other orchestrator
tests) for reading an admin's anchors.txt (pinned lyric lines + song end)
and matching its lines to the song's lyric lines. Pure standard library,
runs on the host:

    python3 test_anchors.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import anchors  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


def write(content):
    folder = tempfile.mkdtemp()
    with open(os.path.join(folder, anchors.ANCHORS_FILE), "w", encoding="utf-8") as f:
        f.write(content)
    return folder


def raises(content):
    try:
        anchors.read_anchors(write(content))
    except ValueError as exc:
        return str(exc)
    return None


# --------------------------------------------------------------------------
# read_anchors()
# --------------------------------------------------------------------------

check("read_anchors: no file -> None", anchors.read_anchors(tempfile.mkdtemp()) is None)

full = anchors.read_anchors(write(
    "# anchors for Six Feet Underground\n"
    "\n"
    "4:12.0 | Deep down in the dark\n"
    "4:18.3 |  I'm loving you, six feet underground \n"
    "265 | Six feet underground\n"
    "END 4:34.5\n"))
check(f"read_anchors: lines (time, text) in file order and the end time (got {full})",
      full == {"lines": [(252.0, "Deep down in the dark"),
                         (258.3, "I'm loving you, six feet underground"),
                         (265.0, "Six feet underground")],
               "end": 274.5})
check("read_anchors: only an end line is fine",
      anchors.read_anchors(write("end 3:20\n")) == {"lines": [], "end": 200.0})
check("read_anchors: only anchor lines is fine (no end)",
      anchors.read_anchors(write("1:00 | la la\n")) == {"lines": [(60.0, "la la")], "end": None})
check("read_anchors: a byte order mark is ignored",
      anchors.read_anchors(write("﻿end 3:20\n")) == {"lines": [], "end": 200.0})
check("read_anchors: a file with only comments -> None",
      anchors.read_anchors(write("# nothing yet\n")) is None)

msg = raises("4:12 | a\nsoon | b\n")
check(f"read_anchors: a bad time is rejected with file name and line number (got {msg!r})",
      msg is not None and anchors.ANCHORS_FILE in msg and "line 2" in msg)
msg = raises("4:12 Deep down\n")
check("read_anchors: a line without '|' and not 'end' is rejected",
      msg is not None and "line 1" in msg)
msg = raises("4:12 | \n")
check("read_anchors: an anchor without text is rejected", msg is not None)
msg = raises("4:20 | a\n4:12 | b\n")
check("read_anchors: anchor times must increase", msg is not None and "line 2" in msg)
msg = raises("end 3:00\nend 3:10\n")
check("read_anchors: only one end line", msg is not None)
msg = raises("4:20 | a\nend 4:00\n")
check("read_anchors: the end must come after the last anchor", msg is not None)

# --------------------------------------------------------------------------
# match_anchor_units(): which lyric line each anchor pins
# --------------------------------------------------------------------------

texts = ["Hello there", "Chorus line", "Verse two", "Chorus line", "Final words!"]
check("match_anchor_units: case, punctuation and spacing do not matter",
      anchors.match_anchor_units([(100.0, "final   WORDS")], texts) == [(4, 100.0)])
check("match_anchor_units: a repeated line means its last occurrence",
      anchors.match_anchor_units([(90.0, "chorus line")], texts) == [(3, 90.0)])
check("match_anchor_units: earlier anchors search before the later ones (from the end)",
      anchors.match_anchor_units([(50.0, "Chorus line"), (60.0, "Verse two"),
                                  (90.0, "Chorus line")], texts)
      == [(1, 50.0), (2, 60.0), (3, 90.0)])
try:
    anchors.match_anchor_units([(10.0, "not in the song")], texts)
    check("match_anchor_units: an unknown line raises ValueError quoting it", False)
except ValueError as exc:
    check("match_anchor_units: an unknown line raises ValueError quoting it",
          "not in the song" in str(exc))
try:
    anchors.match_anchor_units([(10.0, "Final words"), (20.0, "Hello there")], texts)
    check("match_anchor_units: anchors in a different order than the lyrics raise ValueError", False)
except ValueError:
    check("match_anchor_units: anchors in a different order than the lyrics raise ValueError", True)
check("match_anchor_units: no anchors -> empty list",
      anchors.match_anchor_units([], texts) == [])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
