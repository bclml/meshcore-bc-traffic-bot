"""BC traffic bot addon: DriveBC, BC Ferries, BC Transit, TransLink, weather, earthquake and tsunami feeds
broadcast to the mesh, plus the weekly reminder.  Has a master mute and a switch per source.
The chat GUI works fine with this addon disabled (Tools > Addons)."""
import gui_platform
import asyncio, logging, os, threading
import tkinter as tk
from tkinter import ttk

import emergency_agent as ea
import meshcore_io as io
from gui_addons import AddonBase


class Addon(AddonBase):
    # (The "test" auto-reply is its own addon now: Auto reply.)
    SOURCES = [k for k in ea.TX_SOURCES if k != "Test reply"]   # alert types with a switch on the Alerts tab
    title = "BC traffic bot"
    version = "1.2.0"
    author = "built in"
    description = ("Traffic / ferry / transit / weather / earthquake / tsunami alerts. Keeps the map's DriveBC and earthquake layers up to date; "
                   "broadcasting them to the mesh is OFF until you switch it on.")

    def on_load(self):
        self.thread = self.loop = None
        self.tasks = []
        self.apply_settings()
        self.button = self.api.add_toolbar_button("", self.toggle_mute)
        self._refresh_button()
        self.api.add_command("mute", lambda a: self.set_muted(True), "stop ALL transmitting by the broadcast addon, tsunami included")
        self.api.add_command("unmute", lambda a: self.set_muted(False), "resume broadcasting")
        self.api.add_menu_item("Mute / unmute broadcasting", self.toggle_mute)
        self.api.add_map_layer("DriveBC incidents", self._incidents, "#d32f2f")
        self.api.add_map_layer("Earthquakes", self._quakes, "#ef6c00")
        if ea.TX["muted"]: self._start_feeds()       # map-only mode needs no radio: read the feeds right away

    def on_unload(self):
        self._stop_feeds()
        for k in ea.TX["sources"]: ea.TX["sources"][k] = True  # leave the console agent defaults behind
        ea.TX["muted"] = False

    def _start_feeds(self):
        if self.thread and self.thread.is_alive(): return
        ea.reload_active_alerts_from_log()
        ea.reload_critical_alert_ids_from_log()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def on_connect(self): self._start_feeds()

    def on_disconnect(self):
        if not ea.TX["muted"]: self._stop_feeds()      # broadcasting needs the radio; map-only mode keeps reading the feeds

    def _mode_changed(self):
        """After broadcasting was switched on or off."""
        if ea.TX["muted"]:
            io.PENDING_SENDS.clear()                  # nothing queued earlier may go out now
            self._start_feeds()
        elif not self.api.connected:
            self._stop_feeds()                        # broadcasting without a radio is pointless

    def on_demo(self):
        ea.active_traffic_alerts.update({"DriveBC|d1": ("DriveBC", "x"), "DriveBC|d2": ("DriveBC", "x")})
        ea.ALERT_LOCATIONS.update({"DriveBC|d1": (49.245, -122.969, "INCIDENT - Highway 1 (Westbound, Burnaby)"),
                                   "DriveBC|d2": (49.139, -122.84, "INCIDENT - Highway 15 (Southbound, Surrey)")})
        ea.EARTHQUAKE_EVENTS.append((48.9, -126.1, 4.8, "130 km W of Tofino, Canada"))
        for idx, text, alert in ((3, "\U0001F6A8 NEW [DriveBC]: INCIDENT - Highway 1 (Westbound, Burnaby) - Kensington Ave. Closed.", "new"),
                                 (3, "\u2705 CLEARED [DriveBC]: INCIDENT - Highway 17 (Northbound, Delta)", "clear"),
                                 (4, "\U0001F6A8 NEW [BC Ferries]: Tsawwassen - Swartz Bay: Queen of Alberni running ~25 min late", "new"),
                                 (0, "\U0001F30E EARTHQUAKE M4.8: 130 km W of Tofino, Canada, 10km deep", "critical")):
            ea._emit("out", idx, text, alert=alert)

    # ---- feeds ----
    def _run(self):
        async def main():
            self.loop = asyncio.get_running_loop()
            self.tasks = [asyncio.ensure_future(c()) for c in (ea.traffic_loop, ea.weather_loop)]
            await asyncio.gather(*self.tasks, return_exceptions=True)
        try: asyncio.run(main())
        except Exception as e: logging.error(f"Broadcast feeds stopped: {e}")
        self.loop = None

    def _stop_feeds(self):
        if self.loop: self.loop.call_soon_threadsafe(lambda: [t.cancel() for t in self.tasks])

    # ---- settings / mute ----
    def apply_settings(self):
        g = self.api.get
        ea.TX["muted"] = g("muted", True)         # broadcasting is OFF unless the user turned it on (an existing "muted" setting is kept)
        saved = g("sources", {})
        for k in self.SOURCES: ea.TX["sources"][k] = saved.get(k, True)
        ea.USE_SCOPES = g("use_scopes", False)
        ea.REGION_SCOPES = {"Lower Mainland": g("scope_lm", ""), "Vancouver Island": g("scope_vi", ""), "Sunshine Coast": g("scope_sc", "")}
        ea.TRANSLINK_API_KEY = g("translink_key", "").strip() or os.environ.get("TRANSLINK_API_KEY", "").strip() or None

    def set_muted(self, muted):
        self.api.set("muted", muted)
        self.apply_settings()
        self._refresh_button()
        self._mode_changed()
        self.api.log("Broadcasting is OFF - the map keeps updating, nothing is transmitted (tsunami and earthquake included)" if muted else "Broadcasting is ON",
                     "info" if muted else "warn")

    def toggle_mute(self): self.set_muted(not ea.TX["muted"])

    def _refresh_button(self):
        muted = ea.TX["muted"]
        self.button.config(text="BC traffic bot: map only" if muted else "BC traffic bot: BROADCASTING", fg="#555555" if muted else "#006400")

    # ---- map layers ----
    def _incidents(self):
        out = []
        for k in list(ea.active_traffic_alerts):
            loc = ea.ALERT_LOCATIONS.get(k)
            if not loc: continue
            label, detail = ea.ALERT_MAPINFO.get(k, (loc[2], loc[2]))
            out.append((loc[0], loc[1], label, detail))
        return out

    def _quakes(self): return [(lat, lon, f"M{mag:.1f} {place}") for lat, lon, mag, place in list(ea.EARTHQUAKE_EVENTS)]

    # ---- Options page ----
    def build_options(self, parent):
        g = self.api.get
        f = tk.Frame(parent, bg=parent["bg"])
        self.v = {"translink_key": tk.StringVar(value=g("translink_key", "")),
                  "use_scopes": tk.BooleanVar(value=g("use_scopes", False)),
                  "scope_lm": tk.StringVar(value=g("scope_lm", "")), "scope_vi": tk.StringVar(value=g("scope_vi", "")),
                  "scope_sc": tk.StringVar(value=g("scope_sc", ""))}
        saved = g("sources", {})
        self.src = {k: tk.BooleanVar(value=saved.get(k, True)) for k in self.SOURCES}
        self.broadcast = tk.BooleanVar(value=not g("muted", True))
        bg = parent["bg"]
        tk.Label(f, text="BC traffic bot", bg=bg, font=(gui_platform.DIALOG_FONT_NAME, 9, "bold")).pack(anchor="w")
        nb = ttk.Notebook(f)
        nb.pack(fill="both", expand=True, pady=4)
        alerts, accounts = (tk.Frame(nb, bg=bg, padx=8, pady=6) for _ in range(2))
        nb.add(alerts, text="Alerts")
        nb.add(accounts, text="Accounts & scopes")

        tk.Checkbutton(alerts, text="Broadcast alerts to the mesh (this transmits on your radio)", variable=self.broadcast, bg=bg, wraplength=400, justify="left", anchor="w",
                       font=(gui_platform.UI_FONT_NAME, gui_platform.UI_FONT_SIZE, "bold")).pack(anchor="w")
        tk.Label(alerts, text="OFF (the default): the addon still reads the feeds and keeps the map's DriveBC incident and earthquake layers up to date - nothing is "
                              "transmitted, tsunami and earthquake included, and no radio is needed.\nTip: if other stations in range broadcast the same alerts, leave this off or untick "
                              "the alert types you do not need, so identical messages do not jam the mesh during an emergency.", bg=bg, fg="#555", justify="left", wraplength=400).pack(anchor="w", padx=18, pady=2)
        box = tk.LabelFrame(alerts, text="Alert types to send (when broadcasting is on)", bg=bg)
        box.pack(fill="x", pady=6)
        for i, k in enumerate(self.SOURCES):
            tk.Checkbutton(box, text=k, variable=self.src[k], bg=bg, anchor="w").grid(row=i // 3, column=i % 3, sticky="w", padx=6)

        def row(parent_, label, key, width, show=""):
            r = tk.Frame(parent_, bg=bg)
            r.pack(fill="x", pady=2)
            tk.Label(r, text=label, bg=bg, width=24, anchor="w").pack(side="left")
            tk.Entry(r, textvariable=self.v[key], show=show, width=width).pack(side="left")

        row(accounts, "TransLink API key:", "translink_key", 26, "*")
        for label, key in (("Scope  Lower Mainland:", "scope_lm"), ("Scope  Vancouver Island:", "scope_vi"), ("Scope  Sunshine Coast:", "scope_sc")): row(accounts, label, key, 10)
        tk.Checkbutton(accounts, text="Prefix alerts with region scope codes", variable=self.v["use_scopes"], bg=bg).pack(anchor="w", pady=4)
        return f

    def apply_options(self):
        for k, var in self.v.items(): self.api.set(k, var.get())
        self.api.set("muted", not self.broadcast.get())
        self.api.set("sources", {k: var.get() for k, var in self.src.items()})
        self.apply_settings()
        self._mode_changed()
        self._refresh_button()
