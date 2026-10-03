# mcIRC - instructions for AI coding assistants

mcIRC is an mIRC-style chat client for MeshCore mesh radios (Python 3 + tkinter, Windows). Chat is the core; every other
feature is an **addon**.

- **Asked to write or submit an addon?** Follow [docs/AI_ADDON_GUIDE.md](docs/AI_ADDON_GUIDE.md) exactly. Short version: create
  only `packages/<name>/addon.json` and `packages/<name>/<name>.py` (class `Addon(AddonBase)`), run
  `python packages/check_package.py packages/<name>` and `python mcIRC.py --demo`, then open a pull request to `bclml/mcIRC`
  (ask the person first). Never edit core files (`mcIRC.py`, `meshcore_io.py`, `gui_*.py`) from an addon, never edit
  `addons-catalog.json` (maintainers do that after review).
- **Rules:** transmit nothing unless the user enabled it, rate-limit everything, off by default; no secrets or personal data;
  slow work in `self.api.run_background`; stop all threads in `on_unload`; report honestly what you did not test.
- Other contributions: see [CONTRIBUTING.md](CONTRIBUTING.md). API reference: [docs/ADDONS.md](docs/ADDONS.md) and
  `addons/_example_addon.py`.
