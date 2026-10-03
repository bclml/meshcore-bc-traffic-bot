"""Long-term memory of every node the radio has told us about.

A MeshCore node only holds a limited number of contacts (about 350).  This store keeps everything it has ever
listed (in nodes.db next to the script) so the map and node list can show far more than the radio can, and
forgets nodes that haven't been seen for N days."""
import json, logging, os, sqlite3, threading, time

import meshcore_io as ea

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nodes.db")
TYPE_NAMES = {1: "Companion", 2: "Repeater", 3: "Room server", 4: "Sensor"}
MIN_SANE_TS = 1577836800  # 2020-01-01; radio clocks that were never set report tiny/garbage times


def _first_seen(adv, mod, now):
    """Best guess of when a contact was last heard, for a node we've never stored before.  The radio's `lastmod` is
    often years stale even for nodes advertising right now (confirmed on a live node: lastmod Aug 2024, last_advert
    this week), so use whichever of the two is newest.  Values in the future (a node with a wrong clock) count as now."""
    times = [t for t in (adv, mod) if t > MIN_SANE_TS]
    return min(max(times), now) if times else now


class NodeStore:
    def __init__(self, path=DB_PATH):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS nodes (
            public_key TEXT PRIMARY KEY, name TEXT, type INTEGER, lat REAL, lon REAL,
            first_seen INTEGER, last_seen INTEGER, last_advert INTEGER, lastmod INTEGER, on_radio INTEGER)""")
        self.db.commit()

    def update_from_radio(self, contacts, now=None, min_seen=0):
        """contacts: the `.contacts` JSON dict (public_key -> fields).  Returns (new_nodes, on_radio_count).
        Contacts the radio still holds but that were last seen before `min_seen` are not remembered (otherwise a
        pruned node would come straight back on the next sync)."""
        now = int(now or time.time())
        new = 0
        with self.lock:
            known = {r["public_key"]: r for r in self.db.execute("SELECT * FROM nodes")}
            self.db.execute("UPDATE nodes SET on_radio = 0")
            for key, c in contacts.items():
                key = c.get("public_key", key)
                lat, lon = float(c.get("adv_lat") or 0), float(c.get("adv_lon") or 0)
                adv, mod = int(c.get("last_advert") or 0), int(c.get("lastmod") or 0)
                row = known.get(key)
                if row is None:
                    seen = _first_seen(adv, mod, now)
                    if seen < min_seen: continue
                    new += 1
                    self.db.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,1)",
                                    (key, c.get("adv_name") or key[:8], int(c.get("type") or 0), lat, lon, now, seen, adv, mod))
                else:
                    seen = now if (mod > row["lastmod"] or adv > row["last_advert"]) else row["last_seen"]
                    if not (lat or lon): lat, lon = row["lat"], row["lon"]  # keep a remembered position if the radio lost it
                    self.db.execute("UPDATE nodes SET name=?, type=?, lat=?, lon=?, last_seen=?, last_advert=?, lastmod=?, on_radio=1 WHERE public_key=?",
                                    (c.get("adv_name") or row["name"], int(c.get("type") or row["type"]), lat, lon, seen, adv, mod, key))
            self.db.commit()
        return new, len(contacts)

    def touch_contact(self, c, now=None):
        """Remember one radio contact as seen right now (used when a node we only knew by key sends us a message)."""
        now = int(now or time.time())
        key = c.get("public_key")
        if not key: return
        lat, lon = float(c.get("adv_lat") or 0), float(c.get("adv_lon") or 0)
        with self.lock:
            row = self.db.execute("SELECT lat, lon, first_seen FROM nodes WHERE public_key=?", (key,)).fetchone()
            if row and not (lat or lon): lat, lon = row["lat"], row["lon"]
            self.db.execute("INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?,?,?,1)",
                            (key, c.get("adv_name") or key[:8], int(c.get("type") or 0), lat, lon, row["first_seen"] if row else now, now,
                             int(c.get("last_advert") or 0), int(c.get("lastmod") or 0)))
            self.db.commit()

    def max_lastmod(self):
        """Newest 'last modified' stamp the radio ever gave us (the advert listener asks the radio only for what changed after it)."""
        with self.lock:
            r = self.db.execute("SELECT MAX(lastmod) FROM nodes WHERE on_radio = 1").fetchone()
        return int(r[0] or 0)

    def remember_pending(self, c, now=None):
        """An advert from a node the radio did not add (manual-add mode): remember it, marked as not on the radio.  Returns True if new."""
        now = int(now or time.time())
        key = c.get("public_key")
        if not key: return False
        lat, lon = float(c.get("adv_lat") or 0), float(c.get("adv_lon") or 0)
        with self.lock:
            row = self.db.execute("SELECT public_key FROM nodes WHERE public_key=?", (key,)).fetchone()
            if row:
                self.db.execute("UPDATE nodes SET last_seen=?, name=COALESCE(NULLIF(?, ''), name) WHERE public_key=?", (now, c.get("adv_name") or "", key))
            else:
                self.db.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,0)", (key, c.get("adv_name") or key[:8], int(c.get("type") or 0), lat, lon,
                                                                                    now, now, int(c.get("last_advert") or 0), int(c.get("lastmod") or 0)))
            self.db.commit()
        return row is None

    def prune(self, days, now=None):
        """Forget nodes not seen for `days` days (0 = never).  Returns [(public_key, was_on_radio)]."""
        if days <= 0: return []
        cutoff = int(now or time.time()) - int(days * 86400)
        with self.lock:
            rows = [(r["public_key"], bool(r["on_radio"])) for r in self.db.execute("SELECT public_key, on_radio FROM nodes WHERE last_seen < ?", (cutoff,))]
            self.db.execute("DELETE FROM nodes WHERE last_seen < ?", (cutoff,))
            self.db.commit()
        return rows

    def find_by_prefix(self, prefix):
        """The remembered node whose public key starts with this hex prefix (direct messages carry only a prefix)."""
        if not prefix: return None
        with self.lock:
            r = self.db.execute("SELECT * FROM nodes WHERE public_key LIKE ? LIMIT 1", (prefix.lower() + "%",)).fetchone()
        return dict(r) if r else None

    def find_by_name(self, name):
        with self.lock:
            r = self.db.execute("SELECT * FROM nodes WHERE lower(name) = lower(?) ORDER BY last_seen DESC LIMIT 1", (name,)).fetchone()
        return dict(r) if r else None

    def all(self):
        with self.lock: return [dict(r) for r in self.db.execute("SELECT * FROM nodes ORDER BY last_seen DESC")]

    def stats(self):
        with self.lock:
            r = self.db.execute("SELECT COUNT(*) t, COALESCE(SUM(on_radio),0) r, COALESCE(SUM(lat!=0 OR lon!=0),0) p FROM nodes").fetchone()
        return {"total": r["t"], "on_radio": r["r"], "positioned": r["p"]}


def fetch_radio_contacts():
    """Reads the radio's contact list through meshcli (`.contacts` -> JSON dict)."""
    res = ea.execute_mesh_command(ea.CONNECTION_ARGS + [".contacts"], timeout=90, retries=1)
    out = f"{res.stdout}\n{res.stderr}"
    i = out.find("{")
    if i == -1: return {}
    data, _ = json.JSONDecoder().raw_decode(out[i:])
    return data


def remove_from_radio(keys):
    """Deletes contacts from the radio itself (frees slots).  Batches several remove_contact commands per call."""
    removed = 0
    for i in range(0, len(keys), 10):
        args = [a for k in keys[i:i + 10] for a in ("remove_contact", k)]
        try:
            with ea.MESH_LOCK: ea.execute_mesh_command(ea.CONNECTION_ARGS + args, timeout=120, retries=0)
            removed += len(keys[i:i + 10])
        except Exception as e:
            logging.error(f"Removing nodes from the radio failed: {e}")
    return removed


def sync(store, prune_days, also_remove_from_radio):
    """One full cycle: read the radio, remember everything, forget the stale. Returns a summary dict."""
    contacts = fetch_radio_contacts()
    now = time.time()
    new, on_radio = store.update_from_radio(contacts, now, min_seen=now - prune_days * 86400 if prune_days > 0 else 0)
    gone = store.prune(prune_days, now)
    removed_radio = remove_from_radio([k for k, was in gone if was]) if (also_remove_from_radio and gone) else 0
    return {"new": new, "on_radio": on_radio, "pruned": len(gone), "removed_from_radio": removed_radio, **store.stats()}
