"""Slash commands.  Typing "/" in the input line pops up the list of commands (filtered as you type).

* mcIRC commands (/help, /map, /query, ...) work anywhere.
* In a private window with a REPEATER or ROOM SERVER, the commands from the MeshCore CLI (/reboot, /ver, /get radio, /neighbors, /clock sync, ...)
  are sent to that node (via meshcli 'cmd', with 'login' first if you gave /login <admin password>).
* Everywhere else the same names run meshcli commands on YOUR OWN node (/advert, /reboot, /contacts, /get name, ...).
* /meshcli <anything> runs any meshcli command; /rpt <text> sends raw text to the repeater of the current private window."""
import re
import shlex
import tkinter as tk
from tkinter import messagebox

import meshcore_io as io

# (name, arguments, description, confirm-first)
APP = [("help", "", "Show this command list in the Status window", False), ("list", "", "Channel list", False), ("map", "", "Open the map", False),
       ("nodes", "", "Open the node list", False), ("addons", "", "Open the addon manager", False), ("options", "", "Open Options", False),
       ("connect", "", "Connect to your node", False), ("disconnect", "", "Disconnect from your node", False),
       ("freq", "<MHz>", "Change YOUR node's radio frequency (it reboots)", True), ("clear", "", "Clear this window", False),
       ("join", "<#channel>", "Switch to a channel window", False), ("query", "<name>", "Open a private window with a node", False),
       ("msg", "<name> <text>", "Send a direct message", False), ("close", "", "Close this window", False),
       ("login", "<admin password>", "Log in to the repeater / room server in this private window", False),
       ("logout", "", "Forget the login for this repeater", False),
       ("node", "<meshcli command>", "Run a meshcli command on YOUR node (when a repeater window is open)", False),
       ("rpt", "<text>", "Send raw text to the repeater in this private window", False),
       ("meshcli", "<command...>", "Run any meshcli command on your node", False), ("quit", "", "Exit mcIRC", False)]

# MeshCore repeater / room-server CLI (docs/cli_commands.md in the MeshCore firmware repo)
REMOTE = [("reboot", "", "Reboot the repeater", True), ("poweroff", "", "Power the repeater off", True), ("shutdown", "", "Power the repeater off", True),
          ("clkreboot", "", "Reset the clock and reboot", True), ("clock", "", "Show the repeater's time (UTC)", False), ("clock sync", "", "Set the repeater's clock from yours", False),
          ("time", "<epoch_seconds>", "Set the repeater's time", False), ("advert", "", "Send a flood advert", False), ("advert.zerohop", "", "Send a zero-hop advert", False),
          ("start ota", "", "Start an over-the-air firmware update", True), ("erase", "", "FACTORY RESET - erases the repeater", True),
          ("neighbors", "", "List nearby repeaters", False), ("neighbor.remove", "<pubkey_prefix>", "Remove a neighbor", False),
          ("discover.neighbors", "", "Discover zero-hop neighbors", False), ("clear stats", "", "Clear the statistics", False),
          ("stats-core", "", "Battery, uptime, queue length", False), ("stats-radio", "", "Noise floor, RSSI/SNR, airtime", False), ("stats-packets", "", "Packet counters", False),
          ("log start", "", "Start capturing the rx log", False), ("log stop", "", "Stop capturing the rx log", False), ("log erase", "", "Erase the captured log", False),
          ("log", "", "Print the captured log", False), ("ver", "", "Firmware version", False), ("board", "", "Hardware name", False),
          ("get", "<param>", "Read a setting (type /get to see them)", False), ("set", "<param> <value>", "Change a setting (type /set to see them)", False),
          ("tempradio", "<freq>,<bw>,<sf>,<cr>,<minutes>", "Temporary radio parameters", False), ("password", "<new admin password>", "Change the admin password", True),
          ("powersaving", "[on|off]", "Show / set power saving (repeater)", False), ("setperm", "<pubkey> <0-3>", "Set a companion's permission (0 guest .. 3 admin)", False),
          ("sensor list", "[start]", "List the sensors on the node", False),
          ("region", "", "Show the regions", False), ("region load", "[name] [flood_flag]", "Load regions", False), ("region save", "", "Save region changes", False),
          ("region allowf", "<name>", "Allow flooding for a region", False), ("region denyf", "<name>", "Block flooding for a region", False),
          ("region get", "<name>", "Show a region", False), ("region home", "[name]", "Show / set the home region", False),
          ("region default", "[name]", "Show / set the default scope region", False), ("region put", "<name> [parent]", "Add or update a region", False),
          ("region remove", "<name>", "Remove a region", False)]
