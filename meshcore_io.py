"""Shared MeshCore radio plumbing: talking to the node through meshcli (connect, send, receive, node settings).

Used by the GUI core and by the optional broadcast-alerts engine (emergency_agent.py).  Mutable shared state
(CONNECTION_ARGS, CHANNEL_INDEX_BY_NAME, GUI_CALLBACK) lives here; other modules read it as meshcore_io.NAME."""
import json, logging, os, re, subprocess, sys, threading, time

import serial.tools.list_ports

import gui_platform

# When the GUI runs under pythonw (no console of its own), every meshcli.exe call would otherwise flash a console window.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UTF8 = dict(encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})   # meshcli must be able to print emoji/accents in replies (Windows console default can't)
DEFAULT_CLI_PATH = gui_platform.meshcli_path()

BLE_SCAN_TIMEOUT = 10  # seconds to wait while scanning for the node over Bluetooth

TRACE = None  # set by gui_diag: TRACE(args, attempt, outcome, seconds, detail) after every meshcli attempt (no output text)

CONNECTION_ARGS = None  # meshcli connection prefix, e.g. ["-s", "COM4"] or ["-a", "AA:BB:CC:DD:EE:FF"]

# --- OPTIONAL GUI HOOKS (see mcIRC.py; harmless when running headless) ---
class _MeshLock:
    """Only one program may hold the COM port at a time.  An RLock, plus: when somebody has to wait for it, `on_contend` is called so an
    idle-time listener (gui_adverts.py) can let go of the radio at once instead of making them wait for its whole slice."""
    def __init__(self):
        self._lock = threading.RLock()
        self.on_contend = None

    def acquire(self, blocking=True, timeout=-1):
        if self._lock.acquire(False): return True
        if not blocking: return False
        if self.on_contend is not None:
            try: self.on_contend()
            except Exception: pass
        return self._lock.acquire(True, timeout)

    def release(self): self._lock.release()
    def __enter__(self): self.acquire(); return self
    def __exit__(self, *exc): self.release()


MESH_LOCK = _MeshLock()  # only one meshcli process may hold the COM port at a time; the GUI sends chat from its own thread

BOT_NICK = "MyNode"

GUI_CALLBACK = None  # set by the GUI: callback(kind, channel_idx, text, nick, **extra), kind in {"in", "out"}

# NOTE: no longer used as a fallback for category alerts (DriveBC/BC Ferries/BC Transit/TransLink/
# weather) — see _resolve_channel_idx, which withholds those instead of ever sending them to
# Public. Only still used as the last-resort channel for a test-message reply, in the vanishingly
# unlikely case an incoming message payload is missing its own channel_idx field.
DEFAULT_CHANNEL_IDX = 0

CHANNEL_INDEX_BY_NAME = {}  # populated by resolve_channel_indices() at startup: {"drivebc": 1, "bctransit": 3, ...}

# MeshCore channel/group text messages are hard-capped by the firmware itself — per MeshCore's own
# docs/issue tracker this lands somewhere around 120-167 characters depending on node name length,
# channel scoping, and LoRa settings, with anything over the limit silently truncated (sometimes
# mid-word, mid-detail — e.g. a "Route 1 cancelled" alert arrived as a cut-off partial message).
# Rather than let the firmware chop it at an arbitrary point, the message is built to fit a
# conservative budget ourselves, with an explicit "…" so a shortened message is at least obviously
# shortened rather than looking like it just stops.
# BUG FIX: this was 140, but the actual node's own message-compose UI shows a hard "0/127" character
# counter for this channel — 140 was above the real device limit, so even our own "safely truncated"
# 140-char messages were getting a SECOND, uncontrolled truncation by the firmware on top of ours,
# cutting off exactly the trailing details (route/time) that matter most. Lowered below the observed
# real limit with a small safety margin.
MESH_MSG_MAX_CHARS = 120

_TEST_SENDER_RE = re.compile(r'^\s*([^:]+):\s*(.*)$')

