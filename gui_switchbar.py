"""The switchbar: one small button per private (direct-message) window, red when it has unread messages.

Like an old mIRC toolbar it has a grip: drag the grip to the top, bottom, left or right edge of the window to dock it
there, or drop it anywhere else to let it float as a little tool window.  Right-click the grip for a menu, double-click
it to float/dock.  Right- or middle-click a button to close that conversation."""
import tkinter as tk

from gui_common import BG

FONT = ("Tahoma", 8)
EDGE = 46          # how close (pixels) to a window edge a drop must be to dock there
DOCKS = ("top", "bottom", "left", "right")


class SwitchBar:
    def __init__(self, app):
        self.app, self.root = app, app.root
        self.dock = app.settings.get("switchbar_dock", "top")
        self.dock = self.dock if self.dock in DOCKS + ("float",) else "top"
        self.last_dock = self.dock if self.dock in DOCKS else "top"
        self.xy = tuple(app.settings.get("switchbar_xy", (140, 140)))
        self.items, self.buttons = {}, {}          # name -> {state,current,click,close} / name -> tk.Button
        self.host = self.win = self.hint = None
        self.build()

    # ---- content -----------------------------------------------------------------------------------------------
    KIND_FG = {2: "#1565c0", 3: "#6a1b9a"}     # repeaters blue, room servers purple; people black

    def add(self, name, click, close, menu=None, kind=0):
        self.items[name] = {"state": "", "current": False, "click": click, "close": close, "menu": menu, "kind": kind}
        self.build()

    def set_kind(self, name, kind):
        if name in self.items and self.items[name]["kind"] != kind:
            self.items[name]["kind"] = kind
            self._style()

    def remove(self, name):
        self.items.pop(name, None)
        self.build()

    def sync(self, windows, current):
        for name, it in self.items.items():
            w = windows.get(name)
            it["current"] = w is current
            it["state"] = w.unread if w else ""
        self._style()

    def _style(self):
        for name, b in self.buttons.items():
            it = self.items[name]
            if it["current"]: b.config(relief="sunken", bg=BG, fg="black", activebackground=BG)
            elif it["state"] == "msg": b.config(relief="raised", bg="#ff2020", fg="white", activebackground="#ff5050")
            elif it["state"] == "event": b.config(relief="raised", bg=BG, fg="#0000cc", activebackground=BG)
            else: b.config(relief="raised", bg=BG, fg=self.KIND_FG.get(it["kind"], "black"), activebackground=BG)

    # ---- (re)building the host ---------------------------------------------------------------------------------
    def build(self):
        for w in (self.host, self.win):
            if w is not None and w.winfo_exists(): w.destroy()
        self.win = None
        vertical = self.dock in ("left", "right")
        if self.dock == "float":
            self.win = tk.Toplevel(self.root, bg=BG)
            self.win.title("Private messages")
            self.win.transient(self.root)
            try: self.win.wm_attributes("-toolwindow", True)
            except tk.TclError: pass
            self.win.geometry(f"+{self.xy[0]}+{self.xy[1]}")
            self.win.protocol("WM_DELETE_WINDOW", lambda: self.set_dock(self.last_dock))
            self.win.bind("<Configure>", self._moved)
            self.host = tk.Frame(self.win, bg=BG, bd=1, relief="raised")
            self.host.pack(fill="both", expand=True)
        else:
            self.host = tk.Frame(self.root, bg=BG, bd=1, relief="raised")
            if self.dock == "top": self.host.pack(side="top", fill="x", after=self.app.toolbar)
            elif self.dock == "bottom": self.host.pack(side="bottom", fill="x", before=self.app.paned)
            elif self.dock == "left": self.host.pack(side="left", fill="y", before=self.app.paned)
            else: self.host.pack(side="right", fill="y", before=self.app.paned)
        side = "top" if vertical else "left"
        grip = tk.Frame(self.host, bg=BG, cursor="fleur")
        grip.pack(side=side, fill="x" if vertical else "y", padx=(0 if vertical else 2, 0), pady=(2 if vertical else 0, 2 if vertical else 0))
        for _ in range(2):
            line = tk.Frame(grip, bd=1, relief="raised", bg=BG, width=2 if not vertical else 1, height=1 if vertical else 2)
            line.pack(side="left" if not vertical else "top", fill="y" if not vertical else "x", padx=1, pady=1)
        grip.configure(width=8 if not vertical else 1, height=1 if not vertical else 8)
        for w in [grip, *grip.winfo_children()]:
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Button-3>", self._menu)
            w.bind("<Double-Button-1>", lambda e: self.set_dock(self.last_dock if self.dock == "float" else "float"))
        self.buttons = {}
        if not self.items:
            tk.Label(self.host, text="Private\nmessages" if vertical else "Private messages", fg="#808080", bg=BG, font=FONT).pack(side=side, padx=4)
        for name, it in self.items.items():
            label = name.lstrip("@")
            b = tk.Button(self.host, text=label if len(label) <= 12 else label[:11] + "…", font=FONT, bg=BG, bd=1, padx=3, pady=0,
                          width=12 if vertical else 0, command=it["click"], takefocus=False)
            b.bind("<Button-2>", lambda e, n=name: self.items[n]["close"]())      # middle click closes
            b.bind("<Button-3>", lambda e, n=name: (self.items[n]["menu"](e.x_root, e.y_root) if self.items[n]["menu"] else self.items[n]["close"]()))
            b.pack(side=side, fill="x" if vertical else "none", padx=1, pady=1)
            self.buttons[name] = b
        self._style()

    # ---- docking by dragging the grip --------------------------------------------------------------------------
    def _zone(self, xr, yr):
        rx, ry = xr - self.root.winfo_rootx(), yr - self.root.winfo_rooty()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if rx < 0 or ry < 0 or rx > w or ry > h: return "float"
        if ry < EDGE: return "top"
        if ry > h - EDGE: return "bottom"
        if rx < EDGE: return "left"
        if rx > w - EDGE: return "right"
        return "float"

    def _drag_start(self, e):
        self._press = (e.x_root, e.y_root)
        self._moved_enough = False

    def _drag_move(self, e):
        if abs(e.x_root - self._press[0]) + abs(e.y_root - self._press[1]) < 6 and not self._moved_enough: return
        self._moved_enough = True
        self._show_hint(self._zone(e.x_root, e.y_root))

    def _drag_end(self, e):
        self._show_hint(None)
        if not getattr(self, "_moved_enough", False): return
        zone = self._zone(e.x_root, e.y_root)
        if zone == "float": self.xy = (max(0, e.x_root - 20), max(0, e.y_root - 10))
        self.set_dock(zone)

    def _show_hint(self, zone):
        if self.hint is not None: self.hint.destroy(); self.hint = None
        if zone in DOCKS:
            self.hint = tk.Frame(self.root, bg="#316ac5")
            geo = {"top": dict(x=0, y=0, relwidth=1, height=5), "bottom": dict(x=0, rely=1.0, anchor="sw", relwidth=1, height=5),
                   "left": dict(x=0, y=0, width=5, relheight=1), "right": dict(relx=1.0, y=0, anchor="ne", width=5, relheight=1)}[zone]
            self.hint.place(**geo)
            self.hint.lift()

    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        for where in DOCKS: m.add_command(label=("• " if self.dock == where else "   ") + f"Dock {where}", command=lambda w=where: self.set_dock(w))
        m.add_command(label=("• " if self.dock == "float" else "   ") + "Float", command=lambda: self.set_dock("float"))
        m.tk_popup(e.x_root, e.y_root)

    def set_dock(self, where):
        if where == self.dock: return
        if where in DOCKS: self.last_dock = where
        self.dock = where
        self.build()
        self.app.settings["switchbar_dock"], self.app.settings["switchbar_xy"] = where, list(self.xy)
        self.app.save()

    def _moved(self, e):
        if e.widget is self.win: self.xy = (self.win.winfo_x(), self.win.winfo_y())

    def remember(self):
        self.app.settings["switchbar_dock"], self.app.settings["switchbar_xy"] = self.dock, list(self.xy)
