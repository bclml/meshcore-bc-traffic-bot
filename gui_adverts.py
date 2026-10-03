"""Instant node updates from adverts.

meshcli runs one short process per command, and the node only tells a connected program about adverts while that program is connected.  So in
the idle time between message polls this module keeps a small listener process (gui_advert_proc.py) connected and reads its events as they
arrive: a node that advertises shows up in the node list / map and a key-only private window gets its real name within about a second.

The listener holds the radio only while nothing else needs it: as soon as any other part of mcIRC wants the port (sending a message, a
repeater command, the next poll) the listener is stopped, and anything it missed is caught up from the radio's contact list the next time it
starts.  Works over USB and WiFi/TCP (Bluetooth reconnects are too slow for this)."""
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time

import gui_diag
import meshcore_io as io

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_advert_proc.py")
MAX_FAILURES = 3


def helper_args(conn):
    """meshcli-style connection arguments -> listener arguments, or None when listening isn't possible (Bluetooth, nothing set)."""
    if not conn: return None
    a = list(conn)
    try:
        if a[0] == "-s":
            out = ["--serial", a[1]]
            if "-b" in a: out += ["--baud", a[a.index("-b") + 1]]
            return out
        if a[0] == "-t":
            return ["--tcp", a[1]] + (["--port", a[a.index("-p") + 1]] if "-p" in a else [])
    except (IndexError, ValueError):
        return None
    return None


class AdvertWatcher:
    def __init__(self, app):
        self.app, self.proc, self.failures, self.disabled = app, None, 0, False
        self.yield_evt = threading.Event()                # "someone needs the radio": sticky, so a request that arrives while the listener is still starting isn't lost
        io.MESH_LOCK.on_contend = self.interrupt          # anyone who needs the radio makes the listener let go of it

    def interrupt(self):
        self.yield_evt.set()
        p = self.proc
        if p is not None and p.poll() is None:
            try: p.kill()
            except OSError: pass

    def enabled(self):
        return bool(self.app.settings.get("advert_listen", True)) and not self.disabled and helper_args(io.CONNECTION_ARGS) is not None

    def listen(self, seconds, stop_evt):
        """Use `seconds` of idle time listening (or just waiting when listening is off / unavailable).  Runs on the connection thread."""
        if not self.enabled():
            stop_evt.wait(seconds)
            return
        if not io.MESH_LOCK.acquire(False):                # the radio is busy right now
            stop_evt.wait(1)
            return
        self.yield_evt.clear()
        started = time.time()
        try:
            cmd = [sys.executable, HELPER] + helper_args(io.CONNECTION_ARGS) + ["--lastmod", str(self.app.nodes.max_lastmod()), "--seconds", str(int(seconds))]
            try:
                self.proc = proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=io.NO_WINDOW, **io.UTF8)
            except OSError as e:
                return self._failed(f"cannot start the advert listener: {e}", started, seconds, stop_evt)
            if self.yield_evt.is_set(): self.interrupt()
            lines = queue.Queue()
            def pump():
                for line in proc.stdout: lines.put(line)
                lines.put(None)
            threading.Thread(target=pump, daemon=True).start()
            deadline, got_ready, error = started + seconds + 15, False, ""
            while not stop_evt.is_set() and time.time() < deadline and not self.yield_evt.is_set():
                try: line = lines.get(timeout=0.3)
                except queue.Empty: continue
                if line is None: break
                ev = self._parse(line)
                if not ev: continue
                if ev["event"] == "ready": got_ready = True
                elif ev["event"] == "error": error = ev.get("message", "error")
                else: self._event(ev)
            interrupted = self.yield_evt.is_set()
            self.interrupt()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: pass
            if error or (not got_ready and not interrupted and not stop_evt.is_set()):
                return self._failed(error or "the advert listener exited without connecting", started, seconds, stop_evt)
            self.failures = 0
            if interrupted or proc.returncode in (-9, 1):    # we were asked to let go: give the other user a moment
                stop_evt.wait(1.0)
        finally:
            self.proc = None
            io.MESH_LOCK.release()

    def _failed(self, why, started, seconds, stop_evt):
        self.failures += 1
        gui_diag.event("adverts", f"listener failed ({self.failures}/{MAX_FAILURES}): {why}")
        if self.failures >= MAX_FAILURES:
            self.disabled = True
            logging.warning("Instant advert updates switched off for this session (the listener keeps failing); nodes are still read every few minutes.")
        stop_evt.wait(max(1, seconds - (time.time() - started)))

    @staticmethod
    def _parse(line):
        line = line.strip()
        if not line.startswith("{"): return None
        try: ev = json.loads(line)
        except ValueError: return None
        return ev if isinstance(ev, dict) and "event" in ev else None

    def _event(self, ev):
        kind = ev["event"]
        gui_diag.count("adverts" if kind == "advert" else "advert_contacts")
        if kind == "advert":
            gui_diag.event("adverts", f"advert heard from {ev.get('public_key', '')}")
            return
        c = ev.get("contact") or {}
        if c.get("public_key"): self.app.q.put(("advert", kind, c))
