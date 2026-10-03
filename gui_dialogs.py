"""mIRC-style dialogs: Options (category tree + pages, including pages contributed by addons), Addons manager,
node list, channel list, About."""
import os, time
import tkinter as tk
from tkinter import ttk, messagebox

import meshcore_io as ea
import gui_themes
import gui_sounds
from gui_common import BG, CHANNELS, channel_index
from gui_nodes import TYPE_NAMES
from gui_nodepages import NodePages
from gui_logs import LOG_DIR
from gui_devices import DeviceScanDialog
from gui_update_ui import CatalogDialog
import gui_addons as ga
from tkinter import filedialog


class OptionsDialog(tk.Toplevel):
    PAGES = {"Connect": "_page_connect", "Node memory": "_page_nodes", "Map": "_page_map", "Display": "_page_display", "Sounds": "_page_sounds"}

    def __init__(self, app):
        super().__init__(app.root, bg=BG)
        self.app, s = app, app.settings
        self.title("Options")
        self.geometry("720x540")
        self.transient(app.root)
        keys = ("mode", "port", "baud", "ble_target", "tcp_host", "tcp_port", "node_name", "location", "poll_seconds", "node_lat", "node_lon", "node_prune_days",
                "node_sync_minutes", "radio_capacity", "prune_radio", "advert_listen", "advert_notices", "show_time", "font_size", "auto_connect", "log_enabled", "log_history", "check_updates",
                "theme", "highlight_words", "sounds_enabled", "sound_private", "sound_mention", "sound_highlight", "sound_channel", "sound_custom")
        self.vars = {k: (tk.BooleanVar if isinstance(s[k], bool) else tk.StringVar)(value=s[k] if isinstance(s[k], bool) else str(s[k])) for k in keys}
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=6, pady=6)
        self.tree = ttk.Treeview(body, show="tree", selectmode="browse", height=10)
        self.tree.pack(side="left", fill="y")
        self.stage = tk.Frame(body, bg=BG, bd=2, relief="groove")
        self.stage.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.frames = {}
        self.node_pages = NodePages(self)
        node_frames = self.node_pages.build(self.stage)
        order = ["Connect", *node_frames, "Node memory", "Map", "Display", "Sounds"]
        for name in order:
            self.tree.insert("", "end", iid=name, text=name)
            self.frames[name] = node_frames[name] if name in node_frames else getattr(self, self.PAGES[name])(tk.Frame(self.stage, bg=BG))
        for name, (inst, _) in app.addons.loaded.items():
            holder = tk.Frame(self.stage, bg=BG)  # the addon builds inside this; this is what gets shown/hidden
            try: page = inst.build_options(holder)
            except Exception as e:
                app.status_line(f"*** Addon '{name}' options page failed: {e}", "error")
                continue
            if page is not None:
                self.tree.insert("", "end", iid="addon:" + name, text="Addon: " + (inst.title or name))
                self.frames["addon:" + name] = holder
                page.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show(self.tree.selection()[0]))
        self.tree.selection_set("Connect")
        if app.connected: self.node_pages.read()
        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=6, pady=(0, 6))
        for text, cmd in (("Cancel", self.destroy), ("Apply", self.apply), ("OK", self.ok)):
            ttk.Button(btns, text=text, command=cmd).pack(side="right", padx=3)

    def _show(self, name):
        for f in self.frames.values(): f.pack_forget()
        self.frames[name].pack(fill="both", expand=True, padx=10, pady=10)

    def _row(self, parent, label, key, width=24, show=None):
        r = tk.Frame(parent, bg=BG)
        r.pack(fill="x", pady=3)
        tk.Label(r, text=label, bg=BG, width=30, anchor="w").pack(side="left")
        tk.Entry(r, textvariable=self.vars[key], width=width, show=show or "").pack(side="left")

    def _head(self, f, text): tk.Label(f, text=text, bg=BG, font=("Segoe UI", 9, "bold")).pack(anchor="w")

    def _page_connect(self, f):
        self._head(f, "Connection to your MeshCore node")
        tk.Label(f, text="Any board running MeshCore Companion firmware works (Heltec, LilyGo, RAK, Seeed, ...).", bg=BG, fg="#555", wraplength=440, justify="left").pack(anchor="w")
        for text, val in (("USB (serial)", "usb"), ("Bluetooth (BLE)", "bluetooth"), ("WiFi / network (TCP)", "tcp")):
            tk.Radiobutton(f, text=text, value=val, variable=self.vars["mode"], bg=BG, anchor="w").pack(fill="x")
        self._row(f, "USB port ('auto' = detect):", "port", 12)
        self._row(f, "USB baud rate (blank = default):", "baud", 12)
        self._row(f, "Bluetooth name/address:", "ble_target", 24)
        self._row(f, "WiFi host (IP or name):", "tcp_host", 24)
        self._row(f, "WiFi port:", "tcp_port", 8)
        ttk.Button(f, text="Scan for devices...", command=self.scan_devices).pack(anchor="w", pady=4)
        self._row(f, "Node nick:", "node_name")
        self._row(f, "Message poll (sec):", "poll_seconds", 6)
        tk.Checkbutton(f, text="Connect automatically on startup", variable=self.vars["auto_connect"], bg=BG).pack(anchor="w", pady=4)
        return f

    def scan_devices(self):
        def pick(kind, value):
            self.vars["mode"].set(kind)
            self.vars["port" if kind == "usb" else "ble_target"].set(value)
        DeviceScanDialog(self.app, pick)

    def _page_nodes(self, f):
        self._head(f, "Node memory")
        tk.Label(f, text="The radio only holds about 350 nodes. Everything it lists is also kept in nodes.db so the map and node list can show more than the radio can.", bg=BG, fg="#555", justify="left", wraplength=400).pack(anchor="w", pady=(0, 6))
        self._row(f, "Forget nodes unseen for (days):", "node_prune_days", 6)
        tk.Label(f, text="(0 = never forget)", bg=BG, fg="#555").pack(anchor="w")
        self._row(f, "Read the radio every (min):", "node_sync_minutes", 6)
        self._row(f, "Radio capacity (contacts):", "radio_capacity", 6)
        tk.Checkbutton(f, text="Listen for adverts between polls (new nodes appear at once; USB and WiFi)", variable=self.vars["advert_listen"], bg=BG).pack(anchor="w", pady=(6, 0))
        tk.Checkbutton(f, text="Say in the Status window when a node is heard for the first time", variable=self.vars["advert_notices"], bg=BG).pack(anchor="w")
        tk.Checkbutton(f, text="Also delete forgotten nodes from the radio itself", variable=self.vars["prune_radio"], bg=BG).pack(anchor="w", pady=4)
        self.node_stats = tk.Label(f, bg=BG, justify="left", wraplength=400)
        self.node_stats.pack(anchor="w", pady=4)
        self._update_node_stats()
        r = tk.Frame(f, bg=BG)
        r.pack(anchor="w")
        ttk.Button(r, text="Read radio & forget stale now", command=self._node_action).pack(side="left", padx=2)
        ttk.Button(r, text="Node list...", command=self.app.open_node_list).pack(side="left", padx=2)
        return f

    def _update_node_stats(self):
        st = self.app.nodes.stats()
        self.node_stats.config(text=f"Remembered: {st['total']}   On the radio: {st['on_radio']} / {self.vars['radio_capacity'].get()}   With a position: {st['positioned']}")

    def _node_action(self):
        if self.apply(): self.app.sync_nodes_now(then=self._update_node_stats)

    def _page_sounds(self, f):
        self._head(f, "Notification sounds")
        tk.Checkbutton(f, text="Play sounds (only when you are not looking at that window)", variable=self.vars["sounds_enabled"], bg=BG).pack(anchor="w", pady=(0, 6))
        choices = list(gui_sounds.SOUNDS) + ["Custom file"]
        for event, (label, _) in gui_sounds.EVENTS.items():
            r = tk.Frame(f, bg=BG)
            r.pack(fill="x", pady=2)
            tk.Label(r, text=label, bg=BG, width=44, anchor="w").pack(side="left")
            ttk.Combobox(r, textvariable=self.vars["sound_" + event], values=choices, state="readonly", width=13).pack(side="left", padx=4)
            ttk.Button(r, text="Test", width=5, command=lambda e=event: gui_sounds.play(self.vars["sound_" + e].get(), self.vars["sound_custom"].get(), self.bell)).pack(side="left")
        self._row(f, "Custom .wav file:", "sound_custom", 34)
        tk.Label(f, text="Pick 'Custom file' above to use it. A short pause between sounds stops a busy mesh from beeping non-stop.", bg=BG, fg="#555", wraplength=440, justify="left").pack(anchor="w", pady=4)
        return f

    def _page_map(self, f):
        self._head(f, "Position of this node on the map")
        self._row(f, "Latitude:", "node_lat", 12)
        self._row(f, "Longitude:", "node_lon", 12)
        return f

    def _page_display(self, f):
        self._head(f, "Display")
        r = tk.Frame(f, bg=BG)
        r.pack(fill="x", pady=3)
        tk.Label(r, text="Colour theme:", bg=BG, width=30, anchor="w").pack(side="left")
        ttk.Combobox(r, textvariable=self.vars["theme"], values=list(gui_themes.THEMES), state="readonly", width=16).pack(side="left")
        self._row(f, "Highlight words (comma separated):", "highlight_words", 24)
        tk.Label(f, text="@nickname and @[nick name] in messages are highlighted automatically (stronger when it is your name).", bg=BG, fg="#555", wraplength=420, justify="left").pack(anchor="w")
        tk.Checkbutton(f, text="Show timestamps", variable=self.vars["show_time"], bg=BG).pack(anchor="w", pady=(6, 0))
        self._row(f, "Font size:", "font_size", 4)
        tk.Checkbutton(f, text="Check for updates when the GUI starts (once a day)", variable=self.vars["check_updates"], bg=BG).pack(anchor="w", pady=(8, 0))
        tk.Checkbutton(f, text="Keep a log file per window (logs/ folder, one .txt each)", variable=self.vars["log_enabled"], bg=BG).pack(anchor="w", pady=(8, 0))
        self._row(f, "Log lines shown after restart:", "log_history", 6)
        ttk.Button(f, text="Open logs folder", command=lambda: (os.makedirs(LOG_DIR, exist_ok=True), os.startfile(LOG_DIR))).pack(anchor="w", pady=4)
        return f

    def apply(self):
        s = self.app.settings
        try:
            new = {k: v.get() for k, v in self.vars.items()}
            for k in ("poll_seconds", "font_size", "node_sync_minutes", "radio_capacity", "tcp_port"): new[k] = max(1, int(new[k]))
            new["log_history"] = max(0, int(new["log_history"]))
            new["node_prune_days"] = max(0, float(new["node_prune_days"]))
            for k in ("node_lat", "node_lon"): new[k] = float(new[k])
        except ValueError:
            messagebox.showerror("Options", "Please check the numeric fields.", parent=self)
            return False
        s.update(new)
        for name, (inst, _) in self.app.addons.loaded.items():
            if "addon:" + name in self.frames: self.app.addons._call(name, "apply_options")
        self.app.apply_settings()
        return True

    def ok(self):
        if self.apply(): self.destroy()


class AddonsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root, bg=BG)
        self.app = app
        self.title("Addons")
        self.geometry("720x330")
        self.transient(app.root)
        cols = ("on", "title", "version", "file", "description")
        self.t = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (40, 170, 60, 130, 300)):
            self.t.heading(c, text={"on": "On"}.get(c, c.capitalize()))
            self.t.column(c, width=w, anchor="w")
        self.t.pack(fill="both", expand=True, padx=6, pady=6)
        self.t.bind("<Double-1>", lambda e: self.toggle())
        b = tk.Frame(self, bg=BG)
        b.pack(fill="x", padx=6, pady=(0, 6))
        for text, cmd in (("Enable / disable", self.toggle), ("Reload", self.reload), ("Browse online catalog...", lambda: CatalogDialog(app)),
                          ("Install from file...", self.install_file), ("Install from folder...", self.install_folder), ("Uninstall", self.uninstall)):
            ttk.Button(b, text=text, command=cmd).pack(side="left", padx=2)
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        self.hint = tk.Label(self, bg=BG, fg="#555", justify="left", wraplength=690,
                             text="Addons are optional extras (for example broadcasting alerts) - none are installed by default. Use 'Browse online catalog' for "
                                  "tested addons. To write your own: copy addons/_example_addon.py to addons/my_addon.py, edit it, then Reload (see docs/ADDONS.md).")
        self.hint.pack(pady=(0, 6), padx=6)
        self.fill()

    def fill(self):
        self.t.delete(*self.t.get_children())
        for n in self.app.addons.discover():
            title, ver, _, desc = self.app.addons.info(n)
            self.t.insert("", "end", iid=n, values=("[x]" if n in self.app.addons.loaded else "[ ]", title, ver, n + ".py", desc))

    def _sel(self): return self.t.selection()[0] if self.t.selection() else None

    def _do_install(self, path):
        if not messagebox.askokcancel("Install addon", "An addon runs code with full access to your PC.\nInstall only addons you trust.\n\n" + path, parent=self): return
        try: info = self.app.addons.install(path)
        except (ValueError, OSError, KeyError) as e:
            messagebox.showerror("Install addon", f"Could not install:\n{e}", parent=self)
            return
        msg = f"'{info['title']}' {info['version']} installed and switched on."
        if info["missing"]: msg += f"\n\nIt needs extra Python packages - run:\npip install {' '.join(info['missing'])}\nthen use Reload."
        messagebox.showinfo("Install addon", msg, parent=self)
        self.fill()

    def install_file(self):
        path = filedialog.askopenfilename(parent=self, title="Choose an addon (.zip, addon.json or .py)", initialdir=os.path.join(ga.BASE_DIR, "packages"),
                                          filetypes=[("Addon package", "*.zip *.json *.py"), ("All files", "*.*")])
        if path: self._do_install(path)

    def install_folder(self):
        path = filedialog.askdirectory(parent=self, title="Choose an addon package folder (contains addon.json)", initialdir=os.path.join(ga.BASE_DIR, "packages"))
        if path: self._do_install(path)

    def uninstall(self):
        n = self._sel()
        if n and messagebox.askyesno("Uninstall addon", f"Remove the addon '{n}'?\nIts settings are kept in case you install it again.", parent=self):
            self.app.addons.uninstall(n)
            self.fill()

    def toggle(self):
        n = self._sel()
        if n: self.app.addons.set_enabled(n, n not in self.app.addons.loaded); self.fill()

    def reload(self):
        n = self._sel()
        if n: self.app.addons.reload(n); self.fill()


