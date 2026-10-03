"""Help > Report a bug...  Describe the problem, see exactly which troubleshooting log will be attached, then open a pre-filled GitHub issue.

GitHub only lets signed-in people create issues, so mcIRC can't post one silently: it opens the issue form in your browser with everything
filled in (the log is trimmed to fit a web address) and puts the COMPLETE report on your clipboard to paste over the trimmed part."""
import datetime
import os
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox, filedialog
from urllib.parse import quote

import gui_addons as ga
import gui_diag
import gui_update
from gui_common import BG

ISSUE_URL = f"https://github.com/{ga.REPO}/issues/new"
MAX_URL = 7000          # browsers and GitHub reject much longer addresses
MODES = {"usb": "USB", "bluetooth": "Bluetooth", "tcp": "WiFi / TCP"}
PRIVACY = ("The report contains: mcIRC / Python / Windows versions, your USB serial ports, connection steps, which meshcli commands worked or failed, "
           "your node's firmware and radio settings, and errors.\nIt never contains message text (public or private), passwords, your position, "
           "full public keys or full Bluetooth / IP addresses. Check the preview, and edit anything you don't want to share.")


def installed_addons(app):
    try:
        return ", ".join(f"{n} {getattr(inst, 'version', '')}".strip() for n, (inst, _) in app.addons.loaded.items()) or "none"
    except Exception:
        return "unknown"


def build_url(fields, log_text, limit=MAX_URL):
    """Issue-form address with the log tail trimmed until the whole thing fits."""
    def url(log):
        q = dict(fields, log=log)
        return f"{ISSUE_URL}?template=bug_report.yml&" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in q.items() if v)
    log = log_text
    note = "[...trimmed - the complete log was copied to your clipboard: select this box and paste (Ctrl+V) to replace it]\n"
    if len(url(log)) <= limit: return url(log)
    lo, hi = 0, len(log)
    while lo < hi:                                  # keep the newest lines, which are the ones that matter
        mid = (lo + hi) // 2
        if len(url(note + log[mid:])) <= limit: hi = mid
        else: lo = mid + 1
    return url(note + log[lo:])


class BugReportDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root, bg=BG)
        self.app = app
        self.title("Report a bug")
        self.geometry("760x680")
        self.transient(app.root)
        tk.Label(self, text="Report a bug", bg=BG, font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(self, text=PRIVACY, bg=BG, fg="#444", justify="left", anchor="w", wraplength=730).pack(fill="x", padx=10, pady=(2, 6))

        tk.Label(self, text="What went wrong? What did you do, what did you expect, and what happened instead? Which board / device do you use?",
                 bg=BG, anchor="w", wraplength=730, justify="left").pack(fill="x", padx=10)
        self.what = tk.Text(self, height=7, wrap="word", relief="sunken", bd=2, font=("Segoe UI", 10))
        self.what.pack(fill="x", padx=10, pady=(2, 6))
        self.what.focus_set()

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=10)
        self.include = tk.BooleanVar(value=True)
        tk.Checkbutton(row, text="Attach the troubleshooting log", variable=self.include, bg=BG, command=self.refresh).pack(side="left")
        tk.Label(row, text="   Runs to include (newest first):", bg=BG).pack(side="left")
        self.runs = tk.IntVar(value=2)
        sp = tk.Spinbox(row, from_=1, to=gui_diag.KEEP, width=3, textvariable=self.runs, command=self.refresh)
        sp.pack(side="left")
        sp.bind("<KeyRelease>", lambda e: self.refresh())
        self.size = tk.Label(row, bg=BG, fg="#666")
        self.size.pack(side="right")

        tk.Label(self, text="Preview of exactly what will be sent (you can edit it):", bg=BG, anchor="w").pack(fill="x", padx=10, pady=(8, 0))
        box = tk.Frame(self)
        box.pack(fill="both", expand=True, padx=10, pady=2)
        sb = ttk.Scrollbar(box)
        sb.pack(side="right", fill="y")
        self.preview = tk.Text(box, wrap="none", font=("Consolas", 9), yscrollcommand=sb.set, relief="sunken", bd=2)
        self.preview.pack(side="left", fill="both", expand=True)
        sb.config(command=self.preview.yview)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=10, pady=8)
        ttk.Button(btns, text="Send to GitHub...", command=self.send).pack(side="left")
        ttk.Button(btns, text="Copy report", command=self.copy).pack(side="left", padx=4)
        ttk.Button(btns, text="Save as file...", command=self.save).pack(side="left")
        ttk.Button(btns, text="Open logs folder", command=app.open_diag_folder).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")
        self.refresh()

    # ---- content ----
    def log_text(self):
        return gui_diag.read_sessions(self._runs()) if self.include.get() else "(log not attached)"

    def _runs(self):
        try: return max(1, min(gui_diag.KEEP, int(self.runs.get())))
        except (tk.TclError, ValueError): return 1

    def refresh(self):
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", self.log_text())
        self.preview.see("end")
        self.size.config(text=f"{len(self.preview.get('1.0', 'end-1c')) // 1024} KB")

    def fields(self):
        s = self.app.settings
        mode = "Demo mode (no radio)" if self.app.demo else MODES.get(s.get("mode"), "Not applicable")
        return {"title": "[Bug] " + (self.what.get("1.0", "end").strip().splitlines() or [""])[0][:70],
                "version": gui_update.local_version(), "what": self.what.get("1.0", "end").strip(),
                "device": gui_diag.device(), "connection": mode, "addons": installed_addons(self.app)}

    def full_report(self):
        f = self.fields()
        return (f"mcIRC {f['version']} bug report - {datetime.datetime.now():%Y-%m-%d %H:%M}\n\nWhat happened:\n{f['what']}\n\n"
                f"Device: {f['device'] or 'unknown'}\nConnection: {f['connection']}\nAddons: {f['addons']}\n\n"
                f"{gui_diag.header()}\n\n--- troubleshooting log ---\n{self.preview.get('1.0', 'end-1c')}\n")

    # ---- actions ----
    def _need_text(self):
        if self.what.get("1.0", "end").strip(): return True
        messagebox.showinfo("Report a bug", "Please describe what went wrong in the box at the top first.", parent=self)
        self.what.focus_set()
        return False

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.full_report())
        messagebox.showinfo("Report a bug", "The report was copied to the clipboard.", parent=self)

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", initialfile=f"mcirc-report-{datetime.datetime.now():%Y%m%d-%H%M}.txt",
                                            filetypes=[("Text", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f: f.write(self.full_report())

    def send(self):
        if not self._need_text(): return
        report = self.full_report()
        self.clipboard_clear()
        self.clipboard_append(report)
        try:
            os.makedirs(gui_diag.directory(), exist_ok=True)
            with open(os.path.join(gui_diag.directory(), f"report-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt"), "w", encoding="utf-8") as f: f.write(report)
        except OSError:
            pass
        url = build_url(self.fields(), self.preview.get("1.0", "end-1c") if self.include.get() else "")
        gui_diag.event("report", "bug report opened in the browser")
        webbrowser.open(url)
        messagebox.showinfo("Report a bug",
                            "GitHub is opening in your browser with the report filled in.\n\n"
                            "1. Sign in to GitHub if asked (a free account is needed to post an issue).\n"
                            "2. The log box holds only the newest part. To send the whole log, click in that box, press Ctrl+A then Ctrl+V - "
                            "the complete report is on your clipboard.\n"
                            "3. Press Submit new issue.\n\n"
                            "No GitHub account? Use Save as file... and send the file to whoever helps you.", parent=self)