# Every call to execute_mesh_command spins up a brand-new meshcli subprocess that does its own
# full serial/BLE connect-handshake-disconnect cycle for that one command (there's no persistent
# connection kept open between calls). Confirmed live in emergency_agent.log: meshcli prints
# "No response from meshcore node, disconnecting" / "Are you sure your node is a serial companion?"
# on a large fraction of calls (thousands of occurrences per log file) when the handshake loses a
# race with the node being briefly busy — but it still EXITS 0 in that case, so the old code (which
# only checked returncode) silently treated a failed handshake as success. For messages_loop this
# meant "no messages this poll" could actually mean "never asked the node at all", and for
# broadcast_via_cli it meant an alert could silently never go out. Retried like the existing
# _get_with_retry pattern for BC Ferries, since a fresh subprocess attempt a couple seconds later
# routinely succeeds.
# 'could not open port ... Access is denied' = another program (or another meshcli) holds the port right now; it also exits 0, and it
# usually clears within seconds, so it is retried like the handshake failures.
_TRANSIENT_MESHCLI_ERROR_RE = re.compile(r'No response from meshcore node|sure your node is a serial companion|could not open port|Access is denied', re.IGNORECASE)

def redact(text):
    """Hides API keys that requests puts in exception text (the TransLink key travels in the URL query string)."""
    return re.sub(r'(?i)(apikey=)[^&\s)]+', r'\1***', text)

class RedactFilter(logging.Filter):
    def filter(self, record):
        record.msg, record.args = redact(record.getMessage()), ()
        return True

def _emit(kind, channel_idx, text, nick=None, **extra):
    if GUI_CALLBACK is None: return
    try: GUI_CALLBACK(kind, channel_idx, text, nick or BOT_NICK, **extra)
    except Exception as e: logging.debug(f"GUI callback failed: {e}")

def split_sender(raw_text):
    m = _TEST_SENDER_RE.match(raw_text or "")
    return (m.group(1).strip(), m.group(2).strip()) if m else ("someone", (raw_text or "").strip())

# USB vendor IDs of the chips found on boards that run MeshCore: CP210x (Heltec V3, ...), CH340/CH9102 (LilyGo, ...),
# Espressif native USB (ESP32-S3 boards), Adafruit/RAK/Seeed/Nordic (nRF52 boards), FTDI, Raspberry Pi (RP2040).
USB_VENDORS = {0x10C4: "Silicon Labs", 0x1A86: "WCH", 0x303A: "Espressif", 0x239A: "Adafruit/RAK", 0x2886: "Seeed", 0x1915: "Nordic", 0x0403: "FTDI", 0x2E8A: "Raspberry Pi"}


def usb_candidates():
    """USB serial ports that could be a MeshCore node, best guess first.  Built-in COM ports and Bluetooth serial ports are skipped."""
    found = []
    for p in serial.tools.list_ports.comports():
        text = f"{p.description or ''} {p.manufacturer or ''}"
        if "bluetooth" in text.lower() or "bluetooth" in p.device.lower(): continue
        if gui_platform.IS_MAC and p.device.startswith("/dev/tty."): continue      # every Mac port appears twice (tty. and cu.); cu. is the one to use
        if p.vid in USB_VENDORS: score = 3
        elif p.vid and re.search(r"usb|uart|serial|acm", text, re.IGNORECASE): score = 1
        else: continue
        found.append({"device": p.device, "description": p.description or "", "vid": p.vid, "pid": p.pid, "score": score})
    return sorted(found, key=lambda c: (-c["score"], int(re.sub(r"\D", "", c["device"]) or 0)))


def json_docs(text):
    """meshcli prints one JSON document per command; return all of them in order (INFO log lines are skipped)."""
    docs, dec, i = [], json.JSONDecoder(), 0
    while True:
        j = text.find("{", i)
        if j == -1: return docs
        try:
            obj, i = dec.raw_decode(text, j)
            docs.append(obj)
        except ValueError: i = j + 1


def explain_failure(text):
    """Turns meshcli's failure text into something a person can act on."""
    t = (text or "").lower()
    if "serial companion" in t or "no response from meshcore node" in t:
        return ("the device did not answer as a MeshCore Companion - it may be running Repeater / Room-server firmware (flash Companion firmware to chat), "
                "be a different kind of device, or another program is using the port")
    if not gui_platform.IS_WIN and ("permission denied" in t or "errno 13" in t):
        return "the system does not let you use that serial port - " + gui_platform.permission_hint()
    if "access is denied" in t or "could not open port" in t or "permissionerror" in t or "being used by another" in t:
        return "the port is busy - close other programs that use it (serial monitor, the console agent, another meshcli)"
    if "not found" in t or "no such file" in t or "cannot find the file" in t:
        return "meshcli was not found - run: pip install meshcore-cli"
    return (text or "unknown error").strip().splitlines()[-1][:200] if (text or "").strip() else "unknown error"