REMOTE_PARAMS = [("radio", "<freq>,<bw>,<sf>,<cr>"), ("tx", "<dbm>"), ("freq", "<MHz>"), ("name", "<name>"), ("lat", "<degrees>"), ("lon", "<degrees>"),
                 ("owner.info", "<text>"), ("guest.password", "<password>"), ("repeat", "on|off"), ("path.hash.mode", "0-2"), ("loop.detect", "off|minimal|moderate|strict"),
                 ("txdelay", "0-2"), ("direct.txdelay", "0-2"), ("rxdelay", "0-20"), ("dutycycle", "1-100"), ("af", "0-9"), ("int.thresh", "<n>"), ("cad", "on|off"),
                 ("agc.reset.interval", "<seconds>"), ("multi.acks", "0|1"), ("flood.advert.interval", "3-168 hours"), ("advert.interval", "60-240 minutes"),
                 ("flood.max", "0-64"), ("flood.max.unscoped", "0-64"), ("flood.max.advert", "0-64"), ("allow.read.only", "on|off"), ("radio.rxgain", "on|off"),
                 ("radio.fem.rxgain", "on|off"), ("radio.fem.txgain", "on|off"), ("adc.multiplier", "0-10"), ("public.key", "(read only)"), ("role", "(read only)"),
                 ("acl", "(read only)"), ("prv.key", "(read only)"), ("bridge.type", "(read only)"), ("pwrmgt.source", "(read only)")]

# meshcore-cli commands that run on YOUR node
LOCAL = [("advert", "", "Send an advert from your node", False), ("floodadv", "", "Send a flood advert from your node", False),
         ("reboot", "", "Reboot YOUR node", True), ("clock", "[sync]", "Show / sync your node's clock", False), ("time", "<epoch>", "Set your node's clock", False),
         ("ver", "", "Your node's firmware version", False), ("infos", "", "Your node's settings", False), ("get", "<param>", "Read a setting of your node", False),
         ("set", "<param> <value>", "Change a setting of your node", False), ("contacts", "", "List your node's contacts", False),
         ("contact_info", "<name>", "Details of a contact", False), ("share_contact", "<name>", "Share a contact with others", False),
         ("export_contact", "<name>", "A contact's URI", False), ("import_contact", "<URI>", "Import a contact", False),
         ("remove_contact", "<name>", "Remove a contact from your node", True), ("path", "<name>", "Show the path to a contact", False),
         ("disc_path", "<name>", "Discover a new path", False), ("reset_path", "<name>", "Reset the path to flood", False),
         ("change_path", "<name> <path>", "Change the path to a contact", False), ("advert_path", "<key>", "Path from an advert", False),
         ("req_status", "<name>", "Request status from a repeater", False), ("req_telemetry", "<name>", "Request telemetry", False),
         ("req_neighbours", "<name>", "Request a repeater's neighbours", False), ("req_acl", "<name>", "Request a repeater's access list", False),
         ("req_owner", "<name>", "Request a repeater's owner info", False), ("req_regions", "<name>", "Request a repeater's regions", False),
         ("req_clock", "<name>", "Request a repeater's clock", False), ("req_mma", "<name>", "Min/max/avg from a sensor", False),
         ("trace", "<path>", "Run a trace (comma separated path)", False), ("node_discover", "<filter>", "Discover nearby nodes (all, or types)", False),
         ("get_channels", "", "List your node's channels", False), ("get_channel", "<n|name>", "Info for a channel", False),
         ("set_channel", "<n> <name> [key]", "Set a channel", False), ("add_channel", "<name> [key]", "Add a channel", False),
         ("remove_channel", "<n|name>", "Remove a channel", True), ("scope", "<scope>", "Set the scope for flood messages", False),
         ("pending_contacts", "", "Show pending contacts", False), ("add_pending", "<pending>", "Add a pending contact", False), ("flush_pending", "", "Flush pending contacts", False),
         ("chan", "<n> <text>", "Send text to channel number n", False), ("cmd", "<name> <command>", "Send a command to a repeater (no ack)", False)]
