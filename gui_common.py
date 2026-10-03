"""Shared settings, theme constants and helpers for the mIRC-style GUI (see mcIRC.py)."""
import json, os, re

import meshcore_io as ea

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "gui_settings.json")

DEFAULT_SETTINGS = {
    "mode": "usb", "port": "auto", "last_port": "", "baud": "", "ble_target": "", "tcp_host": "", "tcp_port": 5000, "node_name": "MyNode", "location": "Surrey",
    "poll_seconds": 20,
    "node_lat": 49.19, "node_lon": -122.85,
    "node_prune_days": 10,       # forget nodes not seen for this many days (0 = never)
    "node_sync_minutes": 5,      # how often to read the radio's contact list into long-term memory
    "radio_capacity": 350,       # how many contacts the radio itself can hold
    "prune_radio": False,        # also delete forgotten nodes from the radio itself
    "show_time": True, "font_size": 10, "auto_connect": False,
    "check_updates": True,       # look for a newer version at startup (at most once a day)
    "last_update_check": 0,
    "theme": "Classic mIRC", "highlight_words": "",
    "sounds_enabled": True, "sound_private": "Ding", "sound_mention": "Exclamation", "sound_highlight": "Question",
    "sound_channel": "None", "sound_custom": "", "closed_channels": [],
    "log_enabled": True,         # keep one .txt log per window in logs/
    "log_history": 200,          # how many log lines to show again after a restart
    "addons": {}, "addons_enabled": {},
}

# Classic Windows / mIRC palette
BG = "#d4d0c8"
TEXT_BG = "#ffffff"
FONT_FAMILY = "Courier New"
NICK_COLORS = ["#0000cc", "#009300", "#cc0000", "#7f007f", "#fc7f00", "#009393", "#7f0000", "#00007f", "#4b4b4b"]

# Display name -> (topic shown in the topic bar, purpose shown in the channel list)
CHANNELS = {
    "Public": "Public channel - tsunami/earthquake alerts and the weekly reminder",
    "#drivebc": "DriveBC highway incidents and closures (Open511) - checked every 60s",
    "#bcferries": "BC Ferries service notices and live departure delays - checked every 60s",
    "#bctransit": "BC Transit regional service alerts - checked every 60s",
    "#translink": "TransLink SkyTrain / SeaBus / major bus disruptions - checked every 60s",
    "#weather": "Environment Canada warnings and the daily 6AM/8AM forecasts",
    "#bot-van": "Test channel - send 'test' or 't' to check your signal reaches the node",
}

_EMOJI = {"\U0001F6A8": "[!]", "\U0001F30E": "[EQ]"}


def safe_text(text):
    """Tk on Windows can't always draw characters outside the BMP (most emoji) - swap them for plain text."""
    for k, v in _EMOJI.items(): text = text.replace(k, v)
    return re.sub(r'[\U00010000-\U0010FFFF]', '', text)


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f: s.update(json.load(f))
    except (OSError, ValueError): pass
    return s


def save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f: json.dump(s, f, indent=2)


def _norm(name): return name.lstrip("#").strip().lower()


def channel_index(display):
    """Node channel index for a display name like '#drivebc' / 'Public', or None if unresolved."""
    if display == "Public": return 0
    for name, idx in ea.CHANNEL_INDEX_BY_NAME.items():
        if _norm(name) == _norm(display): return idx
    return None


def display_for_index(idx):
    if idx == 0: return "Public"
    for name, i in ea.CHANNEL_INDEX_BY_NAME.items():
        if i == idx: return "#" + name.lstrip("#")
    return f"#channel{idx}"
