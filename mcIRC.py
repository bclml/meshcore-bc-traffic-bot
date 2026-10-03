#!/usr/bin/env python3
"""mcIRC - an mIRC-style chat client for MeshCore nodes.

The core does chat (channel windows, nick list, input line), the node list/map with long-term node memory, and
settings.  Everything else - including broadcasting traffic/weather/earthquake alerts - is an addon in addons/
(Tools > Addons).  Run:  python mcIRC.py     (add --demo to try it with fake data and no radio)"""
import argparse, datetime, logging, os, queue, re, sys, tempfile, threading, time, traceback
try:
    import tkinter as tk
except ImportError:      # Linux Pythons often ship without Tk
    import gui_platform as _gp
    sys.exit("mcIRC needs Tk. " + _gp.tk_missing_hint())
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
import gui_nodes
from gui_logs import WindowLog, LOG_DIR, logged_windows
import gui_update
import gui_style
import gui_diag
import gui_health
import gui_platform
from gui_adverts import AdvertWatcher
from gui_report import BugReportDialog
from gui_donate import DonateDialog
from gui_switchbar import SwitchBar
from gui_update_ui import UpdateDialog, CatalogDialog, LINKS, open_link
import gui_themes
import gui_sounds
from gui_private import PrivateMixin
from gui_menus import MenusMixin
from gui_commands import CommandsMixin, CommandPopup

HELP = ["Commands:", "  /help            this list", "  /list            channel list", "  /map             open the map",
        "  /nodes           node list", "  /addons          addon manager", "  /options         open Options",
        "  /connect         connect to the node", "  /disconnect      disconnect", "  /freq <MHz>      change the radio frequency (node reboots)",
        "  /clear           clear this window", "  /join <#name>    switch to a channel window", "  /query <name>    open a private window with a node",
        "  /msg <name> <text>  send a direct message", "  /close           close this private window", "  /quit            exit",
        "Type text in a channel window to send it to that channel (max ~120 characters).",
        "Type / to see every command as you type. In a private window with a repeater or room server, MeshCore CLI commands",
        "(/reboot, /ver, /get radio, /neighbors, ...) are sent to that node - use /login <admin password> first if it needs one.",
        "Elsewhere the same names, plus all meshcli commands (/contacts, /advert, ...), run on your own node. /meshcli <command> runs anything."]


MENTION = re.compile(r"@\[([^\]]+)\]|@([^\s,:;!?()\[\]]+)")
def _plain(s): return re.sub(r"\W", "", s.lower())


def split_mentions(text, base_tag, my_name, words=()):
    """Cut a message into (text, tag) parts so "@nickname" / "@[nick name]" stand out, in a stronger colour when it is YOUR name, and highlight
    words are underlined.  Returns (parts, mentions_me, hit_highlight_word)."""
    parts, pos, me, word = [], 0, False, False
    hl = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE) if words else None
    def plain(seg, tag):
        nonlocal word
        if not seg: return
        if hl is None: return parts.append((seg, tag))
        i = 0
        for m in hl.finditer(seg):
            if m.start() > i: parts.append((seg[i:m.start()], tag))
            parts.append((m.group(0), "highlight"))
            word, i = True, m.end()
        if i < len(seg): parts.append((seg[i:], tag))
    for m in MENTION.finditer(text):
        plain(text[pos:m.start()], base_tag)
        mine = bool(my_name) and _plain(m.group(1) or m.group(2) or "") == _plain(my_name)
        me = me or mine
        parts.append((m.group(0), "mention_me" if mine else "mention"))
        pos = m.end()
    plain(text[pos:], base_tag)
    return parts, me, word


