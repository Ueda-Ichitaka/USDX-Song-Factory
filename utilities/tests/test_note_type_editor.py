#!/usr/bin/env python3
"""Tests for note_type_editor.py.

About me: plain assert-based checks (no pytest), host only, temp song and
marks folders, no audio is played (the ffplay command is only built). The
Editor class (the app's state: lines, line and note cursor, wanted note
types, saving, the play command) is tested without Tk; a short smoke test
opens the Tk window hidden when a display is there. Run (from utilities/):

    python3 tests/test_note_type_editor.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import note_type_editor as ed  # noqa: E402
import note_types as nt  # noqa: E402

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


def write(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def wanted_in_file(path):
    return nt.wanted(nt.parse(open(song_path, "rb").read()), nt.read_marks(path))


root = tempfile.mkdtemp()
marks_dir = os.path.join(root, "note-types")
folder = os.path.join(root, "lib", "AC-DC - Hells Bells")
song_path = os.path.join(folder, "Hells Bells.txt")
write(song_path, b"#ARTIST:AC/DC\n#TITLE:Hells Bells\n#BPM:300\n#GAP:1000\n#AUDIO:a.opus\n"
                 b"#VOCALS:a [Vocals].opus\nF 0 4 0 Intro\n- 6\n: 8 4 0 Sung\n: 12 4 0  here\n- 18\n"
                 b": 20 4 0 Spoken\nE\n")
write(os.path.join(folder, "a.opus"), b"audio")
write(os.path.join(folder, "a [Vocals].opus"), b"vocals")

e = ed.Editor(marks_dir, player="/usr/bin/ffplay")
e.load(song_path)
check("load: the song's lines", [l.text for l in e.song.lines] == ["Intro", "Sung here", "Spoken"])
check("load: without a marks file the song's own note types", e.wanted() == {1: "F", 2: "::", 3: ":"})
check("load: cursor on line 1, no note picked, nothing to save", e.cursor == 0 and e.note is None and not e.dirty)
check("load: the marks file of the song", e.marks_path == os.path.join(marks_dir, "AC-DC - Hells Bells.tsv"))

e.move(1)
e.set_kind("R")
check("set_kind on a line: all its notes", e.wanted()[2] == "RR" and e.dirty and e.line_kind(2) == "R")
e.note_move(1)
check("note_move right: the first note of the line", e.note == 0)
e.note_move(1)
e.set_kind("*")
check("set_kind on a note: only that note, the line is mixed", e.wanted()[2] == "R*" and e.line_kind(2) == "mixed")
e.note_move(5)
check("note_move: stops at the last note", e.note == 1)
e.note_move(-1)
e.note_move(-1)
check("note_move left from the first note: back to the whole line", e.note is None)
e.move(1)
check("move: to another line drops the picked note", e.cursor == 2 and e.note is None)
e.toggle_freestyle()
check("toggle_freestyle: not all F -> F", e.wanted()[3] == "F")
e.toggle_freestyle()
check("toggle_freestyle: all F -> normal", e.wanted()[3] == ":")
e.move(-5)
check("move: stops at the first line", e.cursor == 0)
e.move(99)
check("move: stops at the last line", e.cursor == 2)
e.go(1)
e.note_move(1)
e.toggle_freestyle()
check("toggle_freestyle on a note", e.wanted()[2] == "F*")
e.set_kind("G")
check("set_kind: rap golden", e.wanted()[2] == "G*")
for bad in ("x", "mixed"):
    try:
        e.set_kind(bad)
        ok = False
    except ValueError:
        ok = True
    check(f"set_kind: '{bad}' is refused", ok)

saved = e.save()
check("save: writes the marks file, nothing left to save",
      saved == e.marks_path and not e.dirty and wanted_in_file(saved) == {1: "F", 2: "G*", 3: ":"})

again = ed.Editor(marks_dir, player="/usr/bin/ffplay")
again.load(song_path)
check("load: an existing marks file sets the types", again.wanted() == {1: "F", 2: "G*", 3: ":"}
      and "marks file" in again.notice)

write(e.marks_path, "band\tAC/DC\ntitle\tHells Bells\n2\tF\tSomething else\n".encode("utf-8"))
odd = ed.Editor(marks_dir, player="/usr/bin/ffplay")
odd.load(song_path)
check(f"load: a marks file that does not fit: the song's own types and a notice (got {odd.notice!r})",
      odd.wanted() == {1: "F", 2: "::", 3: ":"} and "does not fit" in odd.notice and "line 2" in odd.notice)

# playing: ffplay over the line (or the picked note), 0.3 s added on both sides
e.go(1)
cmd = e.play_command(vocals=False)
check(f"play_command: ffplay without a window over the line (beats 8-16 = 1.4-1.8 s) (got {cmd})",
      cmd == ["/usr/bin/ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
              "-ss", "1.100", "-t", "1.000", os.path.join(folder, "a.opus")])
e.note_move(1)
e.note_move(1)
check(f"play_command: the picked note only (beats 12-16 = 1.6-1.8 s) (got {e.play_command(vocals=False)[5:9]})",
      e.play_command(vocals=False)[5:9] == ["-ss", "1.300", "-t", "0.800"])
check("play_command: the vocals file", e.play_command(vocals=True)[-1] == os.path.join(folder, "a [Vocals].opus"))
check("span: the start never before 0 s", ed.Editor.span(0.1, 0.5) == (0.0, 0.8))


def refused(editor, **kw):
    try:
        editor.play_command(**kw)
    except ed.PlayError as err:
        return str(err)
    return ""


no_player = ed.Editor(marks_dir, player=None)
no_player.load(song_path)
check("play_command: no ffplay: named", "ffplay" in refused(no_player, vocals=False))
os.remove(os.path.join(folder, "a [Vocals].opus"))
check("play_command: the audio file is not there: named", "a [Vocals].opus" in refused(e, vocals=True))
write(os.path.join(root, "lib", "Rel", "r.txt"), b"#ARTIST:R\n#TITLE:R\n#BPM:300\n#RELATIVE:yes\n#AUDIO:a.opus\n: 0 4 0 x\nE\n")
rel = ed.Editor(marks_dir, player="/usr/bin/ffplay")
rel.load(os.path.join(root, "lib", "Rel", "r.txt"))
check("play_command: no times (#RELATIVE): named", "time" in refused(rel, vocals=False))
write(os.path.join(root, "lib", "Mp3", "m.txt"), b"#ARTIST:M\n#TITLE:M\n#BPM:300\n#GAP:0\n#MP3:m.mp3\n: 0 4 0 x\nE\n")
write(os.path.join(root, "lib", "Mp3", "m.mp3"), b"audio")
mp3 = ed.Editor(marks_dir, player="/usr/bin/ffplay")
mp3.load(os.path.join(root, "lib", "Mp3", "m.txt"))
check("play_command: #MP3 when there is no #AUDIO", mp3.play_command(vocals=False)[-1].endswith("m.mp3"))

for name, data in (("none.txt", b"#ARTIST:A\n#TITLE:T\nE\n"), ("notags.txt", b": 0 4 0 x\nE\n")):
    write(os.path.join(root, "bad", name), data)
    try:
        ed.Editor(marks_dir, player=None).load(os.path.join(root, "bad", name))
        ok = False
    except ValueError:
        ok = True
    check(f"load: refused: {name} (no lyrics line / no #ARTIST and #TITLE)", ok)

check("clock: m:ss.d", ed.clock(125.04) == "2:05.0" and ed.clock(None) == "")

# the Tk window (hidden), when a display is there
try:
    import tkinter
    tk_root = tkinter.Tk()
except Exception as err:  # no display
    print(f"[SKIP] Tk smoke test: {err}")
    tk_root = None
if tk_root is not None:
    tk_root.withdraw()
    write(e.marks_path, "band\tAC/DC\ntitle\tHells Bells\n1\tF\tIntro\n".encode("utf-8"))
    app = ed.App(tk_root, ed.Editor(marks_dir, player=None))
    app.open_song(song_path)
    rows = app.tree.get_children()
    check("Tk: one row per line", len(rows) == 3)
    check("Tk: the type column", [app.tree.set(r, "type") for r in rows] == ["F", "", ""])
    app.key_move(None, 1)
    check("Tk: Down moves the cursor", app.editor.cursor == 1 and app.tree.focus() == rows[1])
    check("Tk: the notes of the line under the cursor", "Sung" in app.notes_text() and "here" in app.notes_text())
    app.key_note(None, 1)
    app.key_note(None, 1)
    app.key_kind(None, "*")
    check("Tk: Right twice + g: the second note golden, the line mixed",
          app.editor.wanted()[2] == ":*" and app.tree.set(rows[1], "type") == "mixed")
    app.key_freestyle(None)
    check("Tk: Space on a note", app.editor.wanted()[2] == ":F")
    app.key_play(None)
    check("Tk: play without ffplay: a message, no crash", "ffplay" in app.status.get())
    app.key_save(None)
    check("Tk: Ctrl+S saves", wanted_in_file(e.marks_path) == {1: "F", 2: ":F", 3: ":"})
    tk_root.destroy()

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
