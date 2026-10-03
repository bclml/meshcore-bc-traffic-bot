#!/usr/bin/env python3
"""mcIRC - an mIRC-style chat client for MeshCore nodes.

The core does chat (channel windows, nick list, input line), the node list/map with long-term node memory, and
settings.  Everything else - including broadcasting traffic/weather/earthquake alerts - is an addon in addons/
(Tools > Addons).  Run:  python mcIRC.py     (add --demo to try it with fake data and no radio)"""
import argparse, datetime, logging, os, queue, re, sys, tempfile, threading, time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox

import meshcore_io as ea
from gui_addons import AddonManager
from gui_common import (BG, TEXT_BG, FONT_FAMILY, NICK_COLORS, CHANNELS, safe_text, load_settings, save_settings,
                        channel_index, display_for_index)
from gui_dialogs import OptionsDialog, ChannelListDialog, AddonsDialog, NodeListDialog, show_about
from gui_map import MapWindow
from gui_nodes import NodeStore, sync as sync_nodes
import gui_nodecfg
from gui_logs import WindowLog, LOG_DIR, logged_windows
import gui_update
import gui_style
from gui_switchbar import SwitchBar
from gui_update_ui import UpdateDialog, CatalogDialog, LINKS, open_link

HELP = ["Commands:", "  /help            this list", "  /list            channel list", "  /map             open the map",
        "  /nodes           node list", "  /addons          addon manager", "  /options         open Options",
        "  /connect         connect to the node", "  /disconnect      disconnect", "  /freq <MHz>      change the radio frequency (node reboots)",
        "  /clear           clear this window", "  /join <#name>    switch to a channel window", "  /query <name>    open a private window with a node",
        "  /msg <name> <text>  send a direct message", "  /close           close this private window", "  /quit            exit",
        "Type text in a channel window to send it to that channel (max ~120 characters)."]


class ChatWindow:
    def __init__(self, parent, name, topic, font, log=None, history=0):
        self.name, self.topic, self.nicks, self.unread, self.key, self.log = name, topic, set(), "", None, log
        self.frame = tk.Frame(parent, bg=TEXT_BG)
        self.text = tk.Text(self.frame, wrap="word", state="disabled", bg=TEXT_BG, fg="black", font=font,
                            relief="sunken", bd=2, padx=4, pady=2, cursor="arrow")
        sb = ttk.Scrollbar(self.frame, command=self.text.yview)
        self.text.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        t = self.text
        for tag, fg in (("ts", "#808080"), ("text", "black"), ("info", "#0000cc"), ("warn", "#cc6600"), ("error", "#cc0000"),
                        ("new", "#cc0000"), ("clear", "#008000"), ("self", "#7f007f"), ("meta", "#808080")):
            t.tag_config(tag, foreground=fg)
        t.tag_config("critical", foreground="white", background="#cc0000")
        t.tag_config("bot", foreground="#008000", font=(font.cget("family"), font.cget("size"), "bold"))
        for i, c in enumerate(NICK_COLORS): t.tag_config(f"nick{i}", foreground=c)
        t.tag_config("hist", foreground="#606060")
        if log:
            old = log.tail(history)   # earlier sessions, shown in grey
            if old:
                t.config(state="normal")
                for line in old: t.insert("end", safe_text(line) + "\n", "hist")
                t.config(state="disabled")
                t.see("end")
            log.stamp("Session Start")

    def write(self, parts):
        t = self.text
        at_bottom = t.yview()[1] >= 0.999
        t.config(state="normal")
        for text, tag in parts: t.insert("end", safe_text(text), tag)
        t.insert("end", "\n")
        t.config(state="disabled")
        if at_bottom: t.see("end")
        if self.log: self.log.append("".join(text for text, _ in parts))

    def clear(self):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")