LOCAL_PARAMS = [("name", "<name>"), ("radio", "<freq,bw,sf,cr>"), ("tx", "<dbm>"), ("coords", "<lat,lon>"), ("lat", "<deg>"), ("lon", "<deg>"), ("pin", "<n>"),
                ("tuning", "<rx_dly,af>"), ("multi_ack", "on|off"), ("telemetry_mode_base", "off|selected|all"), ("telemetry_mode_loc", "off|selected|all"),
                ("telemetry_mode_env", "off|selected|all"), ("advert_loc_policy", "share|none"), ("manual_add_contacts", "on|off"), ("path_hash_mode", "0-2"),
                ("stats", ""), ("stats_core", ""), ("stats_radio", ""), ("stats_packets", ""), ("help", "")]
EXCLUSIVE = {c[0] for c in APP if c[0] not in ("clear",)}     # these always mean the mcIRC command, never a repeater command


def _clean(res):
    """meshcli output without its INFO log lines."""
    lines = f"{res.stdout}\n{res.stderr}".splitlines()
    return [l.rstrip() for l in lines if l.strip() and not re.match(r"^(INFO|DEBUG|WARNING):meshcore", l)]


def find_remote(text):
    """The longest REMOTE command (they can have two words, e.g. 'clock sync') that `text` starts with, or None."""
    low = text.strip().lower()
    best = None
    for spec in REMOTE:
        n = spec[0]
        if (low == n or low.startswith(n + " ")) and (best is None or len(n) > len(best[0])): best = spec
    return best


