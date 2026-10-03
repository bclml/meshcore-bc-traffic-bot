# Guide for AI coding assistants: write and submit an mcIRC addon

You are probably reading this because a person asked you (Claude, ChatGPT/Codex, Gemini, Copilot, Cursor, ...) to build
an **addon for mcIRC** and submit it. mcIRC is an mIRC-style desktop chat client for MeshCore LoRa mesh radios. The core
is chat only; every other feature is an addon. Follow this guide and the result can be reviewed quickly.

## Ground rules (read first)

1. **Never touch core files.** Do not edit `mcIRC.py`, `meshcore_io.py`, `gui_*.py`, `emergency_agent.py`. Addons cannot
   replace them and pull requests that do are for a different kind of contribution (see CONTRIBUTING.md).
2. **The mesh is a tiny shared radio channel.** Anything that transmits must be rate-limited, have an off switch, and be
   **off by default**. Never auto-reply to every message. Never send in a loop.
3. **No secrets, no personal data** in code, defaults, tests or screenshots. Use `self.api.get/set` plus an Options page
   for keys/passwords/locations. Use obviously fake names/coordinates in examples.
4. **Hooks run on the GUI thread.** Radio calls, network calls and anything slow go in `self.api.run_background(fn, done)`.
   `done` runs back on the GUI thread with the result (or the exception). Never call tkinter from a plain thread.
5. Python 3.10+, standard library + tkinter only unless the addon really needs more; list extras in `requires`.
6. Be honest: say what you did **not** test (e.g. real radio). The reviewer will test it on hardware if it transmits.
7. Do **not** add yourself to `addons-catalog.json`. Only a maintainer does that, after review and testing.

## Files you create

```
packages/<name>/addon.json
packages/<name>/<name>.py
```

`<name>`: lowercase letters, digits and underscores, starts with a letter, no leading underscore (it is a Python identifier).

### `addon.json`

```json
{
  "name": "my_addon",
  "title": "My addon",
  "version": "1.0.0",
  "description": "One sentence a user can understand.",
  "requires": [],
  "files": { "my_addon.py": "addons/my_addon.py" }
}
```

`files` maps a file in the package folder to its install path; the addon must install to `addons/<name>.py`.

### `<name>.py` - minimal working template

```python
"""One line: what this addon does."""
import tkinter as tk

from gui_addons import AddonBase


class Addon(AddonBase):
    title = "My addon"
    version = "1.0.0"
    author = "your-github-name"
    description = "Answers '!ping' with 'pong' (off until you enable it in Options)."
    tick_seconds = 0                       # >0: on_tick() every N seconds

    def on_load(self):                     # enabled: start threads / register commands here
        self.api.add_command("ping", self.cmd_ping, "show the ping counter")

    def on_unload(self):                   # disabled: stop EVERYTHING you started
        pass

    def cmd_ping(self, arg):               # /ping typed by the user (arg = text after the command)
        self.api.log(f"pings answered: {self.api.get('count', 0)}")

    def on_message(self, msg):
        # msg keys: channel ('#name' / 'Public' / '@Person' for DMs), channel_idx, nick, text, snr, hops, raw, dm (DMs only)
        if not self.api.get("enabled", False) or msg.get("dm"):
            return
        if msg["text"].strip().lower() == "!ping":
            self.api.set("count", self.api.get("count", 0) + 1)
            self.api.send(msg["channel"], f"@[{msg['nick']}] pong")

    def build_options(self, parent):       # optional Options page
        self.v = tk.BooleanVar(value=self.api.get("enabled", False))
        f = tk.Frame(parent, bg=parent["bg"])
        tk.Checkbutton(f, text="Answer !ping", variable=self.v, bg=parent["bg"]).pack(anchor="w")
        return f

    def apply_options(self):               # Options > OK / Apply
        self.api.set("enabled", self.v.get())
```

Other hooks: `on_connect`, `on_disconnect`, `on_demo` (fake data for `--demo`). Extras on `self.api`:
`write(window, text, tag)` (your own window), `add_menu_item`, `add_toolbar_button`, `add_map_layer(label, provider, color)`
with `provider() -> [(lat, lon, label)]`, `nodes` (every node seen), `channel_index(name)`, `after(ms, fn)`, `connected`.
Full commented reference: `addons/_example_addon.py`. Real examples: `packages/auto_reply/`, `packages/broadcast_alerts/`.

Mentions in replies: write `@[Nick Name]` (MeshCore convention) so the person's client highlights it.
Messages are short (about 120 characters per packet): keep replies brief and split long text yourself.

## Test it (all of these, in order)

```bash
python packages/check_package.py packages/<name>     # must print only PASS
python mcIRC.py --demo                               # no radio needed; Tools > Addons > install from folder, enable, try it
```

`check_package.py` validates the manifest, destinations, syntax, that the class loads and that `on_load`/`on_unload`/
`on_message`/`build_options` run. It never calls `on_connect`. Demo mode uses fake nodes and never transmits.
Fix every FAIL before submitting. Do not use the user's real serial port while their mcIRC is running.

## Submit it

Pick whichever the person can do:

**A. Pull request (preferred)**
```bash
git clone https://github.com/<their-fork>/mcIRC && cd mcIRC     # fork https://github.com/bclml/mcIRC first
git checkout -b addon-<name>
git add packages/<name>
git commit -m "Add <title> addon"
git push -u origin addon-<name>
gh pr create --repo bclml/mcIRC --fill     # the PR template has the checklist; tick only what is true
```
Only `packages/<name>/` should change in the PR.

**B. Issue** - open "Addon submission" at https://github.com/bclml/mcIRC/issues/new/choose with the name, what it does,
a link to the code, and how it was tested.

Ask the person before pushing, forking or opening anything on their behalf; do not publish under their account unprompted.

## What happens next

A maintainer reads the code, runs `check_package.py`, tries it in demo mode and on a real node if it transmits, then adds it
to `addons-catalog.json` so it appears in **Tools > Addons > Browse online catalog**. Review checklist: `docs/ADDONS.md`.

## Self-check before you say "done"

- [ ] Only `packages/<name>/addon.json` and `packages/<name>/<name>.py` were added
- [ ] `check_package.py` passes; demo mode run
- [ ] Transmits nothing unless the user enabled it; rate-limited; no loops
- [ ] `on_unload` stops every thread/timer
- [ ] No secrets, no real names/keys/coordinates
- [ ] Settings via `self.api.get/set`, slow work via `run_background`
- [ ] You told the person what was and was not tested