class CoreWorker:
    """Connects to the node, then keeps polling for chat and syncing the node memory until stopped."""
    def __init__(self, app): self.app, self.thread, self.stop_evt = app, None, threading.Event()

    @property
    def running(self): return self.thread is not None and self.thread.is_alive()

    def start(self, s):
        if self.running: return
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._run, args=(s,), daemon=True)
        self.thread.start()

    def stop(self): self.stop_evt.set()

    def _run(self, s):
        q = self.app.q
        q.put(("state", "connecting", ""))
        try:
            args = ea.build_connection_args(s["mode"], s["port"], s["ble_target"], s["tcp_host"], s["tcp_port"], s["baud"])
            if not args: return
            ea.CONNECTION_ARGS = args
            ea.resolve_channel_indices()
            q.put(("channels",))
            q.put(("state", "connected", " ".join(args)))
            try: q.put(("nodeinfo", gui_nodecfg.read_node()))
            except Exception as e:
                why = ea.explain_failure(str(e))
                logging.warning(f"Could not read the node's own settings: {why}")
            last_sync = last_resolve = 0
            while not self.stop_evt.is_set():
                now = time.time()
                if not ea.CHANNEL_INDEX_BY_NAME and now - last_resolve > 60:
                    last_resolve = now
                    ea.resolve_channel_indices()
                    q.put(("channels",))
                ea.fetch_incoming_messages()  # each message reaches the GUI through ea.GUI_CALLBACK
                if now - last_sync >= self.app.settings["node_sync_minutes"] * 60:
                    last_sync = now
                    self.app.node_sync_worker()
                self.stop_evt.wait(self.app.settings["poll_seconds"])
        except Exception as e: logging.error(f"Connection ended: {e}")
        finally: q.put(("state", "stopped", ""))


class QueueLogHandler(logging.Handler):
    def __init__(self, q):
        super().__init__(logging.INFO)
        self.addFilter(ea.RedactFilter())
        self.q = q

    def emit(self, record):
        msg = record.getMessage()
        if "[DIAGNOSTIC]" not in msg: self.q.put(("log", record.levelno, msg))


