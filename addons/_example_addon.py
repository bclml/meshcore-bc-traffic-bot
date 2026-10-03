"""EXAMPLE ADDON - copy this file to addons/my_addon.py (no leading underscore) and restart the GUI, or use
Tools > Addons > Reload.  Then tick it on in Tools > Addons.

An addon is a class that extends AddonBase.  Every hook below is optional.  Hooks run on the GUI thread, so use
self.api.run_background(fn, done) for anything slow (sending to the radio, web requests, ...).

What self.api gives you (see gui_addons.py for the full list):
    get(key, default) / set(key, value)      settings saved per addon in gui_settings.json
    log(text, level)                         a line in the Status window (levels: info warn error)
    write(window, text, tag)                 a line in any window, created on demand (your own tab in the tree)
    send(channel, text)                      transmit to '#drivebc' / 'Public' / a channel index
    channel_index(name)                      node channel index for a name, or None
    nodes                                    the NodeStore of every node ever seen (nodes.all(), nodes.stats())
    add_command(name, fn, help)              a /slash command
    add_menu_item(label, fn)                 an entry in the Addons menu
    add_toolbar_button(text, fn)             a toolbar button (returns the tk.Button)
    add_map_layer(label, provider, color)    a toggle on the map; provider() -> [(lat, lon, label), ...]
    run_background(fn, done), after(ms, fn), connected
"""
import tkinter as tk

from gui_addons import AddonBase


class Addon(AddonBase):
    title = "Example: !ping responder"
    version = "1.0"
    author = "you"
    description = "Answers '!ping' in any channel with 'pong' and counts how often it was asked."
    tick_seconds = 0          # set to e.g. 60 to get on_tick() once a minute

    def on_load(self):
        self.count = self.api.get("count", 0)
        self.api.add_command("pings", lambda arg: self.api.log(f"I have answered {self.count} ping(s)"), "show the ping counter")
        self.api.write("Example", "Example addon loaded - say !ping in a channel.", "info")   # opens its own window

    def on_message(self, msg):
        # msg: channel ('#drivebc'), channel_idx, nick, text, snr, hops, raw
        if msg["text"].strip().lower() == "!ping":
            self.count += 1
            self.api.set("count", self.count)
            self.api.send(msg["channel"], f"@{msg['nick']} pong")
            self.api.write("Example", f"Answered {msg['nick']} in {msg['channel']}", "meta")

    def on_connect(self): self.api.log("connected - ready to answer pings")
    def on_disconnect(self): pass
    def on_unload(self): pass          # stop threads / release resources here

    def build_options(self, parent):   # optional: adds a page for this addon in the Options dialog
        f = tk.Frame(parent, bg=parent["bg"])
        tk.Label(f, text="Nothing to configure in this example.", bg=parent["bg"]).pack(anchor="w")
        return f

    def apply_options(self): pass      # called on Options > OK/Apply
