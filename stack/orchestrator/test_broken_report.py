#!/usr/bin/env python3
"""Tests for broken_report.py.

About me: plain assert-based regression checks (same style as
test_orchestrator.py - no pytest dependency). Run with
`python3 test_broken_report.py`.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import broken_report as br  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# normalize_category: exact codes pass through, everything else -> ""
# (blank/legacy reports predate the category field - see BrokenReport model
# in karaoke-dashboard's app/models.py, category is nullable there)
# --------------------------------------------------------------------------

for code in ("gap", "async", "lyrics", "video", "audio", "other"):
    check(f"normalize_category accepts {code!r}", br.normalize_category(code) == code)

check("normalize_category is case/whitespace-insensitive",
      br.normalize_category("  GAP ") == "gap")
check("normalize_category maps blank to ''", br.normalize_category("") == "")
check("normalize_category maps unrecognized to ''",
      br.normalize_category("bogus") == "")


# --------------------------------------------------------------------------
# parse_broken_csv: the real karaoke-dashboard export shape
# (band,song name,category,description - see admin.py reports_csv())
# --------------------------------------------------------------------------

def write_csv(text):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


real_export = (
    "band,song name,category,description\n"
    "Queen,I Want To Break Free,,#GAP\n"
    "ASP,Ich will brennen,gap,\n"
    "Metric,Black Sheep,async,\n"
)
path1 = write_csv(real_export)
rows1 = br.parse_broken_csv(path1)
check("parse_broken_csv reads all rows", len(rows1) == 3)
check("parse_broken_csv: band/title columns",
      rows1[1]["band"] == "ASP" and rows1[1]["title"] == "Ich will brennen")
check("parse_broken_csv: category normalized", rows1[1]["category"] == "gap")
check("parse_broken_csv: blank category stays '' (legacy report)",
      rows1[0]["category"] == "")
check("parse_broken_csv: description carried through",
      rows1[0]["description"] == "#GAP")
check("parse_broken_csv: missing lyrics_url column defaults to ''",
      rows1[1]["lyrics_url"] == "")
os.unlink(path1)


# --------------------------------------------------------------------------
# parse_broken_csv: optional lyrics_url column (requested from
# karaoke-dashboard, same convention as song-requests.csv's lyrics_url)
# --------------------------------------------------------------------------

with_lyrics_url = (
    "band,song name,category,description,lyrics_url\n"
    "ASP,Ich will brennen,lyrics,wrong words,"
    "https://genius.com/Asp-ich-will-brennen-lyrics\n"
    "Metric,Black Sheep,async,,\n"
)
path2 = write_csv(with_lyrics_url)
rows2 = br.parse_broken_csv(path2)
check("parse_broken_csv: lyrics_url column read when present",
      rows2[0]["lyrics_url"] == "https://genius.com/Asp-ich-will-brennen-lyrics")
check("parse_broken_csv: lyrics_url empty cell stays ''", rows2[1]["lyrics_url"] == "")
os.unlink(path2)


# --------------------------------------------------------------------------
# parse_broken_csv: robustness - unknown category code, missing file
# --------------------------------------------------------------------------

bad_category = "band,song name,category,description\nX,Y,not-a-real-code,some notes\n"
path3 = write_csv(bad_category)
rows3 = br.parse_broken_csv(path3)
check("parse_broken_csv: unrecognized category normalized to ''",
      rows3[0]["category"] == "")
check("parse_broken_csv: description kept even for unrecognized category",
      rows3[0]["description"] == "some notes")
os.unlink(path3)

check("parse_broken_csv: missing file returns empty list",
      br.parse_broken_csv("/no/such/broken.csv") == [])
check("parse_broken_csv: empty path returns empty list",
      br.parse_broken_csv("") == [])


print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
