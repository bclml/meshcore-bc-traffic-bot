"""Addon framework for the mIRC-style GUI.

An addon is one Python file in the `addons/` folder (files starting with `_` are ignored) that defines a
subclass of AddonBase.  See addons/_example_addon.py for a commented template.  Hooks run on the GUI thread,
so do slow work (anything that talks to the radio or the network) through `self.api.run_background(...)`.
A crashing addon is isolated: the error is printed in the status window and the GUI keeps running."""
import importlib.util, os, traceback

ADDON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "addons")


class AddonBase:
    title = ""          # shown in the Addons dialog (defaults to the file name)
    version = "1.0"
    author = ""
    description = ""
    tick_seconds = 0    # >0: on_tick() is called about every this many seconds

    def __init__(self, api): self.api = api

    def on_load(self): """The addon was enabled/loaded (also called when the GUI starts)."""
    def on_unload(self): """The addon is being disabled/reloaded - stop threads, release things here."""
    def on_connect(self): """Connected to the node (also called on load if already connected)."""
    def on_disconnect(self): """The connection to the node ended."""
    def on_message(self, msg): """A chat message arrived. msg: dict(channel, channel_idx, nick, text, snr, hops, raw)."""
    def on_tick(self): """Called every `tick_seconds` seconds."""
    def on_demo(self): """Only in `--demo` mode: fill your windows / map layers with fake data."""
    def build_options(self, parent):
        """Return a tk.Frame (child of `parent`) to show as this addon's page in Options, or None."""
        return None
    def apply_options(self): """Options OK/Apply was pressed - read your widgets and store them via self.api.set()."""


class AddonAPI:
    """What an addon gets as `self.api`.  Everything here is safe to call from the GUI thread; send() and
    run_background() are safe from any thread."""
    def __init__(self, app, name):
        self._app, self.name = app, name
        self.display = name   # the addon's human title once it is instantiated (shown in menus)
        self._commands, self._menu, self._layers, self._buttons = [], [], [], []

    # -- settings (persisted in gui_settings.json under "addons") --
    def get(self, key, default=None): return self._app.settings.setdefault("addons", {}).get(self.name, {}).get(key, default)
    def set(self, key, value):
        self._app.settings.setdefault("addons", {}).setdefault(self.name, {})[key] = value
        self._app.save()

    # -- output --
    def log(self, text, level="info"): self._app.q.put(("call", lambda: self._app.status_line(f"*** [{self.name}] {text}", level)))
    def ensure_window(self, name, topic=""): return self._app.ensure_window(name, topic or f"Window of addon '{self.name}'")
    def write(self, window, text, tag="text"):
        """Write a line into a window (created on demand). tags: text info warn error new clear critical meta"""
        self._app.q.put(("call", lambda: self._app.ensure_window(window, "").write(self._app.stamp() + [(text, tag)])))

    # -- radio --
    @property
    def connected(self): return self._app.connected
    def channel_index(self, name):
        from gui_common import channel_index
        return channel_index(name)
    def send(self, channel, text):
        """Send `text` to a channel (display name like '#drivebc'/'Public', or an index). Runs in the background."""
        self._app.send_to(channel, text)
    def channels(self):
        """Names of the channel windows mcIRC knows right now (for pickers): 'Public', '#drivebc', ..."""
        return [n for n in self._app.windows if n != "Status" and not n.startswith("@")]
    def run_background(self, fn, done=None):
        """Run fn() on a worker thread; done(result_or_exception) is then called on the GUI thread."""
        self._app.bg(fn, done or (lambda r: None))
    def after(self, ms, fn): self._app.root.after(ms, fn)
    @property
    def nodes(self): return self._app.nodes

    # -- UI extension points (removed automatically when the addon is unloaded) --
    def add_command(self, name, fn, help=""):
        """Slash command: typing /name args calls fn(args_string)."""
        self._app.commands[name.lower()] = (fn, help, self.name)
        self._commands.append(name.lower())
    def add_menu_item(self, label, fn):
        self._app.addon_menu.add_command(label=f"{self.display}: {label}", command=fn)
        self._menu.append(f"{self.display}: {label}")
    def add_toolbar_button(self, text, fn):
        """Returns the tk.Button so you can change its text/colour later."""
        import tkinter as tk
        b = tk.Button(self._app.toolbar, text=text, command=fn, bg=self._app.toolbar["bg"], relief="flat", overrelief="raised", padx=8, pady=2)
        b.pack(side="left", padx=1, pady=2)
        self._buttons.append(b)
        return b
    def add_map_layer(self, label, provider, color="#d32f2f"):
        """Adds a toggle to the map.  provider() -> list of (lat, lon, label) tuples, called on each map refresh."""
        key = label if label not in self._app.map_layers else f"{self.display}: {label}"
        self._app.map_layers[key] = (provider, color)
        self._layers.append(key)

    def _cleanup(self):
        for c in self._commands: self._app.commands.pop(c, None)
        for label in self._menu:
            try: self._app.addon_menu.delete(label)
            except Exception: pass
        for l in self._layers: self._app.map_layers.pop(l, None)
        for b in self._buttons:
            try: b.destroy()
            except Exception: pass
        self._commands, self._menu, self._layers, self._buttons = [], [], [], []


