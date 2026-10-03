"""Auto reply addon: keyword rules.  Each rule has its own keywords, a match style, the channels it listens on, the reply
text, and the channels the reply goes to.  The first matching rule answers.  Example: "test" typed on #bot-van gets
"Test received, 2 hops"; "test" typed anywhere else is told to use #bot-van; "!traffic" on Public is answered on #drivebc.
Standalone - needs nothing else installed."""
import gui_platform
import re
import time
import tkinter as tk
from tkinter import messagebox, ttk

from gui_addons import AddonBase

MIN_GAP = 2.0                      # seconds between ANY two replies, whatever the rule (burst protection)
MAX_DESTINATIONS = 3               # a rule may send its reply to at most this many channels
MATCHES = ("exact", "starts with", "contains")
PLACEHOLDERS = "{sender} {hops} {snr} {channel} {keyword} {text}"
DEFAULT_RULES = [{"name": "Test", "enabled": True, "triggers": "test, t", "match": "exact", "listen": "",
                  "reply": "@{sender} Test received, {hops} hops", "reply_to": "", "cooldown": 20}]


def norm(name): return (name or "").strip().lstrip("#").lower()
def split_list(text): return [x.strip() for x in (text or "").split(",") if x.strip()]


class _Safe(dict):
    def __missing__(self, key): return "{" + key + "}"     # unknown placeholders stay as typed instead of crashing


def match_rule(rule, text):
    """-> (keyword, rest_of_message) if the message triggers this rule, else None.  Longer keywords are tried first."""
    t = text.strip()
    low = t.lower()
    mode = rule.get("match", "exact")
    for kw in sorted(split_list(rule.get("triggers", "")), key=len, reverse=True):
        k = kw.lower()
        if mode == "exact":
            if low == k: return kw, ""
        elif mode == "starts with":
            if low == k or low.startswith(k + " "): return kw, t[len(k):].strip()
        else:
            m = re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", low)
            if m: return kw, (t[:m.start()] + " " + t[m.end():]).strip()
    return None


