#!/usr/bin/env python3
"""Desktop app to mark the lyrics lines of a song as freestyle.

About me: opens an UltraStar song file (file picker, or the path as the
argument), lists its lyrics lines and lets you step through them with the
keyboard, mark and unmark lines and hear each line (ffplay plays the song's
#AUDIO/#MP3, or its #VOCALS, from the line's start to its end). Saving
writes the song's marks file stack/input/freestyle/<Band> - <Title>.tsv
(freestyle.py has its layout); apply_freestyle.py then writes the marks
into a copy of the song file. Opening a song shows the marks of its marks
file, or - without one, or when it does not fit the song any more - the
lines already freestyle in the song file. The song file itself is only read.

  python3 freestyle_marker.py [SONG.txt] [--marks DIR]

Keys: Up/Down (k/j) line, PgUp/PgDn 10 lines, Home/End, Space mark/unmark,
Enter mark/unmark and next line, p play the line, Esc stop, a play every
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
import freestyle  # noqa: E402

MARKS_DIR = os.path.join(REPO, "stack", "input", "freestyle")
PAD_S = 0.3
KEYS = ("Space mark/unmark   Enter mark + next   Up/Down line   p play   Esc stop   "
        "a autoplay   v vocals   Ctrl+S save   Ctrl+O open   q quit")


class PlayError(Exception):
    """Why a line cannot be played."""


def clock(seconds) -> str:
    if seconds is None:
        return ""
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:04.1f}"


class Marker:
    """The app's state: the song, the cursor, the marked line numbers."""

    def __init__(self, marks_dir: str, player=None):
        self.marks_dir = marks_dir
        self.player = player
        self.song = None
        self.path = ""
        self.band = self.title = ""
        self.marks_path = ""
        self.marked = set()
        self.cursor = 0
        self.dirty = False
        self.notice = ""

    def load(self, path: str) -> None:
        with open(path, "rb") as f:
            song = freestyle.parse(f.read())
        band, title = song.tags.get("ARTIST", ""), song.tags.get("TITLE", "")
        if not band or not title:
            raise ValueError("no song file: #ARTIST or #TITLE missing")
        if not song.lines:
            raise ValueError("the song file has no lyrics lines")
        self.song, self.path, self.band, self.title = song, path, band, title
        self.marks_path = os.path.join(self.marks_dir, freestyle.marks_name(band, title))
        self.marked = {line.number for line in song.lines if line.freestyle}
        self.notice = "no marks file yet - the lines already freestyle in the song are marked"
        if os.path.isfile(self.marks_path):
            m_band, m_title, marks = freestyle.read_marks(self.marks_path)
            problems = freestyle.check_marks(song, marks)
            if (m_band, m_title) != (band, title):
                problems.insert(0, f"it names {m_band} - {m_title}")
            if problems:
                self.notice = (f"the marks file does not fit the song ({'; '.join(problems)}) - "
                               "the lines already freestyle in the song are marked")
            else:
                self.marked = {n for n, _ in marks}
                self.notice = "marks from the marks file"
        self.cursor = 0
        self.dirty = False

    def go(self, index: int) -> None:
        self.cursor = max(0, min(len(self.song.lines) - 1, index))

    def move(self, delta: int) -> None:
        self.go(self.cursor + delta)

    def toggle(self) -> None:
        self.marked ^= {self.song.lines[self.cursor].number}
        self.dirty = True

    def marks(self) -> list:
        return [(line.number, line.text) for line in self.song.lines if line.number in self.marked]

    def save(self) -> str:
        freestyle.write_marks(self.marks_path, self.band, self.title, self.marks())
        self.dirty = False
        return self.marks_path

    @staticmethod
    def span(start: float, end: float) -> tuple:
        """The played time span: the line with PAD_S on both sides."""
        return max(0.0, start - PAD_S), end + PAD_S

    def play_command(self, vocals: bool) -> list:
        """The ffplay command for the line under the cursor."""
        if not self.player:
            raise PlayError("ffplay not found - install ffmpeg to hear the lines")
        line = self.song.lines[self.cursor]
        start, end = self.song.seconds(line.start_beat), self.song.seconds(line.end_beat)
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
    """The Tk window around a Marker."""

    def __init__(self, root, marker: Marker):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.root, self.marker = tk, root, marker
        self.process = None
        self.autoplay = False
        self.vocals = False
        self.folder = (os.environ.get("LIBRARY_DIR")
                       or freestyle.env_value("LIBRARY_DIR", os.path.join(REPO, "stack", ".env"))
                       or os.path.expanduser("~"))
        root.title("Freestyle marker")
        root.geometry("1000x700")
        self.heading = tk.StringVar(value="Ctrl+O opens a song file")
        self.status = tk.StringVar()
        ttk.Label(root, textvariable=self.heading, padding=(8, 6)).pack(fill="x")
        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True)
        ttk.Style(root).configure("Treeview", rowheight=24)
        self.tree = ttk.Treeview(frame, columns=("mark", "no", "time", "text"), show="headings",
                                 selectmode="browse")
        for col, title, width, stretch in (("mark", "F", 40, False), ("no", "#", 50, False),
                                           ("time", "Time", 80, False), ("text", "Line", 700, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w")
        self.tree.tag_configure("marked", background="#f6c667", foreground="#000000")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Label(root, textvariable=self.status, padding=(8, 4), foreground="#7a2e00").pack(fill="x")
        ttk.Label(root, text=KEYS, padding=(8, 4)).pack(fill="x")
        bindings = {"<Up>": lambda e: self.key_move(e, -1), "<k>": lambda e: self.key_move(e, -1),
                    "<Down>": lambda e: self.key_move(e, 1), "<j>": lambda e: self.key_move(e, 1),
                    "<Prior>": lambda e: self.key_move(e, -10), "<Next>": lambda e: self.key_move(e, 10),
                    "<Home>": lambda e: self.key_move(e, -10 ** 6), "<End>": lambda e: self.key_move(e, 10 ** 6),
                    "<space>": self.key_toggle, "<Return>": self.key_toggle_next, "<p>": self.key_play,
                    "<Escape>": self.key_stop, "<a>": self.key_autoplay, "<v>": self.key_vocals,
                    "<Control-s>": self.key_save, "<Control-o>": self.key_open, "<q>": self.key_quit}
        for key, handler in bindings.items():
            self.tree.bind(key, handler)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        root.protocol("WM_DELETE_WINDOW", lambda: self.key_quit(None))
        self.tree.focus_set()

    # --- showing
    def refresh_row(self, index: int) -> None:
        line = self.marker.song.lines[index]
        marked = line.number in self.marker.marked
        item = self.tree.get_children()[index]
        self.tree.set(item, "mark", "F" if marked else ("(F)" if line.partly_freestyle else ""))
        self.tree.item(item, tags=("marked",) if marked else ())

    def show_cursor(self) -> None:
        item = self.tree.get_children()[self.marker.cursor]
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.tree.see(item)

    def show_title(self) -> None:
        m = self.marker
        star = " *" if m.dirty else ""
        self.root.title(f"Freestyle marker - {m.band} - {m.title}{star}")
        self.heading.set(f"{m.band} - {m.title}   ({len(m.marked)} of {len(m.song.lines)} lines freestyle{star})"
                         f"   {m.path}")

    def open_song(self, path: str) -> None:
        try:
            self.marker.load(path)
        except (OSError, ValueError) as e:
            self.status.set(f"cannot open {os.path.basename(path)}: {e}")
            return
        self.folder = os.path.dirname(path)
        self.tree.delete(*self.tree.get_children())
        song = self.marker.song
        for line in song.lines:
            self.tree.insert("", "end", values=("", line.number, clock(song.seconds(line.start_beat)), line.text))
        for index in range(len(song.lines)):
            self.refresh_row(index)
        self.show_cursor()
        self.show_title()
        self.status.set(self.marker.notice)

    # --- keys (each returns "break": the tree's own key handling is not wanted)
    def key_move(self, event, delta: int):
        if self.marker.song:
            self.marker.move(delta)
            self.show_cursor()
            if self.autoplay:
                self.key_play(None)
        return "break"

    def on_select(self, event):
        selected = self.tree.selection()
        if self.marker.song and selected:
            self.marker.go(self.tree.index(selected[0]))

    def key_toggle(self, event):
        if self.marker.song:
            self.marker.toggle()
            self.refresh_row(self.marker.cursor)
            self.show_title()
        return "break"

    def key_toggle_next(self, event):
        self.key_toggle(event)
        return self.key_move(event, 1)

    def key_play(self, event):
        if not self.marker.song:
            return "break"
        self.key_stop(None)
        try:
            command = self.marker.play_command(self.vocals)
        except PlayError as e:
            self.status.set(str(e))
            return "break"
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        self.status.set(f"playing line {self.marker.song.lines[self.marker.cursor].number}"
                        + (" (vocals)" if self.vocals else ""))
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
        if self.marker.song:
            path = self.marker.save()
            self.show_title()
            self.status.set(f"saved {path}")
        return "break"

    def unsaved_ok(self) -> bool:
        """True when nothing is unsaved, or it was saved or may be dropped."""
        from tkinter import messagebox
        if not (self.marker.song and self.marker.dirty):
            return True
        answer = messagebox.askyesnocancel("Freestyle marker", "Save the marks of this song first?")
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
    app = App(root, Marker(args.marks, player=shutil.which("ffplay")))
    if args.song:
        app.open_song(args.song)
    else:
        root.after(100, lambda: app.key_open(None))
    root.mainloop()


if __name__ == "__main__":
    main()