class AddonManager:
    def __init__(self, app):
        self.app, self.loaded, self.errors = app, {}, {}  # name -> (instance, api)
        self._last_tick = {}

    def discover(self):
        if not os.path.isdir(ADDON_DIR): return []
        return sorted(f[:-3] for f in os.listdir(ADDON_DIR) if f.endswith(".py") and not f.startswith("_"))

    def enabled_names(self):
        enabled = self.app.settings.setdefault("addons_enabled", {})
        return [n for n in self.discover() if enabled.get(n, False)]   # nothing is on until it is installed / enabled

    def load_all(self):
        for n in self.enabled_names(): self.load(n)

    def load(self, name):
        if name in self.loaded: return True
        try:
            spec = importlib.util.spec_from_file_location(f"addon_{name}", os.path.join(ADDON_DIR, name + ".py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cls = getattr(mod, "Addon", None) or next(c for c in vars(mod).values()
                                                       if isinstance(c, type) and issubclass(c, AddonBase) and c is not AddonBase)
            api = AddonAPI(self.app, name)
            inst = cls(api)
            api.display = inst.title or name
            self.loaded[name] = (inst, api)
            self.errors.pop(name, None)
            self._call(name, "on_load")
            if self.app.connected: self._call(name, "on_connect")
            return True
        except Exception:
            self.errors[name] = traceback.format_exc()
            self.loaded.pop(name, None)
            self.app.status_line(f"*** Addon '{name}' failed to load: {self.errors[name].strip().splitlines()[-1]}", "error")
            return False

    def unload(self, name):
        if name not in self.loaded: return
        if self.app.connected: self._call(name, "on_disconnect")
        self._call(name, "on_unload")
        self.loaded.pop(name)[1]._cleanup()

    def reload(self, name):
        self.unload(name)
        return self.load(name)

    def set_enabled(self, name, on):
        self.app.settings.setdefault("addons_enabled", {})[name] = on
        self.app.save()
        (self.load if on else self.unload)(name)

    def _call(self, name, hook, *args):
        inst = self.loaded[name][0]
        try: return getattr(inst, hook)(*args)
        except Exception:
            tb = traceback.format_exc().strip().splitlines()
            self.app.status_line(f"*** Addon '{name}' error in {hook}(): {tb[-1]}", "error")

    def dispatch(self, hook, *args):
        for name in list(self.loaded): self._call(name, hook, *args)

    def tick(self, now):
        for name, (inst, _) in list(self.loaded.items()):
            if inst.tick_seconds and now - self._last_tick.get(name, 0) >= inst.tick_seconds:
                self._last_tick[name] = now
                self._call(name, "on_tick")

    def info(self, name):
        if name in self.loaded:
            i = self.loaded[name][0]
            return (i.title or name, i.version, i.author, i.description)
        return (name, "", "", self.errors.get(name, "").strip().splitlines()[-1] if name in self.errors else "(disabled)")


# ===================================================================================================================
# Installing / removing addon packages.  An addon is NOT shipped enabled: it lives as a package (a folder or .zip with
# an addon.json manifest, e.g. packages/broadcast_alerts) and is copied into place by "Install..." in the Addons dialog.
# ===================================================================================================================
import hashlib, json, shutil, time, zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ADDON_DIR, ".installed.json")
PROTECTED_ROOT = {"mcIRC.py", "meshcore_gui.py", "meshcore_io.py"}
PIP_MODULES = {"requests": "requests", "gtfs-realtime-bindings": "google.transit", "pyserial": "serial", "tkintermapview": "tkintermapview"}


