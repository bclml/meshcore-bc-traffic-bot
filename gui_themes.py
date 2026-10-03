"""Colour themes for the chat panes (like mIRC's colour schemes).  The Windows-style chrome (menus, toolbar, buttons) stays classic grey;
a theme recolours the message text, nick list, window tree, input line and topic bar."""
from tkinter import ttk

_BASE_NICKS = ["#0000cc", "#009300", "#cc0000", "#7f007f", "#fc7f00", "#009393", "#7f0000", "#00007f", "#4b4b4b"]
_DARK_NICKS = ["#6fa8ff", "#5fd35f", "#ff6b6b", "#d98cff", "#ffb347", "#4fe0e0", "#ff9a9a", "#9aa8ff", "#c0c0c0"]

THEMES = {
    "Classic mIRC": dict(bg="#ffffff", fg="#000000", ts="#808080", info="#0000cc", warn="#cc6600", error="#cc0000", new="#cc0000", clear="#008000",
                         crit_fg="#ffffff", crit_bg="#cc0000", self="#7f007f", meta="#808080", bot="#008000", hist="#606060",
                         mention_fg="#000080", mention_bg="#fff3b0", me_fg="#ffffff", me_bg="#0054e3", hl_fg="#7f3f00", hl_bg="#ffd9a0",
                         nicks=_BASE_NICKS, pane_bg="#ffffff", pane_fg="#000000", entry_bg="#ffffff", entry_fg="#000000",
                         tree_msg="#cc0000", tree_event="#0000cc", sel_bg="#316ac5", sel_fg="#ffffff"),
    "Night": dict(bg="#1e1e1e", fg="#d4d4d4", ts="#7a7a7a", info="#6fa8ff", warn="#ffb347", error="#ff6b6b", new="#ff6b6b", clear="#5fd35f",
                  crit_fg="#ffffff", crit_bg="#b00020", self="#d98cff", meta="#8a8a8a", bot="#5fd35f", hist="#8a8a8a",
                  mention_fg="#ffe9a0", mention_bg="#4a3d00", me_fg="#ffffff", me_bg="#0b5cd5", hl_fg="#ffd9a0", hl_bg="#5a3300",
                  nicks=_DARK_NICKS, pane_bg="#252526", pane_fg="#d4d4d4", entry_bg="#2d2d2d", entry_fg="#f0f0f0",
                  tree_msg="#ff6b6b", tree_event="#6fa8ff", sel_bg="#094771", sel_fg="#ffffff"),
    "Terminal": dict(bg="#000000", fg="#33ff33", ts="#1f9d1f", info="#66ffcc", warn="#ffff33", error="#ff5555", new="#ff5555", clear="#33ff33",
                     crit_fg="#000000", crit_bg="#ff3333", self="#99ff99", meta="#1f9d1f", bot="#ccffcc", hist="#1f9d1f",
                     mention_fg="#000000", mention_bg="#33ff33", me_fg="#000000", me_bg="#ffff33", hl_fg="#000000", hl_bg="#66ffcc",
                     nicks=["#33ff33", "#66ffcc", "#ffff33", "#99ff99", "#33ccff", "#ff99ff", "#ffcc66", "#ccffcc", "#aaaaaa"],
                     pane_bg="#000000", pane_fg="#33ff33", entry_bg="#001a00", entry_fg="#33ff33",
                     tree_msg="#ff5555", tree_event="#66ffcc", sel_bg="#115511", sel_fg="#ffffff"),
    "Ocean": dict(bg="#00004d", fg="#e6e6ff", ts="#8080c0", info="#66ccff", warn="#ffcc33", error="#ff7777", new="#ff7777", clear="#66ff99",
                  crit_fg="#ffffff", crit_bg="#cc0033", self="#ffa6ff", meta="#9090d0", bot="#66ff99", hist="#8080c0",
                  mention_fg="#00004d", mention_bg="#ffee66", me_fg="#ffffff", me_bg="#cc3300", hl_fg="#00004d", hl_bg="#99ddff",
                  nicks=["#66ccff", "#66ff99", "#ff7777", "#ffa6ff", "#ffcc33", "#66ffff", "#ffaaaa", "#aab8ff", "#cccccc"],
                  pane_bg="#000066", pane_fg="#e6e6ff", entry_bg="#000033", entry_fg="#ffffff",
                  tree_msg="#ff7777", tree_event="#66ccff", sel_bg="#3355cc", sel_fg="#ffffff"),
    "Paper": dict(bg="#fbf6e9", fg="#2b2b2b", ts="#9a9380", info="#1a5fb4", warn="#b35c00", error="#c01c28", new="#c01c28", clear="#26a269",
                  crit_fg="#ffffff", crit_bg="#c01c28", self="#813d9c", meta="#9a9380", bot="#26a269", hist="#8a8470",
                  mention_fg="#1a3a8f", mention_bg="#f9e79f", me_fg="#ffffff", me_bg="#1a5fb4", hl_fg="#5c2d00", hl_bg="#f7c98b",
                  nicks=["#1a5fb4", "#26a269", "#c01c28", "#813d9c", "#e66100", "#1b8a8a", "#8f2f2f", "#33407a", "#5e5c64"],
                  pane_bg="#f6f0dd", pane_fg="#2b2b2b", entry_bg="#fffdf6", entry_fg="#2b2b2b",
                  tree_msg="#c01c28", tree_event="#1a5fb4", sel_bg="#a6c8ff", sel_fg="#000000"),
}
DEFAULT_THEME = "Classic mIRC"


