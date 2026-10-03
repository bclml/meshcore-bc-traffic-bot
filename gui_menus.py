"""Right-click menus for the window tree, the private-message buttons and the nick list, plus closing / reopening windows."""
import os
import time
import tkinter as tk
from tkinter import messagebox, simpledialog

from gui_logs import file_name, LOG_DIR
from gui_nodes import TYPE_NAMES


class MenusMixin:
    # ---- closing ----
    def close_window(self, name):
        """Private windows and channel windows can be closed (a channel comes back when someone speaks in it, or via View > Channels list).
        The Status window can't."""
        w = self.windows.get(name)
        if w is None or w is self.status: return
        if w.log: w.log.stamp("Session Close")
        was_current = w is self.current
        w.frame.destroy()
        del self.windows[name]
        if name.startswith("@"): self.switchbar.remove(name)
        else:
            if self.tree.exists(name): self.tree.delete(name)
            closed = self.settings.setdefault("closed_channels", [])
            if name not in closed and name != "Public": closed.append(name)
            self.save()
        self._style_buttons()
        if was_current: self.select_window("Status")

    def open_channel(self, name):
        from gui_common import CHANNELS
        closed = self.settings.setdefault("closed_channels", [])
        if name in closed: closed.remove(name)
        w = self.windows.get(name) or self.add_window(name, CHANNELS.get(name, "Channel"))
        self.select_window(name)
        self.save()
        return w

    # ---- the menus ----
    def _tree_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid in self.windows:
            self.select_window(iid)
            self.show_window_menu(iid, e.x_root, e.y_root)

    def _nick_menu(self, e):
        i = self.nicklist.nearest(e.y)
        if i < 0 or not self.nicklist.bbox(i): return
        self.nicklist.selection_clear(0, "end")
        self.nicklist.selection_set(i)
        nick = self.nicklist.get(i).lstrip("@")
        m = tk.Menu(self.root, tearoff=0)
        if nick != self.settings["node_name"]:
            m.add_command(label=f"Private message with {nick}", command=lambda: self._open_query_by_name(nick))
            m.add_command(label="Node info...", command=lambda: self.node_info(name=nick))
        m.add_command(label="Copy name", command=lambda: (self.root.clipboard_clear(), self.root.clipboard_append(nick)))
        m.tk_popup(e.x_root, e.y_root)

    def _open_query_by_name(self, nick):
        node = self.nodes.find_by_name(nick)
        self.open_query(nick, node["public_key"] if node else None)

    def show_window_menu(self, name, x, y):
        w = self.windows.get(name)
        if w is None: return
        private = name.startswith("@")
        row = self.node_row(w) if private else None
        m = tk.Menu(self.root, tearoff=0)
        if private:
            m.add_command(label="Node info...", command=lambda: self.node_info(w=w))
            if row and (row["lat"] or row["lon"]): m.add_command(label="Show on map", command=lambda: self.show_on_map(row))
            if row and row["type"] in (2, 3):
                m.add_separator()
                kind = TYPE_NAMES.get(row["type"], "repeater").lower()
                m.add_command(label=f"Log in to this {kind}...", command=lambda: self._login_dialog(w))
                m.add_command(label="Status (stats-core)", command=lambda: self._select_then(w, lambda: self.send_remote(w, "stats-core")))
                m.add_command(label="Neighbors", command=lambda: self._select_then(w, lambda: self.send_remote(w, "neighbors")))
                m.add_command(label="Version", command=lambda: self._select_then(w, lambda: self.send_remote(w, "ver")))
                m.add_command(label=f"Reboot this {kind}...", command=lambda: self._select_then(w, lambda: self.command("reboot")))
            m.add_separator()
        m.add_command(label="Mark as read", command=lambda: self._mark_read(w))
        m.add_command(label="Clear window", command=w.clear)
        m.add_command(label="Open log file", command=lambda: self._open_log(w))
        if w is not self.status:
            m.add_separator()
            m.add_command(label="Close", command=lambda: self.close_window(name))
        m.tk_popup(x, y)

    def _select_then(self, w, fn):
        self.select_window(w.name)
        fn()

    def _mark_read(self, w):
        w.unread = ""
        if self.tree.exists(w.name): self.tree.item(w.name, tags=())
        self._style_buttons()

    def _open_log(self, w):
        path = w.log.path if w.log else os.path.join(self.log_dir, file_name(w.name) + ".txt")
        if os.path.exists(path): os.startfile(path)
        else: messagebox.showinfo("Log", "This window has no log file yet.", parent=self.root)

    def _login_dialog(self, w):
        pw = simpledialog.askstring("Log in", f"Admin password for {w.name[1:]}\n(kept in memory only, never saved or logged):", show="*", parent=self.root)
        if pw:
            self.select_window(w.name)
            self.cmd_login(pw)

    # ---- node info / map ----
    def node_info(self, w=None, name=None):
        row = self.node_row(w) if w else self.nodes.find_by_name(name)
        title = (w.name[1:] if w else name)
        if not row:
            return messagebox.showinfo("Node info", f"{title}\n\nNot in mcIRC's node memory yet (no advert heard / not on the radio).\n" +
                                       (f"Key: {w.key}" if w and w.key else ""), parent=self.root)
        age = (time.time() - row["last_seen"]) / 3600
        lines = [f"Name: {row['name']}", f"Type: {TYPE_NAMES.get(row['type'], 'unknown')}", f"Public key: {row['public_key']}",
                 f"Last seen: {age:.1f} hours ago" if age < 48 else f"Last seen: {age / 24:.1f} days ago",
                 "On the radio's contact list: " + ("yes" if row["on_radio"] else "no (remembered by mcIRC only)"),
                 f"Position: {row['lat']:.5f}, {row['lon']:.5f}" if (row["lat"] or row["lon"]) else "Position: unknown"]
        messagebox.showinfo("Node info", "\n".join(lines), parent=self.root)

    def show_on_map(self, row):
        self.open_map()
        mw = self.map_win
        if mw is not None and hasattr(mw, "focus_on"): mw.focus_on(row["lat"], row["lon"])
