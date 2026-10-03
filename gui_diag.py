"""Troubleshooting log.  One file per run in diagnostics/, the last 5 runs are kept.

What goes in: versions, PC / Python info, the USB serial ports and Bluetooth/TCP connection steps, which meshcli commands ran and whether
they worked, the node's firmware/radio summary, errors and Python tracebacks.

What never goes in: message text (channel or private), the arguments of anything that sends or logs in (msg, cmd, login, chan, password...),
passwords/API keys, your node's position, whole public keys (shortened), Bluetooth addresses and IPs (partly hidden).  Everything written
passes through `scrub()` as a last line of defence.  The Report a bug window shows the exact text before anything leaves the PC."""
import atexit
import datetime
import glob
import logging
import os
import platform
import re
import sys
import tempfile
import threading
import traceback

import gui_platform

KEEP = 5
MAX_BYTES = 1_500_000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIAG_DIR = os.path.join(BASE_DIR, "diagnostics")

_lock = threading.Lock()
_state = {"path": None, "file": None, "bytes": 0, "capped": False, "dir": DIAG_DIR, "device": "", "counts": {}, "header": ""}

# ---------------------------------------------------------------------------------------------------------------- scrubbing
_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")
_HEXLONG = re.compile(r"\b(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{12,63}\b")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})\b")
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}\b")
_USERPATH = re.compile(r"(?i)([A-Z]:\\Users\\|/Users/|/home/)[^\\/\s\"']+")
_SECRET = re.compile(r"(?i)\b(pass(?:word|wd)?|pwd|pw|api[_-]?key|apikey|token|secret)\b(\s*[=:]\s*|=)[^\s&,;)\]}\"']+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LATLON = re.compile(r"(?i)(\"?(?:adv_)?(?:lat|lon|latitude|longitude)\"?\s*[:=]\s*)-?\d+(?:\.\d+)?")


def scrub(text):
    text = str(text)
    text = _HEX64.sub(lambda m: m.group(0)[:8] + "..(key)", text)
    text = _HEXLONG.sub(lambda m: m.group(0)[:6] + "..", text)
    text = _MAC.sub(lambda m: "xx:xx:xx:xx:xx:" + m.group(1), text)
    text = _IPV4.sub(lambda m: f"{m.group(1)}.{m.group(2)}.x.x", text)
    text = _USERPATH.sub(lambda m: m.group(1) + "<user>", text)
    text = _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _EMAIL.sub("<email>", text)
    return _LATLON.sub(lambda m: m.group(1) + "<hidden>", text)


# meshcli commands whose arguments are message text / credentials / channel secrets: how many args after the command word to hide
HIDE_ARGS = {"msg": 2, "m": 2, "cmd": 2, "c": 2, "[": 2, "login": 2, "l": 2, "chan": 2, "ch": 2, "password": 1, "set_channel": 3,
             "add_channel": 2, "reply": 1, "r": 1, "public": 1, "p": 1, "sendto": 2, "to": 2}
_MASK_FLAGS = {"-a": "mac", "-t": "host", "-d": "name"}


def describe_args(args):
    """meshcli arguments as a safe one-liner."""
    out, i, args = [], 0, [str(a) for a in args]
    while i < len(args):
        a = args[i]
        if a in _MASK_FLAGS and i + 1 < len(args):
            out += [a, f"<{_MASK_FLAGS[a]}>"]
            i += 2
        elif a in HIDE_ARGS:
            n = HIDE_ARGS[a]
            out += [a] + ["<hidden>"] * min(n, len(args) - i - 1)
            i += 1 + n
        else:
            out.append(a)
            i += 1
    return scrub(" ".join(out))


# ---------------------------------------------------------------------------------------------------------------- the file
def _write(line):
    with _lock:
        f = _state["file"]
        if f is None: return
        if _state["bytes"] > MAX_BYTES:
            if not _state["capped"]:
                _state["capped"] = True
                f.write("[log capped at 1.5 MB - later events not recorded]\n")
                f.flush()
            return
        data = line.rstrip("\n") + "\n"
        try:
            f.write(data)
            f.flush()
            _state["bytes"] += len(data)
        except (OSError, ValueError):
            pass


def event(category, text):
    """One timestamped line.  Never raises."""
    try:
        _write(f"{datetime.datetime.now():%H:%M:%S} [{category}] {scrub(text)}")
    except Exception:
        pass


def count(name, n=1):
    _state["counts"][name] = _state["counts"].get(name, 0) + n


def set_device(text): _state["device"] = scrub(text)
def device(): return _state["device"]


def current_path(): return _state["path"]
def directory(): return _state["dir"]


def session_files():
    """Newest last."""
    return sorted(glob.glob(os.path.join(_state["dir"], "mcirc-*.log")))


def _environment():
    lines = [f"Python {platform.python_version()} ({platform.architecture()[0]}), {platform.platform()}"]
    try:
        import importlib.metadata as md
        for pkg in ("meshcore-cli", "meshcore", "pyserial", "bleak", "tkintermapview", "requests"):
            try: lines.append(f"{pkg} {md.version(pkg)}")
            except Exception: pass
    except Exception:
        pass
    try:
        import tkinter
        lines.append(f"Tk {tkinter.TkVersion}")
    except Exception:
        pass
    return "; ".join(lines)


def ports_snapshot():
    """Every serial port Windows knows about: names, USB ids and descriptions (this is what identifies an unknown board)."""
    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
    except Exception as e:
        return event("ports", f"could not list serial ports: {e}")
    if not ports: return event("ports", "no serial ports found")
    for p in ports:
        vid = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None and p.pid is not None else "no-usb-id"
        event("ports", f"{p.device}  usb={vid}  desc='{p.description}'  maker='{p.manufacturer}'  product='{getattr(p, 'product', '')}'")


NODE_KEYS = ("model", "ver", "version", "fw_build", "fw ver", "build", "firmware", "max_contacts", "max_channels", "radio_freq", "radio_bw", "radio_sf",
             "radio_cr", "tx_power", "max_tx_power", "path_hash_mode", "manual_add_contacts", "multi_acks", "telemetry_mode_base")


def node_summary(node):
    """node = gui_nodecfg.read_node() result.  Logs firmware / hardware / radio settings (not name, key or position)."""
    bits = []
    for part in ("ver", "info"):
        d = node.get(part) or {}
        keys = ", ".join(sorted(d))
        event("node", f"{part} fields: {keys}")
        for k in NODE_KEYS:
            if k in d: bits.append(f"{k}={d[k]}")
    event("node", "; ".join(bits))
    set_device(" ".join(str(node.get("ver", {}).get(k, "")) for k in ("model", "fw_build", "ver") if node.get("ver", {}).get(k)) or "")


def trace(args, attempt, outcome, secs, detail=""):
    """Called by meshcore_io.execute_mesh_command after every meshcli attempt."""
    word = next((a for a in args if not str(a).startswith("-")), "")
    event("meshcli", f"{describe_args(args)}  try {attempt}  {outcome}  {secs:.1f}s" + (f"  {detail}" if detail else ""))
    count("meshcli_ok" if outcome == "ok" else "meshcli_failed")


class _Handler(logging.Handler):
    def emit(self, record):
        try:
            msg = record.getMessage()
            if "[DIAGNOSTIC]" in msg: return        # raw .sync_msgs output = message text
            event(record.levelname.lower(), msg)
        except Exception:
            pass


def _excepthook(kind, value, tb):
    event("crash", "".join(traceback.format_exception(kind, value, tb)))
    sys.__excepthook__(kind, value, tb)


def tk_exception(kind, value, tb):
    """Assigned to root.report_callback_exception."""
    event("error", "unhandled error in a GUI callback:\n" + "".join(traceback.format_exception(kind, value, tb)))
    traceback.print_exception(kind, value, tb)


def start(version="?", demo=False):
    """Open this run's file, prune old ones, hook logging / exceptions / meshcli tracing.  Safe to call once."""
    try:
        d = tempfile.mkdtemp(prefix="mcirc_diag_") if demo else DIAG_DIR      # demo mode never touches the real folder
        _state["dir"] = d
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"mcirc-{datetime.datetime.now():%Y%m%d-%H%M%S}.log")
        for old in session_files()[:-(KEEP - 1)] if KEEP > 1 else session_files():
            try: os.remove(old)
            except OSError: pass
        saved = sorted(glob.glob(os.path.join(d, "report-*")))          # reports / screenshots from "Report a bug": keep the newest 10 files
        for old in saved[:-10]:
            try: os.remove(old)
            except OSError: pass
        _state.update(path=path, file=open(path, "a", encoding="utf-8"), bytes=0, capped=False, counts={})
        header = f"mcIRC {gui_platform.version_text(version)}{' (demo)' if demo else ''}  |  {_environment()}"
        _state["header"] = header
        _write(f"=== mcIRC diagnostic log - no message text is recorded ===\n=== {scrub(header)} ===\n=== started {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===")
        logging.getLogger().addHandler(_Handler(logging.INFO))
        if logging.getLogger().level in (logging.NOTSET, logging.WARNING): logging.getLogger().setLevel(logging.INFO)
        sys.excepthook = _excepthook
        threading.excepthook = lambda a: event("crash", f"thread {getattr(a.thread, 'name', '?')}:\n" + "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
        import meshcore_io
        meshcore_io.TRACE = trace
        atexit.register(stop)
    except Exception:
        pass


def stop():
    c = _state["counts"]
    event("session", "closed. " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    with _lock:
        f, _state["file"] = _state["file"], None
    try:
        if f: f.close()
    except OSError:
        pass


def read_sessions(n=1):
    """Text of the newest n session files (oldest first), for the bug report."""
    parts = []
    for p in session_files()[-max(1, n):]:
        try:
            with open(p, encoding="utf-8", errors="replace") as f: parts.append(f"----- {os.path.basename(p)} -----\n" + f.read())
        except OSError:
            pass
    return "\n".join(parts)


def header(): return scrub(_state["header"])