class ChatWindow:
    def __init__(self, parent, name, topic, font, log=None, history=0, theme=None, my_name=""):
        self.name, self.topic, self.nicks, self.unread, self.key, self.log = name, topic, set(), "", None, log
        self.frame = tk.Frame(parent, bg=TEXT_BG)
        self.text = tk.Text(self.frame, wrap="word", state="disabled", bg=TEXT_BG, fg="black", font=font,
                            relief="sunken", bd=2, padx=4, pady=2, cursor="arrow")
        sb = ttk.Scrollbar(self.frame, command=self.text.yview)
        self.text.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        gui_themes.style_text(self.text, theme or gui_themes.get(None), font)
        t = self.text
        if log:
            old = log.tail(history)   # earlier sessions, shown in grey
            if old:
                t.config(state="normal")
                for line in old:
                    line = safe_text(line)
                    start, cut = t.index("end-1c"), (line.find("> ") + 2 if "> " in line else 0)
                    t.insert("end", line + "\n", "hist")
                    for m in MENTION.finditer(line, cut):      # old lines get the same @mention highlighting as new ones
                        mine = bool(my_name) and _plain(m.group(1) or m.group(2) or "") == _plain(my_name)
                        t.tag_add("mention_me" if mine else "mention", f"{start}+{m.start()}c", f"{start}+{m.end()}c")
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

    def apply_theme(self, theme, font): gui_themes.style_text(self.text, theme, font)

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
            gui_diag.event("connect", f"connecting: mode={s['mode']} port={s['port']} baud={s['baud'] or 'default'} (last good port: {s.get('last_port') or 'none'})")
            if s["mode"] == "usb": gui_diag.ports_snapshot()
            args = ea.build_connection_args(s["mode"], s["port"], s["ble_target"], s["tcp_host"], s["tcp_port"], s["baud"],
                                            prefer_port=s.get("last_port", ""), should_stop=self.stop_evt.is_set)
            if not args or self.stop_evt.is_set():
                gui_diag.event("connect", "no usable connection found" if not args else "cancelled")
                return
            gui_diag.event("connect", "using " + gui_diag.describe_args(args))
            ea.CONNECTION_ARGS = args
            ea.resolve_channel_indices()
            if self.stop_evt.is_set(): return
            q.put(("channels",))
            q.put(("state", "connected", " ".join(args)))
            gui_diag.event("connect", f"connected; {len(ea.CHANNEL_INDEX_BY_NAME)} channel(s) resolved")
            try:
                node = gui_nodecfg.read_node()
                gui_diag.node_summary(node)
                q.put(("nodeinfo", node))
                if args[0] == "-s": q.put(("lastport", args[1]))   # it answered: next time use this port without probing
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
                self.app.recovery.maybe()      # only acts if the user switched "restart a silent radio" on
                if now - last_sync >= self.app.settings["node_sync_minutes"] * 60:
                    last_sync = now
                    self.app.node_sync_worker()
                self.app.adverts.listen(self.app.settings["poll_seconds"], self.stop_evt)      # idle time = listening for adverts (or just waiting)
        except Exception as e:
            logging.error(f"Connection ended: {e}")
            gui_diag.event("crash", "connection thread:\n" + traceback.format_exc())
        finally:
            gui_diag.event("connect", "stopped")
            q.put(("state", "stopped", ""))


class QueueLogHandler(logging.Handler):
    def __init__(self, q):
        super().__init__(logging.INFO)
        self.addFilter(ea.RedactFilter())
        self.q = q

    def emit(self, record):
        msg = record.getMessage()
        if "[DIAGNOSTIC]" not in msg: self.q.put(("log", record.levelno, msg))