def get(name): return THEMES.get(name, THEMES[DEFAULT_THEME])


def style_text(text, theme, font):
    """(Re)colour a chat Text widget and all its tags."""
    t = theme
    text.config(bg=t["bg"], fg=t["fg"], insertbackground=t["fg"], selectbackground=t["sel_bg"], selectforeground=t["sel_fg"])
    for tag, key in (("ts", "ts"), ("text", "fg"), ("info", "info"), ("warn", "warn"), ("error", "error"), ("new", "new"), ("clear", "clear"),
                     ("self", "self"), ("meta", "meta"), ("hist", "hist")):
        text.tag_config(tag, foreground=t[key])
    bold = (font.cget("family"), font.cget("size"), "bold")
    text.tag_config("critical", foreground=t["crit_fg"], background=t["crit_bg"])
    text.tag_config("bot", foreground=t["bot"], font=bold)
    text.tag_config("mention", foreground=t["mention_fg"], background=t["mention_bg"], font=bold)
    text.tag_config("mention_me", foreground=t["me_fg"], background=t["me_bg"], font=bold)
    text.tag_config("highlight", foreground=t["hl_fg"], background=t["hl_bg"], underline=True)
    for i, c in enumerate(t["nicks"]): text.tag_config(f"nick{i}", foreground=c)


def style_panes(app, theme):
    """Tree, nick list, input line and topic bar."""
    t = theme
    st = ttk.Style()
    st.configure("Treeview", background=t["pane_bg"], fieldbackground=t["pane_bg"], foreground=t["pane_fg"])
    st.map("Treeview", background=[("selected", t["sel_bg"])], foreground=[("selected", t["sel_fg"])])
    app.tree.tag_configure("msg", foreground=t["tree_msg"])
    app.tree.tag_configure("event", foreground=t["tree_event"])
    app.nicklist.config(bg=t["pane_bg"], fg=t["pane_fg"], selectbackground=t["sel_bg"], selectforeground=t["sel_fg"])
    app.entry.config(bg=t["entry_bg"], fg=t["entry_fg"], insertbackground=t["entry_fg"])
    app.topic.config(readonlybackground=t["entry_bg"], fg=t["entry_fg"])