class App:
    def __init__(self, root, demo=False):
        self.root, self.demo, self.connected = root, demo, False
        self.settings = load_settings()
        if demo: self.settings.update(node_name="DemoNode", mode="usb", port="auto", tcp_host="", ble_target="", auto_connect=False)   # demo = fake everything
        self.q = queue.Queue()
        self.windows, self.current, self.history, self.hist_pos = {}, None, [], 0
        self.log_dir = tempfile.mkdtemp(prefix="meshlogs_") if demo else LOG_DIR   # demo mode must never touch the real logs
        self.commands, self.map_layers = {}, {}   # filled by addons
        self.worker = CoreWorker(self)
        self.nodes = NodeStore(":memory:") if demo else NodeStore()
        self.addons = AddonManager(self)
        if demo: self.settings["addons"], self.settings["addons_enabled"] = {}, {n: True for n in self.addons.discover()}
        gui_style.apply_classic(root)   # old-mIRC chrome: must run before any widget exists
        self.font = gui_style.chat_font(self.settings["font_size"])
        self.map_win = self.addons_win = None
        root.title("mcIRC")
        root.geometry("1000x640")
        root.configure(bg=BG)
        self._menu()
        self._toolbar()
        self._status_bar()
        self._body()
        self.switchbar = SwitchBar(self)
        self.status = self.add_window("Status", "status window", in_tree=False)
        self.tree.insert("", 0, iid="Status", text="Status")
        self.tree.insert("", "end", iid="Channels", text="Channels", open=True)
        self.add_window("Public", CHANNELS["Public"])
        for name in logged_windows(self.log_dir):   # windows from earlier sessions come back with their history
            if name not in self.windows:
                self.add_window(name, f"Private conversation with {name[1:]}" if name.startswith("@") else CHANNELS.get(name, "(restored from log)"))
        self.apply_settings()
        ea.GUI_CALLBACK = lambda kind, idx, text, nick, **extra: self.q.put(("chat", kind, idx, text, nick, extra))
        logging.getLogger().addHandler(QueueLogHandler(self.q))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.select_window("Status")
        self.status_line("*** Welcome. Type /help for commands, or press Connect.", "info")
        self.addons.load_all()
        root.after(100, self.drain)
        if not demo and self.settings["check_updates"]: root.after(8000, self.auto_update_check)
        root.after(1000, self.tick)
        if demo: self.load_demo()
        elif self.settings["auto_connect"]: self.connect()

    # ---- layout -------------------------------------------------------------------------------
    def _menu(self):
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Connect", command=self.connect)
        f.add_command(label="Disconnect", command=self.disconnect)
        f.add_separator()
        f.add_command(label="Options...", command=self.open_options)
        f.add_separator()
        f.add_command(label="Exit", command=self.quit)
        v = tk.Menu(m, tearoff=0)
        v.add_command(label="Channels list...", command=lambda: ChannelListDialog(self))
        v.add_command(label="Node list...", command=self.open_node_list)
        v.add_command(label="Map...", command=self.open_map)
        v.add_command(label="Clear window", command=lambda: self.current and self.current.clear())
        t = tk.Menu(m, tearoff=0)
        t.add_command(label="Addons...", command=self.open_addons)
        t.add_command(label="This node's settings...", command=lambda: self.open_options("Node: radio"))
        t.add_command(label="Open logs folder", command=lambda: (os.makedirs(LOG_DIR, exist_ok=True), os.startfile(LOG_DIR)))
        self.addon_menu = tk.Menu(m, tearoff=0)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Commands", command=lambda: self.command("help"))
        h.add_command(label="Check for updates...", command=lambda: UpdateDialog(self))
        h.add_command(label="Browse addons...", command=lambda: CatalogDialog(self))
        h.add_separator()
        for label in LINKS:
            if label != "Project page on GitHub...": h.add_command(label=label, command=lambda l=label: open_link(l))
        h.add_separator()
        h.add_command(label="Project page on GitHub...", command=lambda: open_link("Project page on GitHub..."))
        h.add_command(label=f"About (version {gui_update.local_version()})", command=lambda: show_about(self.root))
        win = tk.Menu(m, tearoff=0)
        win.config(postcommand=lambda: self._fill_window_menu(win))
        for label, menu in (("File", f), ("View", v), ("Tools", t), ("Addons", self.addon_menu), ("Window", win), ("Help", h)): m.add_cascade(label=label, menu=menu)
        self.root.config(menu=m)

    def _fill_window_menu(self, menu):
        menu.delete(0, "end")
        self._winvar = tk.StringVar(value=self.current.name if self.current else "")
        for name in self.windows:
            menu.add_radiobutton(label=name, variable=self._winvar, value=name, command=lambda n=name: self.select_window(n))
        menu.add_separator()
        private = bool(self.current and self.current.name.startswith("@"))
        menu.add_command(label="Close private window", state="normal" if private else "disabled", command=lambda: self.close_window(self.current.name))
        menu.add_command(label="Clear window", command=lambda: self.current and self.current.clear())

    def _toolbar(self):
        self.icons = gui_style.make_icons(self.root)
        self.toolbar = bar = tk.Frame(self.root, bg=BG, bd=2, relief="raised")
        bar.pack(fill="x")
        for item in (("connect", "Connect to the node", self.connect), ("disconnect", "Disconnect", self.disconnect), None,
                     ("channels", "Channels list", lambda: ChannelListDialog(self)), ("nodes", "Node list", self.open_node_list), ("map", "Map", self.open_map), None,
                     ("addons", "Addons", self.open_addons), ("options", "Options", self.open_options), None):
            if item is None:
                tk.Frame(bar, width=2, bd=1, relief="sunken", bg=BG).pack(side="left", fill="y", padx=4, pady=2)
                continue
            b = tk.Button(bar, image=self.icons[item[0]], command=item[2], bg=BG, relief="flat", overrelief="raised", bd=1, width=22, height=22, takefocus=False)
            b.pack(side="left", padx=1, pady=2)
            gui_style.Tooltip(b, item[1])

    def _status_bar(self):
        self.statusbar = bar = tk.Frame(self.root, bg=BG)
        bar.pack(side="bottom", fill="x")
        self.sb_state = tk.Label(bar, bg=BG, relief="sunken", anchor="w", text="Not connected", width=34)
        self.sb_radio = tk.Label(bar, bg=BG, relief="sunken", anchor="w", text="")
        self.sb_clock = tk.Label(bar, bg=BG, relief="sunken", anchor="e", width=10)
        self.sb_state.pack(side="left")
        self.sb_clock.pack(side="right")
        self.sb_radio.pack(side="left", fill="x", expand=True)

    def _body(self):
        self.paned = paned = tk.PanedWindow(self.root, orient="horizontal", bg=BG, sashwidth=4)
        paned.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(paned, show="tree", selectmode="browse")
        self.tree.tag_configure("msg", foreground="#cc0000")
        self.tree.tag_configure("event", foreground="#0000cc")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.tree.selection() and self.tree.selection()[0] in self.windows and self.select_window(self.tree.selection()[0]))
        paned.add(self.tree, width=150)
        right = tk.Frame(paned, bg=BG)
        paned.add(right)
        self.topic = tk.Entry(right, state="readonly", readonlybackground="white", relief="sunken", bd=2)
        self.topic.pack(fill="x")
        entry_row = tk.Frame(right, bg=BG)
        entry_row.pack(side="bottom", fill="x")
        self.entry = tk.Entry(entry_row, font=self.font, relief="sunken", bd=2)
        self.entry.pack(side="left", fill="x", expand=True)
        self.counter = tk.Label(entry_row, bg=BG, width=8, text="0/120")
        self.counter.pack(side="right")
        self.entry.bind("<Return>", self.on_enter)
        self.entry.bind("<Up>", lambda e: self.recall(-1))
        self.entry.bind("<Down>", lambda e: self.recall(1))
        self.entry.bind("<KeyRelease>", lambda e: self.counter.config(text=f"{len(self.entry.get())}/{ea.MESH_MSG_MAX_CHARS}",
                                                                      fg="red" if len(self.entry.get()) > ea.MESH_MSG_MAX_CHARS else "black"))
        mid = tk.Frame(right, bg=BG)
        mid.pack(fill="both", expand=True)
        self.nicklist = tk.Listbox(mid, width=16, font=self.font, bg="white", relief="sunken", bd=2, activestyle="none", exportselection=False)
        self.nicklist.pack(side="right", fill="y")
        self.nicklist.bind("<Double-Button-1>", self.nick_dblclick)
        self.stack = tk.Frame(mid, bg=BG)
        self.stack.pack(side="left", fill="both", expand=True)
        self.stack.grid_rowconfigure(0, weight=1)
        self.stack.grid_columnconfigure(0, weight=1)

    # ---- windows ------------------------------------------------------------------------------
    def add_window(self, name, topic, in_tree=True):
        if name in self.windows: return self.windows[name]
        log = WindowLog(name, self.log_dir) if self.settings["log_enabled"] else None
        w = ChatWindow(self.stack, name, topic, self.font, log, self.settings["log_history"])
        w.frame.grid(row=0, column=0, sticky="nsew")
        self.windows[name] = w
        if in_tree and hasattr(self, "tree"):
            parent = "Queries" if name.startswith("@") else "Channels"
            if parent == "Queries" and not self.tree.exists("Queries"): self.tree.insert("", "end", iid="Queries", text="Queries", open=True)
            if self.tree.exists(parent): self.tree.insert(parent, "end", iid=name, text=name)
        if name.startswith("@"): self._add_button(name)   # the switchbar is for direct messages only
        return w

    ensure_window = add_window

    def select_window(self, name):
        w = self.windows[name]
        self.current = w
        w.frame.tkraise()
        w.unread = ""
        if self.tree.exists(name):
            self.tree.item(name, tags=())
            if self.tree.selection() != (name,): self.tree.selection_set(name)
        self.topic.config(state="normal")
        self.topic.delete(0, "end")
        self.topic.insert(0, safe_text(f"{name}: {w.topic}"))
        self.topic.config(state="readonly")
        self.refresh_nicks()
        self.root.title(f"mcIRC - [{name}]")
        self._style_buttons()
        self.entry.focus_set()

    def refresh_nicks(self):
        self.nicklist.delete(0, "end")
        if self.current is self.status or self.current is None: return
        self.nicklist.insert("end", "@" + self.settings["node_name"])
        for n in sorted(self.current.nicks - {self.settings["node_name"]}, key=str.lower): self.nicklist.insert("end", n)

    def mark_unread(self, w, level):
        if w is self.current: return
        if level == "msg" or not w.unread: w.unread = level
        if self.tree.exists(w.name): self.tree.item(w.name, tags=(w.unread,))
        self._style_buttons()

    # ---- switchbar (see gui_switchbar.py): small red-when-unread buttons for private conversations ----
    @property
    def buttons(self): return self.switchbar.buttons

    def _add_button(self, name):
        self.switchbar.add(name, lambda n=name: self.select_window(n), lambda n=name: self.close_window(n))

    def _style_buttons(self):
        if hasattr(self, "switchbar"): self.switchbar.sync(self.windows, self.current)

    def close_window(self, name):
        w = self.windows.get(name)
        if w is None or not name.startswith("@"): return
        if w.log: w.log.stamp("Session Close")
        was_current = w is self.current
        w.frame.destroy()
        del self.windows[name]
        if self.tree.exists(name): self.tree.delete(name)
        self.switchbar.remove(name)
        if was_current: self.select_window("Status")

    # ---- private (direct message) windows ----
    def open_query(self, name, key=None):
        name = name.lstrip("@")
        w = self.add_window("@" + name, f"Private conversation with {name}")
        if key: w.key = key
        self.select_window("@" + name)
        return w

    def _dm_in(self, text, prefix, extra):
        node = self.nodes.find_by_prefix(prefix)
        name = node["name"] if node else prefix[:8]
        w = self.add_window("@" + name, f"Private conversation with {name}")
        w.key = node["public_key"] if node else prefix
        bits = []
        if extra.get("snr") is not None: bits.append(f"SNR {extra['snr']}")
        hops = extra.get("hops")
        if hops is not None: bits.append("direct" if hops in (0, 255) else f"{hops} hops")
        self.chat_line(w, name, text, "text", f"({', '.join(bits)})" if bits else "")
        self.addons.dispatch("on_message", {"channel": w.name, "channel_idx": None, "nick": name, "text": text, "dm": True,
                                             "snr": extra.get("snr"), "hops": hops, "raw": extra.get("raw")})

    def send_dm(self, w, text):
        key = w.key or (self.nodes.find_by_name(w.name[1:]) or {}).get("public_key")
        if not self.connected or not key:
            self.status_line(f"*** Can't message {w.name[1:]}: " + ("not connected." if not self.connected else "that node's key isn't known yet."), "error")
            return
        w.key = key
        self.chat_line(w, self.settings["node_name"], text, "self")
        def work():
            res = ea.execute_mesh_command(ea.CONNECTION_ARGS + ["msg", key, text])
            out = f"{res.stdout}\n{res.stderr}"
            if re.search(r"unknown destination|\berror\b", out, re.IGNORECASE): raise RuntimeError(out.strip().splitlines()[-1])
        self.bg(work, lambda r: isinstance(r, Exception) and self.status_line(f"*** Message to {w.name[1:]} failed: {r}", "error"))

    # ---- writing ------------------------------------------------------------------------------
    def stamp(self):
        return [(datetime.datetime.now().strftime("[%H:%M] "), "ts")] if self.settings["show_time"] else []

    def status_line(self, text, tag="text"):
        self.status.write(self.stamp() + [(text, tag)])
        self.mark_unread(self.status, "event")

    def chat_line(self, w, nick, text, tag, suffix=""):
        nick_tag = "bot" if nick == self.settings["node_name"] else f"nick{sum(map(ord, nick)) % len(NICK_COLORS)}"
        parts = self.stamp() + [("<", "text"), (nick, nick_tag), ("> ", "text"), (text, tag)]
        if suffix: parts.append((f"  {suffix}", "meta"))
        w.write(parts)
        w.nicks.add(nick)
        self.mark_unread(w, "msg")
        if w is self.current: self.refresh_nicks()

    # ---- queue handlers (GUI thread) ----------------------------------------------------------
    def drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                getattr(self, "_h_" + item[0])(*item[1:])
        except queue.Empty: pass
        self.root.after(100, self.drain)

    def _h_chat(self, kind, idx, text, nick, extra):
        if kind == "dm": return self._dm_in(text, extra.get("pubkey") or nick, extra)
        name = display_for_index(idx)
        w = self.windows.get(name) or self.add_window(name, CHANNELS.get(name, f"Channel {idx}"))
        if kind == "out":
            self.chat_line(w, self.settings["node_name"], text, extra.get("alert", "text"))
            return
        bits = []
        if extra.get("snr") is not None: bits.append(f"SNR {extra['snr']}")
        hops = extra.get("hops")
        if hops is not None: bits.append("direct" if hops in (0, 255) else f"{hops} hops")
        self.chat_line(w, nick, text, "text", f"({', '.join(bits)})" if bits else "")
        self.addons.dispatch("on_message", {"channel": name, "channel_idx": idx, "nick": nick, "text": text,
                                             "snr": extra.get("snr"), "hops": hops, "raw": extra.get("raw")})

    def _h_log(self, level, msg):
        self.status_line("*** " + msg, "error" if level >= logging.ERROR else "warn" if level >= logging.WARNING else "info")

    def _h_state(self, state, detail):
        was = self.connected
        self.connected = state == "connected"
        self.sb_state.config(text={"connecting": "Connecting...", "connected": f"Connected ({detail})", "stopped": "Not connected"}[state])
        if self.connected and not was: self.addons.dispatch("on_connect")
        if was and not self.connected: self.addons.dispatch("on_disconnect")

    def _h_channels(self):
        for name, idx in ea.CHANNEL_INDEX_BY_NAME.items():
            if name != "Public": self.add_window("#" + name.lstrip("#"), CHANNELS.get("#" + name.lstrip("#"), f"Channel {idx}"))
        self.status_line(f"*** Resolved channels: {', '.join(f'{n}={i}' for n, i in ea.CHANNEL_INDEX_BY_NAME.items())}", "info")

    def _h_nodes(self, r):
        cap = self.settings["radio_capacity"]
        if r["new"] or r["pruned"] or r["removed_from_radio"]:
            self.status_line(f"*** Nodes: {r['on_radio']}/{cap} on radio, {r['total']} remembered (+{r['new']} new, {r['pruned']} forgotten"
                             + (f", {r['removed_from_radio']} removed from radio" if r["removed_from_radio"] else "") + ")", "info")
        if r["on_radio"] >= cap * 0.95:
            self.status_line(f"*** Radio contact list nearly full ({r['on_radio']}/{cap}) - new nodes may not fit on the radio, but they are still remembered here.", "warn")
        if self.map_win is not None and self.map_win.winfo_exists(): self.map_win.refresh(force=True)

    def _h_nodeinfo(self, node):
        self.adopt_node_info(node)
        i, v, c = node["info"], node["ver"], node["core"]
        self.status_line(f"*** Node: {i.get('name')} - {v.get('model')} fw {v.get('ver')}, {i.get('radio_freq')} MHz, {i.get('tx_power')} dBm, "
                         f"{v.get('max_contacts')} contacts max, battery {c.get('battery_mv', 0) / 1000:.2f} V", "info")

    def adopt_node_info(self, node):
        """Keep GUI settings in step with what the node itself says (its name, contact capacity, position)."""
        s, i, v = self.settings, node["info"], node["ver"]
        if v.get("max_contacts"): s["radio_capacity"] = int(v["max_contacts"])
        if i.get("name"): s["node_name"], ea.BOT_NICK = i["name"], i["name"]
        if i.get("adv_lat") or i.get("adv_lon"): s["node_lat"], s["node_lon"] = float(i["adv_lat"]), float(i["adv_lon"])
        self.save()
        if self.current: self.refresh_nicks()

    def auto_update_check(self):
        """Quietly look for a newer version (at most once a day); only speaks up if there is one."""
        if time.time() - self.settings["last_update_check"] < 86400: return
        def done(r):
            if isinstance(r, Exception): return   # offline / GitHub unreachable: stay silent
            self.settings["last_update_check"] = time.time()
            self.save()
            if r["newer"]: self.status_line(f"*** Update available: version {r['remote']} (you have {r['local']}) - Help > Check for updates.", "warn")
        self.bg(gui_update.check, done)

    def _h_call(self, fn): fn()

    def tick(self):
        self.sb_clock.config(text=datetime.datetime.now().strftime("%H:%M:%S"))
        st = self.nodes.stats()
        self.sb_radio.config(text=f" nodes: {st['on_radio']}/{self.settings['radio_capacity']} on radio, {st['total']} remembered")
        self.addons.tick(time.time())
        self.root.after(1000, self.tick)

    # ---- actions ------------------------------------------------------------------------------
    def bg(self, fn, done):
        def work():
            try: res = fn()
            except Exception as e: res = e
            self.q.put(("call", lambda: done(res)))
        threading.Thread(target=work, daemon=True).start()

    def require_connection(self):
        if self.connected: return True
        messagebox.showinfo("Not connected", "Connect to the node first (File > Connect).", parent=self.root)
        return False

    def save(self):
        if not self.demo: save_settings(self.settings)   # demo mode never writes the real settings file

    def apply_settings(self):
        s = self.settings
        ea.BOT_NICK = s["node_name"]
        self.font.configure(size=s["font_size"])
        self.save()
        if self.current: self.refresh_nicks()

    def node_sync_worker(self):
        """Runs on a worker thread: read the radio's contacts into long-term memory and forget stale ones."""
        s = self.settings
        try: self.q.put(("nodes", sync_nodes(self.nodes, s["node_prune_days"], s["prune_radio"])))
        except Exception as e: logging.error(f"Node sync failed: {e}")

    def sync_nodes_now(self, then=None):
        if not self.require_connection(): return
        s = self.settings
        def done(r):
            if isinstance(r, Exception): self.status_line(f"*** Node sync failed: {r}", "error")
            else: self._h_nodes(r)
            if then: then()
        self.status_line("*** Reading the radio's node list...", "info")
        self.bg(lambda: sync_nodes(self.nodes, s["node_prune_days"], s["prune_radio"]), done)

    def connect(self):
        if self.worker.running:
            self.status_line("*** Already connected.", "warn")
            return
        self.apply_settings()
        self.status_line("*** Connecting to the node...", "info")
        self.worker.start(self.settings)

    def disconnect(self):
        if self.worker.running:
            self.worker.stop()
            self.status_line("*** Disconnecting...", "info")

    def open_options(self, page="Connect"):
        d = OptionsDialog(self)
        d.tree.selection_set(page)

    def _open_single(self, attr, factory):
        w = getattr(self, attr, None)
        if w is not None and w.winfo_exists(): w.lift()
        else: setattr(self, attr, factory())

    def open_map(self): self._open_single("map_win", lambda: MapWindow(self.root, self))
    def open_node_list(self): self._open_single("nodes_win", lambda: NodeListDialog(self))
    def open_addons(self): self._open_single("addons_win", lambda: AddonsDialog(self))

    def quit(self):
        self.switchbar.remember()
        self.save()
        for w in self.windows.values():
            if w.log: w.log.stamp("Session Close")
        self.worker.stop()
        for n in list(self.addons.loaded): self.addons.unload(n)
        self.root.destroy()

    # ---- input line ---------------------------------------------------------------------------
    def recall(self, step):
        if not self.history: return "break"
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + step))
        self.entry.delete(0, "end")
        if self.hist_pos < len(self.history): self.entry.insert(0, self.history[self.hist_pos])
        return "break"

    def nick_dblclick(self, _):
        sel = self.nicklist.curselection()   # double-click a name = open a private window, like mIRC
        if not sel: return
        nick = self.nicklist.get(sel[0]).lstrip("@")
        if nick != self.settings["node_name"]:
            node = self.nodes.find_by_name(nick)
            self.open_query(nick, node["public_key"] if node else None)

    def on_enter(self, _):
        text = self.entry.get().strip()
        self.entry.delete(0, "end")
        self.counter.config(text=f"0/{ea.MESH_MSG_MAX_CHARS}", fg="black")
        if not text: return
        self.history.append(text)
        self.hist_pos = len(self.history)
        if text.startswith("/"): self.command(text[1:])
        elif self.current is self.status: self.status_line("*** Select a channel window to chat, or type /help.", "warn")
        elif self.current.name.startswith("@"): self.send_dm(self.current, text)
        else: self.send_to(self.current.name, text)

    def send_to(self, channel, text):
        if threading.current_thread() is not threading.main_thread():  # addons may call this from worker threads
            self.q.put(("call", lambda: self.send_to(channel, text)))
            return
        idx = channel if isinstance(channel, int) else channel_index(channel)
        name = display_for_index(idx) if idx is not None else str(channel)
        if idx is None or not self.connected:
            self.q.put(("call", lambda: self.status_line(f"*** Can't send to {name}: not connected / channel not resolved yet.", "error")))
            return
        if len(text) > ea.MESH_MSG_MAX_CHARS: self.status_line(f"*** Message is {len(text)} chars; the node may cut it off.", "warn")
        cmd = ["public", text] if idx == 0 else ["chan", str(idx), text]
        w = self.windows.get(name)
        if w: self.chat_line(w, self.settings["node_name"], text, "self")
        self.bg(lambda: ea.execute_mesh_command(ea.CONNECTION_ARGS + cmd),
                lambda r: isinstance(r, Exception) and self.status_line(f"*** Send to {name} failed: {r}", "error"))

    def command(self, line):
        cmd, _, arg = line.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        simple = {"list": lambda: ChannelListDialog(self), "map": self.open_map, "nodes": self.open_node_list, "addons": self.open_addons,
                  "options": self.open_options, "connect": self.connect, "disconnect": self.disconnect, "quit": self.quit}
        if cmd == "help":
            self.select_window("Status")
            for l in HELP + [f"  /{n:<15}{h}  [{owner}]" for n, (_, h, owner) in sorted(self.commands.items())]: self.status_line(l, "info")
        elif cmd == "clear" and self.current: self.current.clear()
        elif cmd in simple: simple[cmd]()
        elif cmd in self.commands:
            try: self.commands[cmd][0](arg)
            except Exception as e: self.status_line(f"*** /{cmd} failed: {e}", "error")
        elif cmd == "query" and arg: self.open_query(arg, (self.nodes.find_by_name(arg) or {}).get("public_key"))
        elif cmd == "msg" and arg.count(" ") >= 1:
            who, _, body = arg.partition(" ")
            w = self.open_query(who, (self.nodes.find_by_name(who) or {}).get("public_key"))
            self.send_dm(w, body.strip())
        elif cmd == "close":
            if self.current and self.current.name.startswith("@"): self.close_window(self.current.name)
            else: self.status_line("*** /close only closes private (@name) windows.", "warn")
        elif cmd == "join":
            name = "#" + arg.lstrip("#") if arg.lower() != "public" else "Public"
            if name in self.windows: self.select_window(name)
            else: self.status_line(f"*** No such channel: {arg}", "error")
        elif cmd == "freq" and arg:
            if not self.require_connection(): return
            def work():
                with ea.MESH_LOCK: return ea.set_radio_frequency(arg)
            def done(r):
                ok, msg = (False, str(r)) if isinstance(r, Exception) else r
                self.status_line(("*** " if ok else "*** ERROR: ") + msg, "info" if ok else "error")
            self.status_line(f"*** Setting {arg} MHz and rebooting the node (about 20s)...", "info")
            self.bg(work, done)
        else: self.status_line(f"*** Unknown command: /{cmd}  (try /help)", "error")

    # ---- demo ---------------------------------------------------------------------------------
    def load_demo(self):
        ea.CHANNEL_INDEX_BY_NAME.update({"Public": 0, "drivebc": 3, "bcferries": 4, "bctransit": 5, "translink": 6, "weather": 7, "bot-van": 8})
        self.sb_state.config(text="Demo mode (no radio)")
        now = int(time.time())
        spots = [("Surrey Repeater", 2, 49.13, -122.82, 60), ("Mt Seymour Rptr", 2, 49.37, -122.95, 300), ("Cypress Rptr", 2, 49.40, -123.20, 3000),
                 ("Victoria Hub", 2, 48.46, -123.36, 900), ("Nanaimo Rptr", 2, 49.17, -123.94, 7200), ("Alice", 1, 49.28, -123.12, 120),
                 ("Bob", 1, 49.19, -122.85, 30), ("Langley Room", 3, 49.10, -122.60, 5000), ("Tofino Sensor", 4, 49.15, -125.90, 600),
                 ("Old Whistler Rptr", 2, 50.12, -122.95, 8 * 86400), ("Kamloops Rptr", 2, 50.67, -120.33, 2 * 86400)]
        radio = {f"{i:064x}": {"public_key": f"{i:064x}", "adv_name": n, "type": t, "adv_lat": la, "adv_lon": lo, "last_advert": now - a, "lastmod": now - a}
                 for i, (n, t, la, lo, a) in enumerate(spots)}
        self.nodes.update_from_radio(radio, now)
        self.nodes.update_from_radio({k: v for k, v in radio.items() if k not in list(radio)[-2:]}, now)  # last two fall off the radio but stay remembered
        feed = [("in", 0, "Anyone copy from Langley?", "Alice", {"snr": 11.5, "hops": 2}),
                ("in", 0, "Loud and clear in Surrey", "Bob", {"snr": 13.0, "hops": 0}),
                ("in", 8, "test", "Carol", {"snr": 9.25, "hops": 1})]
        for kind, idx, text, nick, extra in feed: self._h_chat(kind, idx, text, nick or self.settings["node_name"], extra)
        self._h_channels()
        self.addons.dispatch("on_demo")
        self._dm_in("Hey, is the Burnaby closure clear yet?", f"{5:064x}", {"snr": 10.5, "hops": 1})
        self._dm_in("Thanks for the relay earlier!", f"{6:064x}", {"snr": 12.0, "hops": 0})
        self.select_window("Public")


def main():
    if sys.stderr is None or sys.stdout is None:   # started with pythonw (no console window): keep tracebacks in a file
        os.makedirs(LOG_DIR, exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(LOG_DIR, "gui_errors.txt"), "a", encoding="utf-8", buffering=1)
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fill the UI with fake data; never touches the radio")
    args = ap.parse_args()
    root = tk.Tk()
    App(root, demo=args.demo)
    root.mainloop()


if __name__ == "__main__":
    main()
