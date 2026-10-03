"""Dialogs for updating the app and browsing/installing tested addons, plus the feedback links in the Help menu."""
import subprocess, sys, tkinter as tk, webbrowser
from tkinter import ttk, messagebox

import gui_addons as ga
import gui_update as gu
from gui_common import BG

SITE = f"https://github.com/{ga.REPO}"
LINKS = {
    "Report a bug...": f"{SITE}/issues/new?template=bug_report.yml",
    "Suggest an idea...": f"{SITE}/issues/new?template=idea.yml",
    "Submit an addon for the catalog...": f"{SITE}/issues/new?template=addon_submission.yml",
    "Addon guide (write your own)...": f"{SITE}/blob/{ga.BRANCH}/docs/ADDONS.md",
    "Contributing...": f"{SITE}/blob/{ga.BRANCH}/CONTRIBUTING.md",
    "Project page on GitHub...": SITE,
}


class UpdateDialog(tk.Toplevel):
    def __init__(self, app, auto_check=True):
        super().__init__(app.root, bg=BG)
        self.app, self.info = app, None
        self.title("Check for updates")
        self.geometry("560x340")
        self.transient(app.root)
        self.head = tk.Label(self, bg=BG, font=("Segoe UI", 10, "bold"), anchor="w", justify="left")
        self.head.pack(fill="x", padx=10, pady=(10, 2))
        tk.Label(self, bg=BG, fg="#555", anchor="w", justify="left", wraplength=530,
                 text="Updating keeps your settings, installed addons, logs and node memory. Every file that is replaced is backed up first, "
                      "and files you may have edited (emergency_agent.py) are kept.").pack(fill="x", padx=10)
        self.log = tk.Text(self, height=9, wrap="word", state="disabled", bg="white", relief="sunken", bd=2)
        self.log.pack(fill="both", expand=True, padx=10, pady=8)
        b = tk.Frame(self, bg=BG)
        b.pack(fill="x", padx=10, pady=(0, 10))
        self.btn_check = ttk.Button(b, text="Check again", command=self.check)
        self.btn_go = ttk.Button(b, text="Download && install".replace("&&", "&"), command=self.install, state="disabled")
        self.btn_restart = ttk.Button(b, text="Restart now", command=self.restart, state="disabled")
        for w in (self.btn_check, self.btn_go, self.btn_restart): w.pack(side="left", padx=3)
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        self.head.config(text=f"Installed version: {gu.local_version()}")
        if auto_check: self.check()

    def say(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def check(self):
        self.say("Checking GitHub for the newest version...")
        self.btn_go.config(state="disabled")
        def done(r):
            if isinstance(r, Exception): return self.say(f"Could not check: {r}")
            self.info = r
            if r["newer"]:
                self.head.config(text=f"Update available: {r['remote']}  (installed: {r['local']})")
                self.btn_go.config(state="normal")
            else: self.head.config(text=f"You are up to date ({r['local']}).")
            self.say(f"Newest version on GitHub: {r['remote']}")
        self.app.bg(gu.check, done)

    def install(self):
        if not messagebox.askokcancel("Install update", "Download and install the newest version now?\nYour settings, addons, logs and node memory are kept.", parent=self):
            return
        self.btn_go.config(state="disabled")
        progress = lambda t: self.app.q.put(("call", lambda: self.say(t)))
        def work():
            res = gu.apply_update(progress=progress)
            return res, self.app.addons.update_installed()    # installed addons whose package got a newer version are refreshed too
        def done(r):
            if isinstance(r, Exception):
                self.say(f"Update failed - nothing was changed: {r}")
                self.btn_go.config(state="normal")
                return
            res, addons = r
            self.say(f"Updated {res['old']} -> {res['new']}: {len(res['updated'])} file(s) replaced, {len(res['added'])} added.")
            for rel in res["kept"]: self.say(f"Kept your edited {rel}; the new version is saved as {rel}.new (copy your changes into it, or rename it to adopt).")
            for name, old, new in addons: self.say(f"Addon '{name}' updated {old} -> {new} (its settings are unchanged).")
            if res["updated"]: self.say(f"Backup of replaced files: {res['backup']}")
            self.head.config(text=f"Updated to {res['new']} - restart to use it.")
            self.btn_restart.config(state="normal")
        self.app.bg(work, done)

    def restart(self):
        self.app.quit()
        subprocess.Popen([sys.executable] + sys.argv, cwd=ga.BASE_DIR)


class CatalogDialog(tk.Toplevel):
    """Tested community addons, straight from the project's addons-catalog.json."""
    def __init__(self, app):
        super().__init__(app.root, bg=BG)
        self.app, self.entries = app, {}
        self.title("Browse addons")
        self.geometry("760x380")
        self.transient(app.root)
        cols = ("title", "version", "author", "state", "description")
        self.t = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (150, 60, 90, 90, 330)):
            self.t.heading(c, text=c.capitalize())
            self.t.column(c, width=w, anchor="w")
        self.t.pack(fill="both", expand=True, padx=6, pady=6)
        self.note = tk.Label(self, bg=BG, fg="#555", anchor="w", justify="left", wraplength=730,
                             text="Only addons that were reviewed and tested by the maintainers are listed. An addon runs with full access to your PC - "
                                  "install ones you trust. Want yours listed? See Help > Submit an addon.")
        self.note.pack(fill="x", padx=6)
        b = tk.Frame(self, bg=BG)
        b.pack(fill="x", padx=6, pady=6)
        ttk.Button(b, text="Install / update", command=self.install).pack(side="left", padx=2)
        ttk.Button(b, text="Refresh", command=self.load).pack(side="left", padx=2)
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        self.load()

    def load(self):
        self.note.config(text=self.note.cget("text"))
        def done(r):
            if isinstance(r, Exception):
                messagebox.showerror("Browse addons", f"Could not load the catalog:\n{r}", parent=self)
                return
            self.t.delete(*self.t.get_children())
            have = ga.read_installed()
            self.entries = {e["name"]: e for e in r}
            for e in r:
                cur = have.get(e["name"])
                state = "not installed" if not cur else "installed" if ga.vkey(cur["version"]) >= ga.vkey(e["version"]) else f"update ({cur['version']})"
                self.t.insert("", "end", iid=e["name"], values=(e["title"], e["version"], e.get("author", ""), state, e.get("description", "")))
        self.app.bg(ga.fetch_catalog, done)

    def install(self):
        sel = self.t.selection()
        if not sel: return
        e = self.entries[sel[0]]
        if not messagebox.askokcancel("Install addon", f"Install '{e['title']}' {e['version']} by {e.get('author', '?')}?\n\nAddons run code with full access to your PC.", parent=self):
            return
        def done(r):
            if isinstance(r, Exception): return messagebox.showerror("Install addon", f"Install failed:\n{r}", parent=self)
            self.app.addons.set_enabled(r["name"], True)
            msg = f"'{r['title']}' {r['version']} installed and switched on."
            if r["missing"]: msg += f"\n\nIt needs extra Python packages - run:\npip install {' '.join(r['missing'])}\nthen restart the GUI."
            messagebox.showinfo("Install addon", msg, parent=self)
            self.load()
        self.app.bg(lambda: ga.install_from_catalog(e), done)


def open_link(label): webbrowser.open(LINKS[label])
