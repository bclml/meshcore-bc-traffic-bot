"""'Scan for devices' dialog: lists USB serial devices (and asks each who it is) and Bluetooth devices, so the right
MeshCore node can be picked when several are plugged in or nearby, whatever board it is."""
import re
import tkinter as tk
from tkinter import ttk

import meshcore_io as io
from gui_common import BG


class DeviceScanDialog(tk.Toplevel):
    COLS = (("kind", 70), ("where", 110), ("device", 250), ("result", 560))

    def __init__(self, app, on_pick):
        super().__init__(app.root, bg=BG)
        self.app, self.on_pick, self.rows = app, on_pick, {}
        self.title("Scan for devices")
        self.geometry("1020x360")
        self.transient(app.root)
        self.t = ttk.Treeview(self, columns=[c for c, _ in self.COLS], show="headings", selectmode="browse")
        for c, w in self.COLS:
            self.t.heading(c, text=c.capitalize())
            self.t.column(c, width=w, anchor="w")
        self.t.pack(fill="both", expand=True, padx=6, pady=6)
        self.t.bind("<Double-1>", lambda e: self.use())
        self.status = tk.Label(self, bg=BG, anchor="w", fg="#555", justify="left", wraplength=990,
                               text="Works with any board running MeshCore Companion firmware (Heltec, LilyGo, RAK, Seeed, ...). "
                                    "Devices running Repeater or Room-server firmware can't chat and show as 'not a Companion'.")
        self.status.pack(fill="x", padx=6)
        b = tk.Frame(self, bg=BG)
        b.pack(fill="x", padx=6, pady=6)
        ttk.Button(b, text="Use selected", command=self.use).pack(side="left", padx=2)
        ttk.Button(b, text="Scan again", command=self.scan).pack(side="left", padx=2)
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        self.scan()

    def _set(self, iid, result):
        if self.winfo_exists() and self.t.exists(iid): self.t.set(iid, "result", result)

    def scan(self):
        self.t.delete(*self.t.get_children())
        self.rows = {}
        cands = io.usb_candidates()
        for c in cands:
            iid = "usb:" + c["device"]
            self.rows[iid] = ("usb", c["device"])
            busy = self.app.connected and io.CONNECTION_ARGS[:2] == ["-s", c["device"]]
            self.t.insert("", "end", iid=iid, values=("USB", c["device"], c["description"], "connected to this app right now" if busy else "asking the device..."))
        if not cands: self.t.insert("", "end", iid="none", values=("USB", "-", "no USB serial devices found", "check the cable (data cable, not charge-only) and driver"))
        todo = [c for c in cands if not (self.app.connected and io.CONNECTION_ARGS[:2] == ["-s", c["device"]])]

        def probe_all():
            out = {}
            for c in todo:
                r = io.probe_device(["-s", c["device"]], timeout=25)
                out[c["device"]] = (f"OK: {r['model']} '{r['name']}', firmware {r['fw']}" + (f", {r['max_contacts']} contacts" if r["max_contacts"] else "")
                                    if r["ok"] else "not a Companion: " + r["why"])
                self.app.q.put(("call", lambda d=c["device"], t=out[c["device"]]: self._set("usb:" + d, t)))
            return len(todo)
        self.app.bg(probe_all, lambda r: None)
        self.status.config(text=self.status.cget("text"))
        self.t.insert("", "end", iid="ble-wait", values=("Bluetooth", "-", "scanning...", ""))

        def ble_done(lines):
            if not self.winfo_exists(): return
            self.t.delete("ble-wait")
            if isinstance(lines, Exception) or not lines:
                self.t.insert("", "end", iid="ble-none", values=("Bluetooth", "-", "no MeshCore Bluetooth devices seen", "power the node on and pair it in Windows Bluetooth settings"))
                return
            for i, line in enumerate(lines):
                iid = f"ble:{i}"
                self.rows[iid] = ("bluetooth", line)
                self.t.insert("", "end", iid=iid, values=("Bluetooth", re.split(r"\s+", line)[0], line, ""))
        self.app.bg(io.scan_ble, ble_done)

    def use(self):
        sel = self.t.selection()
        if not sel or sel[0] not in self.rows: return
        kind, value = self.rows[sel[0]]
        self.on_pick(kind, value if kind == "usb" else re.split(r"\s+", value)[0])
        self.destroy()
