#!/usr/bin/env python3
"""Desktop app to set the note types of the lines and notes of a song.

About me: opens an UltraStar song file (file picker, or the path as the
argument), lists its lyrics lines and lets you step through them with the
keyboard and set the note type of a whole line or of single notes: normal,
golden, freestyle, rap, rap golden. ffplay plays the line or the picked
note (the song's #AUDIO/#MP3, or its #VOCALS). Saving writes the song's
marks file stack/input/note-types/<Band> - <Title>.tsv (note_types.py has
its layout); apply_note_types.py then writes the types into a copy of the
song file. Opening a song shows the types of its marks file, or - without
one, or when it does not fit the song any more - the song file's own types.
The song file itself is only read.

  python3 note_type_editor.py [SONG.txt] [--marks DIR]

Keys: Up/Down (k/j) line, PgUp/PgDn 10 lines, Home/End, Left/Right (h/l)
pick a note of the line (left of the first: the whole line again);
n normal, g golden, f freestyle, r rap, Shift+R rap golden, Space freestyle
on/off, Enter freestyle on/off and next line; p play, Esc stop, a play every
line you move to, v vocals file on/off, Ctrl+S save, Ctrl+O open, q quit.

Tk (Python standard library) and ffplay (ffmpeg) for the sound.
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import note_types  # noqa: E402

MARKS_DIR = os.path.join(REPO, "stack", "input", "note-types")
PAD_S = 0.3
KEYS = ("n normal   g golden   f freestyle   r rap   R rap golden   Space freestyle on/off   "
        "Enter freestyle + next\nUp/Down line   Left/Right note   p play   Esc stop   a autoplay   "
        "v vocals   Ctrl+S save   Ctrl+O open   q quit")
COLORS = {"*": "#f6c667", "F": "#d0d0d0", "R": "#9fd3f5", "G": "#c9b3f2", note_types.MIXED: "#e9e4c9"}


class PlayError(Exception):
    """Why a line or note cannot be played."""


def clock(seconds) -> str:
    if seconds is None:
        return ""
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:04.1f}"


class Editor:
    """The app's state: the song, the line and note cursor, the wanted types."""

    def __init__(self, marks_dir: str, player=None):
        self.marks_dir = marks_dir
        self.player = player
        self.song = None
        self.path = ""
        self.band = self.title = ""
        self.marks_path = ""
        self.types = {}
        self.cursor = 0
        self.note = None
        self.dirty = False
        self.notice = ""

    def load(self, path: str) -> None:
        with open(path, "rb") as f:
            song = note_types.parse(f.read())
        band, title = song.tags.get("ARTIST", ""), song.tags.get("TITLE", "")
        if not band or not title:
            raise ValueError("no song file: #ARTIST or #TITLE missing")
        if not song.lines:
            raise ValueError("the song file has no lyrics lines")
        self.song, self.path, self.band, self.title = song, path, band, title
        self.marks_path = os.path.join(self.marks_dir, note_types.marks_name(band, title))
        want = note_types.state(song)
        self.notice = "no marks file yet - the song file's own note types"
        if os.path.isfile(self.marks_path):
            marks = note_types.read_marks(self.marks_path)
            problems = note_types.check_marks(song, marks)
            if (marks.band, marks.title) != (band, title):
                problems.insert(0, f"it names {marks.band} - {marks.title}")
            if problems:
                self.notice = (f"the marks file does not fit the song ({'; '.join(problems)}) - "
                               "the song file's own note types")
            else:
                want = note_types.wanted(song, marks)
                self.notice = "note types from the marks file"
        self.types = {number: list(types) for number, types in want.items()}
        self.cursor, self.note, self.dirty = 0, None, False

    # --- moving
    def go(self, index: int) -> None:
        self.cursor = max(0, min(len(self.song.lines) - 1, index))
        self.note = None

    def move(self, delta: int) -> None:
        self.go(self.cursor + delta)

    def note_move(self, delta: int) -> None:
        """Pick a note of the line; left of the first note is the whole line."""
        last = len(self.line().notes) - 1
        position = -1 if self.note is None else self.note
        position = max(-1, min(last, position + delta))
        self.note = None if position < 0 else position

    # --- types
    def line(self):
        return self.song.lines[self.cursor]

    def _targets(self) -> list:
        return list(range(len(self.line().notes))) if self.note is None else [self.note]

    def set_kind(self, kind: str) -> None:
        if kind not in note_types.TYPES:
            raise ValueError(f"unknown note type: {kind}")
        types = self.types[self.line().number]
        for index in self._targets():
            types[index] = kind
        self.dirty = True

    def toggle_freestyle(self) -> None:
        types = self.types[self.line().number]
        kind = ":" if all(types[i] == "F" for i in self._targets()) else "F"
        self.set_kind(kind)

    def line_kind(self, number: int) -> str:
        types = self.types[number]
        return types[0] if len(set(types)) == 1 else note_types.MIXED

    def wanted(self) -> dict:
        return {number: "".join(types) for number, types in self.types.items()}

    def save(self) -> str:
        note_types.write_marks(self.marks_path, self.song, self.wanted())
        self.dirty = False
        return self.marks_path

    # --- playing
    @staticmethod
    def span(start: float, end: float) -> tuple:
        """The played time span: the line or note with PAD_S on both sides."""
        return max(0.0, start - PAD_S), end + PAD_S

    def play_command(self, vocals: bool) -> list:
        """The ffplay command for the line, or the picked note, under the cursor."""
        if not self.player:
            raise PlayError("ffplay not found - install ffmpeg to hear the lines")
        line = self.line()
        if self.note is None:
            first, last = line.start_beat, line.end_beat
        else:
            note = line.notes[self.note]
            first, last = note.start, note.start + note.length
        start, end = self.song.seconds(first), self.song.seconds(last)
        if start is None or end is None:
            raise PlayError("no line times in this song (#BPM missing or #RELATIVE:yes)")
        tags = self.song.tags
        name = tags.get("VOCALS") if vocals else (tags.get("AUDIO") or tags.get("MP3"))
        if not name:
            raise PlayError("the song names no " + ("#VOCALS file" if vocals else "#AUDIO or #MP3 file"))
        audio = os.path.join(os.path.dirname(self.path), name)
        if not os.path.isfile(audio):
            raise PlayError(f"the audio file is not there: {name}")
        begin, stop = self.span(start, end)
        return [self.player, "-nodisp", "-autoexit", "-loglevel", "error",
                "-ss", f"{begin:.3f}", "-t", f"{stop - begin:.3f}", audio]


