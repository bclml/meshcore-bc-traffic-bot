# mcIRC

An **mIRC-style chat client for [MeshCore](https://meshcore.co.uk/) LoRa mesh nodes** for Windows (and, experimentally, Linux and macOS): channels and direct messages in the classic treebar / switchbar / nick-list layout, a node list and map, full control of your node's settings, per-window log files, and **addons** for anything more. It works with any board running MeshCore Companion firmware over USB, Bluetooth or WiFi.

Optional addons add features - for example **BC traffic bot** turns mcIRC into a BC traffic / ferry / transit / weather / earthquake / tsunami alert bot (see [BC alerts bot](#bc-alerts-bot)).

## Screenshots

*(Demo mode with made-up data - `python mcIRC.py --demo`. Regenerate with `python docs/make_screenshots.py`.)*

**Channels, alerts and the treebar** - unread windows are red, just like the old days.

![mcIRC channel window](docs/images/chat.png)

**Direct messages** - private `@name` windows with a switchbar of small buttons that turn red when unread. People, repeaters and room servers appear only on this bar (repeaters blue, room servers purple); the window tree on the left holds just Status and the channels. Drag the grip to dock the bar at the top, bottom or either side, or let it float.

![mcIRC direct messages](docs/images/direct-messages.png)
![mcIRC switchbar docked on the left](docs/images/switchbar-docked.png)

**Slash commands** - the command list pops up as you type `/`, and a repeater's private window accepts the MeshCore CLI.

![mcIRC command list](docs/images/commands.png)

**Themes and @mentions** - mentions and highlight words stand out; five colour themes.

![mcIRC Night theme with mentions](docs/images/theme-night.png)

**Map** - every node your radio has ever heard (more than its 350-contact limit), with toggles for node types, age and addon layers.

![mcIRC map](docs/images/map.png)

**Your node's settings, device scanning and the addon catalog**

![Node settings](docs/images/node-settings.png)
![Scan for devices](docs/images/device-scan.png)
![Addon catalog](docs/images/addons-catalog.png)
![Auto reply options](docs/images/auto-reply-options.png)

## Requirements

- Windows 10/11 + Python 3. **Linux and macOS** also run it (*experimental, edition 0.1.0* - see below).
- A LoRa node running **MeshCore Companion firmware**, connected by USB, Bluetooth or WiFi/TCP. Tested on a Heltec V3; the Companion protocol is the same on every board (Heltec V4/T114, LilyGo T-Beam/T-Deck/T-Echo, RAK WisBlock, Seeed Xiao/Wio Tracker, Station G2, ...), and *Scan for devices* in mcIRC finds yours. Repeater / Room-server firmware can't chat - flash the Companion build.
- The Python packages are listed in [`requirements.txt`](requirements.txt) (`pip install -r requirements.txt`) and in [`pyproject.toml`](pyproject.toml), so [uv](https://docs.astral.sh/uv/) works too: `uv sync` then `uv run mcIRC.py`

## Linux and macOS (experimental, edition 0.1.0)

The same code runs on Linux and macOS; it has had far less testing there, so expect rough edges and please report them (**Help > Report a bug**, with a screenshot if it is about how something looks).

1. Install Python 3 with Tk: Debian/Ubuntu `sudo apt install python3-tk`, Fedora `sudo dnf install python3-tkinter`, macOS `brew install python-tk` (or the python.org installer).
2. `pip install -r requirements.txt`  (or with uv: `uv run mcIRC.py`)
3. **Linux USB:** your user must be allowed to use serial ports: `sudo usermod -aG dialout $USER` (some distros: `uucp`), then log out and in again. Ports look like `/dev/ttyUSB0` or `/dev/ttyACM0`. **macOS USB:** ports look like `/dev/cu.usbserial-…`.
4. Start it with `./Run_GUI.sh` (Linux) or double-click `Run_GUI.command` (macOS; the first time: `chmod +x Run_GUI.command Run_GUI.sh`), or run `python3 mcIRC.py`. Try `python3 mcIRC.py --demo` first.

Known differences: macOS ignores button colours (the "red when unread" switchbar buttons are less obvious there, the tree still turns red), right-click is Control-click on a one-button mouse, and notification sounds use `afplay` (macOS) or `canberra-gtk-play` / `paplay` (Linux). Bluetooth works through `bleak` but is untested on both.

## Quick start (Windows)

1. Clone this repo (or download it) into its own folder
2. `pip install -r requirements.txt`  (or with uv: `uv run mcIRC.py` does this for you)
3. Double-click `Run_GUI.bat` (no console window stays open). Try it without a radio first: `python mcIRC.py --demo`
4. Options > Connect > *Scan for devices...* > pick your node > OK, then File > Connect

## Features

`Run_GUI.bat` (or `python mcIRC.py`) opens a desktop client for your node - no console window stays open. Try it without a radio: `python mcIRC.py --demo`. Use either the GUI **or** `Run_Agent.bat`: only one program can hold the node's COM port.

- **Chat** - a treebar of channels, per-channel windows with topic bar and nick list, a status window and an input line (`/help` lists commands). **Direct messages** open as `@name` windows (double-click a name, `/query`, `/msg`, or double-click a node in the node list). A **switchbar** of square buttons along the top has one per window and turns red when a window has unread messages.
- **Slash commands** - type `/` and the matching commands pop up above the input line as you type. In a private window with a **repeater or room server**, the MeshCore CLI commands (`/reboot`, `/ver`, `/neighbors`, `/get radio`, `/clock sync`, `/stats-core`, ... about 40 of them, with `/login <admin password>` when needed) are sent to that node; elsewhere the same names and every meshcli command (`/contacts`, `/advert`, `/get name`, ...) run on your own node. `/meshcli <anything>` runs any meshcli command. Dangerous ones (reboot, erase, power off) ask first.
- **@mentions, themes and sounds** - `@nickname` and `@[nick name]` are highlighted in messages (stronger when it is your name), plus your own highlight words. Five colour themes (Classic mIRC, Night, Terminal, Ocean, Paper) and notification sounds for private messages, mentions and highlight words (Options > Display / Sounds).
- **Right-click menus** - on channels in the window tree, on the private-message buttons (people, repeaters, room servers) and on names in the nick list: node info, show on map, log in / status / reboot for repeaters, mark as read, clear, open log, close.
- **Troubleshooting log and bug reports** (optional screenshot included: it hides chat text, names and the button bar unless you untick that) - each run writes a log to `diagnostics/` (the last 5 runs are kept): versions, USB serial ports, connection steps, which meshcli commands worked or failed, your node's firmware and radio settings, and errors. It never contains message text (public or private), passwords, your position, whole public keys or full Bluetooth/IP addresses. **Help > Report a bug...** (or `/bug`) lets you describe the problem, shows exactly what would be attached, then opens a pre-filled GitHub issue (the complete report is copied to the clipboard; you can also save it as a file). It is how problems with boards the maintainers don't own get fixed.
- **Logs** - every window is logged to its own text file in `logs/` (`#drivebc.txt`, `@Alice.txt`, ...), with session start/close lines like mIRC. After a restart the windows come back with their latest history (Options > Display).
- **Map** - every node the radio has told us about, with toggles for repeaters / companions / room servers / sensors, nodes remembered but no longer on the radio, "seen within N days", names, your node, and any layer an addon adds. Real OpenStreetMap tiles need `pip install tkintermapview` (otherwise a plain plot is shown).
- **Node memory** - a node only holds about 350 contacts. The GUI reads the radio's contact list every few minutes into `nodes.db` so the map and node list show more than the radio can, and forgets nodes that haven't been seen for N days (default 10; optionally also deletes them from the radio). Options > Nodes.
- **Instant advert updates** - between message polls mcIRC keeps a small listener connected to the node (USB and WiFi/TCP), so a node that advertises appears in the node list and on the map, and a private window that only had a key like `3ddcdf84` gets its real name, within about a second instead of at the next 5-minute radio read. The listener steps aside the moment anything else needs the radio, and catches up on what it missed afterwards. A node in manual-add mode shows new adverts as "waiting for approval". Options > Node memory has the two switches (listen / announce first sightings).
- **When the radio stops answering** - after 3 failed commands in a row a red **RADIO NOT RESPONDING** warning appears in the status bar and Status tells you what to do. Messages you type are marked **NOT SENT** in their window (instead of looking delivered) and fail at once instead of after a minute. Alerts from the BC traffic bot that could not be sent are kept for up to 30 minutes and go out in order as soon as the radio answers (a CLEARED notice cancels its still-waiting NEW notice). Optional, **off by default**: *Options > Connect > Restart a silent radio automatically* pulses the USB reset line after about 5 minutes of silence (at most twice per session, only for CP210x / CH340 / FTDI boards such as the Heltec V3 - never native-USB boards); **Tools > Reset radio via USB...** does it once on request.
- **This node's settings** - name, position, frequency / bandwidth / SF / CR, TX power, telemetry, advert policy, path hash mode and more, plus battery/airtime status, send-advert, clock sync and reboot (Options > Node: ...).

## Addons

Everything beyond chat is an **addon** - optional, and **none are installed by default**. Open **Tools > Addons**:

- **Browse online catalog** lists the addons that were reviewed and **tested** by the maintainers ([addons-catalog.json](addons-catalog.json)). Pick one and press Install.
- **Install from file / folder** installs an addon you downloaded or are developing.
- Currently in the catalog: **BC traffic bot** (`packages/broadcast_alerts`) - DriveBC, BC Ferries, BC Transit, TransLink, weather, earthquake and tsunami alerts sent to the mesh, with a master **mute** and a switch per alert type. If several stations in range run it, mute it or untick what you don't need so identical broadcasts don't pile onto the mesh. And **Auto reply** (`packages/auto_reply`) - keyword rules: pick the words, the channels they are heard on, the reply text and the channels the reply goes to (for example "test" on `#bot-van` gets a reply, "test" anywhere else is told to use `#bot-van`), with a per-person cooldown so replies never flood the mesh.

Write your own: copy `addons/_example_addon.py`, edit, Reload - see **[docs/ADDONS.md](docs/ADDONS.md)** for the hooks, API, packaging, a validator (`python packages/check_package.py packages/<name>`) and how an addon gets tested and added to the catalog.

## Updating

**Help > Check for updates** downloads the newest version from GitHub and installs it (the GUI also checks quietly once a day and tells you in the Status window). Your **settings, installed addons, logs and node memory are never touched**, files that get replaced are backed up first to `backup/`, and if you edited `emergency_agent.py` your copy is kept and the new one is saved beside it as `emergency_agent.py.new`. Installed addons are refreshed too when the new version ships a newer package.

## Feedback, ideas and contributing

Ideas and bug reports are very welcome - you don't need to write code:

- **Suggest an idea:** Help > Suggest an idea... in the app, or open an [Idea / suggestion](../../issues/new?template=idea.yml) issue.
- **Report a bug:** Help > Report a bug..., or a [Bug report](../../issues/new?template=bug_report.yml).
- **Share an addon:** build it (see [docs/ADDONS.md](docs/ADDONS.md)), then open an [Addon submission](../../issues/new?template=addon_submission.yml) or a pull request. Submitted addons are tested; the ones that pass are added to the catalog everyone can browse and download in the app.
- **Contribute code or docs:** see [CONTRIBUTING.md](CONTRIBUTING.md).

## BC alerts bot

The original purpose of this project: a bot that scans British Columbia's public transit, highway, ferry, weather, tsunami and earthquake feeds and broadcasts incidents over the mesh through your own node - no paid API subscriptions, no cloud service. In mcIRC it is the **BC traffic bot** addon (Tools > Addons > Browse online catalog); it also still runs standalone as the console agent (`Run_Agent.bat`, see [How to run.txt](How%20to%20run.txt)). The addon needs `pip install requests gtfs-realtime-bindings`.

### What it does

- **DriveBC** road/highway incidents and closures (every 1 min)
- **BC Ferries** service notices and live departure delays (every 1 min)
- **TransLink** (SkyTrain, Bus, SeaBus, West Coast Express) and **BC Transit** (9 regional operators) service-disrupting alerts (every 1 min) — filtered to suspensions/no-service/major delays, not cosmetic notices
- **Environment Canada** severe weather warnings + daily 6AM/8AM forecasts (every 10 min)
- **Tsunami** Warning/Watch alerts (NWS) and **earthquake** alerts (USGS), both filtered to BC relevance and broadcast to every channel — Public first — since they're urgent enough to reach everyone (every 1 min)

Each source routes to its own MeshCore channel (`#drivebc`, `#bcferries`, `#bctransit`, `#translink`, `#weather`) so listeners can subscribe to only what they care about, resolved by channel *name* at startup rather than a hardcoded index. New incidents broadcast immediately and get a follow-up `✅ CLEARED` message the moment they drop off the source feed. Restarting the agent replays its own log to rebuild state, so it never double-broadcasts an already-active incident.

The "test" auto-reply (so people can check their radio reaches the node) is a separate addon, **Auto reply**: keyword rules with their own reply texts and channels. The console agent still has a simple built-in test reply.

### Console agent quick start

1. Run `pip install requests meshcore-cli pyserial gtfs-realtime-bindings`
2. One-time node setup: create the 6 MeshCore channels (`drivebc`, `bcferries`, `bctransit`, `translink`, `weather`, `bot-van`) - see **[How to run.txt](How%20to%20run.txt)** section 4 for exact commands
3. Double-click `Run_Agent.bat`, answer the prompts (connection type, TransLink key, regional scope tags, frequency)

### Configuration

At the top of `emergency_agent.py`:

- `NODE_LOCATION_NAME` — used in log/reply context, customize per deployment
- `CHANNEL_NAMES` / `WEATHER_CHANNEL_NAME` — the MeshCore channel names each source routes to
- `LM` / `VI` / `SC` — the place-name keyword lists defining "Lower Mainland" / "Vancouver Island" / "Sunshine Coast" for region matching
- `EARTHQUAKE_MIN_MAGNITUDE` / `EARTHQUAKE_BC_BBOX` — earthquake alert threshold and bounding box

Region scopes (`[LML]`, `[VI]`, `[SC]`-style prefixes) and the TransLink API key are configured interactively at launch — nothing sensitive is hardcoded in the script.

### Adapting this for your own region

This was built for British Columbia specifically (DriveBC, BC Ferries, BC Transit, TransLink), but the overall pattern — poll public feeds, dedupe against a lifecycle-tracked state dict, route by channel name resolved at startup, broadcast within MeshCore's message-length limit — should port to any region with its own equivalent open data feeds. PRs welcome.

## License

[MIT](LICENSE) — use it, fork it, adapt it for your own region.
