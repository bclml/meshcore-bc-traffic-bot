"""Everything that differs between Windows, macOS and Linux, in one place: fonts, opening files/folders, the mouse buttons, clipboard images
and the serial / permission hints.  The rest of mcIRC asks this module instead of checking the operating system itself."""
import os
import shutil
import subprocess
import sys

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WIN and not IS_MAC
OS_NAME = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"

# The Linux / macOS editions are versioned on their own (experimental until real users have tried them); the shared VERSION file still drives updates.
UNIX_EDITION_VERSION = "0.1.0"


def edition_label():
    """'' on Windows, otherwise e.g. 'Linux edition 0.1.0 (experimental)'."""
    return "" if IS_WIN else f"{OS_NAME} edition {UNIX_EDITION_VERSION} (experimental)"


def version_text(app_version):
    """What About / bug reports show: '1.2.0' on Windows, '1.2.0 - Linux edition 0.1.0 (experimental)' elsewhere."""
    return app_version if IS_WIN else f"{app_version} - {edition_label()}"


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---- fonts ------------------------------------------------------------------------------------------------------------------------------------
UI_FONT_NAME = "Tahoma" if IS_WIN else "Helvetica" if IS_MAC else "DejaVu Sans"
UI_FONT_SIZE = 8 if IS_WIN else 11 if IS_MAC else 9
MONO_FONT_NAME = "Courier New" if IS_WIN else "Menlo" if IS_MAC else "DejaVu Sans Mono"
EDITOR_FONT_NAME = "Consolas" if IS_WIN else MONO_FONT_NAME
DIALOG_FONT_NAME = "Segoe UI" if IS_WIN else UI_FONT_NAME            # headings in dialogs
UI_FONT = (UI_FONT_NAME, UI_FONT_SIZE)

# ---- mouse: macOS numbers the right and middle buttons the other way round ------------------------------------------------------------------------
RIGHT_CLICK = "<Button-2>" if IS_MAC else "<Button-3>"
MIDDLE_CLICK = "<Button-3>" if IS_MAC else "<Button-2>"
EXTRA_RIGHT_CLICK = ("<Control-Button-1>",) if IS_MAC else ()        # Ctrl-click is the right click on a one-button Mac mouse


def bind_right_click(widget, handler):
    for seq in (RIGHT_CLICK,) + EXTRA_RIGHT_CLICK: widget.bind(seq, handler)


# ---- files and folders -------------------------------------------------------------------------------------------------------------------------
def open_path(path):
    """Open a file or folder with the system's default program.  Returns True if it was started."""
    try:
        if IS_WIN: os.startfile(path)
        elif IS_MAC: subprocess.Popen(["open", path])
        else: subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, AttributeError):
        return False


def reveal(path):
    """Show a file selected in the file manager (just opens the folder where selecting isn't possible)."""
    try:
        if IS_WIN: subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif IS_MAC: subprocess.Popen(["open", "-R", path])
        else: return open_path(os.path.dirname(path) or ".")
        return True
    except OSError:
        return False


def meshcli_path():
    """The meshcli program that pip installed next to this Python (falls back to whatever is on the PATH)."""
    exe = os.path.join(os.path.dirname(sys.executable), "Scripts" if IS_WIN else "bin", "meshcli.exe" if IS_WIN else "meshcli")
    return exe if os.path.exists(exe) else (shutil.which("meshcli") or "meshcli")


# ---- clipboard image ----------------------------------------------------------------------------------------------------------------------------
def copy_image_to_clipboard(png_path):
    """Put a PNG on the system clipboard so it can be pasted into a web page.  Returns True if it worked."""
    try:
        if IS_WIN:
            return _win_clipboard_image(png_path)
        if IS_MAC:
            r = subprocess.run(["osascript", "-e", f'set the clipboard to (read (POSIX file "{png_path}") as «class PNGf»)'], capture_output=True, timeout=15)
            return r.returncode == 0
        for cmd in (["wl-copy", "--type", "image/png"], ["xclip", "-selection", "clipboard", "-t", "image/png", "-i"]):
            if shutil.which(cmd[0]):
                with open(png_path, "rb") as f:
                    if cmd[0] == "wl-copy": return subprocess.run(cmd, stdin=f, timeout=15).returncode == 0
                    return subprocess.run(cmd + [png_path], timeout=15).returncode == 0
    except Exception:
        pass
    return False


def _win_clipboard_image(png_path):
    import ctypes
    import io
    from PIL import Image
    img = Image.open(png_path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "BMP")
    dib = buf.getvalue()[14:]                      # a clipboard DIB is a BMP without the 14-byte file header
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    u32.OpenClipboard.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    k32.GlobalLock.argtypes = k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    h = k32.GlobalAlloc(0x0002, len(dib))          # GMEM_MOVEABLE
    p = k32.GlobalLock(h)
    ctypes.memmove(p, dib, len(dib))
    k32.GlobalUnlock(h)
    if not u32.OpenClipboard(None): return False
    try:
        u32.EmptyClipboard()
        return bool(u32.SetClipboardData(8, h))    # CF_DIB
    finally:
        u32.CloseClipboard()


# ---- hints for the problems each system has ------------------------------------------------------------------------------------------------------
def permission_hint():
    if not IS_WIN and not IS_MAC: return "on Linux your user must be allowed to use serial ports: run  sudo usermod -aG dialout $USER  (some distros: uucp), then log out and in again"
    if IS_MAC: return "on macOS allow the app in System Settings > Privacy & Security if it asks for access to USB or Bluetooth devices"
    return ""


def tk_missing_hint():
    if not IS_WIN and not IS_MAC: return "Tk is not installed for Python. Install it with:  sudo apt install python3-tk   (Fedora: sudo dnf install python3-tkinter, Arch: sudo pacman -S tk)"
    if IS_MAC: return "Tk is not available in this Python. Use the python.org installer or:  brew install python-tk"
    return "Tk is not available in this Python - reinstall Python and tick 'tcl/tk and IDLE'."