def probe_device(conn_args, timeout=25, retries=1):
    """Asks a device who it is.  -> {'ok': True, 'name', 'model', 'fw', 'max_contacts'} or {'ok': False, 'why': readable reason}."""
    try:
        res = execute_mesh_command(conn_args + [".infos", ".ver"], timeout=timeout, retries=retries)
    except Exception as e:
        return {"ok": False, "why": explain_failure(str(e))}
    info = ver = {}
    for d in json_docs(f"{res.stdout}\n{res.stderr}"):
        if "tx_power" in d: info = d
        elif "fw_build" in d: ver = d
    if not info: return {"ok": False, "why": "the device answered but did not report its settings"}
    return {"ok": True, "name": info.get("name", "?"), "model": ver.get("model", "?"), "fw": ver.get("ver", "?"), "max_contacts": ver.get("max_contacts")}


def auto_detect_usb_port(prefer=None, should_stop=None):
    """Finds the MeshCore node on USB.  The port that worked last time (prefer) is used straight away without probing; a single
    candidate is used as is; with several, each is asked (quickly) who it is and the first Companion wins.  should_stop() lets the
    caller cancel between probes."""
    cands = usb_candidates()
    if not cands:
        logging.critical("❌ No USB serial device found. Make sure the node is connected with a data cable (not charge-only) and its USB driver is installed.")
        return None
    if prefer and any(c["device"] == prefer for c in cands):
        logging.info(f"✅ Using {prefer} (the port that worked last time)")
        return ["-s", prefer]
    if len(cands) > 1:
        logging.info("Several USB serial devices found (" + ", ".join(f"{c['device']}: {c['description']}" for c in cands) + ") - checking which is a MeshCore Companion...")
        for c in cands:
            if should_stop and should_stop(): return None
            r = probe_device(["-s", c["device"]], timeout=12, retries=0)
            logging.info(f"  {c['device']}: " + (f"{r['model']} '{r['name']}' fw {r['fw']}" if r["ok"] else r["why"]))
            if r["ok"]:
                logging.info(f"✅ Using {c['device']}")
                return ["-s", c["device"]]
        logging.warning(f"None of the USB devices answered as a Companion; trying {cands[0]['device']} anyway.")
    logging.info(f"✅ Auto-detected MeshCore device on port {cands[0]['device']}")
    return ["-s", cands[0]["device"]]


def scan_ble(timeout=6):
    """Bluetooth devices meshcli can see: list of raw lines (format is whatever meshcli prints, usually 'address  name')."""
    try:
        res = subprocess.run([DEFAULT_CLI_PATH if os.path.exists(DEFAULT_CLI_PATH) else "meshcli", "-l", "-T", str(timeout)],
                             capture_output=True, text=True, **UTF8, timeout=timeout + 20, creationflags=NO_WINDOW)
    except Exception as e:
        logging.warning(f"Bluetooth scan failed: {e}")
        return []
    lines, grab = [], False
    for line in f"{res.stdout}\n{res.stderr}".splitlines():
        if line.strip().lower().startswith("ble devices"): grab = True; continue
        if line.strip().lower().startswith("serial ports"): break
        if grab and line.strip(): lines.append(line.strip())
    return lines


def build_connection_args(mode, port="auto", ble_target="", tcp_host="", tcp_port=5000, baud="", prefer_port="", should_stop=None):
    """meshcli connection arguments for USB serial, Bluetooth (first device found, or a given name/address) or WiFi/TCP.  None if impossible."""
    if mode == "tcp":
        if not str(tcp_host).strip():
            logging.critical("❌ WiFi/TCP connection selected but no host is set (Options > Connect).")
            return None
        return ["-t", str(tcp_host).strip(), "-p", str(tcp_port)]
    if mode == "bluetooth":
        return ["-a", ble_target.strip()] if ble_target.strip() else auto_detect_ble_device()
    args = ["-s", port.strip()] if port.strip() and port.strip().lower() != "auto" else auto_detect_usb_port(prefer_port or None, should_stop)
    return args + ["-b", str(baud).strip()] if args and str(baud).strip() else args


