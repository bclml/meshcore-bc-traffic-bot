"""mIRC-style logging: one plain .txt file per window in logs/ (e.g. logs/#drivebc.txt, logs/@Alice.txt).
Each run adds 'Session Start' / 'Session Close' lines, and the newest lines are shown again when the window is
recreated after a restart."""
import os, re, time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
MAX_BYTES = 5 * 1024 * 1024   # a window log bigger than this is moved to <name>.old.txt at startup
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def file_name(window):
    return _BAD.sub("_", window).strip(" .") or "window"


def logged_windows(log_dir=LOG_DIR):
    """Window names that already have a log file (so they can be recreated at startup)."""
    if not os.path.isdir(log_dir): return []
    return sorted(f[:-4] for f in os.listdir(log_dir) if f.endswith(".txt") and not f.endswith(".old.txt") and f != "Status.txt")


class WindowLog:
    def __init__(self, window, log_dir=LOG_DIR):
        os.makedirs(log_dir, exist_ok=True)
        self.path = os.path.join(log_dir, file_name(window) + ".txt")
        if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
            os.replace(self.path, self.path[:-4] + ".old.txt")

    def tail(self, n):
        """The last n lines (reads only the end of the file)."""
        if n <= 0 or not os.path.exists(self.path): return []
        try:
            with open(self.path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 128 * 1024))
                data = f.read().decode("utf-8", "replace")
        except OSError: return []
        return data.splitlines()[-n:]

    def append(self, line):
        try:
            with open(self.path, "a", encoding="utf-8") as f: f.write(line + "\n")
        except OSError: pass   # a full disk / locked file must never break chat

    def stamp(self, what):
        self.append(f"{what}: {time.strftime('%a %b %d %H:%M:%S %Y')}")
