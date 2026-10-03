"""Help > Report a bug...  Describe the problem, see exactly which troubleshooting log will be attached, then open a pre-filled GitHub issue.

GitHub only lets signed-in people create issues, so mcIRC can't post one silently: it opens the issue form in your browser with everything
filled in (the log is trimmed to fit a web address) and puts the COMPLETE report on your clipboard to paste over the trimmed part."""
import gui_platform
import datetime
import os
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox, filedialog
from urllib.parse import quote

import gui_addons as ga
import gui_diag
import gui_shot
import gui_update
from gui_common import BG

ISSUE_URL = f"https://github.com/{ga.REPO}/issues/new"
MAX_URL = 7000          # browsers and GitHub reject much longer addresses
MODES = {"usb": "USB", "bluetooth": "Bluetooth", "tcp": "WiFi / TCP"}
PRIVACY = ("The report contains: mcIRC / Python / system versions, your USB serial ports, connection steps, which meshcli commands worked or failed, "
           "your node's firmware and radio settings, and errors.\nIt never contains message text (public or private), passwords, your position, "
           "full public keys or full Bluetooth / IP addresses. Check the preview, and edit anything you don't want to share.\n"
           "A screenshot is optional, off unless you tick it, and by default hides the chat text, names and the button bar.")


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
        self.geometry("760x640")
        self.minsize(560, 480)
        self.transient(app.root)
        btns = tk.Frame(self, bg=BG)
        btns.pack(side="bottom", fill="x", padx=10, pady=8)
        ttk.Button(btns, text="Send to GitHub...", command=self.send).pack(side="left")
        ttk.Button(btns, text="Copy report", command=self.copy).pack(side="left", padx=4)
        ttk.Button(btns, text="Save as file...", command=self.save).pack(side="left")
        ttk.Button(btns, text="Open logs folder", command=app.open_diag_folder).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")
        tk.Label(self, text="Report a bug", bg=BG, font=(gui_platform.DIALOG_FONT_NAME, 11, "bold"), anchor="w").pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(self, text=PRIVACY, bg=BG, fg="#444", justify="left", anchor="w", wraplength=730).pack(fill="x", padx=10, pady=(2, 6))

        tk.Label(self, text="What went wrong? What did you do, what did you expect, and what happened instead? Which board / device do you use?",
                 bg=BG, anchor="w", wraplength=730, justify="left").pack(fill="x", padx=10)
        self.what = tk.Text(self, height=6, wrap="word", relief="sunken", bd=2, font=(gui_platform.DIALOG_FONT_NAME, 10))
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

        shot = tk.Frame(self, bg=BG)
        shot.pack(fill="x", padx=10, pady=(6, 0))
        self.want_shot, self.redact, self.shot_img, self.shot_path, self._thumb = tk.BooleanVar(value=False), tk.BooleanVar(value=True), None, None, None
        self.shot_check = tk.Checkbutton(shot, text="Attach a screenshot of mcIRC", variable=self.want_shot, bg=BG, command=self.toggle_shot)
        self.shot_check.pack(side="left")
        tk.Checkbutton(shot, text="Black out chat text, names and the button bar", variable=self.redact, bg=BG).pack(side="left", padx=6)
        self.retake = ttk.Button(shot, text="Retake", command=self.take_shot, state="disabled")
        self.retake.pack(side="left")
        self.thumb = tk.Label(shot, bg=BG, fg="#666", text="")
        self.thumb.pack(side="left", padx=8)
        if not gui_shot.available():
            self.shot_check.config(state="disabled", text="Attach a screenshot (needs:  pip install pillow)")

        tk.Label(self, text="Preview of exactly what will be sent (you can edit it):", bg=BG, anchor="w").pack(fill="x", padx=10, pady=(8, 0))
        box = tk.Frame(self)
        box.pack(fill="both", expand=True, padx=10, pady=2)
        sb = ttk.Scrollbar(box)
        sb.pack(side="right", fill="y")
        self.preview = tk.Text(box, wrap="none", height=8, font=(gui_platform.EDITOR_FONT_NAME, 9), yscrollcommand=sb.set, relief="sunken", bd=2)
        self.preview.pack(side="left", fill="both", expand=True)
        sb.config(command=self.preview.yview)

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
                "version": gui_platform.version_text(gui_update.local_version()), "what": self.what.get("1.0", "end").strip(),
                "device": f"{gui_diag.device() or 'unknown board'} - connected by {mode}", "connection": mode,   # GitHub doesn't always pre-select dropdowns, so the device line says it too
                 "addons": installed_addons(self.app)}

    def full_report(self):
        f = self.fields()
        return (f"mcIRC {f['version']} bug report - {datetime.datetime.now():%Y-%m-%d %H:%M}\n\nWhat happened:\n{f['what']}\n\n"
                f"Device: {f['device'] or 'unknown'}\nConnection: {f['connection']}\nAddons: {f['addons']}\n"
                f"Screenshot: {'yes - saved as a PNG next to the troubleshooting logs; paste it into the issue' if self.shot_img is not None else 'none'}\n\n"
                f"{gui_diag.header()}\n\n--- troubleshooting log ---\n{self.preview.get('1.0', 'end-1c')}\n")

    # ---- screenshot ----
    def toggle_shot(self):
        if self.want_shot.get(): self.take_shot()
        else:
            self.shot_img = self.shot_path = self._thumb = None
            self.thumb.config(image="", text="")
            self.retake.config(state="disabled")

    def take_shot(self):
        """Hide this window (it would cover the app), photograph mcIRC, bring it back."""
        self.withdraw()
        try:
            self.shot_img = gui_shot.capture(self.app, redact=self.redact.get(), exclude=(self,))
        except Exception as e:
            self.shot_img = None
            self.want_shot.set(False)
            self.deiconify()
            messagebox.showwarning("Screenshot", f"No screenshot could be taken: {e}", parent=self)
            return
        self.deiconify()
        self.lift()
        self._thumb = gui_shot.thumbnail(self.shot_img)
        if self._thumb: self.thumb.config(image=self._thumb, text="")
        else: self.thumb.config(image="", text=f"screenshot taken ({self.shot_img.width}x{self.shot_img.height})")
        self.retake.config(state="normal")

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
        self.shot_path = None
        if self.shot_img is not None:
            try: self.shot_path = gui_shot.save(self.shot_img, gui_diag.directory(), f"{datetime.datetime.now():%Y%m%d-%H%M%S}")
            except OSError: self.shot_path = None
        url = build_url(self.fields(), self.preview.get("1.0", "end-1c") if self.include.get() else "")
        gui_diag.event("report", "bug report opened in the browser" + (" (with a screenshot)" if self.shot_path else ""))
        webbrowser.open(url)
        SendHelp(self, report, self.shot_path)


