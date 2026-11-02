#!/usr/bin/env python3
"""Tests for import_local_media.py.

About me: plain assert-based checks like the other utility tests, on temp
folders; mounts, host names and gio are fakes. Run:
python3 tests/test_import_local_media.py (from utilities/)
"""

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import import_local_media as ilm  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# parse_mounts(): /proc/self/mounts, octal escapes decoded
# --------------------------------------------------------------------------

_mounts = ilm.parse_mounts(
    "//192.168.0.176/media /dhuaddan/media cifs rw,relatime 0 0\n"
    "nas:/export/music /mnt/nfs\\040music nfs4 rw 0 0\n"
    "/dev/nvme0n1p2 / btrfs rw 0 0\n")
check(f"parse_mounts: network mounts with source and target (got {_mounts})",
      _mounts == [("//192.168.0.176/media", "/dhuaddan/media", "cifs"),
                  ("nas:/export/music", "/mnt/nfs music", "nfs4")])

# --------------------------------------------------------------------------
# locate(): where a song-list path can be read
# --------------------------------------------------------------------------

root = tempfile.mkdtemp()
inp = os.path.join(root, "input")
os.makedirs(os.path.join(inp, "videos"))
hosts = {"nas": "192.168.0.176", "192.168.0.176": "192.168.0.176"}


def _locate(value, maps=None):
    return ilm.locate(value, inp, mounts=_mounts, resolve_host=lambda h: hosts.get(h, h),
                      maps=maps or {})


check("locate: a path relative to the input stays",
      _locate("videos/a.mp4") == ("input", "videos/a.mp4"))
check("locate: the container's /data/input path becomes relative",
      _locate("/data/input/videos/a.mp4") == ("input", "videos/a.mp4"))
check("locate: an absolute path inside the input becomes relative",
      _locate(os.path.join(inp, "videos", "a.mp4")) == ("input", "videos/a.mp4"))
check("locate: an absolute path elsewhere is read from there",
      _locate("/mnt/usb/a.mp4") == ("local", "/mnt/usb/a.mp4"))
check("locate: smb:// on a mounted share -> the mounted path",
      _locate("smb://192.168.0.176/media/Videos/a b.mp4") == ("local", "/dhuaddan/media/Videos/a b.mp4"))
check("locate: smb:// by host name -> the share mounted by IP",
      _locate("smb://nas/media/a.mp4") == ("local", "/dhuaddan/media/a.mp4"))
check("locate: share names ignore case (SMB does)",
      _locate("smb://nas/Media/a.mp4") == ("local", "/dhuaddan/media/a.mp4"))
check("locate: a UNC path on a mounted share",
      _locate("\\\\192.168.0.176\\media\\Videos\\a.mp4") == ("local", "/dhuaddan/media/Videos/a.mp4"))
check("locate: //host/share on a mounted share",
      _locate("//nas/media/a.mp4") == ("local", "/dhuaddan/media/a.mp4"))
check("locate: percent-escapes in an smb:// URL are decoded",
      _locate("smb://nas/media/a%20b.mp4") == ("local", "/dhuaddan/media/a b.mp4"))
check("locate: an smb share that is not mounted -> gio (reference)",
      _locate("smb://other/share/a.mp4") == ("gio", "smb://other/share/a.mp4")
      and _locate("\\\\other\\share\\a.mp4") == ("gio", "smb://other/share/a.mp4"))
check("locate: nfs:// on a mounted export (reference)",
      _locate("nfs://nas/export/music/x/a.mp3") == ("local", "/mnt/nfs music/x/a.mp3"))
check("locate: nfs:// not mounted -> gio (reference)",
      _locate("nfs://other/export/a.mp3") == ("gio", "nfs://other/export/a.mp3"))
check("locate: a Windows drive path through --map",
      _locate("Z:\\Videos\\a.mp4", {"Z:\\": "/dhuaddan/video/"}) == ("local", "/dhuaddan/video/Videos/a.mp4"))
check("locate: a Windows drive path without a map -> error naming --map",
      _locate("Z:\\Videos\\a.mp4")[0] == "error" and "--map" in _locate("Z:\\Videos\\a.mp4")[1])
check("locate: --map works for any prefix (longest wins)",
      _locate("smb://nas/media/a.mp4", {"smb://nas/": "/x/", "smb://nas/media/": "/y/"})
      == ("local", "/y/a.mp4"))

# --------------------------------------------------------------------------
# import_media(): copy into input/videos, rewrite the song list
# --------------------------------------------------------------------------

src = os.path.join(root, "nas")
os.makedirs(os.path.join(src, "sub"))
for name, data in (("Band - Clip.mp4", b"video"), ("Band - Song.mp3", b"audio"),
                   ("notes.txt", b"x"), ("sub/Band - Song.mp3", b"other audio")):
    with open(os.path.join(src, name), "wb") as f:
        f.write(data)
with open(os.path.join(inp, "videos", "Band - Song.mp3"), "wb") as f:
    f.write(b"audio")   # copied by an earlier run: same name, same size
