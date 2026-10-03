# Addons

The GUI is a chat client for your MeshCore node. Everything else is an **addon**: optional, installed by you, and
switched on or off in **Tools > Addons**. No addon is installed by default.

## Installing

- **Browse online catalog** (Tools > Addons, or Help > Browse addons): the list of addons that were reviewed and
  tested by the maintainers. Pick one and press *Install / update*.
- **Install from file / folder**: for an addon you downloaded or are developing. Choose its `.zip`, its `addon.json`,
  its folder, or a single `.py` file.
- Updating the app (Help > Check for updates) also refreshes installed addons whose package has a newer version. Addon
  settings are stored in `gui_settings.json` and are never touched.

An addon runs Python code with full access to your PC. Install only addons you trust; the catalog lists only reviewed ones.

## Writing one

1. Copy `addons/_example_addon.py` to `addons/my_addon.py` (no leading underscore) and edit it.
2. Tools > Addons > *Reload*. Use `python mcIRC.py --demo` to try it without a radio (implement `on_demo` to fake data).
3. An addon is a class extending `AddonBase` with optional hooks:

| Hook | When |
|---|---|
| `on_load` / `on_unload` | enabled / disabled (start and stop your threads here) |
| `on_connect` / `on_disconnect` | the node connection came up / went down |
| `on_message(msg)` | a chat message or direct message arrived (`msg["dm"]` is true for DMs) |
| `on_tick` | every `tick_seconds` seconds |
| `build_options(parent)` / `apply_options` | adds a page to Options |
| `on_demo` | `--demo` mode only |

`self.api` gives you: `get/set` (saved settings), `log`, `write` (your own window), `send` (to a channel),
`add_command` (a `/slash` command), `add_menu_item`, `add_toolbar_button`, `add_map_layer`, `nodes` (every node ever
seen), `run_background`, `after`, `connected`. Hooks run on the GUI thread: put anything slow (radio, network) in
`run_background`. The fully commented `addons/_example_addon.py` shows each of them.

## Packaging (for sharing)

Put your addon in `packages/<name>/` with an `addon.json`:

```json
{
  "name": "my_addon",
  "title": "My addon",
  "version": "1.0.0",
  "description": "One sentence.",
  "requires": ["requests"],
  "files": { "my_addon.py": "addons/my_addon.py" }
}
```

`name` must match the file name in `addons/`. Files may only be written to `addons/<name>.py` or a plain `.py` next to the
app; core files (`mcIRC.py`, `meshcore_io.py`, `gui_*.py`) cannot be replaced by an addon.

## Testing before you submit

```
python packages/check_package.py packages/my_addon
```

checks the manifest, destinations, syntax, that the addon loads, and that `on_load` / `on_unload` run (it never calls `on_connect`, so no radio or network activity). Then try it in
`--demo` mode and, if it talks to the radio, on a real node. Never put secrets (API keys, passwords) in the code - use
`self.api.get/set` and an Options page.

## Submitting

1. Open an **Addon submission** issue (Help > Submit an addon...) or send a pull request that adds `packages/<name>/`.
2. A maintainer reviews the code and tests it (checklist below). Anything found is reported on the issue / PR.
3. Once it passes, the maintainer adds it to `addons-catalog.json`. From then on it shows up in
   *Browse online catalog* for everyone, and new versions you submit are re-tested before the catalog entry is bumped.

### Review and test checklist

- [ ] `check_package.py` passes
- [ ] Loads, runs, and unloads cleanly from a fresh install; disabling it leaves nothing running
- [ ] Works in `--demo` mode (no radio) or says clearly why it can't
- [ ] Tested on a real node if it transmits or reads from the radio
- [ ] Does not flood the mesh (rate-limited, has an off switch) and respects the mute convention where relevant
- [ ] No secrets, no hard-coded personal data, no network calls the description doesn't mention
- [ ] Settings and options are kept via `self.api.get/set`, not by editing files
- [ ] Description, version and author in `addon.json` are accurate

The catalog is deliberately short: *tested* matters more than *many*.