class NodeListDialog(tk.Toplevel):
    COLS = (("name", 170), ("type", 90), ("last seen", 110), ("radio", 60), ("lat", 80), ("lon", 80), ("key", 140))

    def __init__(self, app):
        super().__init__(app.root, bg=BG)
        self.app, self.sort = app, ("last seen", True)
        self.title("Nodes")
        self.geometry("820x460")
        self.t = ttk.Treeview(self, columns=[c for c, _ in self.COLS], show="headings")
        for c, w in self.COLS:
            self.t.heading(c, text=c.capitalize(), command=lambda c=c: self.sort_by(c))
            self.t.column(c, width=w, anchor="w")
        self.t.pack(fill="both", expand=True, padx=6, pady=6)
        self.t.bind("<Double-1>", self.open_query)
        self.count = tk.Label(self, bg=BG, anchor="w")
        self.count.pack(fill="x", padx=6)
        b = tk.Frame(self, bg=BG)
        b.pack(fill="x", padx=6, pady=6)
        ttk.Button(b, text="Read radio now", command=lambda: app.sync_nodes_now(then=self.fill)).pack(side="left")
        tk.Label(b, text="  Double-click a node to message it.", bg=BG, fg="#555").pack(side="left")
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        self.fill()

    def open_query(self, _):
        """Double-click a node to start a direct message with it."""
        sel = self.t.selection()
        if not sel: return
        vals = self.t.item(sel[0], "values")
        node = self.app.nodes.find_by_prefix(vals[6])
        self.app.open_query(vals[0], node["public_key"] if node else None)

    def sort_by(self, col):
        self.sort = (col, not self.sort[1] if self.sort[0] == col else False)
        self.fill()

    def fill(self):
        if not self.winfo_exists(): return
        keyf = {"name": lambda n: n["name"].lower(), "type": lambda n: n["type"], "last seen": lambda n: n["last_seen"], "radio": lambda n: n["on_radio"],
                "lat": lambda n: n["lat"], "lon": lambda n: n["lon"], "key": lambda n: n["public_key"]}[self.sort[0]]
        rows = sorted(self.app.nodes.all(), key=keyf, reverse=self.sort[1])
        self.t.delete(*self.t.get_children())
        for n in rows:
            age = (time.time() - n["last_seen"]) / 86400
            self.t.insert("", "end", values=(n["name"], TYPE_NAMES.get(n["type"], "?"), f"{age:.1f} d ago" if age >= 1 else f"{age * 24:.1f} h ago",
                                              "yes" if n["on_radio"] else "memory", f"{n['lat']:.4f}" if n["lat"] else "", f"{n['lon']:.4f}" if n["lon"] else "", n["public_key"][:16]))
        st = self.app.nodes.stats()
        self.count.config(text=f"{st['total']} remembered, {st['on_radio']} on the radio, {st['positioned']} with a position")


class ChannelListDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root, bg=BG)
        self.title("Channels list")
        self.geometry("640x260")
        self.transient(app.root)
        cols = ("channel", "index", "users", "topic")
        t = ttk.Treeview(self, columns=cols, show="headings")
        for c, w in zip(cols, (110, 50, 50, 410)):
            t.heading(c, text=c.capitalize())
            t.column(c, width=w, anchor="w")
        names = [n for n in app.windows if n != "Status" and not n.startswith("@")]
        for raw in ea.CHANNEL_INDEX_BY_NAME:
            n = "Public" if raw == "Public" else "#" + raw.lstrip("#")
            if n not in names: names.append(n)
        for name in names:
            idx = channel_index(name)
            w = app.windows.get(name)
            t.insert("", "end", iid=name, values=(name, "?" if idx is None else idx, len(w.nicks) if w else 0, w.topic if w else "(closed - double-click to reopen)"))
        t.pack(fill="both", expand=True, padx=6, pady=6)
        t.bind("<Double-1>", lambda e: (app.open_channel(t.selection()[0]), self.destroy()) if t.selection() else None)
        tk.Label(self, text="Double-click a channel to open it. Right-click a channel in the window tree for more options.", bg=BG).pack(pady=(0, 6))


def show_about(root):
    messagebox.showinfo("About", "mcIRC\n\nAn mIRC-style chat client for MeshCore nodes.\n"
                       "Alerts, broadcasting and anything else are addons (Tools > Addons).", parent=root)
