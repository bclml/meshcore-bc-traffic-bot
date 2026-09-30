# MeshCore BC Traffic Bot

A zero-configuration Windows agent that scans British Columbia's public transit, highway, ferry, weather, tsunami, and earthquake feeds and broadcasts incidents over a [MeshCore](https://meshcore.co.uk/) LoRa mesh network via a Heltec V3 node — no paid API subscriptions, no cloud service, runs entirely on your own PC.

## What it does

- **DriveBC** road/highway incidents and closures (every 1 min)
- **BC Ferries** service notices and live departure delays (every 1 min)
- **TransLink** (SkyTrain, Bus, SeaBus, West Coast Express) and **BC Transit** (9 regional operators) service-disrupting alerts (every 1 min) — filtered to suspensions/no-service/major delays, not cosmetic notices
- **Environment Canada** severe weather warnings + daily 6AM/8AM forecasts (every 10 min)
- **Tsunami** Warning/Watch alerts (NWS) and **earthquake** alerts (USGS), both filtered to BC relevance and broadcast to every channel — Public first — since they're urgent enough to reach everyone (every 1 min)

Each source routes to its own MeshCore channel (`#drivebc`, `#bcferries`, `#bctransit`, `#translink`, `#weather`) so listeners can subscribe to only what they care about, resolved by channel *name* at startup rather than a hardcoded index. New incidents broadcast immediately and get a follow-up `✅ CLEARED` message the moment they drop off the source feed. Restarting the agent replays its own log to rebuild state, so it never double-broadcasts an already-active incident.

A `#bot-van` channel (or any channel) also supports a "test"/"t" auto-reply so people can confirm their radio is reaching the node.

## Requirements

- Windows 11 (or similar) + Python 3
- A Heltec V3 LoRa node running MeshCore firmware, connected via USB or Bluetooth
- `pip install requests meshcore-cli pyserial gtfs-realtime-bindings`
- (Optional) a free [TransLink API key](https://developer.translink.ca/Account/Register) for SkyTrain/Bus/SeaBus/WCE alerts — BC Transit needs no key

## Quick start

1. Clone this repo (or download the 3 files) into its own folder
2. Run `pip install requests meshcore-cli pyserial gtfs-realtime-bindings`
3. One-time node setup: create the 6 MeshCore channels (`drivebc`, `bcferries`, `bctransit`, `translink`, `weather`, `bot-van`) — see **[How to run.txt](How%20to%20run.txt)** section 4 for exact commands
4. Double-click `Run_Agent.bat`, answer the prompts (connection type, TransLink key, regional scope tags, frequency)

Full install/setup walkthrough, including Python setup and channel provisioning: **[How to run.txt](How%20to%20run.txt)**

## Configuration

At the top of `emergency_agent.py`:

- `NODE_LOCATION_NAME` — used in log/reply context, customize per deployment
- `CHANNEL_NAMES` / `WEATHER_CHANNEL_NAME` — the MeshCore channel names each source routes to
- `LM` / `VI` / `SC` — the place-name keyword lists defining "Lower Mainland" / "Vancouver Island" / "Sunshine Coast" for region matching
- `EARTHQUAKE_MIN_MAGNITUDE` / `EARTHQUAKE_BC_BBOX` — earthquake alert threshold and bounding box

Region scopes (`[LML]`, `[VI]`, `[SC]`-style prefixes) and the TransLink API key are configured interactively at launch — nothing sensitive is hardcoded in the script.

## Adapting this for your own region

This was built for British Columbia specifically (DriveBC, BC Ferries, BC Transit, TransLink), but the overall pattern — poll public feeds, dedupe against a lifecycle-tracked state dict, route by channel name resolved at startup, broadcast within MeshCore's message-length limit — should port to any region with its own equivalent open data feeds. PRs welcome.

## License

[MIT](LICENSE) — use it, fork it, adapt it for your own region.
