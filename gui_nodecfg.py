"""Read and write the settings of the USB-attached MeshCore node itself (name, position, radio, power, telemetry,
behaviour) through meshcli, plus actions like sending an advert or rebooting."""
import json, re, time

import meshcore_io as ea

TELEMETRY = ["off", "selected", "all"]   # firmware values 0, 1, 2
BW_CHOICES = ["7.8", "10.4", "15.6", "20.8", "31.25", "41.7", "62.5", "125", "250", "500"]


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


def read_node():
    """Everything the node reports about itself: {'info','ver','core','radio'} (each a dict, possibly empty)."""
    res = ea.execute_mesh_command(ea.CONNECTION_ARGS + [".infos", ".ver", ".get", "stats_core", ".get", "stats_radio"], timeout=60)
    out = {"info": {}, "ver": {}, "core": {}, "radio": {}}
    for d in json_docs(f"{res.stdout}\n{res.stderr}"):
        if "tx_power" in d: out["info"] = d
        elif "fw_build" in d: out["ver"] = d
        elif "battery_mv" in d: out["core"] = d
        elif "noise_floor" in d: out["radio"] = d
    if not out["info"]: raise RuntimeError("the node did not report its settings")
    return out


def values_from(node):
    """Editable settings as plain typed values."""
    i, v = node["info"], node["ver"]
    return {"name": i.get("name", ""), "lat": float(i.get("adv_lat") or 0), "lon": float(i.get("adv_lon") or 0),
            "freq": float(i["radio_freq"]), "bw": float(i["radio_bw"]), "sf": int(i["radio_sf"]), "cr": int(i["radio_cr"]),
            "tx": int(i["tx_power"]), "multi_acks": bool(i.get("multi_acks")),
            "telem_base": int(i.get("telemetry_mode_base", 0)), "telem_loc": int(i.get("telemetry_mode_loc", 0)),
            "telem_env": int(i.get("telemetry_mode_env", 0)), "loc_policy": bool(i.get("adv_loc_policy")),
            "manual_add": bool(i.get("manual_add_contacts")), "path_hash": int(v.get("path_hash_mode", 0)), "pin": int(v.get("ble_pin", 0))}


def _onoff(b): return "on" if b else "off"


def build_commands(old, new):
    """[(label, meshcli args)] for just the settings that changed."""
    ch = lambda *keys: any(old[k] != new[k] for k in keys)
    cmds = []
    if ch("name"): cmds.append(("name", ["set", "name", new["name"]]))
    if ch("lat", "lon"): cmds.append(("position", ["set", "coords", f"{new['lat']},{new['lon']}"]))
    if ch("freq", "bw", "sf", "cr"): cmds.append(("radio", ["set", "radio", f"{new['freq']},{new['bw']},{new['sf']},{new['cr']}"]))
    if ch("tx"): cmds.append(("tx power", ["set", "tx", str(new["tx"])]))
    if ch("multi_acks"): cmds.append(("multi-acks", ["set", "multi_ack", _onoff(new["multi_acks"])]))
    for key, label in (("telem_base", "base telemetry"), ("telem_loc", "location telemetry"), ("telem_env", "environment telemetry")):
        if ch(key): cmds.append((label, ["set", "telemetry_mode_" + key[6:], str(new[key])]))
    if ch("loc_policy"): cmds.append(("advert location", ["set", "advert_loc_policy", "share" if new["loc_policy"] else "none"]))
    if ch("manual_add"): cmds.append(("manual add contacts", ["set", "manual_add_contacts", _onoff(new["manual_add"])]))
    if ch("path_hash"): cmds.append(("path hash mode", ["set", "path_hash_mode", str(new["path_hash"])]))
    if ch("pin"): cmds.append(("BLE pin", ["set", "pin", str(new["pin"])]))
    return cmds


def write_node(old, new):
    """Applies the changes one setting at a time.  Returns ([(label, ok, detail)], radio_changed)."""
    results = []
    with ea.MESH_LOCK:
        for label, args in build_commands(old, new):
            try:
                res = ea.execute_mesh_command(ea.CONNECTION_ARGS + args, timeout=40)
                out = f"{res.stdout}\n{res.stderr}"
                bad = re.search(r"\berror\b", out, re.IGNORECASE)
                results.append((label, not bad, out.strip().splitlines()[-1] if bad else "ok"))
            except Exception as e:
                results.append((label, False, str(e)))
    return results, any(r[0] == "radio" and r[1] for r in results)


def reboot_and_wait():
    """Reboots the node and waits until it answers again.  Returns the fresh node dict."""
    with ea.MESH_LOCK:
        try: ea.execute_mesh_command(ea.CONNECTION_ARGS + ["reboot"], retries=0)
        except Exception: pass  # the node drops the link while restarting
        time.sleep(8)
        for _ in range(6):
            try: return read_node()
            except Exception: time.sleep(4)
    raise RuntimeError("the node did not come back after the reboot")


def action(name):
    """Simple one-shot commands: 'advert', 'floodadv', 'clock sync'.  Returns the node's reply text."""
    with ea.MESH_LOCK:
        res = ea.execute_mesh_command(ea.CONNECTION_ARGS + name.split(), timeout=40)
    return (res.stdout.strip().splitlines() or ["done"])[-1]