class App:
    """The Tk window around an Editor."""

    def __init__(self, root, editor: Editor):
        import tkinter as tk
        from tkinter import font, ttk
        self.root, self.editor = root, editor
        self.process = None
        self.autoplay = False
        self.vocals = False
        self.folder = (os.environ.get("LIBRARY_DIR")
                       or note_types.env_value("LIBRARY_DIR", os.path.join(REPO, "stack", ".env"))
                       or os.path.expanduser("~"))
        root.title("Note type editor")
        root.geometry("1000x720")
        self.heading = tk.StringVar(value="Ctrl+O opens a song file")
        self.where = tk.StringVar()
        self.status = tk.StringVar()
        ttk.Label(root, textvariable=self.heading, padding=(8, 6)).pack(fill="x")
        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True)
        ttk.Style(root).configure("Treeview", rowheight=24)
        self.tree = ttk.Treeview(frame, columns=("type", "no", "time", "text"), show="headings",
                                 selectmode="browse")
        for col, title, width, stretch in (("type", "Type", 60, False), ("no", "#", 50, False),
                                           ("time", "Time", 80, False), ("text", "Line", 700, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w")
        for kind, color in COLORS.items():
            self.tree.tag_configure(f"kind{kind}", background=color, foreground="#000000")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        base = font.nametofont("TkDefaultFont")
        size = max(12, base.actual("size") + 3)
        self.notes = tk.Text(root, height=2, wrap="word", padx=8, pady=6, relief="flat", cursor="arrow",
                             font=(base.actual("family"), size))
        self.notes.pack(fill="x")
        for kind, color in COLORS.items():
            self.notes.tag_configure(f"kind{kind}", background=color, foreground="#000000")
        self.notes.tag_configure("picked", underline=True, font=(base.actual("family"), size, "bold"))
        self.notes.configure(state="disabled")
        ttk.Label(root, textvariable=self.where, padding=(8, 2)).pack(fill="x")
        ttk.Label(root, textvariable=self.status, padding=(8, 2), foreground="#7a2e00").pack(fill="x")
        ttk.Label(root, text=KEYS, padding=(8, 4)).pack(fill="x")
        bindings = {"<Up>": lambda e: self.key_move(e, -1), "<k>": lambda e: self.key_move(e, -1),
                    "<Down>": lambda e: self.key_move(e, 1), "<j>": lambda e: self.key_move(e, 1),
                    "<Prior>": lambda e: self.key_move(e, -10), "<Next>": lambda e: self.key_move(e, 10),
                    "<Home>": lambda e: self.key_move(e, -10 ** 6), "<End>": lambda e: self.key_move(e, 10 ** 6),
                    "<Left>": lambda e: self.key_note(e, -1), "<h>": lambda e: self.key_note(e, -1),
                    "<Right>": lambda e: self.key_note(e, 1), "<l>": lambda e: self.key_note(e, 1),
                    "<n>": lambda e: self.key_kind(e, ":"), "<g>": lambda e: self.key_kind(e, "*"),
                    "<f>": lambda e: self.key_kind(e, "F"), "<r>": lambda e: self.key_kind(e, "R"),
                    "<R>": lambda e: self.key_kind(e, "G"),
                    "<space>": self.key_freestyle, "<Return>": self.key_freestyle_next, "<p>": self.key_play,
                    "<Escape>": self.key_stop, "<a>": self.key_autoplay, "<v>": self.key_vocals,
                    "<Control-s>": self.key_save, "<Control-o>": self.key_open, "<q>": self.key_quit}
        for key, handler in bindings.items():
            self.tree.bind(key, handler)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        root.protocol("WM_DELETE_WINDOW", lambda: self.key_quit(None))
        self.tree.focus_set()

    # --- showing
    def refresh_row(self, index: int) -> None:
        number = self.editor.song.lines[index].number
        kind = self.editor.line_kind(number)
        item = self.tree.get_children()[index]
        self.tree.set(item, "type", "" if kind == ":" else kind)
        self.tree.item(item, tags=() if kind == ":" else (f"kind{kind}",))

    def show_notes(self) -> None:
        line = self.editor.line()
        types = self.editor.types[line.number]
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        for index, (note, kind) in enumerate(zip(line.notes, types)):
            text = note.text if index else note.text.lstrip()
            core = text.strip()
            lead = text[:len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):] if core else ""
            tags = [] if kind == ":" else [f"kind{kind}"]
            if index == self.editor.note:
                tags.append("picked")
            self.notes.insert("end", lead)
            self.notes.insert("end", core or "~", tuple(tags))
            self.notes.insert("end", trail)
        self.notes.configure(state="disabled")
        if self.editor.note is None:
            kind = self.editor.line_kind(line.number)
            self.where.set(f"line {line.number}: whole line ({note_types.TYPES.get(kind, kind)})"
                           "   Left/Right picks a note")
        else:
            note = line.notes[self.editor.note]
            self.where.set(f"line {line.number}, note {self.editor.note + 1} of {len(line.notes)}: "
                           f"'{note.text.strip()}' ({note_types.TYPES[types[self.editor.note]]})")

    def notes_text(self) -> str:
        return self.notes.get("1.0", "end").strip()

    def show_cursor(self) -> None:
        item = self.tree.get_children()[self.editor.cursor]
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.tree.see(item)
        self.show_notes()

    def show_title(self) -> None:
        e = self.editor
        star = " *" if e.dirty else ""
        changed = sum(1 for n in e.types if e.line_kind(n) != ":")
        self.root.title(f"Note type editor - {e.band} - {e.title}{star}")
        self.heading.set(f"{e.band} - {e.title}   ({changed} of {len(e.song.lines)} lines not normal{star})"
                         f"   {e.path}")

    def open_song(self, path: str) -> None:
        try:
            self.editor.load(path)
        except (OSError, ValueError) as err:
            self.status.set(f"cannot open {os.path.basename(path)}: {err}")
            return
        self.folder = os.path.dirname(path)
        self.tree.delete(*self.tree.get_children())
        song = self.editor.song
        for line in song.lines:
            self.tree.insert("", "end", values=("", line.number, clock(song.seconds(line.start_beat)), line.text))
        for index in range(len(song.lines)):
            self.refresh_row(index)
        self.show_cursor()
        self.show_title()
        self.status.set(self.editor.notice)

    def changed(self) -> None:
        self.refresh_row(self.editor.cursor)
        self.show_notes()
        self.show_title()

    # --- keys (each returns "break": the tree's own key handling is not wanted)
    def key_move(self, event, delta: int):
        if self.editor.song:
            self.editor.move(delta)
            self.show_cursor()
            if self.autoplay:
                self.key_play(None)
        return "break"

    def key_note(self, event, delta: int):
        if self.editor.song:
            self.editor.note_move(delta)
            self.show_notes()
            if self.autoplay:
                self.key_play(None)
        return "break"

    def on_select(self, event):
        selected = self.tree.selection()
        if self.editor.song and selected and self.tree.index(selected[0]) != self.editor.cursor:
            self.editor.go(self.tree.index(selected[0]))
            self.show_notes()

    def key_kind(self, event, kind: str):
        if self.editor.song:
            self.editor.set_kind(kind)
            self.changed()
        return "break"

    def key_freestyle(self, event):
        if self.editor.song:
            self.editor.toggle_freestyle()
            self.changed()
        return "break"

    def key_freestyle_next(self, event):
        self.key_freestyle(event)
        return self.key_move(event, 1)

    def key_play(self, event):
        if not self.editor.song:
            return "break"
        self.key_stop(None)
        try:
            command = self.editor.play_command(self.vocals)
        except PlayError as err:
            self.status.set(str(err))
            return "break"
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        what = "line" if self.editor.note is None else f"note {self.editor.note + 1} of line"
        self.status.set(f"playing {what} {self.editor.line().number}" + (" (vocals)" if self.vocals else ""))
        return "break"

    def key_stop(self, event):
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.process = None
        return "break"

    def key_autoplay(self, event):
        self.autoplay = not self.autoplay
        self.status.set(f"autoplay {'on' if self.autoplay else 'off'}")
        return "break"

    def key_vocals(self, event):
        self.vocals = not self.vocals
        self.status.set(f"playing {'the vocals file' if self.vocals else 'the song audio'}")
        return "break"

    def key_save(self, event):
        if self.editor.song:
            path = self.editor.save()
            self.show_title()
            self.status.set(f"saved {path}")
        return "break"

    def unsaved_ok(self) -> bool:
        """True when nothing is unsaved, or it was saved or may be dropped."""
        from tkinter import messagebox
        if not (self.editor.song and self.editor.dirty):
            return True
        answer = messagebox.askyesnocancel("Note type editor", "Save the note types of this song first?")
        if answer is None:
            return False
        if answer:
            self.key_save(None)
        return True

    def key_open(self, event):
        from tkinter import filedialog
        if not self.unsaved_ok():
            return "break"
        path = filedialog.askopenfilename(title="Open a song file", initialdir=self.folder,
                                          filetypes=[("UltraStar song file", "*.txt"), ("All files", "*")])
        if path:
            self.open_song(path)
        return "break"

    def key_quit(self, event):
        if self.unsaved_ok():
            self.key_stop(None)
            self.root.destroy()
        return "break"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("song", nargs="?", help="the song file to open (default: a file picker)")
    parser.add_argument("--marks", default=MARKS_DIR, help="the folder of the marks files")
    args = parser.parse_args()
    import tkinter as tk
    root = tk.Tk()
    app = App(root, Editor(args.marks, player=shutil.which("ffplay")))
    if args.song:
        app.open_song(args.song)
    else:
        root.after(100, lambda: app.key_open(None))
    root.mainloop()


if __name__ == "__main__":
    main()
