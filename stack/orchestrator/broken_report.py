#!/usr/bin/env python3
"""Parses broken.csv - the karaoke-dashboard's "report a broken song" export
(`GET /admin/reports.csv` in that project, `app/routes/admin.py`).

About me: a repair job now uses the report's category to do only the
specific fix the category calls for (see orchestrator.py's
build_job_plan()/prepare_media_repair()), instead of always running the
same blind full repair - so the category codes here MUST stay in sync
with that project's app/broken_categories.py (CATEGORY_CHOICES):

    gap, async, lyrics, video, audio, other

`category` may legitimately be blank - karaoke-dashboard's BrokenReport
model made the field nullable for reports created before it existed
(pre-dates the category feature). An unrecognized code is treated the
same way. Either case falls back to orchestrator.py's "needs_review"
handling (see 02-DESIGN.md) rather than guessing an action.

The `lyrics_url` column is optional and not yet exported by
karaoke-dashboard (requested - see that project's UPSTREAM_REQUESTS.md);
reading it here now means no further change is needed here once it is.
"""

import csv
import os

CATEGORY_CODES = frozenset({"gap", "async", "lyrics", "video", "audio", "other"})


def normalize_category(raw: str) -> str:
    """A known category code, lowercased/stripped - or "" for anything
    else (blank, unrecognized, legacy pre-category reports)."""
    code = (raw or "").strip().lower()
    return code if code in CATEGORY_CODES else ""


def parse_broken_csv(path: str) -> list:
    """Parse broken.csv into a list of dicts: {"band", "title", "category",
    "description", "lyrics_url"}. Column names are matched case-
    insensitively against a few accepted spellings (the dashboard's own
    header is "band,song name,category,description[,lyrics_url]") so a
    hand-edited file isn't overly fragile."""
    entries = []
    if not path or not os.path.isfile(path):
        return entries

    with open(path, encoding="utf-8-sig", errors="replace") as f:
        raw_lines = [l.rstrip("\n").rstrip("\r") for l in f]

    non_empty = [l for l in raw_lines if l.strip() and not l.strip().startswith("#")]
    if not non_empty:
        return entries

    first = non_empty[0].strip()
    delimiter = "," if first.count(",") >= first.count(";") else ";"

    for row in csv.DictReader(non_empty, delimiter=delimiter):
        lower = {k.lower().strip(): (v or "").strip() for k, v in row.items()}
        band = lower.get("band") or lower.get("band name") or lower.get("artist") or ""
        title = lower.get("title") or lower.get("song name") or lower.get("song") or \
            lower.get("name") or ""
        if not band and not title:
            continue
        entries.append({
            "band": band,
            "title": title,
            "category": normalize_category(lower.get("category", "")),
            "description": lower.get("description", ""),
            "lyrics_url": lower.get("lyrics_url") or lower.get("lyrics link") or "",
        })
    return entries