class Addon(AddonBase):
    title = "Auto reply"
    version = "1.1.1"
    author = "bclml"
    description = "Keyword rules: answer chosen words on chosen channels with your own text, to the channels you pick."

    def on_load(self):
        self.last_by_key, self.last_any = {}, 0.0
        self.rules = []
        self.button = self.api.add_toolbar_button("", self.toggle)
        self._refresh_button()
        self.api.add_command("autoreply", self._command, "autoreply on|off - switch the auto reply on or off")

    # ---- the responder ----
    def on_message(self, msg):
        if msg.get("dm") or not self.api.get("enabled", True): return
        here, now = norm(msg["channel"]), time.time()
        for i, r in enumerate(self.api.get("rules", DEFAULT_RULES)):
            if not r.get("enabled", True): continue
            listen = {norm(c) for c in split_list(r.get("listen", ""))}
            if listen and here not in listen: continue
            hit = match_rule(r, msg["text"])
            if hit is None: continue
            key = (i, msg["nick"])                                   # the first matching rule decides; later rules are skipped
            if now - self.last_any < MIN_GAP or now - self.last_by_key.get(key, 0) < float(r.get("cooldown", 20)): return
            hops = msg.get("hops")
            try:
                reply = r.get("reply", "").format_map(_Safe(sender=msg["nick"], hops=0 if hops in (None, 255) else hops, snr=msg.get("snr", "?"),
                                                              channel=msg["channel"], keyword=hit[0], text=hit[1]))
            except (IndexError, ValueError):
                reply = r.get("reply", "")
            if not reply.strip(): return
            self.last_any = self.last_by_key[key] = now
            for dest in (split_list(r.get("reply_to", ""))[:MAX_DESTINATIONS] or [msg["channel"]]): self.api.send(dest, reply)
            return

    # ---- on/off ----
    def toggle(self): self._set(not self.api.get("enabled", True))

    def _command(self, arg):
        if arg.strip().lower() in ("on", "off"): self._set(arg.strip().lower() == "on")
        else: self.api.log(f"Auto reply is {'ON' if self.api.get('enabled', True) else 'OFF'} (use /autoreply on|off)")

    def _set(self, on):
        self.api.set("enabled", on)
        self._refresh_button()
        self.api.log("Auto reply " + ("switched ON" if on else "switched OFF - nothing will be answered"), "info" if on else "warn")

    def _refresh_button(self):
        on = self.api.get("enabled", True)
        self.button.config(text="Auto reply: ON" if on else "Auto reply: OFF", fg="#006400" if on else "#cc0000")

    # ---- Options page: the list of rules ----
    def build_options(self, parent):
        bg = parent["bg"]
        f = tk.Frame(parent, bg=bg)
        self.rules = [dict(r) for r in self.api.get("rules", DEFAULT_RULES)]
        self.enabled_var = tk.BooleanVar(value=self.api.get("enabled", True))
        tk.Label(f, text="Auto reply", bg=bg, font=(gui_platform.DIALOG_FONT_NAME, 9, "bold")).pack(anchor="w")
        tk.Checkbutton(f, text="Auto reply is ON", variable=self.enabled_var, bg=bg).pack(anchor="w")
        cols = (("on", "On", 30), ("name", "Rule", 90), ("keywords", "Keywords", 110), ("listen", "Heard on", 90), ("to", "Replies to", 90))
        self.tree = ttk.Treeview(f, columns=[c for c, _, _ in cols], show="headings", height=7, selectmode="browse")
        for c, label, w in cols:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x", pady=4)
        self.tree.bind("<Double-1>", lambda e: self.edit_rule())
        row = tk.Frame(f, bg=bg)
        row.pack(anchor="w")
        for text, cmd in (("Add rule...", self.add_rule), ("Edit...", self.edit_rule), ("Delete", self.delete_rule), ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1))):
            ttk.Button(row, text=text, command=cmd).pack(side="left", padx=2)
        tk.Label(f, text="The first rule that matches answers; later rules are skipped, so put specific rules above general ones. "
                         "Blank 'Heard on' = every channel; blank 'Replies to' = the channel it was heard on.\nPlaceholders: " + PLACEHOLDERS,
                 bg=bg, fg="#555", justify="left", wraplength=440).pack(anchor="w", pady=6)
        tk.Label(f, text="Tip: if other stations in range auto-reply too, keep rules narrow (exact words, only the channels you need) so a burst "
                         "of replies doesn't jam the mesh. Each person is answered at most once per rule cooldown.",
                 bg=bg, fg="#555", justify="left", wraplength=440).pack(anchor="w")
        self._fill()
        return f

    def _fill(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rules):
            self.tree.insert("", "end", iid=str(i), values=("yes" if r.get("enabled", True) else "no", r.get("name", ""), r.get("triggers", ""),
                                                            r.get("listen", "") or "all", r.get("reply_to", "") or "same channel"))
        if select is not None and self.tree.exists(str(select)): self.tree.selection_set(str(select))

    def _selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def add_rule(self):
        RuleDialog(self.tree, self.api, None, lambda r: (self.rules.append(r), self._fill(len(self.rules) - 1)))

    def edit_rule(self):
        i = self._selected()
        if i is not None: RuleDialog(self.tree, self.api, self.rules[i], lambda r, i=i: (self.rules.__setitem__(i, r), self._fill(i)))

    def delete_rule(self):
        i = self._selected()
        if i is not None:
            del self.rules[i]
            self._fill(min(i, len(self.rules) - 1))

    def move(self, step):
        i = self._selected()
        if i is None or not 0 <= i + step < len(self.rules): return
        self.rules[i], self.rules[i + step] = self.rules[i + step], self.rules[i]
        self._fill(i + step)

    def apply_options(self):
        self.api.set("rules", [dict(r) for r in self.rules])
        self.api.set("enabled", self.enabled_var.get())
        self._refresh_button()


