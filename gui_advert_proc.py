"""Advert listener process.  Started by mcIRC (gui_adverts.py) in the idle time between polls; not meant to be run by hand.

Connects to the node, asks for the contacts that changed since the last one mcIRC knows (so adverts missed while the port was in use are caught
up), then stays connected for --seconds and prints one JSON line per event as it happens:

    {"event": "ready"}
    {"event": "advert", "public_key": "..."}              a node advertised (the details follow a moment later)
    {"event": "contact", "contact": {...}}                a contact that was added or changed on the radio (name, type, position, ...)
    {"event": "new_contact", "contact": {...}}            an advert waiting for approval (the node is in manual-add mode)
    {"event": "error", "message": "..."}

No message text is ever read or printed here."""
import argparse
import asyncio
import json
import sys
import time

from meshcore import MeshCore, EventType


def out(**kw):
    print(json.dumps(kw), flush=True)


async def run(a):
    try:
        if a.serial: mc = await MeshCore.create_serial(a.serial, a.baud)
        elif a.tcp: mc = await MeshCore.create_tcp(a.tcp, a.port)
        else: return out(event="error", message="no connection given")
    except Exception as e:
        return out(event="error", message=f"{type(e).__name__}: {e}")
    if mc is None: return out(event="error", message="could not connect")

    emit = {"on": a.lastmod > 0}          # with nothing known yet the first full load is silent: those contacts are not "new adverts"
    if a.lastmod > 0:
        try: mc._lastmod = a.lastmod      # only fetch what changed since then
        except Exception: pass
    mc.auto_update_contacts = True         # the library re-reads changed contacts by itself after every advert / path update

    async def on_contacts(ev):
        if emit["on"]:
            for c in (ev.payload or {}).values(): out(event="contact", contact=c)

    async def on_new(ev): out(event="new_contact", contact=ev.payload)
    async def on_advert(ev): out(event="advert", public_key=ev.payload.get("public_key", ""))

    mc.subscribe(EventType.CONTACTS, on_contacts)
    mc.subscribe(EventType.NEW_CONTACT, on_new)
    mc.subscribe(EventType.ADVERTISEMENT, on_advert)
    try:
        await mc.commands.get_contacts(lastmod=a.lastmod)       # catch-up (silent when mcIRC knew nothing)
        emit["on"] = True
        out(event="ready")
        await asyncio.sleep(max(1, a.seconds - 2))
    finally:
        try: await mc.disconnect()
        except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--tcp")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--lastmod", type=int, default=0)
    ap.add_argument("--seconds", type=int, default=10)
    a = ap.parse_args()
    try:
        asyncio.run(run(a))
    except Exception as e:
        out(event="error", message=f"{type(e).__name__}: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