class App(PrivateMixin, MenusMixin, CommandsMixin):
    def __init__(self, root, demo=False):
        self.root, self.demo, self.connected = root, demo, False
        self.settings = load_settings()
        if demo: self.settings.update(node_name="DemoNode", mode="usb", port="auto", tcp_host="", ble_target="", auto_connect=False)   # demo = fake everything
        self.q = queue.Queue()
        self.windows, self.current, self.history, self.hist_pos = {}, None, [], 0
        self.log_dir = tempfile.mkdtemp(prefix="meshlogs_") if demo else LOG_DIR   # demo mode must never touch the real logs
        self.commands, self.map_layers = {}, {}   # filled by addons
        self._name_lookups = {}                   # key prefix -> time of the last radio lookup (rate limit)
        self.worker = CoreWorker(self)
        self.nodes = NodeStore(":memory:") if demo else NodeStore()
        self.addons = AddonManager(self)
        self.adverts = AdvertWatcher(self)
        self.recovery = gui_health.Recovery(self)
        ea.HEALTH.listeners.append(lambda ev, info: self.q.put(("health", ev, info)))      # called on the connection thread
        if demo: self.settings["addons"], self.settings["addons_enabled"] = {}, {n: True for n in self.addons.discover()}
        gui_style.apply_classic(root)   # old-mIRC chrome: must run before any widget exists
        self.font = gui_style.chat_font(self.settings["font_size"])
        self.theme = gui_themes.get(self.settings.get("theme"))
        self.map_win = self.addons_win = None
        root.title("mcIRC")
        root.geometry("1000x640")
        root.configure(bg=BG)
        self._menu()
        self._toolbar()
        self._status_bar()
        self._body()
        self.switchbar = SwitchBar(self)
        gui_themes.style_panes(self, self.theme)
        self.status = self.add_window("Status", "status window", in_tree=False)
        self.tree.insert("", 0, iid="Status", text="Status")
        self.tree.insert("", "end", iid="Channels", text="Channels", open=True)
        self.add_window("Public", CHANNELS["Public"])
        for name in logged_windows(self.log_dir):   # windows from earlier sessions come back with their history
            if name not in self.windows and name not in self.settings.get("closed_channels", []):
                self.add_window(name, f"Private conversation with {name[1:]}" if name.startswith("@") else CHANNELS.get(name, "(restored from log)"))
        self.apply_settings()
        ea.GUI_CALLBACK = lambda kind, idx, text, nick, **extra: self.q.put(("chat", kind, idx, text, nick, extra))
        logging.getLogger().addHandler(QueueLogHandler(self.q))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.select_window("Status")
        self.status_line("*** Welcome. Type /help for commands, or press Connect.", "info")
        self.addons.load_all()
        self.resolve_key_windows()
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
        t.add_command(label="Reset radio via USB...", command=self.reset_radio_now)
        t.add_command(label="Open logs folder", command=lambda: (os.makedirs(LOG_DIR, exist_ok=True), gui_platform.open_path(LOG_DIR)))
        self.addon_menu = tk.Menu(m, tearoff=0)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Commands", command=lambda: self.command("help"))
        h.add_command(label="Check for updates...", command=lambda: UpdateDialog(self))
        h.add_command(label="Browse addons...", command=lambda: CatalogDialog(self))
        h.add_separator()
        h.add_command(label="Report a bug...", command=lambda: BugReportDialog(self))
        h.add_command(label="Open troubleshooting logs folder", command=self.open_diag_folder)
        for label in LINKS:
            if label not in ("Project page on GitHub...", "Report a bug..."): h.add_command(label=label, command=lambda l=label: open_link(l))
        h.add_separator()
        h.add_command(label="Support mcIRC (optional donation)...", command=lambda: DonateDialog(self))
        h.add_command(label="Project page on GitHub...", command=lambda: open_link("Project page on GitHub..."))
        h.add_command(label=f"About (version {gui_platform.version_text(gui_update.local_version())})", command=lambda: show_about(self.root))
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
        menu.add_command(label="Close window", state="disabled" if self.current is self.status else "normal", command=lambda: self.close_window(self.current.name))
        menu.add_command(label="Clear window", command=lambda: self.current and self.current.clear())

    def _toolbar(self):
        self.icons = gui_style.make_icons(self.root)
        self.toolbar = bar = tk.Frame(self.root, bg=BG, bd=2, relief="raised")
        bar.pack(fill="x")
        for item in (("connect", "Connect to the node", self.connect), ("disconnect", "Disconnect", self.disconnect), None,
                     ("channels", "Channels list", lambda: ChannelListDialog(self)), ("nodes", "Node list", self.open_node_list), ("map", "Map", self.open_map), None,
                     ("addons", "Addons", self.open_addons), ("options", "Options", self.open_options), None,
                     ("donate", "Support mcIRC - optional donation", lambda: DonateDialog(self))):
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
        self.sb_warn = tk.Label(bar, bg=BG, fg="#c00000", relief="sunken", anchor="w", font=(gui_platform.UI_FONT_NAME, gui_platform.UI_FONT_SIZE, "bold"))
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
        self.cmd_popup = CommandPopup(self, right, entry_row, self.entry)   # also binds Return / Up / Down / Tab / Esc and the key counter
        mid = tk.Frame(right, bg=BG)
        mid.pack(fill="both", expand=True)
        self.nicklist = tk.Listbox(mid, width=16, font=self.font, bg="white", relief="sunken", bd=2, activestyle="none", exportselection=False)
        self.nicklist.pack(side="right", fill="y")
        self.nicklist.bind("<Double-Button-1>", self.nick_dblclick)
        gui_platform.bind_right_click(self.nicklist, self._nick_menu)
        gui_platform.bind_right_click(self.tree, self._tree_menu)
        self.stack = tk.Frame(mid, bg=BG)
        self.stack.pack(side="left", fill="both", expand=True)
        self.stack.grid_rowconfigure(0, weight=1)
        self.stack.grid_columnconfigure(0, weight=1)

    # ---- windows ------------------------------------------------------------------------------
    def add_window(self, name, topic, in_tree=True):
        if name in self.windows: return self.windows[name]
        log = WindowLog(name, self.log_dir) if self.settings["log_enabled"] else None
        w = ChatWindow(self.stack, name, topic, self.font, log, self.settings["log_history"], self.theme, self.settings["node_name"])
        w.frame.grid(row=0, column=0, sticky="nsew")
        self.windows[name] = w
        if in_tree and hasattr(self, "tree") and not name.startswith("@") and self.tree.exists("Channels"):   # the tree holds Status + channels only; people/repeaters/rooms live on the top bar
            self.tree.insert("Channels", "end", iid=name, text=name)
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
        elif self.tree.selection(): self.tree.selection_set(())      # private windows aren't in the tree: don't leave a stale highlight (or a pending select event) on a channel
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
        row = self.node_row(self.windows.get(name))
        self.switchbar.add(name, lambda n=name: self.select_window(n), lambda n=name: self.close_window(n),
                           lambda x, y, n=name: self.show_window_menu(n, x, y), row["type"] if row else 0)

    def _style_buttons(self):
        if hasattr(self, "switchbar"): self.switchbar.sync(self.windows, self.current)





    # ---- writing ------------------------------------------------------------------------------
    def stamp(self):
        return [(datetime.datetime.now().strftime("[%H:%M] "), "ts")] if self.settings["show_time"] else []

    def open_diag_folder(self):
        os.makedirs(gui_diag.directory(), exist_ok=True)
        gui_platform.open_path(gui_diag.directory())

    def status_line(self, text, tag="text", log=True):
        if log and tag in ("error", "warn"): gui_diag.event(tag, text)       # (records that came through logging are already in the diagnostic log)
        self.status.write(self.stamp() + [(text, tag)])
        self.mark_unread(self.status, "event")

    def chat_line(self, w, nick, text, tag, suffix="", event=None):
        mine = nick == self.settings["node_name"]
        nick_tag = "bot" if mine else f"nick{sum(map(ord, nick)) % len(self.theme['nicks'])}"
        words = [x.strip() for x in self.settings.get("highlight_words", "").split(",") if x.strip()]
        body, me, word = (split_mentions(text, tag, self.settings["node_name"], words) if not mine else ([(text, tag)], False, False))
        parts = self.stamp() + [("<", "text"), (nick, nick_tag), ("> ", "text")] + body
        if suffix: parts.append((f"  {suffix}", "meta"))
        w.write(parts)
        w.nicks.add(nick)
        self.mark_unread(w, "msg")
        if w is self.current: self.refresh_nicks()
        if not mine:
            watching = w is self.current and self.root.focus_displayof() is not None
            kind = "private" if event == "private" else "mention" if me else "highlight" if word else "channel"
            if not watching: gui_sounds.notify(self.settings, kind, self.root.bell)

    # ---- queue handlers (GUI thread) ----------------------------------------------------------
    def drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                getattr(self, "_h_" + item[0])(*item[1:])
        except queue.Empty: pass
        self.root.after(100, self.drain)

    def _h_chat(self, kind, idx, text, nick, extra):
        gui_diag.count(f"messages_{kind}")          # only counted: the text itself is never logged
        if kind == "dm": return self._dm_in(text, extra.get("pubkey") or nick, extra)
        name = display_for_index(idx)
        closed = self.settings.get("closed_channels", [])
        if name in closed and name not in self.windows: closed.remove(name)       # someone spoke: the channel comes back
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
        if ea.HEALTH.is_down and msg.startswith("Failed to poll for incoming messages"): return      # one "radio not responding" notice is enough; the log has the rest
        self.status_line("*** " + msg, "error" if level >= logging.ERROR else "warn" if level >= logging.WARNING else "info", log=False)

    def _h_state(self, state, detail):
        was = self.connected
        self.connected = state == "connected"
        self.sb_state.config(text={"connecting": "Connecting...", "connected": f"Connected ({detail})", "stopped": "Not connected"}[state])
        gui_diag.event("state", f"{state} {'(' + gui_diag.describe_args(detail.split()) + ')' if detail else ''}")
        if self.connected and not was:
            self.addons.dispatch("on_connect")
            self.resolve_key_windows(ask_radio=True)
        if was and not self.connected: self.addons.dispatch("on_disconnect")

    def _h_channels(self):
        closed = self.settings.get("closed_channels", [])
        for name, idx in ea.CHANNEL_INDEX_BY_NAME.items():
            if name != "Public" and "#" + name.lstrip("#") not in closed: self.add_window("#" + name.lstrip("#"), CHANNELS.get("#" + name.lstrip("#"), f"Channel {idx}"))
        self.status_line(f"*** Resolved channels: {', '.join(f'{n}={i}' for n, i in ea.CHANNEL_INDEX_BY_NAME.items())}", "info")

    def _h_nodes(self, r):
        cap = self.settings["radio_capacity"]
        if r["new"] or r["pruned"] or r["removed_from_radio"]:
            self.status_line(f"*** Nodes: {r['on_radio']}/{cap} on radio, {r['total']} remembered (+{r['new']} new, {r['pruned']} forgotten"
                             + (f", {r['removed_from_radio']} removed from radio" if r["removed_from_radio"] else "") + ")", "info")
        if r["on_radio"] >= cap * 0.95:
            self.status_line(f"*** Radio contact list nearly full ({r['on_radio']}/{cap}) - new nodes may not fit on the radio, but they are still remembered here.", "warn")
        self.resolve_key_windows()
        if self.map_win is not None and self.map_win.winfo_exists(): self.map_win.refresh(force=True)

    def _h_advert(self, kind, c):
        """A node advertised (or changed) while the advert listener was connected."""
        key = c["public_key"]
        known = self.nodes.find_by_prefix(key)
        if kind == "new_contact": new = self.nodes.remember_pending(c)
        else:
            self.nodes.touch_contact(c)
            new = known is None
        if new and self.settings.get("advert_notices", True):
            what = gui_nodes.TYPE_NAMES.get(int(c.get("type") or 0), "Node")
            note = " (waiting for approval - the radio is in manual-add mode)" if kind == "new_contact" else ""
            self.status_line(f"*** New {what.lower()} heard: {c.get('adv_name') or key[:8]}{note}", "info")
        self.resolve_key_windows()                                 # a private window that only had a key gets its real name now
        for attr, update in (("map_win", "refresh"), ("nodes_win", "fill")):      # open map / node list show it straight away
            w = getattr(self, attr, None)
            try:
                if w is not None and w.winfo_exists(): getattr(w, update)()
            except Exception: pass

    def _h_health(self, ev, info):
        """The radio stopped answering / answers again / was restarted."""
        clock = lambda t: datetime.datetime.fromtimestamp(t).strftime("%H:%M")
        gui_diag.event("health", f"{ev} {info}")
        if ev == "down":
            self.sb_warn.config(text=f" RADIO NOT RESPONDING since {clock(info['since'])} ")
            self.sb_warn.pack(side="left", after=self.sb_state)
            waiting = len(ea.PENDING_SENDS)
            self.status_line(f"*** The radio has not answered since {clock(info['since'])}. Check the USB cable or press the board's reset button. "
                             "Messages you send are marked NOT SENT; alerts are kept for up to 30 minutes and go out when it answers again.", "error")
        elif ev == "up":
            self.sb_warn.pack_forget()
            mins = max(1, int((time.time() - info["since"]) / 60))
            waiting = len(ea.PENDING_SENDS)
            self.status_line(f"*** The radio is answering again (it was silent for about {mins} min)." + (f" Sending {waiting} queued alert(s)..." if waiting else ""), "info")
        elif ev == "reset":
            self.status_line(f"*** Restarting the radio on {info['port']} ({info['how']}, {info['chip']}) by pulsing its reset line...", "warn")
        elif ev == "reset_refused":
            self.status_line(f"*** Not restarting the radio: {info['why']}.", "warn")
        elif ev == "reset_failed":
            self.status_line(f"*** Could not restart the radio: {info['why']}", "error")

    def unsent(self, w, text, why):
        """A message that did not go out: say so right where it was typed (never leave it looking delivered)."""
        short = text if len(text) <= 60 else text[:59] + "..."
        gui_diag.event("unsent", why)
        gui_diag.count("messages_not_sent")
        (w or self.status).write(self.stamp() + [(f"* NOT SENT ({why}): {short}", "error")])
        if w is not None and w is not self.current: self.status_line(f"*** Not sent to {w.name}: {why}", "error")

    def reset_radio_now(self):
        ok, why = gui_health.reset_allowed(ea.CONNECTION_ARGS)
        if not ok: return messagebox.showinfo("Reset radio", f"Can't do this automatically: {why}.", parent=self.root)
        if not messagebox.askyesno("Reset radio", f"Restart the node ({why}) by pulsing its USB reset line?\n\nIt reboots and reconnects in about 10 seconds. "
                                   "Use this when the radio has stopped answering.", parent=self.root): return
        self.bg(lambda: self.recovery.reset("manual"), lambda r: None)

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

    def _h_lastport(self, port):
        if self.settings.get("last_port") != port:
            self.settings["last_port"] = port
            self.save()





    def raise_window(self):
        r = self.root
        r.deiconify()
        r.lift()
        r.attributes("-topmost", True)
        r.after(300, lambda: r.attributes("-topmost", False))
        r.focus_force()

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
        if hasattr(self, "tree"): self.apply_theme()
        self.save()
        if self.current: self.refresh_nicks()

    def apply_theme(self):
        self.theme = gui_themes.get(self.settings.get("theme"))
        for w in self.windows.values(): w.apply_theme(self.theme, self.font)
        gui_themes.style_panes(self, self.theme)

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
            if self.worker.stop_evt.is_set(): self.status_line("*** Still cancelling the previous attempt - try again in a few seconds.", "warn")
            else: self.status_line("*** Already connected." if self.connected else "*** Already connecting - this can take a little while.", "warn")
            return
        self.apply_settings()
        self.status_line("*** Connecting to the node...", "info")
        self.worker.start(self.settings)

    def disconnect(self):
        if self.worker.running:
            self.worker.stop()
            self.status_line("*** Disconnecting..." if self.connected else "*** Cancelling the connection attempt (it stops once the current radio command finishes)...", "info")

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
        if not text.lower().startswith("/login"): self.history.append(text)      # never keep an admin password in the recall list
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
        if ea.HEALTH.is_down:                      # don't make people wait a minute to learn what we already know
            return self.unsent(w, text, "the radio is not answering")
        if w: self.chat_line(w, self.settings["node_name"], text, "self")
        self.bg(lambda: ea.execute_mesh_command(ea.CONNECTION_ARGS + cmd),
                lambda r: isinstance(r, Exception) and self.unsent(w, text, ea.explain_failure(str(r))))

    def command(self, line):
        cmd, _, arg = line.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd == "login": return self.cmd_login(arg)
        if cmd == "logout": return self.cmd_logout(arg)
        if cmd == "rpt":
            if self.is_remote_window() and arg: return self.send_remote(self.current, arg)
            return self.status_line("*** /rpt <text> sends raw text to the repeater of the current private window.", "warn")
        if self.try_remote_command(cmd, arg): return          # in a repeater / room-server window, CLI commands go to that node
        if cmd in ("bug", "report"): return BugReportDialog(self)
        if cmd in ("donate", "support"): return DonateDialog(self)
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
        elif self.run_node_command(cmd, arg): pass            # everything else meshcli knows runs on your own node
        else: self.status_line(f"*** Unknown command: /{cmd}  (type / to see the list)", "error")

    # ---- demo ---------------------------------------------------------------------------------
    def load_demo(self):
        ea.CHANNEL_INDEX_BY_NAME.update({"Public": 0, "drivebc": 3, "bcferries": 4, "bctransit": 5, "translink": 6, "weather": 7, "bot-van": 8})
        self.sb_state.config(text="Demo mode (no radio)")
        now = int(time.time())
        spots = [("Surrey Repeater", 2, 49.13, -122.82, 60), ("Mt Seymour Rptr", 2, 49.37, -122.95, 300), ("Cypress Rptr", 2, 49.40, -123.20, 3000),
                 ("Victoria Hub", 2, 48.46, -123.36, 900), ("Nanaimo Rptr", 2, 49.17, -123.94, 7200), ("Alice", 1, 49.28, -123.12, 120),
                 ("Bob", 1, 49.19, -122.85, 30), ("Langley Room", 3, 49.10, -122.60, 5000), ("Tofino Sensor", 4, 49.15, -125.90, 600),
                 ("Old Whistler Rptr", 2, 50.12, -122.95, 8 * 86400), ("Kamloops Rptr", 2, 50.67, -120.33, 2 * 86400)]
        radio = {f"{i + 16:02x}" * 32: {"public_key": f"{i + 16:02x}" * 32, "adv_name": n, "type": t, "adv_lat": la, "adv_lon": lo, "last_advert": now - a, "lastmod": now - a}
                 for i, (n, t, la, lo, a) in enumerate(spots)}
        self.nodes.update_from_radio(radio, now)
        self.nodes.update_from_radio({k: v for k, v in radio.items() if k not in list(radio)[-2:]}, now)  # last two fall off the radio but stay remembered
        feed = [("in", 0, "Anyone copy from Langley?", "Alice", {"snr": 11.5, "hops": 2}),
                ("in", 0, "Loud and clear in Surrey", "Bob", {"snr": 13.0, "hops": 0}),
                ("in", 8, "test", "Carol", {"snr": 9.25, "hops": 1})]
        for kind, idx, text, nick, extra in feed: self._h_chat(kind, idx, text, nick or self.settings["node_name"], extra)
        self._h_channels()
        self.addons.dispatch("on_demo")
        self._dm_in("Hey, is the Burnaby closure clear yet?", f"{5 + 16:02x}" * 32, {"snr": 10.5, "hops": 1})
        self._dm_in("Thanks for the relay earlier!", f"{6 + 16:02x}" * 32, {"snr": 12.0, "hops": 0})
        self.select_window("Public")


def main():
    if sys.stderr is None or sys.stdout is None:   # started with pythonw (no console window): keep tracebacks in a file
        os.makedirs(LOG_DIR, exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(LOG_DIR, "gui_errors.txt"), "a", encoding="utf-8", buffering=1)
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fill the UI with fake data; never touches the radio")
    args = ap.parse_args()
    if args.demo: os.environ["MCIRC_NO_LOG_FILE"] = "1"      # demo mode must never write to the bot's real log
    holder, lock = {}, None
    if not args.demo:   # one copy at a time: two would fight over the radio port
        import gui_single
        lock = gui_single.acquire(lambda: holder["app"].q.put(("call", holder["app"].raise_window)) if "app" in holder else None)
        if lock is None and gui_single.notify_existing():
            print("mcIRC is already running - brought it to the front.")
            return
    gui_diag.start(gui_update.local_version(), demo=args.demo)
    root = tk.Tk()
    root.report_callback_exception = gui_diag.tk_exception
    holder["app"] = App(root, demo=args.demo)
    root.mainloop()


if __name__ == "__main__":
    main()
