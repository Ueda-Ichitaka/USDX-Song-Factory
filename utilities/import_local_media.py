#!/usr/bin/env python3
"""Copy the local song files a song list names into the stack input.

About me: a song-request row may name an audio or video file instead of a
YouTube link (a song that is not on YouTube). The stack runs in a container
that only sees its input folder, so before a run this tool copies every
such file from wherever it is into stack/input/videos/ and rewrites the
row's link cell to "videos/<file name>" (the stack resolves it relative to
its input). Run it after saving a fresh dashboard export: a file copied by
an earlier run (same name, same size) is not copied again.

Where a path can be read from (locate()):

  videos/x.mp4, /data/input/...,  already in the stack input; an absolute
  an absolute path inside it      one is rewritten relative to it
  /any/absolute/path.mp4          read from there (local disk, a mounted share)
  smb://host/share/path,          the share's mount point when the share is
  //host/share/path,              mounted (/proc/self/mounts; host names and
  \\\\host\\share\\path           IPs both match, share names ignore case)
  nfs://host/export/path          the export's mount point when mounted *
  anything --map PREFIX=DIR       PREFIX replaced by DIR, e.g. a Windows
  names (Z:\\videos\\x.mp4)        drive: --map 'Z:\\=/dhuaddan/video/'
  an smb:// / nfs:// / UNC path   copied with "gio copy" (the desktop's
  whose share is not mounted      network layer and stored logins) *

* reference implementation: there are no NFS mounts and no unmounted
  shares on the machine this was written on, so these two paths are not
  tested against a real server - open for contribution.

Accepted files: the audio and video types the stack takes
(LOCAL_MEDIA_EXTENSIONS, the same list as stack/orchestrator). A name that
is taken in input/videos by a different file gets " (1)". The song list is
rewritten only with --apply (a backup song-requests.csv.bak-<time> is
kept); without it the tool lists what it would do.

  python3 import_local_media.py [--csv FILE] [--input DIR] [--map PREFIX=DIR ...]
                                [--apply]
"""

import argparse
import csv
import io
import ntpath
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

VIDEOS = "videos"
CONTAINER_INPUT = "/data/input/"
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".ogg", ".flac", ".opus")
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mkv", ".avi", ".mov")
LOCAL_MEDIA_EXTENSIONS = AUDIO_EXTENSIONS + VIDEO_EXTENSIONS
NETWORK_FS = ("cifs", "smb3", "smbfs", "nfs", "nfs4")
# the song list's link column, as stack/orchestrator's parse_songs_file() finds it
LINK_COLUMNS = ("url", "link", "youtube", "youtube link")
SKIP_VALUES = ("", "-", "skip", "none", "x")


def parse_mounts(text: str) -> list:
    """The network mounts of a /proc/self/mounts text: [(source, mount
    point, file system)], octal escapes (\\040 = space) decoded."""
    def unescape(value):
        return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)
    mounts = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] in NETWORK_FS:
            mounts.append((unescape(parts[0]), unescape(parts[1]), parts[2]))
    return mounts


def system_mounts() -> list:
    try:
        with open("/proc/self/mounts", encoding="utf-8") as f:
            return parse_mounts(f.read())
    except OSError:
        return []


def resolve_host(host: str) -> str:
    """The IP of a host name (the name itself when it cannot be resolved)."""
    try:
        return socket.gethostbyname(host)
    except OSError:
        return host


def _smb_parts(value: str):
    """(host, share, rest) of an smb:// URL, a //host/share or a UNC path,
    else None."""
    if value.lower().startswith("smb://"):
        path = urllib.parse.unquote(urllib.parse.urlsplit(value).path)
        host = urllib.parse.urlsplit(value).hostname or ""
    elif value.startswith("\\\\"):
        host, _, path = value[2:].replace("\\", "/").partition("/")
        path = "/" + path
    elif value.startswith("//"):
        host, _, path = value[2:].partition("/")
        path = "/" + path
    else:
        return None
    share, _, rest = path.lstrip("/").partition("/")
    return host, share, rest


def _smb_mount(host, share, rest, mounts, resolve):
    for source, target, fs in mounts:
        if fs not in ("cifs", "smb3", "smbfs") or not source.startswith("//"):
            continue
        m_host, _, m_share = source[2:].partition("/")
        if m_share.strip("/").lower() == share.lower() and \
                (m_host.lower() == host.lower() or resolve(m_host) == resolve(host)):
            return os.path.join(target, rest)
    return None


