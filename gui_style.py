"""Old-mIRC look: Tahoma-8 Windows chrome, Fixedsys chat text when available, small pixel-art toolbar icons, tooltips."""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import gui_platform

UI_FONT = gui_platform.UI_FONT


def apply_classic(root):
    """Call before creating widgets: 3D Windows widgets, small Tahoma chrome like Windows 98/2000-era mIRC."""
    root.option_add("*Font", UI_FONT)
    root.option_add("*Menu.font", UI_FONT)
    st = ttk.Style()
    try: st.theme_use("winnative" if gui_platform.IS_WIN else "clam")
    except tk.TclError: st.theme_use("clam")
    st.configure(".", font=UI_FONT)
    st.configure("Treeview", rowheight=16, font=UI_FONT)
    st.configure("TButton", padding=(6, 1), font=UI_FONT)


def chat_font(size):
    """mIRC's classic chat font is Fixedsys; use it when Windows has it, otherwise the best fixed-width font this system has."""
    have = {x.lower() for x in tkfont.families()}
    for fam in (["Fixedsys"] if gui_platform.IS_WIN else []) + [gui_platform.MONO_FONT_NAME, "DejaVu Sans Mono", "Liberation Mono", "Menlo", "Consolas", "Courier New", "Courier"]:
        if fam.lower() in have: return tkfont.Font(family=fam, size=size)
    return tkfont.Font(family="TkFixedFont", size=size)


class Tooltip:
    def __init__(self, widget, text, delay=500):
        self.w, self.text, self.delay, self.tip, self.job = widget, text, delay, None, None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _): self.job = self.w.after(self.delay, self._show)

    def _show(self):
        x, y = self.w.winfo_rootx() + 4, self.w.winfo_rooty() + self.w.winfo_height() + 2
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg="#ffffe1", fg="black", relief="solid", bd=1, padx=3, font=UI_FONT).pack()

    def _leave(self, _=None):
        if self.job: self.w.after_cancel(self.job)
        self.job = None
        if self.tip: self.tip.destroy()
        self.tip = None


# ---- 16x16 icons drawn from a few primitives (no image files, no Pillow) ----
def _px(im, x, y, c):
    if 0 <= x < 16 and 0 <= y < 16: im.put(c, (x, y))

def _rect(im, x0, y0, x1, y1, c):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1): _px(im, x, y, c)

def _disc(im, cx, cy, r, c):
    for y in range(cy - r, cy + r + 1):
        for x in range(cx - r, cx + r + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r + r: _px(im, x, y, c)

def _line(im, x0, y0, x1, y1, c):
    dx, dy, sx, sy = abs(x1 - x0), -abs(y1 - y0), 1 if x0 < x1 else -1, 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        _px(im, x0, y0, c)
        if (x0, y0) == (x1, y1): return
        e2 = 2 * err
        if e2 >= dy: err, x0 = err + dy, x0 + sx
        if e2 <= dx: err, y0 = err + dx, y0 + sy


def _frame(im, c):
    _rect(im, 1, 1, 14, 14, c)


def make_icons(master):
    icons = {}
    def new(name):
        im = tk.PhotoImage(master=master, width=16, height=16)
        icons[name] = im
        return im
    im = new("connect")                                  # green disc with a white "go" triangle
    _disc(im, 8, 8, 7, "#1b5e20"); _disc(im, 8, 8, 6, "#43a047")
    for x in range(6, 12):
        h = (11 - x) * 4 // 5
        _line(im, x, 8 - h, x, 8 + h, "#ffffff")
    im = new("disconnect")                               # red disc with a white cross
    _disc(im, 8, 8, 7, "#7f0000"); _disc(im, 8, 8, 6, "#e53935")
    for d in (0, 1):
        _line(im, 5 + d, 5, 11 + d, 11, "#ffffff"); _line(im, 11 - d, 5, 5 - d, 11, "#ffffff")
    im = new("channels")                                 # a navy '#' on white paper
    _rect(im, 1, 1, 14, 14, "#808080"); _rect(im, 2, 2, 13, 13, "#ffffff")
    for x in (6, 9): _line(im, x, 3, x - 1, 12, "#000080")
    for y in (6, 9): _line(im, 3, y, 12, y, "#000080")
    im = new("nodes")                                    # three boxes joined by lines
    _line(im, 4, 4, 11, 4, "#404040"); _line(im, 4, 4, 8, 11, "#404040"); _line(im, 11, 4, 8, 11, "#404040")
    for x, y in ((2, 2), (10, 2), (6, 10)): _rect(im, x, y, x + 4, y + 4, "#1565c0"); _rect(im, x + 1, y + 1, x + 3, y + 3, "#90caf9")
    im = new("map")                                      # folded map with a red pin
    _rect(im, 1, 3, 14, 13, "#606060"); _rect(im, 2, 4, 13, 12, "#c8e6c9")
    _line(im, 2, 11, 13, 6, "#4fc3f7"); _line(im, 2, 12, 13, 7, "#4fc3f7")
    _line(im, 5, 4, 5, 12, "#9e9e9e"); _line(im, 10, 4, 10, 12, "#9e9e9e")
    _disc(im, 8, 7, 2, "#d32f2f")
    im = new("options")                                  # a grey gear
    for a, b in ((8, 1), (8, 14), (1, 8), (14, 8), (3, 3), (12, 3), (3, 12), (12, 12)): _rect(im, a - 1, b - 1, a, b, "#505050")
    _disc(im, 8, 8, 5, "#808080"); _disc(im, 8, 8, 2, "#d4d0c8")
    im = new("addons")                                   # a puzzle-ish block
    _rect(im, 2, 5, 12, 14, "#6a1b9a"); _rect(im, 3, 6, 11, 13, "#ab47bc"); _rect(im, 5, 2, 9, 5, "#6a1b9a"); _rect(im, 6, 3, 8, 5, "#ab47bc")
    return icons
