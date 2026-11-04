#!/usr/bin/env python3
"""Tests for freestyle_marker.py.

About me: plain assert-based checks (no pytest), host only, temp song and
marks folders, no audio is played (the ffplay command is only built). The
Marker class (the app's state: lines, cursor, marks, saving, the play
command) is tested without Tk; a short smoke test opens the Tk window
hidden when a display is there. Run (from utilities/):

    python3 tests/test_freestyle_marker.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import freestyle as fs  # noqa: E402
import freestyle_marker as fm  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


def write(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


root = tempfile.mkdtemp()
marks_dir = os.path.join(root, "freestyle")
folder = os.path.join(root, "lib", "AC-DC - Hells Bells")
song_path = os.path.join(folder, "Hells Bells.txt")
write(song_path, b"#ARTIST:AC/DC\n#TITLE:Hells Bells\n#BPM:300\n#GAP:1000\n#AUDIO:a.opus\n"
                 b"#VOCALS:a [Vocals].opus\nF 0 4 0 Intro\n- 6\n: 8 4 0 Sung\n- 14\n: 16 4 0 Spoken\nE\n")
write(os.path.join(folder, "a.opus"), b"audio")
write(os.path.join(folder, "a [Vocals].opus"), b"vocals")

m = fm.Marker(marks_dir, player="/usr/bin/ffplay")
m.load(song_path)
check("load: the song's lines", [l.text for l in m.song.lines] == ["Intro", "Sung", "Spoken"])
check("load: without a marks file the song's freestyle lines are marked", m.marked == {1})
check("load: the cursor on line 1, nothing to save", m.cursor == 0 and not m.dirty)
check("load: the marks file of the song", m.marks_path == os.path.join(marks_dir, "AC-DC - Hells Bells.tsv"))

m.move(1)
m.toggle()
check("toggle: marks the line under the cursor, something to save", m.marked == {1, 2} and m.dirty)
m.move(-5)
check("move: stops at the first line", m.cursor == 0)
m.toggle()
check("toggle: unmarks a marked line", m.marked == {2})
m.move(99)
check("move: stops at the last line", m.cursor == 2)
m.go(1)
check("go: to a line by index", m.cursor == 1)
check("marks: (number, text) of the marked lines", m.marks() == [(2, "Sung")])

saved = m.save()
check("save: writes the marks file, nothing left to save",
      saved == m.marks_path and not m.dirty
      and fs.read_marks(saved) == ("AC/DC", "Hells Bells", [(2, "Sung")]))

again = fm.Marker(marks_dir, player="/usr/bin/ffplay")
again.load(song_path)
check("load: an existing marks file sets the marks (not the song's F lines)",
      again.marked == {2} and "marks file" in again.notice)

fs.write_marks(m.marks_path, "AC/DC", "Hells Bells", [(2, "Something else")])
odd = fm.Marker(marks_dir, player="/usr/bin/ffplay")
odd.load(song_path)
check(f"load: a marks file that does not fit: the song's F lines and a notice (got {odd.notice!r})",
      odd.marked == {1} and "does not fit" in odd.notice and "line 2" in odd.notice)

# playing a line: ffplay from the line's start to its end, 0.3 s added on both sides
m.go(1)
cmd = m.play_command(vocals=False)
check(f"play_command: ffplay without a window, the line's time span (got {cmd})",
      cmd == ["/usr/bin/ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
              "-ss", "1.100", "-t", "0.800", os.path.join(folder, "a.opus")])
check("play_command: the vocals file", m.play_command(vocals=True)[-1] == os.path.join(folder, "a [Vocals].opus"))
m.go(0)
check("play_command: the start never before 0 s",
      fm.Marker.span(0.1, 0.5) == (0.0, 0.8) and m.play_command(vocals=False)[6] == "0.700")


def refused(marker, **kw):
    try:
        marker.play_command(**kw)
    except fm.PlayError as e:
        return str(e)
    return ""


no_player = fm.Marker(marks_dir, player=None)
no_player.load(song_path)
check("play_command: no ffplay: named", "ffplay" in refused(no_player, vocals=False))
os.remove(os.path.join(folder, "a [Vocals].opus"))
check("play_command: the audio file is not there: named", "a [Vocals].opus" in refused(m, vocals=True))
write(os.path.join(root, "lib", "Rel", "r.txt"), b"#ARTIST:R\n#TITLE:R\n#BPM:300\n#RELATIVE:yes\n#AUDIO:a.opus\n: 0 4 0 x\nE\n")
rel = fm.Marker(marks_dir, player="/usr/bin/ffplay")
rel.load(os.path.join(root, "lib", "Rel", "r.txt"))
check("play_command: no times (#RELATIVE): named", "time" in refused(rel, vocals=False))
write(os.path.join(root, "lib", "Mp3", "m.txt"), b"#ARTIST:M\n#TITLE:M\n#BPM:300\n#GAP:0\n#MP3:m.mp3\n: 0 4 0 x\nE\n")
write(os.path.join(root, "lib", "Mp3", "m.mp3"), b"audio")
mp3 = fm.Marker(marks_dir, player="/usr/bin/ffplay")
mp3.load(os.path.join(root, "lib", "Mp3", "m.txt"))
check("play_command: #MP3 when there is no #AUDIO", mp3.play_command(vocals=False)[-1].endswith("m.mp3"))

for name, data in (("none.txt", b"#ARTIST:A\n#TITLE:T\nE\n"), ("notags.txt", b": 0 4 0 x\nE\n")):
    write(os.path.join(root, "bad", name), data)
    try:
        fm.Marker(marks_dir, player=None).load(os.path.join(root, "bad", name))
        ok = False
    except ValueError:
        ok = True
    check(f"load: refused: {name} (no lyrics line / no #ARTIST and #TITLE)", ok)

check("clock: m:ss.d", fm.clock(125.04) == "2:05.0" and fm.clock(None) == "")

# the Tk window (hidden), when a display is there
try:
    import tkinter
    tk_root = tkinter.Tk()
except Exception as e:  # no display
    print(f"[SKIP] Tk smoke test: {e}")
    tk_root = None
if tk_root is not None:
    tk_root.withdraw()
    fs.write_marks(m.marks_path, "AC/DC", "Hells Bells", [(2, "Sung")])
    app = fm.App(tk_root, fm.Marker(marks_dir, player=None))
    app.open_song(song_path)
    rows = app.tree.get_children()
    check("Tk: one row per line", len(rows) == 3)
    check("Tk: marked lines shown as marked", app.tree.set(rows[1], "mark") == "F" and app.tree.set(rows[0], "mark") == "")
    app.key_toggle(None)
    check("Tk: Space on line 1 marks it", app.tree.set(rows[0], "mark") == "F" and app.marker.marked == {1, 2})
    app.key_move(None, 1)
    check("Tk: Down moves the cursor", app.marker.cursor == 1 and app.tree.focus() == rows[1])
    app.key_play(None)
    check("Tk: play without ffplay: a message, no crash", "ffplay" in app.status.get())
    app.key_save(None)
    check("Tk: Ctrl+S saves", fs.read_marks(m.marks_path)[2] == [(1, "Intro"), (2, "Sung")])
    tk_root.destroy()

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