def vkey(version):
    """'1.10.2' -> (1, 10, 2) so versions compare numerically; junk compares lowest."""
    try: return tuple(int(p) for p in str(version).strip().lstrip("v").split("."))
    except ValueError: return (0,)


def read_installed():
    try:
        with open(STATE_PATH, encoding="utf-8") as f: return json.load(f)
    except (OSError, ValueError): return {}


def _write_installed(state):
    os.makedirs(ADDON_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f: json.dump(state, f, indent=2)


def _dest_ok(rel):
    """Where a package may put files: addons/<name>.py, or a plain <name>.py next to the app (the engine) - never core files."""
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    if rel.startswith("/") or ".." in parts or not rel.endswith(".py") or len(parts) > 2: return False
    if len(parts) == 2: return parts[0] == "addons" and not parts[1].startswith("_")
    return parts[0] not in PROTECTED_ROOT and not parts[0].startswith(("gui_", "_"))


def _backup(path, label):
    if not os.path.exists(path): return
    dest = os.path.join(BASE_DIR, "backup", f"{label}-{time.strftime('%Y%m%d-%H%M%S')}", os.path.relpath(path, BASE_DIR))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(path, dest)


def _read_package(source):
    """-> (manifest, {dest: bytes}) from a folder / addon.json / .zip / single .py file; raises ValueError if unusable."""
    source = os.path.abspath(source)
    if source.lower().endswith(".py"):
        name = os.path.splitext(os.path.basename(source))[0]
        with open(source, "rb") as f: data = f.read()
        return {"name": name, "title": name, "version": "?", "files": {os.path.basename(source): f"addons/{name}.py"}}, {f"addons/{name}.py": data}
    if source.lower().endswith(".zip"):
        with zipfile.ZipFile(source) as zf:
            names = zf.namelist()
            root = next((n[:-len("addon.json")] for n in names if n.endswith("addon.json") and n.count("/") <= 1), None)
            if root is None: raise ValueError("this zip has no addon.json")
            manifest = json.loads(zf.read(root + "addon.json"))
            files = {}
            for src, dest in manifest["files"].items():
                if ".." in src.replace("\\", "/").split("/"): raise ValueError(f"unsafe path in package: {src}")
                files[dest] = zf.read(root + src)
        return manifest, files
    folder = os.path.dirname(source) if source.lower().endswith(".json") else source
    mpath = os.path.join(folder, "addon.json")
    if not os.path.exists(mpath): raise ValueError("no addon.json in that folder")
    with open(mpath, encoding="utf-8") as f: manifest = json.load(f)
    files, real_folder, real_base = {}, os.path.realpath(folder), os.path.realpath(BASE_DIR)
    for src, dest in manifest["files"].items():
        path = os.path.realpath(os.path.join(folder, src))
        inside = path.startswith(real_folder + os.sep) or (real_folder.startswith(real_base + os.sep) and path.startswith(real_base + os.sep))
        if not inside: raise ValueError(f"unsafe path in package: {src}")
        with open(path, "rb") as f: files[dest] = f.read()
    return manifest, files


def install_package(source):
    """Copies a package (folder / addon.json / .zip / .py) into place.  See install_files()."""
    manifest, files = _read_package(source)
    return install_files(manifest, files, os.path.abspath(source))


def install_files(manifest, files, origin):
    """Validates and writes a package given as (manifest, {dest: bytes}).  Returns {name, title, version, files, missing};
    raises ValueError with a readable reason.  Existing files that differ are backed up first."""
    name = manifest.get("name", "")
    if not name.isidentifier() or name.startswith("_"): raise ValueError(f"bad addon name '{name}'")
    if f"addons/{name}.py" not in files: raise ValueError(f"the package must provide addons/{name}.py")
    for dest, data in files.items():
        if not _dest_ok(dest): raise ValueError(f"the package wants to write '{dest}', which is not allowed")
        try: compile(data, dest, "exec")
        except SyntaxError as e: raise ValueError(f"{dest} has a syntax error: {e}")
    written = []
    for dest, data in files.items():
        path = os.path.join(BASE_DIR, *dest.split("/"))
        if os.path.exists(path):
            with open(path, "rb") as f: same = f.read() == data
            if same: written.append(dest); continue
            _backup(path, "addon-" + name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f: f.write(data)
        os.replace(tmp, path)
        written.append(dest)
    state = read_installed()
    state[name] = {"version": manifest.get("version", "?"), "title": manifest.get("title", name), "files": written, "package": origin}
    _write_installed(state)
    import importlib.util
    missing = [p for p in manifest.get("requires", []) if importlib.util.find_spec(PIP_MODULES.get(p, p.replace("-", "_"))) is None]
    return {"name": name, "title": manifest.get("title", name), "version": manifest.get("version", "?"), "files": written, "missing": missing}


def uninstall_package(name):
    """Deletes the addon file (the engine/support files stay: the console bot can still use them).  Settings are kept."""
    state = read_installed()
    path = os.path.join(ADDON_DIR, name + ".py")
    if os.path.exists(path):
        _backup(path, "removed-" + name)
        os.remove(path)
    state.pop(name, None)
    _write_installed(state)


def _install(self, source):
    info = install_package(source)
    self.set_enabled(info["name"], True)
    return info


def _uninstall(self, name):
    self.unload(name)
    self.app.settings.setdefault("addons_enabled", {}).pop(name, None)
    uninstall_package(name)
    self.app.save()


def _update_installed(self, repo_root=BASE_DIR):
    """After an app update: refresh installed addons whose package (in repo_root/packages) has a newer version.
    Their settings live in gui_settings.json and are never touched.  Returns [(name, old, new)]."""
    done = []
    for name, rec in read_installed().items():
        manifest_path = os.path.join(repo_root, "packages", name, "addon.json")
        if not os.path.exists(manifest_path): continue
        with open(manifest_path, encoding="utf-8") as f: new = json.load(f).get("version", "0")
        if vkey(new) > vkey(rec.get("version", "0")):
            was_loaded = name in self.loaded
            if was_loaded: self.unload(name)
            install_package(manifest_path)
            if was_loaded or self.app.settings.get("addons_enabled", {}).get(name): self.load(name)
            done.append((name, rec.get("version"), new))
    return done


AddonManager.install = _install
AddonManager.uninstall = _uninstall
AddonManager.update_installed = _update_installed


# ===================================================================================================================
# Catalog of tested addons (addons-catalog.json in the GitHub repo).  Only addons that have been reviewed and tested
# by a maintainer are listed; the GUI's "Browse addons..." shows them and installs a chosen one with a click.
# ===================================================================================================================
import posixpath, urllib.request

REPO = "bclml/mcIRC"
BRANCH = "master"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"


def _http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "mcIRC"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read()


def fetch_catalog(raw=RAW, get=_http_get):
    """-> list of catalog entries: {name, title, version, author, description, path, tested}."""
    data = json.loads(get(raw + "addons-catalog.json").decode("utf-8"))
    return data.get("addons", [])


def install_from_catalog(entry, raw=RAW, get=_http_get):
    """Downloads one catalog entry's package straight from the repo and installs it (same checks as a local install)."""
    base = entry["path"].strip("/")
    manifest = json.loads(get(f"{raw}{base}/addon.json").decode("utf-8"))
    files = {}
    for src, dest in manifest["files"].items():
        repo_path = posixpath.normpath(posixpath.join(base, src))
        if repo_path.startswith("..") or repo_path.startswith("/"): raise ValueError(f"unsafe path in package: {src}")
        files[dest] = get(raw + repo_path)
    return install_files(manifest, files, raw + base)