def auto_detect_ble_device():
    logging.info(f"Scanning for MeshCore BLE devices ({BLE_SCAN_TIMEOUT}s)...")
    binary = DEFAULT_CLI_PATH if os.path.exists(DEFAULT_CLI_PATH) else "meshcli"
    try:
        # -d "" tells meshcli to scan and connect to the first MeshCore companion device
        # it finds over Bluetooth (no address/name filter). "infos" is just a cheap command
        # to force the connection so we can read back which device it picked.
        result = subprocess.run(
            [binary, "-d", "", "-T", str(BLE_SCAN_TIMEOUT), "infos"],
            capture_output=True, text=True, **UTF8, timeout=BLE_SCAN_TIMEOUT + 15, creationflags=NO_WINDOW
        )
    except subprocess.TimeoutExpired:
        logging.critical("❌ BLE scan timed out. Make sure the node is powered on, in range, and already paired in Windows Bluetooth settings.")
        return None
    except Exception as e:
        logging.critical(f"❌ BLE scan failed: {e}")
        return None

    combined = f"{result.stdout}\n{result.stderr}"
    m = re.search(r'Found device\s*:\s*(.+)', combined)
    if m:
        addr, _, name = m.group(1).strip().rpartition(': ')
        addr = addr.strip() or m.group(1).strip()
        logging.info(f"✅ Auto-detected Bluetooth MeshCore device: {addr} ({name.strip() or 'unknown name'})")
        return ["-a", addr]

    logging.critical("❌ No MeshCore Bluetooth device detected. Make sure the node is powered on, within range, and paired with this computer first (Windows Settings > Bluetooth & devices > Add device).")
    return None

def _trace(args, attempt, outcome, started, detail=""):
    if TRACE is None: return
    try: TRACE(args, attempt + 1, outcome, time.time() - started, detail)
    except Exception: pass