class SendHelp(tk.Toplevel):
    """Shown next to the browser after Send: the clipboard holds one thing at a time, so the log and the screenshot are pasted one after the other."""
    def __init__(self, parent, report, shot_path):
        super().__init__(parent.master, bg=BG)
        self.report, self.shot_path = report, shot_path
        self.title("Finish your report on GitHub")
        self.transient(parent.master)
        self.geometry("+80+80")
        steps = ["GitHub is opening in your browser with the report filled in. Sign in if asked (a free account is needed to post an issue).",
                 "The log box only holds the newest part. To send the whole log: press Copy full log below, click in that box, press Ctrl+A then Ctrl+V."]
        if shot_path:
            steps.append("To add the screenshot: press Copy screenshot below, click in the 'Screenshot' box on the GitHub page and press Ctrl+V "
                         "(or drag the saved picture into it with Show screenshot file).")
        steps.append("Press Submit new issue.  No GitHub account? Use Save as file... in the report window and send the file to whoever is helping you.")
        for i, text in enumerate(steps, 1):
            tk.Label(self, text=f"{i}.  {text}", bg=BG, justify="left", anchor="w", wraplength=520).pack(fill="x", padx=12, pady=(8 if i == 1 else 2, 0))
        self.status = tk.Label(self, text="", bg=BG, fg="#555", anchor="w", wraplength=520, justify="left")
        self.status.pack(fill="x", padx=12, pady=(6, 0))
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=12, pady=10)
        ttk.Button(row, text="Copy full log", command=self.copy_log).pack(side="left")
        if shot_path:
            ttk.Button(row, text="Copy screenshot", command=self.copy_shot).pack(side="left", padx=4)
            ttk.Button(row, text="Show screenshot file", command=lambda: gui_platform.reveal(shot_path)).pack(side="left")
        ttk.Button(row, text="Done", command=self.destroy).pack(side="right")
        self.copy_log(quiet=True)

    def copy_log(self, quiet=False):
        self.clipboard_clear()
        self.clipboard_append(self.report)
        if not quiet: self.status.config(text="The full report is on the clipboard.")

    def copy_shot(self):
        ok = gui_platform.copy_image_to_clipboard(self.shot_path)
        self.status.config(text="The screenshot is on the clipboard - click in the Screenshot box on GitHub and paste." if ok else
                           f"This system would not copy the picture. Drag the file into the box instead:  {self.shot_path}")