csv_path = os.path.join(inp, "song-requests.csv")
rows = [["band name", "song name", "youtube link", "language", "genre"],
        ["Band", "Clip", os.path.join(src, "Band - Clip.mp4"), "de", "default,gothic"],
        ["Band", "Song", os.path.join(src, "Band - Song.mp3"), "de", "default"],
        ["Band", "Other", os.path.join(src, "sub", "Band - Song.mp3"), "en", "rock"],
        ["Band", "Link", "https://www.youtube.com/watch?v=x", "", ""],
        ["Band", "Notes", os.path.join(src, "notes.txt"), "", ""],
        ["Band", "Folder", src, "", ""],
        ["Band", "Gone", os.path.join(src, "gone.mp4"), "", ""],
        ["Band", "Remote", "smb://other/share/Band - Remote.mp4", "", ""],
        ["Band", "Inside", "videos/Band - Song.mp3", "", ""],
        ["Band", "Skip", "skip", "", ""]]
with open(csv_path, "w", encoding="utf-8", newline="") as f:
    csv.writer(f).writerows(rows)
with open(csv_path, "rb") as f:
    original = f.read()

gio_calls = []


def fake_gio(url, dest):
    gio_calls.append((url, dest))
    with open(dest, "wb") as f:
        f.write(b"remote")
    return True


def _import(apply):
    return ilm.import_media(csv_path, inp, apply=apply, mounts=_mounts,
                            resolve_host=lambda h: hosts.get(h, h), maps={}, gio_copy=fake_gio)


dry = _import(False)
with open(csv_path, "rb") as f:
    check("dry run: the song list is not changed", f.read() == original)
check(f"dry run: nothing copied (got {sorted(os.listdir(os.path.join(inp, 'videos')))})",
      sorted(os.listdir(os.path.join(inp, "videos"))) == ["Band - Song.mp3"] and not gio_calls)
check(f"dry run: what would happen is counted (got {dry['counts']})",
      dry["counts"] == {"copied": 3, "reused": 1, "rewritten": 0, "errors": 3})

summary = _import(True)
with open(csv_path, encoding="utf-8", newline="") as f:
    after = list(csv.reader(f))
cells = [r[2] for r in after]
check(f"apply: the copied files are named in the song list relative to the input (got {cells})",
      cells[1] == "videos/Band - Clip.mp4" and cells[2] == "videos/Band - Song.mp3"
      and cells[3] == "videos/Band - Song (1).mp3" and cells[8] == "videos/Band - Remote.mp4")
check("apply: same name and size is reused, a different file gets ' (1)'",
      open(os.path.join(inp, "videos", "Band - Song (1).mp3"), "rb").read() == b"other audio"
      and open(os.path.join(inp, "videos", "Band - Clip.mp4"), "rb").read() == b"video")
check("apply: an unmounted share is copied with gio into input/videos",
      gio_calls == [("smb://other/share/Band - Remote.mp4",
                     os.path.join(inp, "videos", "Band - Remote.mp4"))])
check("apply: links, skips, paths already in the input and errors stay as they are",
      cells[4] == "https://www.youtube.com/watch?v=x" and cells[9] == "videos/Band - Song.mp3"
      and cells[10] == "skip" and cells[5] == os.path.join(src, "notes.txt")
      and cells[6] == src and cells[7] == os.path.join(src, "gone.mp4"))
check("apply: the other columns stay", [r[:2] + r[3:] for r in after] == [r[:2] + r[3:] for r in rows])
check(f"apply: errors name the row and the reason (got {summary['errors']})",
      len(summary["errors"]) == 3
      and any("Notes" in e and "not an audio or video file" in e for e in summary["errors"])
      and any("Folder" in e and "not a file" in e for e in summary["errors"])
      and any("Gone" in e and "not found" in e for e in summary["errors"]))
check("apply: a backup of the old song list is kept",
      any(n.startswith("song-requests.csv.bak-") for n in os.listdir(inp)))

again = _import(True)
check(f"apply twice: everything is in the input now, nothing copied again (got {again['counts']})",
      again["counts"]["copied"] == 0 and len(gio_calls) == 1)

# a dry run counts a file two rows name once
_twice = os.path.join(root, "twice")
os.makedirs(os.path.join(_twice, "videos"))
with open(os.path.join(_twice, "song-requests.csv"), "w", encoding="utf-8") as f:
    f.write("band name,song name,youtube link\n"
            f"A,One,{os.path.join(src, 'Band - Clip.mp4')}\nA,Two,{os.path.join(src, 'Band - Clip.mp4')}\n")
_tw = ilm.import_media(os.path.join(_twice, "song-requests.csv"), _twice, apply=False, mounts=[],
                       maps={})
check(f"dry run: the same file in two rows is one copy and one reuse (got {_tw['counts']})",
      _tw["counts"]["copied"] == 1 and _tw["counts"]["reused"] == 1)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