class CommandPopup:
    """The list of matching commands above the input line."""
    def __init__(self, app, parent, entry_row, entry):
        self.app, self.entry, self.items, self.navigated = app, entry, [], False
        self.lb = tk.Listbox(parent, height=1, bg="#ffffe1", fg="black", relief="solid", bd=1, activestyle="none", exportselection=False,
                             selectbackground="#316ac5", selectforeground="white", font=("Tahoma", 8))
        self.row = entry_row
        self.lb.bind("<ButtonRelease-1>", lambda e: self.accept())
        entry.bind("<KeyRelease>", self._key)
        entry.bind("<Up>", lambda e: self._arrow(-1))
        entry.bind("<Down>", lambda e: self._arrow(1))
        entry.bind("<Tab>", lambda e: "break" if self.accept() else "break")
        entry.bind("<Escape>", lambda e: self.hide())
        entry.bind("<Return>", self._return)

    @property
    def visible(self): return bool(self.lb.winfo_ismapped())

    def _key(self, e):
        self.app.on_key(e)
        if e.keysym in ("Up", "Down", "Tab", "Escape", "Return", "Shift_L", "Shift_R", "Control_L", "Control_R"): return
        self.navigated = False
        self.update(self.entry.get())

    def update(self, text):
        self.items = self.matches(text) if text.startswith("/") else []
        if not self.items: return self.hide()
        self.lb.delete(0, "end")
        width = max(len(i[0]) for i in self.items) + 2
        for label, _, desc in self.items: self.lb.insert("end", f"{label:<{width}} {desc}")
        self.lb.config(height=min(len(self.items), 9))
        self.lb.place(in_=self.row, x=0, y=0, anchor="sw", relwidth=1.0)
        self.lb.lift()

    def hide(self):
        self.lb.place_forget()
        self.navigated = False
        return "break"

    def _arrow(self, step):
        if not self.visible: return self.app.recall(step)
        cur = self.lb.curselection()
        i = (cur[0] + step) if cur else (0 if step > 0 else len(self.items) - 1)
        i = max(0, min(len(self.items) - 1, i))
        self.lb.selection_clear(0, "end")
        self.lb.selection_set(i)
        self.lb.see(i)
        self.navigated = True
        return "break"

    def accept(self):
        """Put the highlighted (or only/first) suggestion into the input line.  True if something was completed."""
        if not self.visible or not self.items: return False
        cur = self.lb.curselection()
        label, completion, _ = self.items[cur[0] if cur else 0]
        if not completion: return False
        self.entry.delete(0, "end")
        self.entry.insert(0, completion)
        self.navigated = False
        self.update(completion)
        return True

    def _return(self, e):
        if self.visible and self.navigated and self.accept(): return "break"
        self.hide()
        return self.app.on_enter(e)

    # ---- what to suggest ----
    def matches(self, text):
        app, body = self.app, text[1:]
        remote = app.is_remote_window()
        word, _, rest = body.partition(" ")
        word = word.lower()
        if " " in body.strip() or (body.endswith(" ") and word):
            return self._second_level(word, rest, remote)
        out, seen = [], set()
        def add(spec, tag=""):
            n, a, d, _ = spec
            if n.lower().startswith(word) and n not in seen:
                seen.add(n)
                out.append((f"/{n}" + (f" {a}" if a else ""), f"/{n} ", (f"[{tag}] " if tag else "") + d))
        if remote:
            for s in REMOTE: add(s, "repeater")
        for s in APP: add(s)
        for n, (fn, h, owner) in sorted(app.commands.items()): add((n, "", h, False), owner)
        if not remote:
            for s in LOCAL: add(s, "your node")
        return out[:60]

    def _second_level(self, word, rest, remote):
        params = REMOTE_PARAMS if remote else LOCAL_PARAMS
        if word in ("get", "set") and " " not in rest.strip():
            return [(f"/{word} {p}" + (f" {h}" if h else ""), f"/{word} {p} ", "setting") for p, h in params if p.startswith(rest.strip().lower())]
        spec = find_remote(f"{word} {rest}") if remote else None
        if spec is None:
            for s in APP + LOCAL:
                if s[0] == word: spec = s
        return [(f"/{spec[0]} {spec[1]}".rstrip(), "", spec[2])] if spec else []


