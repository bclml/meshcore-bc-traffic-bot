"""Map window: every node the radio has ever told us about (from the long-term node memory) plus map layers
contributed by addons (e.g. incidents, earthquakes).  Everything has a show/hide toggle.

Uses real OpenStreetMap tiles when `pip install tkintermapview` is available, otherwise a plain lat/lon plot."""
import time
import tkinter as tk
from tkinter import ttk

from gui_common import BG, safe_text
from gui_nodes import TYPE_NAMES

try:
    import tkintermapview
except ImportError:
    tkintermapview = None

BC_CENTER, BC_ZOOM = (49.3, -123.0), 8
REFRESH_MS = 15000
TYPE_COLORS = {1: "#2e7d32", 2: "#1565c0", 3: "#6a1b9a", 4: "#00838f"}
OFF_RADIO_OUTLINE = "#9e9e9e"


def ago(ts):
    d = max(0, time.time() - ts)
    return f"{int(d // 60)} min ago" if d < 3600 else f"{d / 3600:.1f} h ago" if d < 172800 else f"{d / 86400:.0f} days ago"


class MapWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app, self.markers, self._sig, self._job = app, [], None, None
        self.title("Map - nodes and layers")
        self.geometry("1000x640")
        self.show_type = {t: tk.BooleanVar(value=True) for t in TYPE_NAMES}
        self.show_memory = tk.BooleanVar(value=True)
        self.show_names = tk.BooleanVar(value=False)
        self.show_me = tk.BooleanVar(value=True)
        self.max_days = tk.StringVar(value="0")
        self.layer_vars = {}
        side = tk.Frame(self, bg=BG, width=210)
        side.pack(side="left", fill="y", padx=4, pady=4)
        side.pack_propagate(False)
        box = tk.LabelFrame(side, text="Nodes", bg=BG)
        box.pack(fill="x")
        for t, name in TYPE_NAMES.items():
            tk.Checkbutton(box, text=name + "s", variable=self.show_type[t], command=self.refresh, bg=BG, fg=TYPE_COLORS[t],
                           selectcolor="white", anchor="w").pack(fill="x")
        tk.Checkbutton(box, text="Remembered, not on radio", variable=self.show_memory, command=self.refresh, bg=BG, anchor="w").pack(fill="x")
        tk.Checkbutton(box, text="Show names", variable=self.show_names, command=self.refresh, bg=BG, anchor="w").pack(fill="x")
        r = tk.Frame(box, bg=BG)
        r.pack(fill="x")
        tk.Label(r, text="Seen within (days, 0=all):", bg=BG).pack(side="left")
        e = tk.Entry(r, textvariable=self.max_days, width=4)
        e.pack(side="left")
        e.bind("<Return>", lambda _: self.refresh())
        self.layer_box = tk.LabelFrame(side, text="Layers", bg=BG)
        self.layer_box.pack(fill="x", pady=6)
        tk.Checkbutton(self.layer_box, text="My node", variable=self.show_me, command=self.refresh, bg=BG, anchor="w").pack(fill="x")
        ttk.Button(side, text="Refresh", command=lambda: self.refresh(force=True)).pack(fill="x", pady=2)
        ttk.Button(side, text="Center on BC", command=self.center).pack(fill="x", pady=2)
        ttk.Button(side, text="Node list...", command=app.open_node_list).pack(fill="x", pady=2)
        self.stats = tk.Label(side, bg=BG, justify="left", anchor="nw", wraplength=200)
        self.stats.pack(fill="x", pady=6)
        self.info = tk.Label(side, bg="white", relief="sunken", justify="left", anchor="nw", wraplength=200, height=6, text="Click a marker for details.")
        self.info.pack(fill="x", side="bottom")
        if tkintermapview:
            self.map = tkintermapview.TkinterMapView(self, corner_radius=0)
            self.center()
        else:
            self.map = tk.Canvas(self, bg="white", highlightthickness=0)
            self.map.bind("<Configure>", lambda e: self.refresh(force=True))
        self.map.pack(side="left", fill="both", expand=True)
        self.refresh()

    def center(self):
        if tkintermapview:
            self.map.set_position(*BC_CENTER)
            self.map.set_zoom(BC_ZOOM)

    # ---- data ----
    def _sync_layer_boxes(self):
        for name, (_, color) in self.app.map_layers.items():
            if name not in self.layer_vars:
                self.layer_vars[name] = tk.BooleanVar(value=True)
                tk.Checkbutton(self.layer_box, text=name, variable=self.layer_vars[name], command=self.refresh, bg=BG, fg=color,
                               selectcolor="white", anchor="w").pack(fill="x")

    def points(self):
        s, pts = self.app.settings, []
        try: max_age = float(self.max_days.get() or 0) * 86400
        except ValueError: max_age = 0
        now = time.time()
        shown = 0
        for n in self.app.nodes.all():
            if not (n["lat"] or n["lon"]) or not self.show_type.get(n["type"], tk.BooleanVar(value=False)).get(): continue
            if not n["on_radio"] and not self.show_memory.get(): continue
            if max_age and now - n["last_seen"] > max_age: continue
            kind = TYPE_NAMES.get(n["type"], "Node")
            info = f"{n['name']}\n{kind}{'' if n['on_radio'] else ' (remembered, not on radio)'}\nseen {ago(n['last_seen'])}\n{n['lat']:.4f}, {n['lon']:.4f}\n{n['public_key'][:12]}..."
            pts.append((n["lat"], n["lon"], n["name"] if self.show_names.get() else "", TYPE_COLORS.get(n["type"], "#555"),
                        "#555" if n["on_radio"] else OFF_RADIO_OUTLINE, info))
            shown += 1
        if self.show_me.get():
            pts.append((s["node_lat"], s["node_lon"], s["node_name"], "#000000", "#000000", f"{s['node_name']} (this node)"))
        for name, (provider, color) in self.app.map_layers.items():
            if not self.layer_vars.get(name, tk.BooleanVar(value=True)).get(): continue
            try: items = provider()
            except Exception: items = []
            for lat, lon, label in items: pts.append((lat, lon, safe_text(label)[:30], color, color, safe_text(label)))
        st = self.app.nodes.stats()
        self.stats.config(text=f"{shown} node(s) shown\n{st['total']} remembered, {st['on_radio']} on the radio\n{st['positioned']} with a position")
        return pts

    def refresh(self, force=False):
        if not self.winfo_exists(): return
        self._sync_layer_boxes()
        pts = self.points()
        sig = hash(tuple(pts))
        if force or sig != self._sig:
            self._sig = sig
            (self._draw_tiles if tkintermapview else self._draw_plain)(pts)
        if self._job: self.after_cancel(self._job)
        self._job = self.after(REFRESH_MS, self.refresh)

    # ---- drawing ----
    def _draw_tiles(self, pts):
        for m in self.markers: m.delete()
        self.markers = []
        for lat, lon, label, fill, outline, info in pts:
            m = self.map.set_marker(lat, lon, text=label, marker_color_circle=outline, marker_color_outside=fill,
                                    command=lambda marker: self.info.config(text=marker.data))
            m.data = info
            self.markers.append(m)

    def _draw_plain(self, pts):
        c = self.map
        c.delete("all")
        w, h = max(c.winfo_width(), 200), max(c.winfo_height(), 200)
        lats, lons = [p[0] for p in pts] or [49.0], [p[1] for p in pts] or [-123.0]
        lat0, lat1 = min(lats) - 0.2, max(lats) + 0.2
        lon0, lon1 = min(lons) - 0.2, max(lons) + 0.2
        x = lambda lon: 30 + (lon - lon0) / (lon1 - lon0) * (w - 60)
        y = lambda lat: h - 30 - (lat - lat0) / (lat1 - lat0) * (h - 60)
        c.create_text(10, 10, anchor="nw", text="pip install tkintermapview for the street map", fill="#808080")
        for lat, lon, label, fill, outline, info in pts:
            px, py = x(lon), y(lat)
            c.create_oval(px - 4, py - 4, px + 4, py + 4, fill=fill, outline=outline)
            if label: c.create_text(px + 7, py, text=label, anchor="w", font=("Segoe UI", 8))
