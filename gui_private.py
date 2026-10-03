"""Private (direct-message) windows.

One node = one window, always: windows are matched by the node's public key (a DM only carries the first 12 hex characters, our
node memory has all 64), and only then by name.  So "@3ddcdf84" and "@Frenchie's Science Cap" can never exist side by side: as soon
as the name is known the key-only window is renamed - or merged into the named window if that already exists."""
import os
import re
import time

import meshcore_io as io
from gui_logs import WindowLog
import gui_nodes


class PrivateMixin:
    KEY_NAME = re.compile(r"^@[0-9a-f]{8,12}$")

    # ---- identity ----
    def find_private_window(self, key=None, name=None):
        k = (key or "").lower()
        if k:
            for n, w in self.windows.items():
                wk = (w.key or "").lower()
                if n.startswith("@") and wk and (wk.startswith(k[:12]) or k.startswith(wk[:12])): return w
        if name:
            for n, w in self.windows.items():
                if n.startswith("@") and n[1:].lower() == name.lower(): return w
        return None

    def _node_for(self, key=None, name=None):
        return (self.nodes.find_by_prefix(key[:12]) if key else None) or (self.nodes.find_by_name(name) if name else None)

    def _new_private(self, name, key):
        w = self.add_window("@" + name, f"Private conversation with {name}")
        w.key = key
        row = self.node_row(w)
        self.switchbar.set_kind(w.name, row["type"] if row else 0)
        return w

    # ---- opening / receiving ----
    def open_query(self, name, key=None):
        name = name.lstrip("@")
        node = self._node_for(key, name)
        if node and node["name"]: key, name = node["public_key"], node["name"]
        w = self.find_private_window(key, name)
        if w is None: w = self._new_private(name, key)
        else:
            if key and len(key) > len(w.key or ""): w.key = key
            if node and w.name != "@" + name: self.rename_query(w.name, name, key)
            w = self.windows.get("@" + name, w)
        self.select_window(w.name)
        return w

    def _dm_in(self, text, prefix, extra):
        node = self.nodes.find_by_prefix(prefix)
        name = node["name"] if node and node["name"] else None
        key = node["public_key"] if node else prefix
        w = self.find_private_window(key, name)
        if w is None: w = self._new_private(name or prefix[:8], key)
        else:
            if len(key) > len(w.key or ""): w.key = key
            if name and w.name != "@" + name:
                self.rename_query(w.name, name, key)
                w = self.windows.get("@" + name, w)
        if not name: self.lookup_sender_name(prefix)
        who = w.name[1:]
        bits = []
        if extra.get("snr") is not None: bits.append(f"SNR {extra['snr']}")
        hops = extra.get("hops")
        if hops is not None: bits.append("direct" if hops in (0, 255) else f"{hops} hops")
        self.chat_line(w, who, text, "text", f"({', '.join(bits)})" if bits else "", event="private")
        self.addons.dispatch("on_message", {"channel": w.name, "channel_idx": None, "nick": who, "text": text, "dm": True,
                                             "snr": extra.get("snr"), "hops": hops, "raw": extra.get("raw")})

    def send_dm(self, w, text):
        key = w.key or (self.nodes.find_by_name(w.name[1:]) or {}).get("public_key")
        if not self.connected or not key:
            self.status_line(f"*** Can't message {w.name[1:]}: " + ("not connected." if not self.connected else "that node's key isn't known yet."), "error")
            return
        w.key = key
        self.chat_line(w, self.settings["node_name"], text, "self")
        def work():
            res = io.execute_mesh_command(io.CONNECTION_ARGS + ["msg", key, text])
            out = f"{res.stdout}\n{res.stderr}"
            if re.search(r"unknown destination|\berror\b", out, re.IGNORECASE): raise RuntimeError(out.strip().splitlines()[-1])
        self.bg(work, lambda r: isinstance(r, Exception) and self.status_line(f"*** Message to {w.name[1:]} failed: {r}", "error"))

    # ---- turning "@3ddcdf84" into the real name ----
    def resolve_key_windows(self, ask_radio=False):
        for name in [n for n in self.windows if self.KEY_NAME.match(n)]:
            prefix = (self.windows[name].key or name[1:])[:12].lower()
            node = self.nodes.find_by_prefix(prefix)
            if node and node["name"]: self.rename_query(name, node["name"], node["public_key"])
            elif ask_radio and self.connected: self.lookup_sender_name(prefix)

    def lookup_sender_name(self, prefix):
        """Ask the radio for the name behind a key prefix (it may know a contact that mcIRC's memory has forgotten).  At most every 10 minutes per key."""
        now = time.time()
        if not self.connected or now - self._name_lookups.get(prefix, 0) < 600: return
        self._name_lookups[prefix] = now
        def work():
            for key, c in gui_nodes.fetch_radio_contacts().items():
                if key.startswith(prefix):
                    self.nodes.touch_contact(c)   # it just talked to us, so it counts as seen now
                    return c.get("adv_name") or None, c.get("public_key", key)
            return None
        def done(r):
            if not r or isinstance(r, Exception): return
            name, key = r
            w = self.find_private_window(key=prefix)
            if name and w is not None: self.rename_query(w.name, name, key)
        self.bg(work, done)

    def rename_query(self, old, name, key=None):
        new = "@" + name
        w = self.windows.get(old)
        if w is None or new == old: return
        if key and len(key) > len(w.key or ""): w.key = key
        target = self.windows.get(new)
        if target is not None and target is not w: return self.merge_windows(w, target)
        self._move_log(w, new)
        del self.windows[old]
        w.name, w.topic = new, f"Private conversation with {name}"
        self.windows[new] = w
        self.switchbar.remove(old)
        self._add_button(new)
        self._style_buttons()
        if self.current is w: self.select_window(new)
        self.status_line(f"*** {old[1:]} is {name}.", "info")

    def _move_log(self, w, new_name):
        if not (w.log and self.settings["log_enabled"]): return
        try:
            new_log = WindowLog(new_name, self.log_dir)
            history = open(w.log.path, encoding="utf-8").read() if os.path.exists(w.log.path) else ""
            existing = open(new_log.path, encoding="utf-8").read() if os.path.exists(new_log.path) else ""
            if new_log.path != w.log.path:
                with open(new_log.path, "w", encoding="utf-8") as f: f.write(history + existing)
                os.remove(w.log.path)
            w.log = new_log
        except OSError:
            pass

    def merge_windows(self, src, dst):
        """Fold the key-only window `src` into the already-open window `dst` of the same node: its history goes in front, then it disappears."""
        text = src.text.get("1.0", "end-1c")
        if text.strip():
            dst.text.config(state="normal")
            dst.text.insert("1.0", text + "\n", "hist")
            dst.text.config(state="disabled")
        if src.log and dst.log and self.settings["log_enabled"]:
            try:
                history = open(src.log.path, encoding="utf-8").read() if os.path.exists(src.log.path) else ""
                existing = open(dst.log.path, encoding="utf-8").read() if os.path.exists(dst.log.path) else ""
                with open(dst.log.path, "w", encoding="utf-8") as f: f.write(history + existing)
                if os.path.exists(src.log.path): os.remove(src.log.path)
            except OSError:
                pass
        dst.nicks |= src.nicks
        if len(src.key or "") > len(dst.key or ""): dst.key = src.key
        if src.unread == "msg" or not dst.unread: dst.unread = src.unread or dst.unread
        was_current = self.current is src
        src.log = None
        src.frame.destroy()
        self.windows.pop(src.name, None)
        self.switchbar.remove(src.name)
        self._style_buttons()
        if was_current: self.select_window(dst.name)
        self.status_line(f"*** {src.name[1:]} is {dst.name[1:]} - merged into one window.", "info")