def _nfs_mount(value, mounts, resolve):
    """Reference implementation (untested against a real NFS server): the
    mounted path of an nfs://host/export/path URL, the longest matching
    export first."""
    parts = urllib.parse.urlsplit(value)
    host, path = parts.hostname or "", urllib.parse.unquote(parts.path)
    best = None
    for source, target, fs in mounts:
        if not fs.startswith("nfs") or ":" not in source:
            continue
        m_host, _, export = source.partition(":")
        export = "/" + export.strip("/")
        if (m_host.lower() == host.lower() or resolve(m_host) == resolve(host)) and \
                (path == export or path.startswith(export + "/")):
            if best is None or len(export) > len(best[0]):
                best = (export, os.path.join(target, path[len(export):].lstrip("/")))
    return best[1] if best else None


def locate(value: str, input_dir: str, mounts: list, resolve_host=resolve_host, maps=None):
    """Where a song-list path is read from: ("input", path relative to the
    input), ("local", absolute path), ("gio", URL) or ("error", reason)."""
    maps = maps or {}
    for prefix in sorted(maps, key=len, reverse=True):
        if value.startswith(prefix):
            return locate(maps[prefix] + value[len(prefix):].replace("\\", "/"), input_dir,
                          mounts, resolve_host)
    if value.startswith(CONTAINER_INPUT):
        return "input", value[len(CONTAINER_INPUT):]
    smb = _smb_parts(value)
    if smb:
        local = _smb_mount(*smb, mounts, resolve_host)
        if local:
            return "local", local
        host, share, rest = smb
        return "gio", f"smb://{host}/{share}/{rest}"
    if value.lower().startswith("nfs://"):
        local = _nfs_mount(value, mounts, resolve_host)
        return ("local", local) if local else ("gio", value)
    if re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
        return "error", f"a link that is no file path: {value}"
    if ntpath.splitdrive(value)[0] and re.match(r"^[A-Za-z]:", value):
        return "error", (f"a Windows drive path: {value} - tell the tool where that drive is "
                         "mounted here with --map 'X:\\=/mount/point/'")
    if os.path.isabs(value):
        real_input = os.path.realpath(input_dir)
        real = os.path.realpath(value)
        if real.startswith(real_input + os.sep):
            return "input", os.path.relpath(real, real_input).replace(os.sep, "/")
        return "local", value
    return "input", value


def gio_copy(url: str, dest: str) -> bool:
    """Reference implementation (untested against a real unmounted share):
    copies a network URL with the desktop's network layer."""
    try:
        proc = subprocess.run(["gio", "copy", url, dest], capture_output=True, timeout=3600)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and os.path.isfile(dest)


def target_name(videos_dir: str, name: str, size, planned=None) -> tuple:
    """(file name in input/videos, already there): the file's own name, a
    file of that name and size counts as the same file; a different file of
    that name gets " (1)", " (2)", ... `planned` ({name: size}) holds the
    copies of this run, so a dry run counts like a real one."""
    planned = {} if planned is None else planned
    stem, ext = os.path.splitext(name)
    candidate, n = name, 0
    while True:
        path = os.path.join(videos_dir, candidate)
        if candidate in planned:
            there = planned[candidate]
        elif os.path.exists(path):
            there = os.path.getsize(path)
        else:
            return candidate, False
        if size is not None and there == size:
            return candidate, True
        n += 1
        candidate = f"{stem} ({n}){ext}"


def _read_csv(path: str):
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = "," if first.count(",") >= first.count(";") else ";"
    newline = "\r\n" if "\r\n" in text else "\n"
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)), delimiter, newline, bom


def _write_csv(path: str, rows: list, delimiter: str, newline: str, bom: bool):
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter, lineterminator=newline).writerows(rows)
    with open(path, "wb") as f:
        f.write((b"\xef\xbb\xbf" if bom else b"") + buffer.getvalue().encode("utf-8"))