class CommandsMixin:
    """Methods added to the main window for running node / repeater commands."""
    admin_pw = None

    def node_row(self, w):
        """Node-memory row for a private window (by key, then by name), or None."""
        if w is None or not w.name.startswith("@"): return None
        return (self.nodes.find_by_prefix((w.key or "")[:12]) if w.key else None) or self.nodes.find_by_name(w.name[1:])

    def is_remote_window(self):
        row = self.node_row(self.current)
        return bool(row and row["type"] in (2, 3))

    def try_remote_command(self, cmd, arg):
        """In a repeater / room-server window: send the CLI command to that node.  True if handled."""
        if cmd in EXCLUSIVE or not self.is_remote_window(): return False
        w = self.current
        text = f"{cmd} {arg}".strip()
        spec = find_remote(text)
        if cmd == "clear" and not arg: return False                 # plain /clear still clears the window
        if spec is None: return False
        if spec[3] and not messagebox.askyesno("Send command", f"Send '{text}' to {w.name[1:]}?\n\n{spec[2]}", parent=self.root): return True
        self.send_remote(w, text)
        return True

    def cmd_login(self, arg):
        w = self.current
        if not self.is_remote_window():
            return self.status_line("*** /login is for a repeater or room-server window. From anywhere else use /meshcli login <name> <password>.", "warn")
        if not arg.strip(): return self.status_line("*** Usage: /login <admin password>  (kept in memory only, never saved or logged)", "warn")
        self.admin_pw = self.admin_pw or {}
        self.admin_pw[(w.key or w.name)[:12]] = arg.strip()
        self.send_remote(w, None, announce=f"logging in to {w.name[1:]}...")

    def cmd_logout(self, arg):
        if self.admin_pw and self.current: self.admin_pw.pop((self.current.key or self.current.name)[:12], None)
        self.status_line("*** Login forgotten for this repeater.", "info")

    def send_remote(self, w, text, announce=None):
        row = self.node_row(w)
        key = row["public_key"] if row else w.key
        if not self.connected or not key:
            return self.status_line("*** Not connected, or that node's key isn't known yet.", "error")
        pw = (self.admin_pw or {}).get((w.key or w.name)[:12])
        args = (["login", key, pw] if pw else []) + (["cmd", key, text, "wmt8"] if text else [])
        w.write(self.stamp() + [(f"* {announce or 'sent to ' + w.name[1:] + ': ' + text}", "info")])
        ctype = str((row or {}).get("type") or 2)
        cname = (row or {}).get("name") or w.name[1:]
        def work():
            with io.MESH_LOCK:
                out = _clean(io.execute_mesh_command(io.CONNECTION_ARGS + args, timeout=45, retries=1))
                if any(l.startswith(("Unknown contact", "Unknown destination")) for l in out):
                    # the radio has forgotten this node (its contact slots are full / it was pruned): re-add it from mcIRC's memory, flood-routed
                    out = _clean(io.execute_mesh_command(io.CONNECTION_ARGS + ["add_contact", key, ctype, cname, "reset_path", key] + args, timeout=60, retries=1))
                return out
        def done(r):
            if isinstance(r, Exception): return w.write(self.stamp() + [(f"* failed: {io.explain_failure(str(r))}", "error")])
            if not r: return w.write(self.stamp() + [("* no reply within 8 seconds (out of range, wrong/no admin password - use /login - or this command has no reply)", "warn")])
            for line in r: self.chat_line(w, w.name[1:], line, "text")
        self.bg(work, done)

    def run_node_command(self, cmd, arg):
        """meshcli commands on YOUR node.  True if `cmd` is one."""
        spec = next((s for s in LOCAL if s[0] == cmd), None)
        if cmd == "meshcli" or cmd == "node":
            if not arg.strip(): return self.status_line("*** Usage: /meshcli <command...>   e.g. /meshcli contacts", "warn") or True
            args = shlex.split(arg)
        elif spec: args = [cmd] + shlex.split(arg)
        else: return False
        if spec and spec[3] and not messagebox.askyesno("Run on your node", f"Run '{(cmd + ' ' + arg).strip()}' on YOUR node?\n\n{spec[2]}", parent=self.root):
            return True
        if not self.connected: return self.status_line("*** Not connected.", "error") or True
        win = self.current
        def work():
            with io.MESH_LOCK: return _clean(io.execute_mesh_command(io.CONNECTION_ARGS + args, timeout=60, retries=1))
        def done(r):
            if isinstance(r, Exception): return win.write(self.stamp() + [(f"* {io.explain_failure(str(r))}", "error")])
            for line in (r or ["(done, no output)"]): win.write(self.stamp() + [(f"  {line}", "meta")])
        win.write(self.stamp() + [(f"* your node: {' '.join(args)}", "info")])
        self.bg(work, done)
        return True

    def on_key(self, _e=None):
        n = len(self.entry.get())
        self.counter.config(text=f"{n}/{io.MESH_MSG_MAX_CHARS}", fg="red" if n > io.MESH_MSG_MAX_CHARS else "black")
