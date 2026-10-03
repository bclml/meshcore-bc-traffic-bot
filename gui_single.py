"""Only one mcIRC may run at a time: two copies would fight over the same radio port.

The first copy listens on a local socket; starting a second copy asks the first to come to the front and then exits."""
import socket
import threading

PORT = 47811
HELLO = b"mcIRC\n"


def acquire(on_raise):
    """Become the one running instance.  Returns the listening socket (keep it alive), or None if another mcIRC already is."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"): srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        srv.bind(("127.0.0.1", PORT))
        srv.listen(4)
    except OSError:
        srv.close()
        return None
    threading.Thread(target=_serve, args=(srv, on_raise), daemon=True).start()
    return srv


def _serve(srv, on_raise):
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return   # socket closed
        try:
            conn.settimeout(2)
            conn.sendall(HELLO)
            if conn.recv(16).startswith(b"raise"): on_raise()
        except OSError:
            pass
        finally:
            conn.close()


def notify_existing():
    """Ask a running mcIRC to come to the front.  True if one answered (so this copy should just exit)."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=2) as c:
            c.settimeout(2)
            if c.recv(16) != HELLO: return False
            c.sendall(b"raise\n")
            return True
    except OSError:
        return False