def import_media(csv_path: str, input_dir: str, apply: bool = False, mounts=None,
                 resolve_host=resolve_host, maps=None, gio_copy=gio_copy) -> dict:
    """Copy the song list's local files into input/videos and rewrite their
    cells; returns {"counts": {"copied", "reused", "rewritten", "errors"},
    "errors": [...], "actions": [...]}."""
    mounts = system_mounts() if mounts is None else mounts
    rows, delimiter, newline, bom = _read_csv(csv_path)
    counts = {"copied": 0, "reused": 0, "rewritten": 0, "errors": 0}
    errors, actions = [], []
    if not rows:
        return {"counts": counts, "errors": errors, "actions": actions}
    header = [h.strip().lower() for h in rows[0]]
    column = next((header.index(c) for c in LINK_COLUMNS if c in header), None)
    if column is None:
        errors.append(f"no link column ({', '.join(LINK_COLUMNS)}) in {csv_path}")
        counts["errors"] += 1
        return {"counts": counts, "errors": errors, "actions": actions}
    videos_dir = os.path.join(input_dir, VIDEOS)
    changed = False
    planned = {}
    for row in rows[1:]:
        if len(row) <= column:
            continue
        value = row[column].strip()
        label = " - ".join(c for c in row[:2] if c.strip()) or value
        if value.lower() in SKIP_VALUES or value.lower().startswith(("http://", "https://")):
            continue
        kind, where = locate(value, input_dir, mounts, resolve_host, maps)
        if kind == "error":
            errors.append(f"{label}: {where}")
            continue
        if kind == "input":
            if where != value:
                actions.append(f"rewrite  {label}: {value} -> {where}")
                counts["rewritten"] += 1
                row[column] = where
                changed = True
            continue
        if kind == "local" and os.path.isdir(where):
            errors.append(f"{label}: not a file but a folder: {value}")
            continue
        name = os.path.basename(urllib.parse.unquote(where) if kind == "gio" else where)
        if os.path.splitext(name)[1].lower() not in LOCAL_MEDIA_EXTENSIONS:
            errors.append(f"{label}: not an audio or video file: {value} "
                          f"(supported: {' '.join(LOCAL_MEDIA_EXTENSIONS)})")
            continue
        size = None
        if kind == "local":
            if not os.path.isfile(where):
                errors.append(f"{label}: not found: {value}")
                continue
            size = os.path.getsize(where)
        target, present = target_name(videos_dir, name, size, planned)
        new_value = f"{VIDEOS}/{target}"
        if present:
            actions.append(f"reuse    {label}: {new_value} (already copied)")
            counts["reused"] += 1
        else:
            actions.append(f"copy     {label}: {value} -> {new_value}")
            counts["copied"] += 1
            planned[target] = size
            if apply:
                os.makedirs(videos_dir, exist_ok=True)
                dest = os.path.join(videos_dir, target)
                if kind == "local":
                    shutil.copy2(where, dest)
                elif not gio_copy(where, dest):
                    errors.append(f"{label}: could not copy {value} with gio")
                    counts["copied"] -= 1
                    continue
        row[column] = new_value
        changed = True
    counts["errors"] = len(errors)
    if apply and changed:
        shutil.copy2(csv_path, f"{csv_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        _write_csv(csv_path, rows, delimiter, newline, bom)
    return {"counts": counts, "errors": errors, "actions": actions}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", default=os.path.join(REPO, "stack", "input", "song-requests.csv"))
    parser.add_argument("--input", default=None, help="the stack input folder (default: the csv's folder)")
    parser.add_argument("--map", action="append", default=[], metavar="PREFIX=DIR",
                        help="read paths starting with PREFIX from DIR (e.g. 'Z:\\=/mnt/z/')")
    parser.add_argument("--apply", action="store_true", help="copy and rewrite (default: dry run)")
    args = parser.parse_args()
    maps = {}
    for item in args.map:
        prefix, sep, folder = item.partition("=")
        if not sep:
            parser.error(f"--map needs PREFIX=DIR, got {item}")
        maps[prefix] = folder
    input_dir = args.input or os.path.dirname(os.path.abspath(args.csv))
    summary = import_media(args.csv, input_dir, apply=args.apply, maps=maps)
    for line in summary["actions"]:
        print(line)
    for line in summary["errors"]:
        print(f"error    {line}")
    c = summary["counts"]
    print(f"\n{c['copied']} to copy, {c['reused']} already copied, {c['rewritten']} paths made "
          f"relative, {c['errors']} errors")
    if not args.apply:
        print("dry run - nothing copied or written, use --apply")


if __name__ == "__main__":
    main()
