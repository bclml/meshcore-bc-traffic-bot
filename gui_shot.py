"""Screenshots for bug reports.

Takes a picture of mcIRC's own windows (the main window and any dialog that is open, never the rest of the desktop), and by default blacks out
the chat text, the nick list, the topic line and the private-message buttons, because those show messages and people's names.  Needs Pillow
(`pip install pillow`); on Linux it needs an X11 session or a tool such as `grim` / `gnome-screenshot` that Pillow can use."""
import os
import time
import tkinter as tk

try:
    from PIL import Image, ImageDraw, ImageGrab
except ImportError:      # Pillow is optional
    Image = ImageDraw = ImageGrab = None

HIDE_NOTE = "hidden for privacy"
MAX_WIDTH = 1800


def available():
    return ImageGrab is not None


def _windows(app, exclude):
    """The main window plus every visible dialog of mcIRC (not tooltips, not the windows in `exclude`)."""
    out = [app.root]
    for w in app.root.winfo_children():
        if isinstance(w, tk.Toplevel) and w not in exclude and w.winfo_viewable() and not w.overrideredirect():
            out.append(w)
    return out


def _private_widgets(app):
    """Everything that can show message text or people's names."""
    widgets = [w.text for w in app.windows.values()] + [app.nicklist, app.topic]
    try: widgets += [b for b in app.switchbar.host.winfo_children()]
    except Exception: pass
    return widgets


def _box(widget, origin):
    return (widget.winfo_rootx() - origin[0], widget.winfo_rooty() - origin[1], widget.winfo_width(), widget.winfo_height())


def capture(app, redact=True, exclude=()):
    """-> a PIL image of mcIRC's windows stacked top to bottom.  Raises RuntimeError with a readable reason if the system won't allow it."""
    if not available(): raise RuntimeError("screenshots need Pillow:  pip install pillow")
    root = app.root
    root.update_idletasks()
    prev_top = root.attributes("-topmost")
    root.attributes("-topmost", True)            # make sure nothing else is lying over the window
    root.lift()
    root.update()
    time.sleep(0.25)                             # let the window manager repaint
    shots = []
    try:
        private = _private_widgets(app) if redact else []
        for win in _windows(app, exclude):
            win.update_idletasks()
            x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
            if w < 10 or h < 10: continue
            try: img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).convert("RGB")
            except Exception as e: raise RuntimeError(f"the system would not let mcIRC take a screenshot ({type(e).__name__}: {e})")
            if redact and win is root:
                d = ImageDraw.Draw(img)
                for wd in private:
                    try:
                        if not wd.winfo_ismapped(): continue
                        bx, by, bw, bh = _box(wd, (x, y))
                    except tk.TclError:
                        continue
                    d.rectangle([bx, by, bx + bw, by + bh], fill="#303030")
                    if bw > 90 and bh > 14: d.text((bx + 4, by + 2), HIDE_NOTE, fill="#a0a0a0")
            shots.append(img)
    finally:
        root.attributes("-topmost", prev_top)
    if not shots: raise RuntimeError("no window to capture")
    return stack(shots)


def stack(images, gap=8):
    width = max(i.width for i in images)
    height = sum(i.height for i in images) + gap * (len(images) - 1)
    canvas = Image.new("RGB", (width, height), "#808080")
    y = 0
    for i in images:
        canvas.paste(i, (0, y))
        y += i.height + gap
    if canvas.width > MAX_WIDTH:
        canvas = canvas.resize((MAX_WIDTH, int(canvas.height * MAX_WIDTH / canvas.width)))
    return canvas


def save(img, directory, stamp):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"report-{stamp}.png")
    img.save(path, "PNG", optimize=True)
    return path


def thumbnail(img, width=200):
    """A Tk PhotoImage for the preview, or None if Pillow's Tk support is missing."""
    try:
        from PIL import ImageTk
        t = img.copy()
        t.thumbnail((width, 90))
        return ImageTk.PhotoImage(t)
    except Exception:
        return None
