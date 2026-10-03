"""Options pages for the USB node's own settings: radio, behaviour, and a status/actions page."""
import gui_platform
import tkinter as tk
from tkinter import ttk, messagebox

import gui_nodecfg as cfg
from gui_common import BG


class NodePages:
    TITLES = ("Node: radio", "Node: behaviour", "Node: status")

    def __init__(self, dlg):
        self.dlg, self.app, self.old, self.node = dlg, dlg.app, None, None
        S, B = tk.StringVar, tk.BooleanVar
        self.v = {k: S() for k in ("name", "lat", "lon", "freq", "bw", "sf", "cr", "tx", "telem_base", "telem_loc", "telem_env", "path_hash", "pin")}
        self.v.update({k: B() for k in ("multi_acks", "loc_policy", "manual_add")})
        self.msg = []   # message labels, one per page

    # ---- layout helpers ----
    def _row(self, f, label, key, values=None, width=14):
        r = tk.Frame(f, bg=BG)
        r.pack(fill="x", pady=2)
        tk.Label(r, text=label, bg=BG, width=24, anchor="w").pack(side="left")
        w = ttk.Combobox(r, textvariable=self.v[key], values=values, width=width - 2) if values else tk.Entry(r, textvariable=self.v[key], width=width)
        w.pack(side="left")

    def _buttons(self, f):
        m = tk.Label(f, bg=BG, fg="#555", justify="left", wraplength=420, anchor="w", text="Not read yet - connect, then press 'Read from node'.")
        m.pack(fill="x", side="bottom", pady=4)
        self.msg.append(m)
        r = tk.Frame(f, bg=BG)
        r.pack(side="bottom", anchor="w", pady=4)
        ttk.Button(r, text="Read from node", command=self.read).pack(side="left", padx=2)
        ttk.Button(r, text="Write changes to node", command=self.write).pack(side="left", padx=2)

    def build(self, stage):
        a, b, c = (tk.Frame(stage, bg=BG) for _ in range(3))
        tk.Label(a, text="This node: identity and radio", bg=BG, font=(gui_platform.DIALOG_FONT_NAME, 9, "bold")).pack(anchor="w")
        self._buttons(a)
        for label, key, vals in (("Node name:", "name", None), ("Latitude:", "lat", None), ("Longitude:", "lon", None),
                                 ("Frequency (MHz):", "freq", None), ("Bandwidth (kHz):", "bw", cfg.BW_CHOICES),
                                 ("Spreading factor (5-12):", "sf", [str(n) for n in range(5, 13)]),
                                 ("Coding rate (5-8):", "cr", ["5", "6", "7", "8"]), ("TX power (dBm):", "tx", None)):
            self._row(a, label, key, vals)
        tk.Label(a, text="Changing frequency / bandwidth / SF / CR needs a reboot; you'll be asked after writing. All stations on the mesh must use the same radio settings.",
                 bg=BG, fg="#555", justify="left", wraplength=440).pack(anchor="w", pady=4)
        tk.Label(b, text="This node: behaviour", bg=BG, font=(gui_platform.DIALOG_FONT_NAME, 9, "bold")).pack(anchor="w")
        self._buttons(b)
        tk.Checkbutton(b, text="Multi-acks (extra acknowledgements)", variable=self.v["multi_acks"], bg=BG).pack(anchor="w")
        tk.Checkbutton(b, text="Share my location in adverts", variable=self.v["loc_policy"], bg=BG).pack(anchor="w")
        tk.Checkbutton(b, text="Add contacts manually (don't auto-add from adverts)", variable=self.v["manual_add"], bg=BG).pack(anchor="w")
        for label, key in (("Base telemetry:", "telem_base"), ("Location telemetry:", "telem_loc"), ("Environment telemetry:", "telem_env")):
            self._row(b, label, key, cfg.TELEMETRY)
        self._row(b, "Path hash mode (0-2):", "path_hash", ["0", "1", "2"])
        self._row(b, "BLE pin (0 = default):", "pin")
        tk.Label(c, text="This node: status and actions", bg=BG, font=(gui_platform.DIALOG_FONT_NAME, 9, "bold")).pack(anchor="w")
        self.status_lbl = tk.Label(c, bg="white", relief="sunken", justify="left", anchor="nw", wraplength=440, height=13, text="Not read yet.")
        self.status_lbl.pack(fill="x", pady=4)
        for row in ((("Refresh", self.read), ("Send advert", lambda: self.act("advert")), ("Send flood advert", lambda: self.act("floodadv"))),
                    (("Sync clock", lambda: self.act("clock sync")), ("Reboot node", self.reboot))):
            r = tk.Frame(c, bg=BG)
            r.pack(anchor="w")
            for text, fn in row: ttk.Button(r, text=text, command=fn).pack(side="left", padx=2, pady=2)
        return dict(zip(self.TITLES, (a, b, c)))

    # ---- reading ----
    def say(self, text):
        for m in self.msg: m.config(text=text)

    def read(self):
        if not self.app.require_connection(): return
        self.say("Reading from the node...")
        self.app.bg(cfg.read_node, lambda r: self.say(f"Read failed: {r}") if isinstance(r, Exception) else self.fill(r))

    def fill(self, node):
        self.node, self.old = node, cfg.values_from(node)
        for k, val in self.old.items():
            if k.startswith("telem_"): self.v[k].set(cfg.TELEMETRY[val] if 0 <= val < 3 else str(val))
            elif isinstance(val, bool): self.v[k].set(val)
            else: self.v[k].set(f"{val:g}" if isinstance(val, float) and k in ("freq", "bw") else str(val))
        i, ver, core, rad = (node[k] for k in ("info", "ver", "core", "radio"))
        up = int(core.get("uptime_secs", 0))
        self.status_lbl.config(text=(
            f"Model: {ver.get('model', '?')}    Firmware: {ver.get('ver', '?')} (built {ver.get('fw_build', '?')})\n"
            f"Public key: {i.get('public_key', '?')}\n"
            f"Contacts: up to {ver.get('max_contacts', '?')}    Channels: up to {ver.get('max_channels', '?')}\n"
            f"Battery: {core.get('battery_mv', 0) / 1000:.2f} V    Uptime: {up // 86400}d {up % 86400 // 3600}h {up % 3600 // 60}m    Errors: {core.get('errors', '?')}\n"
            f"Noise floor: {rad.get('noise_floor', '?')} dBm    Last RSSI: {rad.get('last_rssi', '?')} dBm    Last SNR: {rad.get('last_snr', '?')} dB\n"
            f"TX airtime: {rad.get('tx_air_secs', '?')} s    RX airtime: {rad.get('rx_air_secs', '?')} s\n"
            f"Max TX power: {i.get('max_tx_power', '?')} dBm"))
        self.say("Settings read from the node.")
        self.app.adopt_node_info(node)
        if "radio_capacity" in self.dlg.vars: self.dlg.vars["radio_capacity"].set(str(self.app.settings["radio_capacity"]))

    # ---- writing ----
    def collect(self):
        v, o = self.v, self.old
        new = {"name": v["name"].get().strip(), "lat": float(v["lat"].get()), "lon": float(v["lon"].get()), "freq": float(v["freq"].get()),
               "bw": float(v["bw"].get()), "sf": int(v["sf"].get()), "cr": int(v["cr"].get()), "tx": int(v["tx"].get()),
               "path_hash": int(v["path_hash"].get()), "pin": int(v["pin"].get())}
        for k in ("multi_acks", "loc_policy", "manual_add"): new[k] = bool(v[k].get())
        for k in ("telem_base", "telem_loc", "telem_env"): new[k] = cfg.TELEMETRY.index(v[k].get()) if v[k].get() in cfg.TELEMETRY else o[k]
        max_tx = int(self.node["info"].get("max_tx_power", 30))
        problems = [m for ok, m in ((new["name"] and len(new["name"]) <= 31, "name must be 1-31 characters"), (-90 <= new["lat"] <= 90, "latitude out of range"),
                                    (-180 <= new["lon"] <= 180, "longitude out of range"), (cfg.ea.frequency_ok(new["freq"]), "frequency must be 137-1020 MHz (or 2400-2500 MHz for 2.4 GHz radios)"),
                                    (5 <= new["sf"] <= 12, "spreading factor must be 5-12"), (5 <= new["cr"] <= 8, "coding rate must be 5-8"),
                                    (1 <= new["tx"] <= max_tx, f"TX power must be 1-{max_tx} dBm"), (new["path_hash"] in (0, 1, 2), "path hash mode must be 0-2"),
                                    (0 <= new["pin"] <= 999999, "BLE pin must be 0-999999")) if not ok]
        if problems: raise ValueError("; ".join(problems))
        return new

    def write(self):
        if not self.app.require_connection(): return
        if self.old is None:
            messagebox.showinfo("This node", "Press 'Read from node' first.", parent=self.dlg)
            return
        try: new = self.collect()
        except ValueError as e:
            messagebox.showerror("This node", f"Please check the values: {e}", parent=self.dlg)
            return
        if not cfg.build_commands(self.old, new):
            self.say("Nothing changed.")
            return
        old = self.old
        self.say("Writing to the node...")
        def done(r):
            if isinstance(r, Exception): return self.say(f"Write failed: {r}")
            results, radio_changed = r
            lines = [f"{'OK' if ok else 'FAILED'}: {label} - {detail}" for label, ok, detail in results]
            self.say("\n".join(lines))
            for l in lines: self.app.status_line("*** Node settings - " + l, "info" if l.startswith("OK") else "error")
            if radio_changed and messagebox.askyesno("Reboot node", "The new radio settings only take effect after a reboot. Reboot the node now?", parent=self.dlg):
                self.reboot(confirm=False)
            else: self.read()
        self.app.bg(lambda: cfg.write_node(old, new), done)

    def act(self, name):
        if not self.app.require_connection(): return
        self.app.bg(lambda: cfg.action(name), lambda r: self.app.status_line(f"*** Node: {name} - {r}", "error" if isinstance(r, Exception) else "info"))

    def reboot(self, confirm=True):
        if not self.app.require_connection(): return
        if confirm and not messagebox.askyesno("Reboot node", "Reboot the node now? Chat is interrupted for about 20 seconds.", parent=self.dlg): return
        self.say("Rebooting the node, waiting for it to come back (about 20s)...")
        self.app.bg(cfg.reboot_and_wait, lambda r: self.say(f"Reboot failed: {r}") if isinstance(r, Exception) else self.fill(r))
