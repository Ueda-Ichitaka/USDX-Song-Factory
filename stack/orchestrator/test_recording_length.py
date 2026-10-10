#!/usr/bin/env python3
"""Tests for recording_length.py.

About me: plain assert-based checks (same style as the other orchestrator
tests) for turning a song list's musicbrainz_id - a recording, release or
release-group id - into the official recording length. A fake client
stands in for musicbrainzngs, so this runs on the host without network:

    python3 test_recording_length.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import recording_length as rl  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


class FakeClient:
    """Answers like musicbrainzngs for a small fixed catalogue."""

    class WebServiceError(Exception):
        pass

    class ResponseError(WebServiceError):
        pass

    def __init__(self, recordings=None, releases=None, groups=None, down=False):
        self.recordings = recordings or {}
        self.releases = releases or {}
        self.groups = groups or {}
        self.down = down
        self.calls = []

    def _answer(self, table, key, kind):
        self.calls.append((kind, key))
        if self.down:
            raise self.WebServiceError("network down")
        if key not in table:
            raise self.ResponseError("404")
        return {kind: table[key]}

    def get_recording_by_id(self, rid, includes=None):
        return self._answer(self.recordings, rid, "recording")

    def get_release_by_id(self, rid, includes=None):
        return self._answer(self.releases, rid, "release")

    def get_release_group_by_id(self, rid, includes=None):
        return self._answer(self.groups, rid, "release-group")


def release(tracks):
    return {"medium-list": [{"track-list": [
        {"length": str(ms) if ms else None, "recording": {"id": rid, "title": title,
                                                           "length": str(ms) if ms else None}}
        for rid, title, ms in tracks]}]}


client = FakeClient(
    recordings={"rec-1": {"title": "Six Feet Underground", "length": "279226"},
                "rec-nolen": {"title": "No Length"}},
    releases={"rel-1": release([("r1", "Intro", 60000), ("r2", "Lempo", 335346),
                                ("r3", "Rauta", 186146)]),
              "rel-2": release([("r4", "Other Song", 200000)]),
              "rel-3": release([("r5", "Kali Yuga III", 221266)])},
    groups={"grp-1": {"release-list": [{"id": "rel-2"}, {"id": "rel-3"}]}})

check("recording_seconds: a recording id gives its length in seconds",
      rl.recording_seconds("rec-1", "Six Feet Underground", client) == 279.226)
check("recording_seconds: a release id gives the length of the track with the song's title",
      rl.recording_seconds("rel-1", "Lempo", client) == 335.346)
check("recording_seconds: a release-group id searches its releases for the song",
      rl.recording_seconds("grp-1", "Kali Yuga III", client) == 221.266)
check("recording_seconds: no track with a similar title -> None",
      rl.recording_seconds("rel-1", "Something Else Entirely", client) is None)
check("recording_seconds: a recording without a length -> None",
      rl.recording_seconds("rec-nolen", "No Length", client) is None)
check("recording_seconds: an unknown id -> None",
      rl.recording_seconds("nope", "Lempo", client) is None)
check("recording_seconds: an empty id -> None, no lookup at all",
      rl.recording_seconds("  ", "Lempo", client) is None)
down = FakeClient(down=True)
check("recording_seconds: a network problem -> None (never raises)",
      rl.recording_seconds("rec-1", "Six Feet Underground", down) is None)
check("recording_seconds: a network problem is not retried as release/release-group",
      len(down.calls) == 1)
check("recording_seconds: titles match despite case, brackets and punctuation",
      rl.recording_seconds("rel-1", "LEMPO (Official Video)", client) == 335.346)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