class RuleDialog(tk.Toplevel):
    """Edit one rule.  on_done(rule_dict) is called when OK is pressed with valid values."""
    def __init__(self, parent, api, rule, on_done):
        super().__init__(parent.winfo_toplevel())
        self.api, self.on_done = api, on_done
        self.title("Edit rule" if rule else "New rule")
        self.transient(parent.winfo_toplevel())
        r = rule or {"name": "", "enabled": True, "triggers": "", "match": "exact", "listen": "", "reply": "", "reply_to": "", "cooldown": 20}
        self.v = {"name": tk.StringVar(value=r.get("name", "")), "enabled": tk.BooleanVar(value=r.get("enabled", True)),
                  "triggers": tk.StringVar(value=r.get("triggers", "")), "match": tk.StringVar(value=r.get("match", "exact")),
                  "listen": tk.StringVar(value=r.get("listen", "")), "reply": tk.StringVar(value=r.get("reply", "")),
                  "reply_to": tk.StringVar(value=r.get("reply_to", "")), "cooldown": tk.StringVar(value=str(r.get("cooldown", 20)))}
        body = tk.Frame(self, padx=10, pady=8)
        body.pack(fill="both", expand=True)
        tk.Checkbutton(body, text="This rule is on", variable=self.v["enabled"]).grid(row=0, column=1, sticky="w")
        self._row(body, 1, "Rule name:", "name", 24)
        self._row(body, 2, "Keywords (comma separated):", "triggers", 40)
        tk.Label(body, text="Match:").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Combobox(body, textvariable=self.v["match"], values=MATCHES, state="readonly", width=14).grid(row=3, column=1, sticky="w")
        self._row(body, 4, "Heard on channels:", "listen", 40, picker=True)
        tk.Label(body, text="blank = every channel", fg="#555").grid(row=5, column=1, sticky="w")
        self._row(body, 6, "Reply text:", "reply", 40)
        tk.Label(body, text="Placeholders: " + PLACEHOLDERS, fg="#555").grid(row=7, column=1, sticky="w")
        self._row(body, 8, "Send the reply to:", "reply_to", 40, picker=True)
        tk.Label(body, text=f"blank = the channel it was heard on (at most {MAX_DESTINATIONS} channels)", fg="#555").grid(row=9, column=1, sticky="w")
        self._row(body, 10, "Seconds between replies to\nthe same person:", "cooldown", 6)
        btns = tk.Frame(self, pady=6)
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        ttk.Button(btns, text="OK", command=self.ok).pack(side="right")

    def _row(self, body, row, label, key, width, picker=False):
        tk.Label(body, text=label, justify="left").grid(row=row, column=0, sticky="w", pady=2)
        cell = tk.Frame(body)
        cell.grid(row=row, column=1, sticky="w")
        tk.Entry(cell, textvariable=self.v[key], width=width).pack(side="left")
        if picker:
            mb = tk.Menubutton(cell, text="Pick ▾", relief="raised", padx=6)
            mb.pack(side="left", padx=4)
            menu = tk.Menu(mb, tearoff=0, postcommand=lambda m=None: self._fill_picker(menu, key))
            mb.config(menu=menu)

    def _fill_picker(self, menu, key):
        """A tick-list of the channels mcIRC knows; ticking adds/removes the name in the text box."""
        menu.delete(0, "end")
        chosen = {norm(c) for c in split_list(self.v[key].get())}
        self._picker_vars = []
        for name in self.api.channels():
            var = tk.BooleanVar(value=norm(name) in chosen)
            self._picker_vars.append(var)
            menu.add_checkbutton(label=name, variable=var, command=lambda n=name, var=var, k=key: self._toggle(k, n, var.get()))
        if not self._picker_vars: menu.add_command(label="(connect first to see your channels)", state="disabled")

    def _toggle(self, key, name, on):
        items = [c for c in split_list(self.v[key].get()) if norm(c) != norm(name)]
        if on: items.append(name)
        self.v[key].set(", ".join(items))

    def ok(self):
        r = {k: (var.get() if k == "enabled" else var.get().strip()) for k, var in self.v.items()}
        try: r["cooldown"] = max(0.0, float(r["cooldown"]))
        except ValueError:
            messagebox.showerror("Rule", "Seconds between replies must be a number.", parent=self)
            return
        if not split_list(r["triggers"]) or not r["reply"] or r["match"] not in MATCHES:
            messagebox.showerror("Rule", "A rule needs at least one keyword and a reply text.", parent=self)
            return
        r["name"] = r["name"] or split_list(r["triggers"])[0]
        self.on_done(r)
        self.destroy()
