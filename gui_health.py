"""When the radio stops answering: tell the user, and (only if they switched it on) restart the board by pulsing its USB reset line.

The reset line trick is the one flashing tools use: boards with a CP210x / CH340 / FTDI USB-to-serial chip (Heltec V3, LilyGo, many ESP32 boards)
wire the RTS signal to the chip's EN / reset pin.  It is limited to those chips and to USB serial connections: boards with a native USB port
(ESP32-S3 / nRF52 / RP2040) can be sent into their bootloader by control-line toggling, so those are never touched."""
import logging
import threading
import time

import meshcore_io as io

UART_BRIDGE_VENDORS = {0x10C4: "Silicon Labs CP210x", 0x1A86: "WCH CH340/CH9102", 0x0403: "FTDI"}
COOLDOWN = 30 * 60              # never reset more than once in half an hour
MAX_PER_SESSION = 2
RESET_AFTER_FAILURES = 6        # consecutive failed commands (about 4-5 minutes of silence)
BOOT_WAIT = 10                  # seconds for the board to start up again


def port_vendor(port):
    try:
        import serial.tools.list_ports as lp
        for p in lp.comports():
            if p.device == port: return p.vid
    except Exception:
        pass
    return None


def reset_allowed(conn_args):
    """(ok, why) - only a USB serial connection through a UART bridge chip may be reset this way."""
    if not conn_args or conn_args[0] != "-s" or len(conn_args) < 2: return False, "the node is not connected by USB serial"
    vid = port_vendor(conn_args[1])
    if vid not in UART_BRIDGE_VENDORS:
        return False, "this board has a native USB port (or an unknown chip); resetting it by control lines could put it into its bootloader - use the reset button or replug it"
    return True, UART_BRIDGE_VENDORS[vid]


def pulse_reset(port):
    """Restart the board: RTS low for a moment, DTR kept released so it boots normally (not into the bootloader)."""
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 1
    s.dtr = False                  # GPIO0 stays high = normal boot
    s.rts = False
    s.open()
    try:
        s.rts = True               # EN / reset low
        time.sleep(0.15)
        s.rts = False
        time.sleep(0.1)
    finally:
        s.close()


class Recovery:
    """Used by the connection thread after every poll."""
    def __init__(self, app):
        self.app, self.last_reset, self.count = app, 0.0, 0

    def maybe(self):
        s = self.app.settings
        if not s.get("auto_reset_radio", False): return False
        h = io.HEALTH
        if not h.is_down or h.fails < RESET_AFTER_FAILURES: return False
        if self.count >= MAX_PER_SESSION or time.time() - self.last_reset < COOLDOWN: return False
        return self.reset("automatic")

    def reset(self, how="manual"):
        ok, why = reset_allowed(io.CONNECTION_ARGS)
        if not ok:
            self.app.q.put(("health", "reset_refused", {"why": why}))
            return False
        port = io.CONNECTION_ARGS[1]
        if not io.MESH_LOCK.acquire(True, 60): return False
        try:
            self.last_reset, self.count = time.time(), self.count + 1
            self.app.q.put(("health", "reset", {"how": how, "port": port, "chip": why}))
            try: pulse_reset(port)
            except Exception as e:
                logging.error(f"Could not reset the radio on {port}: {type(e).__name__}: {e}")
                self.app.q.put(("health", "reset_failed", {"why": str(e)}))
                return False
            time.sleep(BOOT_WAIT)
        finally:
            io.MESH_LOCK.release()
        return True