def execute_mesh_command(args_list, timeout=30, retries=2, retry_delay=2):
    binary = DEFAULT_CLI_PATH if os.path.exists(DEFAULT_CLI_PATH) else "meshcli"
    last_err = None
    for attempt in range(retries + 1):
        started = time.time()
        try:
            with MESH_LOCK:
                result = subprocess.run([binary] + args_list, capture_output=True, text=True, **UTF8, timeout=timeout, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            last_err = RuntimeError(f"meshcli timed out after {timeout}s (BLE connection may have stalled or the node is out of range)")
            _trace(args_list, attempt, "TIMEOUT", started, f"after {timeout}s")
            if attempt < retries: time.sleep(retry_delay)
            continue
        except OSError as e:
            _trace(args_list, attempt, "CANNOT-START", started, str(e)[:200])
            raise
        if result.returncode != 0:
            last_err = RuntimeError(result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}")
            _trace(args_list, attempt, f"EXIT {result.returncode}", started, str(last_err)[:300].replace("\n", " | "))
        elif _TRANSIENT_MESHCLI_ERROR_RE.search(f"{result.stdout}\n{result.stderr}"):
            hit = _TRANSIENT_MESHCLI_ERROR_RE.search(f"{result.stdout}\n{result.stderr}").group(0)
            last_err = RuntimeError(f"meshcli reported '{hit}' (serial/BLE connection failed this attempt)")
            _trace(args_list, attempt, "FAILED", started, hit)
        else:
            _trace(args_list, attempt, "ok", started, f"{len(result.stdout)} chars")
            return result
        if attempt < retries: time.sleep(retry_delay)
    raise last_err

def resolve_channel_indices():
    """Fetches the node's real channel list (name -> index) via `.get_channels` — the leading dot
    forces JSON output from meshcore-cli regardless of any global -j flag. Channel indices aren't
    assumed/hardcoded (see CHANNEL_NAMES above); whatever this returns is what broadcast_via_cli
    actually uses.

    BUG FIX: confirmed live in production — this used to make exactly ONE attempt at startup with
    no retry of its own. `.get_channels` hits the exact same transient "No response from meshcore
    node, disconnecting" serial handshake race documented on execute_mesh_command (thousands of
    occurrences/day in emergency_agent.log), and a single unlucky attempt right after boot (when
    the node is still settling) permanently left CHANNEL_INDEX_BY_NAME empty for that ENTIRE run —
    for as long as 5+ days across several restarts (2026-09-06 through -13) — silently routing
    EVERY DriveBC/BC Ferries/BC Transit/TransLink/weather alert to the Public channel (0) instead
    of its category channel, which is exactly the "why is the bot defaulting to public?" complaint
    seen live on the mesh. execute_mesh_command already retries the transient handshake error
    itself now, but this adds a second, slower retry layer on top specifically for startup timing
    (node not fully awake yet), independent of that fix."""
    last_err = None
    for attempt in range(3):
        try:
            result = execute_mesh_command(CONNECTION_ARGS + [".get_channels"])
            combined = f"{result.stdout}\n{result.stderr}"
            # meshcli sometimes prints its own INFO: log lines before the JSON payload (seen with
            # other commands like `infos`/BLE scans) — scan for wherever the actual JSON array
            # starts rather than assuming stdout is pure JSON.
            starts = [i for i in (combined.find("["), combined.find("{")) if i != -1]
            if not starts:
                raise ValueError("no JSON payload found in .get_channels output")
            # BUG FIX: confirmed live — meshcli prints trailing text (e.g. a disconnect log line)
            # AFTER the JSON array on its own line, which plain json.loads() rejects outright
            # ("Extra data") since it requires the ENTIRE string to be exactly one JSON value.
            # raw_decode() instead parses just the first complete JSON value and ignores whatever
            # trails after it.
            data, _ = json.JSONDecoder().raw_decode(combined[min(starts):])
            CHANNEL_INDEX_BY_NAME.clear()
            CHANNEL_INDEX_BY_NAME.update({c["channel_name"]: c["channel_idx"] for c in data if c.get("channel_name")})
            logging.info(f"Resolved node channels: {CHANNEL_INDEX_BY_NAME}")
            return
        except Exception as e:
            last_err = e
            if attempt < 2: time.sleep(5)
    logging.error(f"Could not read channel list from the node after 3 attempts ({last_err}). Category "
                   f"alerts will be WITHHELD (not sent to Public) until channels resolve — this is "
                   f"retried automatically every scan; see 'Resolved node channels' in the log once it "
                   f"succeeds. If it never does, create #drivebc/#bcferries/#bctransit/#translink/"
                   f"#weather (see How to run.txt) and restart.")

def _json_from_output(result):
    combined = f"{result.stdout}\n{result.stderr}"
    starts = [i for i in (combined.find("["), combined.find("{")) if i != -1]
    if not starts: raise ValueError("no JSON payload in meshcli output")
    data, _ = json.JSONDecoder().raw_decode(combined[min(starts):])
    return data

def get_radio_params():
    """Returns the node's current {'radio_freq','radio_bw','radio_sf','radio_cr'} via `.get radio`."""
    return _json_from_output(execute_mesh_command(CONNECTION_ARGS + [".get", "radio"]))

def frequency_ok(freq_mhz):
    return 137.0 <= freq_mhz <= 1020.0 or 2400.0 <= freq_mhz <= 2500.0


def set_radio_frequency(freq_text):
    """Changes only the node's LoRa frequency, keeping its current bandwidth/SF/CR, then reboots and
    verifies the node actually came back on the new frequency. Returns (ok, message).

    BUG FIX: this used to run `meshcli set freq <MHz>`, but meshcore-cli has no `freq` setting at all
    (confirmed in its source: the `set` handler only implements radio/name/tx/lat/lon/coords/pin/...)
    — the unknown param was silently ignored with exit code 0, so only the following `reboot` ever
    took effect and the node came back on its old frequency. The real command sets all four radio
    params together: `set radio <freq>,<bw>,<sf>,<cr>`, so the current bw/sf/cr are read first and
    re-sent unchanged. Success is verified by reading the frequency back after the reboot."""
    try:
        freq = float(freq_text)
    except ValueError:
        return False, f"'{freq_text}' is not a valid frequency in MHz."
    if not frequency_ok(freq):
        return False, f"{freq} MHz is not a LoRa frequency (sub-GHz radios: 137-1020 MHz, 2.4 GHz radios: 2400-2500 MHz)."
    try:
        cur = get_radio_params()
        new_radio = f"{freq},{cur['radio_bw']},{cur['radio_sf']},{cur['radio_cr']}"
        res = execute_mesh_command(CONNECTION_ARGS + ["set", "radio", new_radio])
        out = f"{res.stdout}\n{res.stderr}"
        if re.search(r'\berror\b', out, re.IGNORECASE):
            return False, f"Node rejected 'set radio {new_radio}': {out.strip()}"
        try: execute_mesh_command(CONNECTION_ARGS + ["reboot"])
        except Exception as e: logging.info(f"reboot command ended with: {e} (normal — the node drops the link while restarting)")
        logging.info("Rebooting node to apply the new radio settings, waiting for it to come back...")
        time.sleep(8)
        for attempt in range(5):
            try:
                after = get_radio_params()
                break
            except Exception:
                time.sleep(4)
        else:
            return False, "Node did not respond after reboot, couldn't verify the new frequency."
        if abs(float(after["radio_freq"]) - freq) < 0.001:
            return True, f"Radio frequency changed {cur['radio_freq']} -> {after['radio_freq']} MHz (bw {after['radio_bw']}, sf {after['radio_sf']}, cr {after['radio_cr']})."
        return False, f"Node still reports {after['radio_freq']} MHz after reboot (wanted {freq})."
    except Exception as e:
        return False, f"Failed to set radio frequency to {freq_text} MHz: {e}"

def fetch_incoming_messages():
    """Polls `.sync_msgs` (fetch-and-dequeue all unread messages from the node) and returns the
    channel messages as a list of dicts, emitting each to the GUI (if any) as it goes. Per MeshCore's
    group text message format, the sender's name is embedded in the message text itself as
    "name: body" (see _TEST_SENDER_RE note above) rather than a separate field, so that's parsed out
    here. path_len of 255 means the message arrived direct (0 hops); any other value is the literal
    hop count. Only channel messages are returned; direct/private messages are skipped."""
    found = []
    try:
        result = execute_mesh_command(CONNECTION_ARGS + [".sync_msgs"])
    except Exception as e:
        logging.error(f"Failed to poll for incoming messages: {e}")
        return found
    combined = f"{result.stdout}\n{result.stderr}"
    # TEMPORARY DIAGNOSTIC: log every non-trivial .sync_msgs response verbatim, so a real incoming
    # message's actual JSON shape is visible in the log instead of having to guess at field names
    # blind (get_channels already surprised us once - same risk here). Harmless to leave since it
    # only logs when there's actually something more than an empty "[]" response.
    if combined.strip() not in ("", "[]"):
        logging.info(f"[DIAGNOSTIC] Raw .sync_msgs output: {combined!r}")
    for line in combined.splitlines():
        line = line.strip()
        # BUG FIX: confirmed live that `.sync_msgs` prints ALL fetched messages as a single JSON
        # ARRAY on one line (`[{...}, {...}]`), not one JSON object per line as originally assumed.
        if not line or line[0] not in "{[": continue
        try:
            # raw_decode() only needs the line to START with valid JSON, not end there - meshcli
            # prints trailing text (e.g. a disconnect log line) after the payload on the same line.
            parsed, _ = json.JSONDecoder().raw_decode(line)
        except ValueError:
            continue
        for data in (parsed if isinstance(parsed, list) else [parsed]):
            if not isinstance(data, dict): continue
            if data.get("type") == "PRIV":  # direct message: only the sender's key prefix is known here
                prefix = data.get("pubkey_prefix", "")
                _emit("dm", -1, (data.get("text") or "").strip(), nick=prefix, snr=data.get("SNR"), hops=data.get("path_len"), raw=data, pubkey=prefix)
                continue
            if data.get("type") != "CHAN": continue
            sender, body = split_sender(data.get("text", ""))
            _emit("in", data.get("channel_idx", DEFAULT_CHANNEL_IDX), body, nick=sender, snr=data.get("SNR"), hops=data.get("path_len"), raw=data)
            found.append(data)
    return found
