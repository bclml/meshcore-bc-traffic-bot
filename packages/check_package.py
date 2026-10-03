"""Validate an addon package before submitting it:  python packages/check_package.py packages/my_addon

Checks the manifest, where files would be written, syntax, that the addon class loads, and that on_load /
on_unload (and build_options, if a display is available) run against a stand-in API.  Exit code 0 = all passed."""
import importlib.util, json, os, sys

os.environ["MCIRC_NO_LOG_FILE"] = "1"      # validating an addon must never write to the real operational log
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import gui_addons as ga   # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    return ok


def main(folder):
    folder = os.path.abspath(folder)
    mpath = os.path.join(folder, "addon.json")
    if not check("addon.json exists", os.path.exists(mpath)): return
    try:
        manifest = json.load(open(mpath, encoding="utf-8"))
    except ValueError as e:
        check("addon.json is valid JSON", False, str(e))
        return
    missing = [k for k in ("name", "title", "version", "description", "files") if not manifest.get(k)]
    check("manifest has name/title/version/description/files", not missing, f"missing: {missing}" if missing else "")
    name = manifest.get("name", "")
    check("name is a valid identifier", name.isidentifier() and not name.startswith("_"))
    check(f"provides addons/{name}.py", f"addons/{name}.py" in manifest.get("files", {}).values())
    sources = {}
    for src, dest in manifest.get("files", {}).items():
        check(f"destination allowed: {dest}", ga._dest_ok(dest))
        path = os.path.realpath(os.path.join(folder, src))
        if not check(f"source exists: {src}", os.path.isfile(path)): continue
        sources[dest] = path
        try:
            compile(open(path, "rb").read(), src, "exec")
            check(f"compiles: {src}", True)
        except SyntaxError as e:
            check(f"compiles: {src}", False, str(e))
    addon_file = sources.get(f"addons/{name}.py")
    if not addon_file: return
    try:
        spec = importlib.util.spec_from_file_location(f"check_{name}", addon_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, "Addon", None) or next(c for c in vars(mod).values() if isinstance(c, type) and issubclass(c, ga.AddonBase) and c is not ga.AddonBase)
        check("defines an AddonBase subclass", issubclass(cls, ga.AddonBase))
    except Exception as e:
        check("imports and defines an AddonBase subclass", False, f"{type(e).__name__}: {e}")
        return
    api = mock.MagicMock()
    api.get = lambda key, default=None: default
    inst = cls(api)
    for hook in ("on_load", "on_unload"):   # on_connect/on_disconnect are not called: addons start real work (threads, radio) there
        try:
            getattr(inst, hook)()
            check(f"{hook}() runs", True)
        except Exception as e:
            check(f"{hook}() runs", False, f"{type(e).__name__}: {e}")
    try:
        inst.on_message({"channel": "Public", "channel_idx": 0, "nick": "tester", "text": "hello", "snr": 1, "hops": 0, "raw": None})
        check("on_message() handles a normal message", True)
    except Exception as e:
        check("on_message() handles a normal message", False, f"{type(e).__name__}: {e}")
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        page = inst.build_options(tk.Frame(root))
        check("build_options() runs", True, "no options page" if page is None else "")
        root.destroy()
    except tk.TclError:
        print("[skip] build_options(): no display available")
    except Exception as e:
        check("build_options() runs", False, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python packages/check_package.py packages/<name>")
    main(sys.argv[1])
    print("\nALL CHECKS PASSED" if all(results) else f"\n{results.count(False)} CHECK(S) FAILED")
    sys.exit(0 if all(results) else 1)
