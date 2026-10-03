import os, sys, asyncio, re, logging, requests, subprocess, datetime, base64, html, json, time, threading, serial.tools.list_ports
from logging.handlers import RotatingFileHandler
import meshcore_io as io
from meshcore_io import (execute_mesh_command, _emit, MESH_LOCK, MESH_MSG_MAX_CHARS, DEFAULT_CHANNEL_IDX, _TEST_SENDER_RE, auto_detect_usb_port,
                         auto_detect_ble_device, resolve_channel_indices, fetch_incoming_messages, split_sender, redact, RedactFilter,
                         get_radio_params, set_radio_frequency)

# GTFS-Realtime (protobuf) support for TransLink and BC Transit service alerts. Both agencies'
# alert *websites* (translink.ca/translink/alerts, alerts.bctransit.com) are JavaScript-only
# single-page apps that render "Loading..." / "JavaScript Required" to a plain HTTP GET, so no
# amount of HTML scraping can ever pull real alerts from them. Their official machine-readable
# feeds use the GTFS-Realtime protobuf format instead, which requires this library to decode.
# Install with: pip install gtfs-realtime-bindings
try:
    from google.transit import gtfs_realtime_pb2
    GTFS_RT_AVAILABLE = True
except ImportError:
    GTFS_RT_AVAILABLE = False

ALERT_LOCATIONS = {}  # "<source>|<guid>" -> (lat, lon, label) for alerts that carry coordinates (DriveBC); shown on the GUI map
EARTHQUAKE_EVENTS = []  # recent BC-relevant quakes as (lat, lon, magnitude, place) for the GUI map

# Broadcast switches (driven by the GUI's "BC traffic bot" addon; the console agent leaves everything on).
# Muting only stops TRANSMITTING: feeds keep being read and incident state keeps updating, so unmuting
# never floods the mesh with alerts that were suppressed in the meantime.
TX_SOURCES = ["DriveBC", "BC Ferries", "BC Transit", "TransLink", "Weather", "Earthquake", "Tsunami", "Weekly reminder", "Test reply"]
TX = {"muted": False, "sources": {k: True for k in TX_SOURCES}}

def tx_allowed(kind):
    if not TX["sources"].get(kind, True): return False
    if TX["muted"]: return False  # master mute silences EVERYTHING, tsunami included - many stations run this bot, and they must not all transmit the same alert at once
    return True

def _tx_kind(source):
    return "Weather" if source.startswith("Weather Warning:") or source in ("WX_6AM", "WX_8AM") else source


# --- SYSTEM PATHS & COMS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE_PATH = os.path.join(BASE_DIR, 'emergency_agent.log')
TRANSLINK_API_KEY = None  # free key from https://developer.translink.ca/Account/Register
USE_SCOPES = False
REGION_SCOPES = {"Lower Mainland": "", "Vancouver Island": "", "Sunshine Coast": ""}

# --- MULTI-CHANNEL ROUTING ---
# Per request: each source broadcasts to its own channel instead of everything going out on one
# shared channel, so operators can subscribe to only the categories they care about.
# NOTE: channel *indices* are deliberately NOT hardcoded here. meshcore-cli assigns a channel to
# whatever free slot happens to be available when it's created with `add_channel` (see "How to
# run.txt"), so the same channel name can land at a different index on different nodes/setups.
# Instead, the node's actual channel list is fetched once at startup (resolve_channel_indices(),
# via `.get_channels`) and channel names below are looked up by NAME against that real list.
CHANNEL_NAMES = {
    "DriveBC": "drivebc",
    "BC Ferries": "bcferries",
    "BC Transit": "bctransit",
    "TransLink": "translink",
}
WEATHER_CHANNEL_NAME = "weather"  # covers Environment Canada warnings + the daily 6AM/8AM forecast broadcasts

_missing_channel_warned = set()  # so the "channel not found, falling back" warning only logs once per channel, not every single broadcast

# --- WEEKLY PUBLIC-CHANNEL REMINDER ---
last_weekly_ad_sent = None  # (iso_year, iso_week) tuple of the last week this successfully went out, so re-checking every 10 min within the same hour doesn't resend it

# --- INCOMING "test" MESSAGE AUTO-REPLY ---
# NOTE: per MeshCore's own payload format docs, channel/group messages don't carry a separate
# structured "sender" field the way direct messages do (which resolve a pubkey to a saved contact
# name) — the protocol instead embeds the sender's name directly IN the message text itself, as
# "<sender name>: <message body>". So incoming channel messages have to be split on the first ":"
# to recover who actually sent it; there's no more reliable field available for this.
MESSAGE_POLL_SECONDS = 20  # how often to check for new incoming messages via `.sync_msgs`
NODE_LOCATION_NAME = "Surrey"  # physical location of this radio, used in the auto-reply text
_recent_test_replies = set()  # small dedup guard: (channel_idx, sender_timestamp, text) tuples already replied to, in case a message is ever re-delivered across polls

# Tracks the date each region's daily forecast last broadcast successfully (region -> "YYYY-MM-DD"),
# so a single region's failed request doesn't lose that day's forecast for every region — it just
# retries on the next 10-minute weather cycle within the same hour instead of waiting 24h.
last_6am_sent = {}
last_8am_sent = {}

# --- GEOGRAPHIC REGIONS (Broad Location Profiles) ---
LM = "lower mainland,langley,surrey,vancouver,burnaby,coquitlam,richmond,delta,abbotsford,chilliwack,maple ridge,pitt meadows,peace arch,pacific highway,aldergrove,abbotsford-huntingdon,skytrain,seabus,translink,highway 10,hwy 10,fraser highway,lougheed,west coast express,wce,waterfront,moody centre,port coquitlam,maple meadows,port haney,mission city,willingdon,douglas rd,grandview,cariboo,gaglardi,cape horn,brunette,kensington,boundary rd,ironworkers,boundary,massey,alex fraser,patullo,port mann,united blvd,highway 7b,hwy 7b,highway 15,golden ears,castle park,citadel,mary hill,pitt river,new westminster,port moody,anvil island".split(",")
VI = "vancouver island,victoria,nanaimo,duncan,comox,campbell river,malahat,pat bay,gabriola,quinsam,salt spring,bowen,quadra,cortes,texada,denman,hornby,alert bay,sointula,haida gwaii,klemtu,bella bella,prince rupert,duke point,swartz bay,departure bay,mill bay".split(",")
SC = "sunshine coast,sechelt,gibsons,langdale,earls cove,saltery bay,powell river,texada,lund,halfmoon bay,pender harbour,secret cove".split(",")
TARGET_REGIONS = LM + VI + SC

TRANSIT_KEYWORDS = "disruption,detour,delay,cancelled,suspended,station closed,elevator,crowding,blockage,maintenance".split(",")

# --- TSUNAMI WARNING/WATCH: ALL-CHANNEL BROADCAST ---
# Source: NWS National Tsunami Warning Center (Palmer, AK) public Atom feed — this is the office
# actually responsible for Alaska, British Columbia, and the US West Coast (confirmed via a live
# fetch: one of its own product parameters literally reads "Public Tsunami Information Statements
# for AK, BC, and US West Coast"), not a generic US-only feed.
TSUNAMI_FEED_URL = "https://www.tsunami.gov/events/xml/PAAQAtom.xml"
TSUNAMI_ALERT_CATEGORIES = ("warning", "watch")  # only these two — "Advisory" and "Information" are excluded per request
# TARGET_REGIONS (LM+VI+SC place names, defined just above) already covers most BC place names;
# these extra terms catch broader/provincial phrasing the CAP area description tends to use instead
# of specific city names (e.g. "Coastal British Columbia" rather than "Victoria").
TSUNAMI_BC_KEYWORDS = TARGET_REGIONS + ["british columbia", "b.c.", "canada", "bc coast", "pacific coast of canada", "west coast of canada"]
_tsunami_broadcast_ids = set()  # entry <id> values already broadcast, so re-polling the same still-active Warning doesn't resend it every cycle

# --- EARTHQUAKE: PUBLIC-CHANNEL BROADCAST ---
# Source: USGS's public real-time GeoJSON feed — global coverage (USGS's ComCat catalog ingests
# contributed picks from regional networks, including the Pacific Northwest, for anything strong
# enough to be worth reporting), no API key needed, refreshed every 5 minutes. Filtered client-side
# to a BC-relevant bounding box and a minimum magnitude, same pattern as DriveBC's bbox queries and
# the tsunami CAP <areaDesc> filter above.
EARTHQUAKE_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
EARTHQUAKE_MIN_MAGNITUDE = 4.5  # BC's coast sees frequent M2-4 shocks that would spam Public if broadcast; this is roughly the "likely felt onshore" threshold
# Generous box covering onshore BC plus the offshore Cascadia subduction zone and Haida Gwaii fault
# — where earthquakes actually relevant to BC residents originate, not just the province's own land
# borders. (min_lon, min_lat, max_lon, max_lat)
EARTHQUAKE_BC_BBOX = (-136.0, 47.5, -120.0, 60.5)
_earthquake_broadcast_ids = set()  # USGS event ids already broadcast — an earthquake is a one-time event, never "cleared" like a DriveBC closure

# Open511 bbox format is "west,south,east,north". Deliberately generous/overlapping — region-keyword
# matching (check_regions_match) still runs afterward as a second filter, so a bbox that's a bit too
# large just means a few extra out-of-region events get fetched-then-discarded; a bbox that's too
# small would silently miss real events the way the old unbounded province-wide query did. NOTE: the
# VI keyword list above also includes far-north coastal names (Haida Gwaii, Prince Rupert, Bella
# Bella, Klemtu) that fall well outside any of these three boxes — those specific areas aren't
# covered by this bbox-based fetch and would need a 4th North Coast box if that ever matters.
DRIVEBC_REGION_BBOXES = {
    "Lower Mainland": "-123.4,48.95,-121.5,49.6",
    "Vancouver Island": "-125.8,48.3,-123.0,50.9",
    "Sunshine Coast": "-124.8,49.3,-123.4,50.1",
    # Covers Haida Gwaii, Prince Rupert, Bella Bella, and Klemtu — the VI keyword list (see VI
    # above) already matches these place names in check_regions_match, but none of the other 3
    # boxes' coordinates reach this far north, so events up here were being fetched by neither
    # query and silently dropped regardless of keyword matching.
    "North Coast": "-132.6,51.9,-127.7,54.5",
}

# Some DriveBC roads[].from/to values are just another name for the highway itself (e.g.
# from="Trans-Canada Highway" on a "Highway 1" event) rather than a real cross-street or exit —
# confirmed live on a Langley Highway 1 incident where this made the broadcast location both
# uninformative AND long enough to force the description to get cut off. Filtered out below;
# roads[].direction (single-letter code) is used as a fallback so there's still *some* location detail.
DRIVEBC_DIRECTION_WORDS = {"N": "Northbound", "S": "Southbound", "E": "Eastbound", "W": "Westbound",
                           "NB": "Northbound", "SB": "Southbound", "EB": "Eastbound", "WB": "Westbound"}
_DRIVEBC_GENERIC_ALIAS_RE = re.compile(r'trans.?canada', re.IGNORECASE)

def _clean_drivebc_loc_bit(text, road_name):
    t = (text or "").strip()
    if not t or _DRIVEBC_GENERIC_ALIAS_RE.search(t) or t.lower() == (road_name or "").strip().lower():
        return ""
    return t

# BC Transit operator IDs covering the regions this agent monitors, from
# https://www.bctransit.com/open-data/ ("Real-time Service Alerts" column). Each feed is
# already scoped to that operator's own service area, so no further region-keyword filtering
# is needed or wanted for these — an unmatched keyword shouldn't be allowed to silently drop
# a real alert for a region we deliberately picked.
BCTRANSIT_OPERATORS = {
    "48": "Victoria", "41": "Nanaimo", "45": "Comox Valley", "10": "Cowichan Valley",
    "39": "Salt Spring Island", "12": "Campbell River", "11": "Port Alberni",
    "29": "Powell River", "18": "Sunshine Coast",
}

BLOCKLIST = [
    "partnership", "help us improve", "sign up", "change transit system", "closest transit",
    "found near you", "feedback", "newsletter", "download", "customer service", "fifa world cup",
    "what's new", "translink on x", "email updates", "performance review", "annual report",
    "summary report", "business plan", "financial", "critical alerts", "advisory alerts",
    "service alerts", "transit alerts", "active alerts", "current alerts", "schedules", "maps",
    "temporarily closed", "temporary closure", "temporarily moved", "temporary move", "detour"
]

# --- WEATHER FEEDS ---
# BUG FIX: this used to point all three regions at the plain weather.gc.ca homepage, which has no
# <item> tags at all — scrape_weather_warnings() had nothing to parse every single cycle, which is
# exactly why a real heavy rainfall warning never got broadcast. Confirmed live (via the browser,
# since a plain fetch of these URLs came back blank through this session's other web tools) that EC
# publishes real per-forecast-zone alert feeds at weather.gc.ca/rss/battleboard/<zone-code>_e.xml —
# at the moment of checking, bcrm1516_e.xml (Metro Vancouver - central) had an active "YELLOW
# WARNING - RAINFALL" entry, matching what the user's phone had just alerted on. Each broad region
# here needs several of these zone feeds, not one — a warning for, say, Surrey doesn't show up on
# the Greater Victoria feed at all, they're independent per-zone documents.
WEATHER_LOCATIONS = {
    "Lower Mainland": {"lat": 49.19, "lon": -122.85, "feeds": [
        "https://weather.gc.ca/rss/battleboard/bcrm1516_e.xml",  # Metro Vancouver - central (Vancouver/Burnaby/New West)
        "https://weather.gc.ca/rss/battleboard/bcrm28_e.xml",    # Metro Vancouver - North Shore
        "https://weather.gc.ca/rss/battleboard/bcrm30_e.xml",    # Metro Vancouver - southeast (Surrey/Langley)
        "https://weather.gc.ca/rss/battleboard/bcrm31_e.xml",    # Metro Vancouver - southwest (Richmond/Delta)
        "https://weather.gc.ca/rss/battleboard/bcrm1517_e.xml",  # Metro Vancouver - northeast (Coquitlam/Maple Ridge)
        "https://weather.gc.ca/rss/battleboard/bcrm1519_e.xml",  # Fraser Valley - central (Chilliwack)
        "https://weather.gc.ca/rss/battleboard/bcrm16_e.xml",    # Fraser Valley - east (Hope)
        "https://weather.gc.ca/rss/battleboard/bcrm17_e.xml",    # Fraser Valley - west (Abbotsford)
    ]},
    "Vancouver Island": {"lat": 48.42, "lon": -123.36, "feeds": [
        "https://weather.gc.ca/rss/battleboard/bc43_e.xml",      # Greater Victoria
        "https://weather.gc.ca/rss/battleboard/bcrm1510_e.xml",  # East VI - Duncan to Nanaimo
        "https://weather.gc.ca/rss/battleboard/bcrm1511_e.xml",  # East VI - Nanoose Bay to Fanny Bay
        "https://weather.gc.ca/rss/battleboard/bcrm1512_e.xml",  # East VI - Courtenay to Campbell River
        "https://weather.gc.ca/rss/battleboard/bc44_e.xml",      # West Vancouver Island
        "https://weather.gc.ca/rss/battleboard/bc45_e.xml",      # Inland Vancouver Island
        "https://weather.gc.ca/rss/battleboard/bc47_e.xml",      # North Vancouver Island
    ]},
    "Sunshine Coast": {"lat": 49.47, "lon": -123.75, "feeds": [
        "https://weather.gc.ca/rss/battleboard/bcrm1513_e.xml",  # Sunshine Coast - Gibsons to Earls Cove
        "https://weather.gc.ca/rss/battleboard/bcrm1514_e.xml",  # Sunshine Coast - Saltery Bay to Powell River
        "https://weather.gc.ca/rss/battleboard/bc41_e.xml",      # Howe Sound
    ]},
}
WEATHER_EMOJIS = {0: "☀️", 1: "☀️", 2: "⛅", 3: "☁️", 45: "🌫️", 48: "🌫️", 51: "🌦️", 53: "🌦️", 55: "🌦️", 61: "🌧️", 63: "🌧️", 65: "🌧️", 71: "❄️", 73: "❄️", 75: "❄️", 80: "🌧️", 81: "🌧️", 82: "🌧️", 95: "⛈️"}

# --- LOGGING SETUP ---
_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
# The log is also the bot's memory: after a restart it is replayed to learn which alerts are already announced (reload_active_alerts_from_log), so
# nothing but the real bot may write to it.  Tests, demo mode and tools set MCIRC_NO_LOG_FILE=1 (an invented "NEW" line would later be answered
# with a real "CLEARED" broadcast).
if os.environ.get("MCIRC_NO_LOG_FILE"): _file_handler = logging.NullHandler()
else: _file_handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
_file_handler.setFormatter(_log_formatter)



_file_handler.addFilter(RedactFilter())
console = logging.StreamHandler()
console.setFormatter(_log_formatter)
console.addFilter(RedactFilter())
logging.getLogger('').setLevel(logging.INFO)
logging.getLogger('').addHandler(_file_handler)
logging.getLogger('').addHandler(console)
# Kept as two separate state dicts so the traffic loop (1 min) and weather loop (10 min)
# can each detect their own new/cleared incidents without stepping on each other's state.
active_traffic_alerts = {}
active_weather_alerts = {}



def check_regions_match(text, is_transit=False):
    if not text: return False
    txt = " ".join(text.lower().split())
    if any(b in txt for b in BLOCKLIST): return False
    region_passed = any(r in txt for r in TARGET_REGIONS)
    if not region_passed: return False
    if is_transit: return any(k in txt for k in TRANSIT_KEYWORDS)
    return True

def clean_html_tags(raw_html):
    if not raw_html: return ""
    clean = re.sub(r'<[^>]*>', '', raw_html)
    return html.unescape(clean).strip()

def _is_open511_event_live(schedule):
    """DriveBC/Open511 events carry a 'schedule': {'intervals': ['<start>/<end>', ...]} field
    (verified via a live fetch of api.open511.gov.bc.ca) using ISO8601 'start/end' strings — end
    is blank for open-ended/ongoing events. status=ACTIVE alone isn't enough: some events are
    scheduled ahead of time (future start) or linger past their end before DriveBC's own status
    flips to ARCHIVED, so this checks 'now' actually falls inside the window. No schedule/intervals
    at all is treated as always-live, since that field is optional.
    BUG FIX: these interval timestamps are in UTC, not local time — confirmed by cross-referencing
    several events' `created` field (e.g. one created at "...T14:29:50-07:00" = 21:29:50 UTC had a
    schedule start of "...T21:29", matching UTC to the minute). This previously compared against
    datetime.datetime.now() (local/Pacific), which made every freshly-created event look ~7-8 hours
    in the future and get wrongly excluded as 'not live yet' — this is exactly why a real, active
    Massey Tunnel stalled-vehicle incident (severity MAJOR, created minutes earlier) never got
    broadcast. Now compared against UTC 'now' instead."""
    intervals = (schedule or {}).get("intervals") or []
    if not intervals: return True
    # datetime.utcnow() is deprecated (Python 3.12+); DriveBC's interval strings are naive (no
    # offset, per the BUG FIX note above) so `now` must stay naive too to compare against them —
    # .replace(tzinfo=None) keeps the exact same naive-UTC value utcnow() used to return.
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    for interval in intervals:
        start_s, _, end_s = interval.partition("/")
        start_s, end_s = start_s.strip(), end_s.strip()
        try:
            start = datetime.datetime.fromisoformat(start_s) if start_s else None
            end = datetime.datetime.fromisoformat(end_s) if end_s else None
        except ValueError:
            return True  # unparsable timestamp -> don't risk silently dropping a real event
        if start and now < start: continue  # scheduled for the future, not live yet
        if end and now > end: continue      # window already ended
        return True
    return False

# GTFS-Realtime Alert.Effect / Alert.SeverityLevel enum values (per the spec) used to decide
# what counts as "important" for feeds where that's requested. Effects that only ever describe
# minor/cosmetic issues (an elevator out, a stop moved a block, a schedule tweak) are excluded;
# service-disrupting effects and WARNING/SEVERE severity are always kept.
GTFS_IMPORTANT_EFFECTS = {1, 2, 3}      # NO_SERVICE, REDUCED_SERVICE, SIGNIFICANT_DELAYS (DETOUR excluded by request)
GTFS_IMPORTANT_SEVERITIES = {3, 4}      # WARNING, SEVERE
# Many real-world agency feeds (TransLink included, based on past experience with this feed type)
# don't reliably populate effect/severity_level on every alert, which would make a strict
# effect/severity-only filter throw away everything. Keyword text is used as a second signal so a
# genuinely major alert ("Suspended", "No Service") still gets through even if those fields are
# left blank, while cosmetic notices get dropped either way.
GTFS_IMPORTANT_KEYWORDS = "suspended,cancelled,canceled,no service,major delay,significant delay,emergency,reduced service,evacuat,derail,collision,disabled train,track fire,police incident,medical incident".split(",")
# "closed"/"closure" moved here by request: stop closures, station-entrance closures, and street
# closures are all excluded now — only "reduced service" (and anything else in
# GTFS_IMPORTANT_KEYWORDS/EFFECTS/SEVERITIES above) still gets through. Since minor-keyword
# exclusion is checked first in _is_important_gtfs_alert (see below), this wins outright even if
# the feed also flags the alert WARNING/SEVERE.
GTFS_MINOR_KEYWORDS = "elevator,escalator,washroom,accessibility,minor delay,schedule change,bike rack,fare,temporarily closed,temporary closure,temporarily moved,temporary move,detour,mechanical issue,mechanical problem,due to mechanical,closed,closure".split(",")

# BC Transit alerts sometimes carry nothing but a bare one-or-two-word status as header_text (e.g.
# "Delayed") with no routes and no description — a real observed broadcast, "Victoria: Delayed",
# said nothing about WHICH route or WHY. These generic bare headers are only skipped when there's
# no route info and no description to give them meaning; anything more specific still goes out.
GTFS_GENERIC_BARE_HEADERS = {"delayed", "delay", "detour", "detoured", "cancelled", "canceled",
                             "alert", "service alert", "service change", "notice", "disruption", "advisory"}

def _is_important_gtfs_alert(alert, header, desc):
    # Minor-keyword exclusion is checked FIRST and wins outright — this used to run after the
    # effect/severity check, so a detour (or temporary closure/move) that TransLink tagged with a
    # WARNING/SEVERE severity (agencies often set this broadly, not just for genuinely major
    # events) sailed straight past the keyword filter and got broadcast anyway. Explicitly
    # unwanted categories are now excluded no matter what severity/effect the feed attaches.
    txt = f"{header} {desc}".lower()
    if any(k in txt for k in GTFS_MINOR_KEYWORDS): return False
    if alert.effect in GTFS_IMPORTANT_EFFECTS or alert.severity_level in GTFS_IMPORTANT_SEVERITIES:
        return True
    return any(k in txt for k in GTFS_IMPORTANT_KEYWORDS)

# TransLink-specific scoping, per request: only SkyTrain, SeaBus, and *major* bus issues. There's
# no reliable route_type lookup available here (that requires downloading TransLink's static GTFS
# routes.txt, which isn't wired in), so mode is inferred from the alert text itself — TransLink's
# alert copy consistently names the line/mode explicitly ("Expo Line", "SeaBus", etc.), so this is
# a good proxy without that extra download.
GTFS_RAIL_SEABUS_KEYWORDS = "skytrain,expo line,millennium line,canada line,seabus,sea bus".split(",")
# Stricter bar for anything NOT recognized as SkyTrain/SeaBus (assumed to be a bus alert): only
# these count as "major" for a bus route. Routine bus delays/reroutes/reduced service — which would
# otherwise still pass the general _is_important_gtfs_alert bar above — are filtered out here.
GTFS_MAJOR_BUS_KEYWORDS = "suspended,no service,collision,police incident,medical incident,emergency,evacuat".split(",")

# BC Transit's "trip running late" alerts use a verbose canned template — "The following trip is
# operating approx. 35 minutes behind schedule on Friday, July 3rd, 2026. <route/time specifics>."
# — where the actual useful part (which route, what time, which stop) is a trailing clause AFTER
# all that boilerplate. A real broadcast got truncated mid-boilerplate ("...on Friday, July 3rd,
# 2026. 7…"), losing the specifics entirely even though the message technically fit the character
# budget. The date is redundant anyway (these are near-real-time alerts), so it's condensed down to
# just the delay amount + whatever specific detail follows, freeing up room for the part that matters.
_BCT_TRIP_DELAY_RE = re.compile(
    r'^\s*the following trip is operating approx\.?\s*(\d+)\s*minutes?\s*behind schedule on [^.]+\.\s*(.*)$',
    re.IGNORECASE
)

def _condense_bct_desc(desc):
    m = _BCT_TRIP_DELAY_RE.match(desc or "")
    if not m: return desc
    minutes, rest = m.group(1), m.group(2).strip()
    return f"{minutes} min late" + (f" - {rest}" if rest else "")

def _translink_mode_passes(title, desc):
    txt = f"{title} {desc}".lower()
    if any(k in txt for k in GTFS_RAIL_SEABUS_KEYWORDS): return True
    return any(k in txt for k in GTFS_MAJOR_BUS_KEYWORDS)

def _is_gtfs_alert_live(alert):
    """Per the GTFS-Realtime spec, Alert.active_period is a list of TimeRange(start, end) epoch-
    second windows during which the alert applies; an empty list means it's always active for as
    long as it's in the feed. Used to drop alerts scheduled for the future (not live yet) or whose
    window already ended (stale/expired) but that are still sitting in the feed."""
    if not alert.active_period: return True
    now_ts = datetime.datetime.now().timestamp()
    for period in alert.active_period:
        start = period.start if period.HasField("start") else None
        end = period.end if period.HasField("end") else None
        if start and now_ts < start: continue
        if end and now_ts > end: continue
        return True
    return False

def parse_gtfs_alerts(raw_bytes, source_label, important_only=False):
    """Decodes a GTFS-Realtime FeedMessage and yields (guid, title, description) per alert that
    is currently live (see _is_gtfs_alert_live) — future-scheduled and already-expired alerts are
    skipped. GTFS_MINOR_KEYWORDS (detours, temporary closures/moves, elevator/accessibility notices,
    etc.) are excluded for every source, not just TransLink — this used to only apply when
    important_only was set, so BC Transit's 9 regional feeds went out completely unfiltered. If
    important_only is True, additionally requires a real service-disrupting effect/severity/keyword
    match — see _is_important_gtfs_alert."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw_bytes)
    for entity in feed.entity:
        if not entity.HasField("alert"): continue
        alert = entity.alert
        if not _is_gtfs_alert_live(alert): continue
        header = alert.header_text.translation[0].text if alert.header_text.translation else ""
        desc = alert.description_text.translation[0].text if alert.description_text.translation else ""
        txt = f"{header} {desc}".lower()
        if any(k in txt for k in GTFS_MINOR_KEYWORDS): continue
        if important_only and not _is_important_gtfs_alert(alert, header, desc): continue
        # BUG FIX: route_id was only checked directly on the InformedEntity itself. Some agencies
        # (BC Transit's Trapeze/tmix feed included, apparently) instead nest it under the entity's
        # `trip` sub-message (TripDescriptor.route_id) for some alerts, which the old check silently
        # missed — losing real route info and leaving a bare, useless header like "Delayed" as the
        # only content. Both locations are checked now.
        routes = sorted({r for ie in alert.informed_entity
                          for r in filter(None, [ie.route_id, ie.trip.route_id if ie.HasField("trip") else ""])})
        # NOTE: this used to silently drop route info whenever header_text was blank (`header and
        # routes` required BOTH), collapsing distinct alerts down to a bare, uninformative "Service
        # Alert" title. That used to also accidentally hide duplicates under the old title-based
        # dedup key (several different alerts all rendering as the identical string "Service
        # Alert"); now that keying is guid-based they show up as the separate alerts they really
        # are, so giving each one an actually distinct/useful title matters more than before.
        # Route numbers alone are NOT treated as substantive content — a bare "Service Alert
        # (22-VIC, 38-VIC)" with no real header or description text is still an empty alert as far
        # as the person reading the broadcast is concerned, so it's skipped just like a fully blank
        # one rather than transmitted with route numbers as its only content.
        if header and routes: title = f"{header} ({', '.join(routes)})"
        elif header:
            # BUG FIX: a bare generic status word ("Delayed", "Detour", ...) with no route and no
            # description says nothing about WHAT is affected — a real broadcast, "Victoria:
            # Delayed", left the reader with no idea which route or why. Skipped in that specific
            # case; a more specific header (anything not in the generic set above) still goes out
            # on its own, same as before.
            if not routes and not desc and header.strip().lower() in GTFS_GENERIC_BARE_HEADERS:
                continue
            title = header
        elif desc: title = desc[:80] + ("…" if len(desc) > 80 else "")
        else: continue  # no real header or description text — nothing substantive to say
        guid = entity.id or f"{source_label}_{hash(title + desc[:20])}"
        yield guid, title, desc
def get_applicable_scope(text, forced_region=None):
    if forced_region and USE_SCOPES: return f"[{REGION_SCOPES.get(forced_region, '')}] "
    if not USE_SCOPES or not text: return ""
    txt, sc = text.lower(), []
    if any(k in txt for k in LM) and REGION_SCOPES["Lower Mainland"]: sc.append(REGION_SCOPES["Lower Mainland"])
    if any(k in txt for k in VI) and REGION_SCOPES["Vancouver Island"]: sc.append(REGION_SCOPES["Vancouver Island"])
    if any(k in txt for k in SC) and REGION_SCOPES["Sunshine Coast"]: sc.append(REGION_SCOPES["Sunshine Coast"])
    return f"[{' '.join(sc)}] " if sc else ""



def _channel_name_for_source(source):
    if source.startswith("Weather Warning:") or source in ("WX_6AM", "WX_8AM"):
        return WEATHER_CHANNEL_NAME
    return CHANNEL_NAMES.get(source)


def _resolve_channel_idx(source):
    """Returns None (never DEFAULT_CHANNEL_IDX/Public) when a source's channel can't be resolved —
    broadcast_via_cli treats None as "withhold this alert" rather than silently dumping it onto the
    Public channel. Public is reserved for the intentionally-Public-wide tsunami/earthquake alerts
    and weekly reminder (see broadcast_critical_all_channels / check_weekly_channel_ad); category
    alerts must never land there as a fallback (see resolve_channel_indices docstring for why this
    changed)."""
    name = _channel_name_for_source(source)
    if name is None: return None
    if name in io.CHANNEL_INDEX_BY_NAME: return io.CHANNEL_INDEX_BY_NAME[name]
    if name not in _missing_channel_warned:
        logging.warning(f"Channel '#{name}' not found on this node — {source} alerts will be withheld "
                         f"(not sent to Public) until it's created. Create it with `add_channel {name} <key>` "
                         f"(see How to run.txt) and restart to fix this.")
        _missing_channel_warned.add(name)
    return None

# Matches "[source] title" with an optional trailing " {id:GUID}" bookkeeping tag (see
# broadcast_via_cli) — the tag is never transmitted over the radio, it only exists in the log so
# reload_active_alerts_from_log() can key entries by the feed's own stable ID instead of title
# text. Older log lines written before this existed simply won't have the tag (group 3 is None),
# and fall back to a title-based key for just those legacy entries.
# BUG FIX: "Channel Index 1" was hardcoded here, back when everything broadcast on one shared
# channel. Now that each source routes to its own channel (see CHANNEL_NAMES/resolve_channel_indices
# above), the index varies by source and log line — matched generically as "Channel Index <any number>"
# so restart persistence keeps working for every channel, not just whichever used to be index 1.
_LOG_LINE_RE_TMPL = r'Broadcasting {} to Channel Index \d+:\s*\[(.*?)\]\s*(.*?)(?:\s\{{id:([^}}]*)\}})?$'

def reload_active_alerts_from_log():
    if not os.path.exists(LOG_FILE_PATH): return
    logging.info("Syncing active data profiles from log traces...")
    temp_active = {}
    new_re = re.compile(_LOG_LINE_RE_TMPL.format("NEW"))
    clear_re = re.compile(_LOG_LINE_RE_TMPL.format("CLEAR"))
    try:
        with open(LOG_FILE_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                nm = new_re.search(line)
                if nm:
                    src, ttl, guid = nm.group(1).strip(), nm.group(2).strip(), nm.group(3)
                    if "5-Day Summary:" not in ttl and "3-Day Forecast:" not in ttl:
                        key = f"{src}|{guid}" if guid else f"{src}|{ttl}"
                        temp_active[key] = (src, ttl)
                cm = clear_re.search(line)
                if cm:
                    src, ttl, guid = cm.group(1).strip(), cm.group(2).strip(), cm.group(3)
                    key = f"{src}|{guid}" if guid else f"{src}|{ttl}"
                    if key in temp_active: del temp_active[key]
        for key, (src, ttl) in temp_active.items():
            if src.startswith("Weather Warning:"): active_weather_alerts[key] = (src, ttl)
            else: active_traffic_alerts[key] = (src, ttl)
        logging.info(f"Sync complete. Restored {len(active_traffic_alerts)} traffic + {len(active_weather_alerts)} weather instances.")
    except Exception as e: logging.warning(f"Log sync failed: {e}")

def reload_critical_alert_ids_from_log():
    """Earthquake/tsunami broadcasts (see check_earthquake_warnings/check_tsunami_warnings) don't go
    through broadcast_via_cli's NEW/CLEAR log format at all — they're one-shot, so they log their own
    "... detected ... broadcasting to ... ({id})" line instead, and reload_active_alerts_from_log()
    above never looks for that line. BUG: this meant _earthquake_broadcast_ids/_tsunami_broadcast_ids
    started as an empty set on every restart, with nothing to say "already announced" — so restarting
    the agent re-broadcast every single earthquake/tsunami still sitting in the upstream feed's
    rolling window (2.5 days of M2.5+ quakes worldwide, for USGS), not just genuinely new ones. This
    is exactly why quakes already announced in a previous run were being announced all over again.
    Pre-populating both id sets from past log lines before the loops start fixes it the same way
    reload_active_alerts_from_log() already does for traffic/weather."""
    if not os.path.exists(LOG_FILE_PATH): return
    eq_re = re.compile(r'Earthquake M[\d.]+ detected near BC.*?\(([^)]+)\)\s*$')
    ts_re = re.compile(r'TSUNAMI \w+ detected affecting BC.*?\(([^)]+)\)\s*$')
    try:
        with open(LOG_FILE_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.rstrip('\n')
                m = eq_re.search(line)
                if m: _earthquake_broadcast_ids.add(m.group(1))
                m = ts_re.search(line)
                if m: _tsunami_broadcast_ids.add(m.group(1))
        logging.info(f"Restored {len(_earthquake_broadcast_ids)} earthquake + {len(_tsunami_broadcast_ids)} tsunami id(s) already announced in a previous run.")
    except Exception as e: logging.warning(f"Critical-alert id log sync failed: {e}")


def broadcast_via_cli(source, title, description, is_clear=False, forced_region=None, guid=None):
    # guid (when known) is the feed's own stable identifier (GTFS entity.id, DriveBC event id, BC
    # Ferries serviceNoticeCode) — logged as a trailing bookkeeping tag so restarts key off the
    # same stable ID instead of title text. It is deliberately left OUT of `msg`, so nothing extra
    # ever goes out over the air.
    prefix = get_applicable_scope(f"{title} {description}", forced_region)
    header = f"{prefix}✅ CLEARED [{source}]: " if is_clear else f"{prefix}🚨 NEW [{source}]: "
    body = title if is_clear else (f"{title} - {description}" if description else title)
    budget = max(MESH_MSG_MAX_CHARS - len(header), 20)
    if len(body) > budget:
        body = body[:budget - 1].rstrip() + "…"
    msg = header + body
    guid_tag = f" {{id:{guid}}}" if guid else ""
    chan_idx = _resolve_channel_idx(source)
    if not tx_allowed(_tx_kind(source)):
        # Same "Broadcasting ... to Channel Index N:" wording as a real send (prefixed so it's obvious in the
        # log) so reload_active_alerts_from_log() still sees this alert as already announced after a restart.
        logging.info(f"[MUTED - not transmitted] Broadcasting {'CLEAR' if is_clear else 'NEW'} to Channel Index {chan_idx if chan_idx is not None else 0}: [{source}] {title}{guid_tag}")
        return
    if chan_idx is None:
        # Never fall back to Public (0) for a category alert — see _resolve_channel_idx. The
        # warning about which channel is missing/unresolved was already logged there (once).
        logging.info(f"Withholding {'CLEAR' if is_clear else 'NEW'} [{source}] {title}{guid_tag} "
                      f"(channel not resolved yet)")
        return
    logging.info(f"Broadcasting {'CLEAR' if is_clear else 'NEW'} to Channel Index {chan_idx}: [{source}] {title}{guid_tag}")

    try:
        execute_mesh_command(io.CONNECTION_ARGS + ["chan", str(chan_idx), msg])
        _emit("out", chan_idx, msg, alert="clear" if is_clear else "new")
    except Exception as err:
        logging.error(f"Mesh CLI broadcast failed: {err}")
        io.queue_for_retry(chan_idx, msg, "clear" if is_clear else "new", guid, is_clear)       # sent later if the radio comes back in time

def handle_weather_broadcast(source, title, body, is_clear=False, specific_region=None):
    broadcast_via_cli(source, title, body, is_clear, specific_region)

async def broadcast_critical_all_channels(msg_body, label="ALERT", kind=None):
    """Sends msg_body, verbatim, to #public FIRST — that's where most listeners actually are, per
    request — then every category/testing channel currently resolved on this node. Used for alert
    types urgent enough to justify reaching every listener regardless of which single channel they
    happen to be subscribed to (tsunami Warning/Watch, earthquake), rather than routing to just one
    category channel like everything else. Public is guaranteed first here because channel 0 is
    always the lowest index and `targets` is sorted ascending — no special-casing needed."""
    if not tx_allowed(kind or ("Tsunami" if "TSUNAMI" in label else "Earthquake")):
        logging.info(f"[MUTED - not transmitted] {label}: {msg_body}")
        return
    msg = msg_body if len(msg_body) <= MESH_MSG_MAX_CHARS else msg_body[:MESH_MSG_MAX_CHARS - 1].rstrip() + "…"
    targets = sorted({0, *io.CHANNEL_INDEX_BY_NAME.values()})
    for idx in targets:
        try:
            cmd = ["public", msg] if idx == 0 else ["chan", str(idx), msg]
            execute_mesh_command(io.CONNECTION_ARGS + cmd)
            logging.info(f"Broadcasting {label} to Channel Index {idx}: {msg}")
            _emit("out", idx, msg, alert="critical")
        except Exception as err:
            logging.error(f"{label} broadcast failed on channel {idx}: {err}")
            io.queue_for_retry(idx, msg, "critical")
        await asyncio.sleep(BROADCAST_PACING_SECONDS)

async def check_tsunami_warnings():
    """Polls the NTWC's public Atom feed for active Warning/Watch-level tsunami messages. The
    Atom entry's own summary only gives the earthquake epicenter ("Affected Region"), not the list
    of actually-warned coastal zones, so for any Warning/Watch entry this also fetches the linked
    CAP XML document and checks its <areaDesc> list (plus headline/description) for BC place names
    before broadcasting — an Alaska- or Hawaii-only warning with no BC zone listed is skipped."""
    try:
        res = requests.get(TSUNAMI_FEED_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if res.status_code != 200:
            logging.error(f"Tsunami feed fetch failed: HTTP {res.status_code}")
            return
        for entry_m in re.finditer(r'<entry>(.*?)</entry>', res.text, re.DOTALL):
            entry = entry_m.group(1)
            id_m = re.search(r'<id>([^<]+)</id>', entry)
            entry_id = id_m.group(1).strip() if id_m else None
            if not entry_id or entry_id in _tsunami_broadcast_ids: continue
            cat_m = re.search(r'Category:\s*</strong>\s*([^<]+)', entry, re.IGNORECASE)
            category = (cat_m.group(1).strip() if cat_m else "").lower()
            if category not in TSUNAMI_ALERT_CATEGORIES: continue
            title_m = re.search(r'<title>([^<]*)</title>', entry)
            title = clean_html_tags(title_m.group(1)) if title_m else "Tsunami Alert"
            cap_m = re.search(r'<link rel="related"[^>]+href="([^"]+cap[^"]*)"', entry, re.IGNORECASE)
            area_text, headline, description = "", "", ""
            if cap_m:
                try:
                    cap_res = requests.get(cap_m.group(1), headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
                    if cap_res.status_code == 200:
                        area_text = " ".join(re.findall(r'<areaDesc>(.*?)</areaDesc>', cap_res.text, re.DOTALL))
                        hl_m = re.search(r'<headline>(.*?)</headline>', cap_res.text, re.DOTALL)
                        headline = clean_html_tags(hl_m.group(1)) if hl_m else ""
                        desc_m = re.search(r'<description>(.*?)</description>', cap_res.text, re.DOTALL)
                        description = clean_html_tags(desc_m.group(1)) if desc_m else ""
                except Exception as e:
                    logging.error(f"Tsunami CAP fetch failed: {e}")
            search_text = f"{title} {area_text} {headline} {description}".lower()
            if not any(k in search_text for k in TSUNAMI_BC_KEYWORDS): continue
            _tsunami_broadcast_ids.add(entry_id)
            msg = f"🚨 TSUNAMI {category.upper()}: {headline or title}"
            logging.info(f"TSUNAMI {category.upper()} detected affecting BC — broadcasting to all channels: {title} ({entry_id})")
            await broadcast_critical_all_channels(msg, "TSUNAMI ALERT", "Tsunami")
    except Exception as e:
        logging.error(f"Tsunami feed check failed: {e}")

def _in_earthquake_bbox(lon, lat):
    min_lon, min_lat, max_lon, max_lat = EARTHQUAKE_BC_BBOX
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat

async def check_earthquake_warnings():
    """Polls USGS's public GeoJSON feed (all M2.5+ earthquakes worldwide in the last 24h, refreshed
    every 5 min) and broadcasts any quake at/above EARTHQUAKE_MIN_MAGNITUDE whose epicenter falls
    inside EARTHQUAKE_BC_BBOX to Public + every channel (see broadcast_critical_all_channels), same
    treatment as a tsunami Warning/Watch. Broadcast once per USGS event id and never followed by a
    CLEARED message — an earthquake is a one-time event, not an incident with a lifecycle like a
    DriveBC closure."""
    try:
        res = requests.get(EARTHQUAKE_FEED_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if res.status_code != 200:
            logging.error(f"Earthquake feed fetch failed: HTTP {res.status_code}")
            return
        for feat in res.json().get("features", []):
            eq_id = feat.get("id")
            if not eq_id or eq_id in _earthquake_broadcast_ids: continue
            props = feat.get("properties") or {}
            mag = props.get("mag")
            if mag is None or mag < EARTHQUAKE_MIN_MAGNITUDE: continue
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2: continue
            lon, lat = coords[0], coords[1]
            if not _in_earthquake_bbox(lon, lat): continue
            _earthquake_broadcast_ids.add(eq_id)
            EARTHQUAKE_EVENTS.append((lat, lon, mag, props.get("place") or "Unknown location"))
            del EARTHQUAKE_EVENTS[:-20]
            place = props.get("place") or "Unknown location"
            depth_km = coords[2] if len(coords) > 2 else None
            depth_txt = f", {depth_km:.0f}km deep" if isinstance(depth_km, (int, float)) else ""
            msg = f"🌎 EARTHQUAKE M{mag:.1f}: {place}{depth_txt}"
            logging.info(f"Earthquake M{mag:.1f} detected near BC — broadcasting to Public + all channels: {place} ({eq_id})")
            await broadcast_critical_all_channels(msg, "EARTHQUAKE ALERT", "Earthquake")
    except Exception as e:
        logging.error(f"Earthquake feed check failed: {e}")

def check_weekly_channel_ad():
    """Every Sunday around noon, sends a two-part reminder on the public channel (0): first a
    plain heads-up line, then a second message listing all 5 category channels currently in use.
    Checked from weather_loop (already runs every 10 min), so this can fire anywhere from 12:00 to
    12:10 depending on when that loop happens to tick — deliberately not more precise than that,
    per the "around noon" request."""
    global last_weekly_ad_sent
    now = datetime.datetime.now()
    if now.weekday() != 6 or now.hour != 12: return  # weekday() == 6 is Sunday
    wk = (now.isocalendar()[0], now.isocalendar()[1])
    if last_weekly_ad_sent == wk: return
    if not tx_allowed("Weekly reminder"):
        last_weekly_ad_sent = wk  # dropped, not queued - don't retry every 10 minutes for the rest of the hour
        return
    try:
        execute_mesh_command(io.CONNECTION_ARGS + ["public", "Don't forget to check the traffic this week."])
        _emit("out", 0, "Don't forget to check the traffic this week.")
        channel_list = ", ".join(f"#{n}" for n in [*CHANNEL_NAMES.values(), WEATHER_CHANNEL_NAME])
        execute_mesh_command(io.CONNECTION_ARGS + ["public", f"Channels in use: {channel_list}"])
        _emit("out", 0, f"Channels in use: {channel_list}")
        logging.info(f"Sent weekly public-channel reminder ({channel_list}).")
        last_weekly_ad_sent = wk
    except Exception as e:
        logging.error(f"Weekly public-channel reminder failed: {e}")



# The auto-reply to "test" / "t".  Placeholders in the texts: {sender} {hops} {snr} {channel}.  The GUI addon exposes all four as settings.
TEST_REPLY_DEFAULT = "@{sender} Test received, {hops} hops"                 # answer given on the test channel
TEST_REDIRECT_DEFAULT = "@{sender} Please send test messages in {channel}"   # answer given on any other watched channel
TEST_REPLY_TEXT = TEST_REPLY_DEFAULT
TEST_REDIRECT_TEXT = TEST_REDIRECT_DEFAULT
TEST_CHANNEL = ""   # where tests belong, e.g. "#bot-van" ("" = no special channel: every watched channel gets TEST_REPLY_TEXT)
TEST_WATCH = []     # channels to check for "test" (names like "Public", "#bot-van"); [] = every channel

def _norm_chan(name): return (name or "").strip().lstrip("#").lower()

def _channel_name(idx):
    if idx == 0: return "Public"
    for name, i in io.CHANNEL_INDEX_BY_NAME.items():
        if i == idx: return name
    return None

def handle_test_message(data):
    """Auto-replies on the same channel if this channel message is exactly "test" or "t" and the channel is one we check.
    On the test channel (TEST_CHANNEL) the reply is TEST_REPLY_TEXT; on other checked channels it points people to the test channel."""
    if data.get("type") != "CHAN": return
    raw_text = data.get("text", "")
    sender, body = split_sender(raw_text)
    if body.lower() not in ("test", "t"): return  # "t" treated as shorthand for "test", per request
    chan_idx = data.get("channel_idx", DEFAULT_CHANNEL_IDX)
    here = _norm_chan(_channel_name(chan_idx))
    watch = {_norm_chan(c) for c in TEST_WATCH if _norm_chan(c)}
    if watch and TEST_CHANNEL: watch.add(_norm_chan(TEST_CHANNEL))   # the test channel itself is always checked
    if watch and here not in watch: return                           # not a channel we were asked to check
    dedup_key = (data.get("channel_idx"), data.get("sender_timestamp"), raw_text)
    if dedup_key in _recent_test_replies: return
    _recent_test_replies.add(dedup_key)
    if len(_recent_test_replies) > 200:  # bounded so this never grows unbounded over a long uptime
        _recent_test_replies.pop()
    if not tx_allowed("Test reply"):
        logging.info(f"Muted: not replying to test message from {sender}")
        return
    on_test_channel = not TEST_CHANNEL or here == _norm_chan(TEST_CHANNEL)
    shown = TEST_CHANNEL if TEST_CHANNEL.strip().startswith("#") or _norm_chan(TEST_CHANNEL) == "public" else "#" + TEST_CHANNEL.strip()
    path_len = data.get("path_len")
    try: reply = (TEST_REPLY_TEXT if on_test_channel else TEST_REDIRECT_TEXT).format(
        sender=sender, hops=0 if path_len == 255 else (path_len or 0), snr=data.get("SNR", "?"), channel=shown)
    except (KeyError, IndexError, ValueError): reply = f"@{sender} Test received"
    try:
        execute_mesh_command(io.CONNECTION_ARGS + ["chan", str(chan_idx), reply])
        _emit("out", chan_idx, reply)
        logging.info(f"Replied to test message from {sender} on channel {chan_idx}: {reply}")
    except Exception as e:
        logging.error(f"Failed to send test-reply on channel {chan_idx}: {e}")

def check_incoming_test_messages():
    for data in fetch_incoming_messages(): handle_test_message(data)

BROADCAST_PACING_SECONDS = 1.5  # gap between consecutive over-the-air transmissions

def _is_one_shot_transit_cancellation(source, title):
    """TransLink/BC Transit 'NN Route trip leaving X Station at H:MM am is cancelled today due to
    ...' notices describe a single specific bus/train run on one specific day/time — there's
    nothing to 'clear' since the trip's departure time just passes and it drops out of the feed
    on its own. These are broadcast once as NEW and then silently dropped when they roll off the
    feed instead of also sending a follow-up CLEARED message per user request."""
    if source not in ("TransLink", "BC Transit"): return False
    txt = title.lower()
    return "trip" in txt and ("cancelled" in txt or "canceled" in txt)

async def process_scraped_alerts(current_scan_dict, state_dict, skip_clear_sources=None):
    # Keyed by "source|guid" — guid is the feed's own stable identifier (GTFS entity.id, DriveBC
    # event id, BC Ferries serviceNoticeCode), NOT rendered title text. Title text can legitimately
    # change between scans for the exact same underlying incident (agencies prepend "UPDATE 11:40
    # AM:" or "Update - ..." as they refresh an alert) — keying by title used to make the same
    # incident look brand-new and get broadcast twice. state_dict is whichever category (traffic
    # or weather) this scan belongs to, so the two independently-scheduled loops never clear each
    # other's alerts.
    # NOTE: broadcasts are paced with a short sleep between each one. Firing them back-to-back
    # with no gap (e.g. ~25-50 new alerts on the very first scan) was observed overwhelming the
    # Heltec node's connection and crashing meshcli mid-batch (exit code 3221225786), silently
    # dropping the rest of that batch. LoRa airtime/duty-cycle limits also make rapid-fire sends
    # a bad idea on real hardware regardless.
    # skip_clear_sources: source labels (e.g. "BC Ferries") whose upstream fetch failed/timed out
    # this cycle. Without this, a transient network hiccup on just one feed makes that feed's
    # already-active alerts vanish from current_scan_dict for one scan, which this function reads
    # as "the incident ended" and broadcasts a false CLEARED — seen in production logs (BC Ferries
    # read timeouts / connection-aborted errors). Sources listed here are left untouched this
    # round and re-checked on the next successful fetch instead.
    skip_clear_sources = skip_clear_sources or set()
    current = {f"{src}|{g}": (src, t, d) for g, (src, t, d) in current_scan_dict.items()}
    for key, (src, t, d) in current.items():
        if key not in state_dict:
            state_dict[key] = (src, t)
            guid = key.split("|", 1)[1]
            if src.startswith("Weather Warning:"): handle_weather_broadcast(src, t, d, False, src.replace("Weather Warning: ", "").strip())
            else: broadcast_via_cli(src, t, d, False, guid=guid)
            await asyncio.sleep(BROADCAST_PACING_SECONDS)

    cleared = []
    for key in list(state_dict.keys()):
        if key not in current:
            src_str, t_str = state_dict[key]
            if src_str in skip_clear_sources: continue
            if src_str.startswith("WX_"): continue
            if _is_one_shot_transit_cancellation(src_str, t_str):
                # Logged (for reload_active_alerts_from_log's bookkeeping) but never transmitted —
                # without this, these entries would have no matching CLEAR line in the log at all,
                # so every future restart's log replay would resurrect them as "active" forever
                # (reload only trusts NEW/CLEAR pairs, it has no concept of a trip's time passing).
                guid = key.split("|", 1)[1] if "|" in key else None
                guid_tag = f" {{id:{guid}}}" if guid else ""
                logging.info(f"Broadcasting CLEAR to Channel Index {_resolve_channel_idx(src_str)}: [{src_str}] {t_str}{guid_tag}")
                del state_dict[key]
                continue
            cleared.append(key)

    for key in cleared:
        src_lbl, t = state_dict[key]
        guid = key.split("|", 1)[1] if "|" in key else None
        broadcast_via_cli(src_lbl, t, "", True, guid=guid)
        del state_dict[key]
        await asyncio.sleep(BROADCAST_PACING_SECONDS)

async def check_daily_weather_broadcasts():
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    hdrs = {"User-Agent": "Mozilla/5.0"}
    # FIXED: this was "https://open-meteo.com" with no path — the plain marketing homepage, not
    # the API. res.json() on an HTML page always throws "Expecting value: line 1 column 1", which
    # is exactly the error this script's own log showed for every single 6AM/8AM run. The forecast
    # API actually lives on the api. subdomain under /v1/forecast.
    domain_api = "https://api.open-meteo.com/v1/forecast"

    if now.hour == 6:
        for region, coords in WEATHER_LOCATIONS.items():
            if last_6am_sent.get(region) == today: continue
            try:
                url = f"{domain_api}?latitude={coords['lat']}&longitude={coords['lon']}&daily=weathercode,temperature_2m_max,temperature_2m_min&timezone=America%2FVancouver"
                res = requests.get(url, headers=hdrs, timeout=15).json().get("daily", {})
                t_arr, w_arr, h_arr, l_arr = res.get("time", []), res.get("weathercode", []), res.get("temperature_2m_max", []), res.get("temperature_2m_min", [])

                parts = []
                if len(t_arr) >= 3:
                    d1 = datetime.datetime.strptime(t_arr[0], "%Y-%m-%d").strftime("%a")
                    parts.append(f"{WEATHER_EMOJIS.get(w_arr[0], '☀️')} {d1} H{int(round(h_arr[0]))} L{int(round(l_arr[0]))}")
                    d2 = datetime.datetime.strptime(t_arr[1], "%Y-%m-%d").strftime("%a")
                    parts.append(f"{WEATHER_EMOJIS.get(w_arr[1], '☀️')} {d2} H{int(round(h_arr[1]))} L{int(round(l_arr[1]))}")
                    d3 = datetime.datetime.strptime(t_arr[2], "%Y-%m-%d").strftime("%a")
                    parts.append(f"{WEATHER_EMOJIS.get(w_arr[2], '☀️')} {d3} H{int(round(h_arr[2]))} L{int(round(l_arr[2]))}")

                msg_payload = f"3-Day Weather Report -> {' | '.join(parts)}"
                handle_weather_broadcast("WX_6AM", f"3-Day Forecast: {region}", msg_payload, False, region)
                last_6am_sent[region] = today
            except Exception as e: logging.error(f"6AM Forecast failed for {region}: {e}")

    if now.hour == 8:
        for region, coords in WEATHER_LOCATIONS.items():
            if last_8am_sent.get(region) == today: continue
            try:
                url = f"{domain_api}?latitude={coords['lat']}&longitude={coords['lon']}&daily=weathercode,temperature_2m_max&timezone=America%2FVancouver"
                res = requests.get(url, headers=hdrs, timeout=15).json().get("daily", {})
                t_arr, w_arr = res.get("time", []), res.get("weathercode", [])

                sm = ""
                if len(t_arr) >= 5:
                    sm += f"{datetime.datetime.strptime(t_arr[0], '%Y-%m-%d').strftime('%a')[:1]}{WEATHER_EMOJIS.get(w_arr[0], '☀️')} "
                    sm += f"{datetime.datetime.strptime(t_arr[1], '%Y-%m-%d').strftime('%a')[:1]}{WEATHER_EMOJIS.get(w_arr[1], '☀️')} "
                    sm += f"{datetime.datetime.strptime(t_arr[2], '%Y-%m-%d').strftime('%a')[:1]}{WEATHER_EMOJIS.get(w_arr[2], '☀️')} "
                    sm += f"{datetime.datetime.strptime(t_arr[3], '%Y-%m-%d').strftime('%a')[:1]}{WEATHER_EMOJIS.get(w_arr[3], '☀️')} "
                    sm += f"{datetime.datetime.strptime(t_arr[4], '%Y-%m-%d').strftime('%a')[:1]}{WEATHER_EMOJIS.get(w_arr[4], '☀️')}"

                msg_payload = f"5-Day Graphical Outlook -> {sm.strip()}"
                handle_weather_broadcast("WX_8AM", f"5-Day Summary: {region}", msg_payload, False, region)
                last_8am_sent[region] = today
            except Exception as e: logging.error(f"8AM Summary failed for {region}: {e}")
def _get_with_retry(url, headers, timeout, retries=1):
    """Plain requests.get() with one automatic retry on timeout/connection errors before giving up.
    Added specifically for bcferries.com, which read-times-out often enough in production logs to
    be a known characteristic of that site rather than a one-off blip — a single retry gives each
    of its two fetches (service notices + departures) a second chance within the same scan instead
    of silently sitting out the whole minute-long cycle on one bad connection."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            return requests.get(url, headers=headers, timeout=timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = e
    raise last_err

async def scrape_traffic_feeds():
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Encoding": "gzip, deflate"}
    cur = {}
    # Sources whose upstream fetch fails/times out this cycle go in here so process_scraped_alerts
    # doesn't mistake "request failed" for "incident actually cleared" — see its docstring.
    skip_clear_sources = set()
    logging.info("Checking Road, Ferry & Rail Notices...")
    
    d_part1, d_part2 = "https://api.open511", ".gov.bc.ca/events?status=ACTIVE"
    f_part1, f_part2 = "https://www.bcferries", ".com/current-conditions/service-notices?feed=rss"

    # --- DRIVEBC JSON FEED ---
    # NOTE: the Open511 endpoint returns JSON (confirmed via its Content-Type header), not XML.
    # This used to run an XML tag regex (<event>...</event>) against that JSON body, which can
    # never match anything, so DriveBC never produced a single incident, accident, or closure —
    # every event type (INCIDENT/ACCIDENT, CONSTRUCTION, SPECIAL_EVENT, etc.) silently vanished
    # here with no error logged. Parsing the actual JSON structure fixes all of it at once.
    #
    # BUG FIX: the single unbounded `?status=ACTIVE` request (no bbox/limit) does NOT reliably
    # return today's incidents. Verified live: many events created back in 2020-2023 are still
    # marked ACTIVE (DriveBC apparently rarely archives long-running advisories), and the API's
    # default/unfiltered ordering isn't sorted by recency — a real, active, severity-MAJOR "vehicle
    # stall" at the George Massey Tunnel (created same-day) was completely absent from this query's
    # results, which is exactly why it never got broadcast. Querying per-region via `bbox=` instead
    # keeps each response small and scoped, and reliably surfaced that same incident in testing.
    for region_name, bbox in DRIVEBC_REGION_BBOXES.items():
        try:
            res = requests.get(f"{d_part1}{d_part2}&bbox={bbox}", headers=hdrs, timeout=15)
            if res.status_code == 200:
                for ev in res.json().get("events", []):
                    # Construction events excluded by request — checked against the real event_type
                    # field (not the headline text) since that's the actual Open511 classification and
                    # won't misfire on, say, an INCIDENT whose headline happens to mention "construction".
                    if (ev.get("event_type") or "").strip().upper() == "CONSTRUCTION": continue
                    headline = (ev.get("headline") or ev.get("event_type") or "").strip()
                    d_txt = clean_html_tags(ev.get("description") or "")
                    g_txt = ev.get("id") or f"DriveBC_{hash(headline + d_txt[:20])}"

                    named_roads = [r for r in (ev.get("roads") or []) if r.get("name", "").strip().lower() not in ("", "other roads")]
                    road_names = [r.get("name", "").strip() for r in named_roads]
                    area_names = [a.get("name", "").strip() for a in (ev.get("areas") or []) if a.get("name", "").strip()]
                    search_text = " ".join([headline, d_txt] + road_names + area_names)

                    # NOTE: DriveBC's `areas[].name` is only a broad district (e.g. "Lower Mainland
                    # District"), not a city. The actual city name only shows up buried in the
                    # "+ivr_message" extension field, in a recurring "In <City>" pattern (e.g.
                    # "...Highway 99, In Richmond. Left lane blocked..."), confirmed against ~45 live
                    # events. Extract it from there since nothing else in the payload has it.
                    ivr_txt = ev.get("+ivr_message") or ""
                    city_m = re.search(r'\bIn ([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)', ivr_txt)
                    city = city_m.group(1).strip() if city_m else ""

                    if (headline or d_txt) and check_regions_match(search_text, is_transit=False) and _is_open511_event_live(ev.get("schedule")):
                        # NOTE: this used to only use roads[].name (e.g. "Highway 91"), dropping the
                        # from/to location DriveBC actually provides (e.g. "at 88th Ave", "near Alex
                        # Fraser Bridge") — a real Highway 91 incident got broadcast with no specific
                        # location as a result. roads[].from/to are included now when present, and the
                        # city (from +ivr_message, see above) is appended too since from/to road
                        # segments alone still weren't specific enough per user feedback.
                        if named_roads:
                            r0 = named_roads[0]
                            # BUG FIX: some events' from/to fields are just another alias of the highway
                            # itself (e.g. from="Trans-Canada Highway" on a "Highway 1" event) rather
                            # than a real cross-street/exit — that's not a location at all, just wasted
                            # characters, and was crowding out the city and truncating the description.
                            # Filter those out and fall back to direction (e.g. "Westbound") instead.
                            loc_bits = [b for b in (_clean_drivebc_loc_bit(r0.get("from", ""), road_names[0]),
                                                     _clean_drivebc_loc_bit(r0.get("to", ""), road_names[0])) if b]
                            loc_str = " to ".join(dict.fromkeys(loc_bits))  # dedupe if from==to
                            if not loc_str:
                                loc_str = DRIVEBC_DIRECTION_WORDS.get((r0.get("direction") or "").strip().upper(), "")
                            if city and city not in loc_str:
                                loc_str = f"{loc_str}, {city}" if loc_str else city
                            t_txt = f"{headline} - {road_names[0]}" + (f" ({loc_str})" if loc_str else "")
                        else:
                            t_txt = f"{headline} ({city})" if city and headline else (headline or "Transportation Notice")
                        # BUG FIX: confirmed live — DriveBC's own description field re-states the same
                        # road name and city already extracted into t_txt above, as a fixed lead-in
                        # ("Vehicle incident at Highway 1, In Burnaby and Kensington Ave...") before the
                        # actually new detail (the cross street, lane closures, etc.). Broadcasting title
                        # + description back to back meant that boilerplate repeat ate the character
                        # budget, truncating the message before reaching the cross street — the one
                        # piece of the description not already shown in the title. Stripping everything
                        # up through "In <city> and " (which is always redundant with the title once a
                        # city was found) leaves only the new part for the budget to be spent on.
                        if city:
                            d_txt = re.sub(r'^.*?\bIn\s+' + re.escape(city) + r'\s+and\s+', '', d_txt, count=1, flags=re.IGNORECASE)
                        # BUG FIX: DriveBC appends a "Last update: <date/time>. Next update: <date/time>."
                        # scheduling footer to essentially every description (confirmed live across
                        # construction and incident events alike) — pure admin boilerplate, never useful
                        # over a 120-char radio broadcast, and it was eating the budget that should go to
                        # the actual incident detail (lane closures, detours, etc.) once the redundant
                        # city/road lead-in above was already stripped. Stripped unconditionally, not
                        # just when a city was found.
                        d_txt = re.sub(r'\s*Last update:.*$', '', d_txt, flags=re.IGNORECASE).strip()
                        geo = ev.get("geography") or {}
                        if geo.get("type") == "Point" and len(geo.get("coordinates") or []) >= 2:
                            ALERT_LOCATIONS[f"DriveBC|{g_txt}"] = (geo["coordinates"][1], geo["coordinates"][0], t_txt)
                        cur[g_txt] = ("DriveBC", t_txt, d_txt)
            else:
                skip_clear_sources.add("DriveBC")
        except Exception as e:
            logging.error(f"DriveBC JSON parse error ({region_name}): {e}")
            skip_clear_sources.add("DriveBC")

    # --- BC FERRIES SERVICE NOTICES ---
    # NOTE: ?feed=rss no longer actually returns RSS/XML — it now returns Content-Type: text/html,
    # the same page a browser gets. The old code hunted for <item>/<guid> tags that don't exist in
    # that HTML at all, so it never matched a single notice. Real notices are plain-text links whose
    # href contains "serviceNoticeCode=", grouped under region headers — scrape those directly.
    # (I couldn't fetch raw HTML from this sandbox to verify exact surrounding tags byte-for-byte,
    # so this anchors on the serviceNoticeCode= URL pattern specifically since that's a stable,
    # distinctive signal regardless of the exact wrapping markup. Worth a log check after first run.)
    try:
        # NOTE: bcferries.com has repeatedly shown "Read timed out" / "Connection aborted" errors
        # in production logs at the original 15s timeout, more often than the other feeds — their
        # server appears to just be slow. skip_clear_sources already prevents this from causing a
        # false CLEARED broadcast, but a longer timeout cuts down on how often the fetch fails at all.
        res = _get_with_retry(f_part1 + f_part2, hdrs, timeout=25)
        if res.status_code == 200:
            html_text = res.text
            current_region = ""
            for m in re.finditer(r'<h[2-4][^>]*>(.*?)</h[2-4]>|<a[^>]+href="([^"]*service-notices\?serviceNoticeCode=[^"]*)"[^>]*>(.*?)</a>', html_text, re.DOTALL):
                if m.group(1) is not None:
                    current_region = clean_html_tags(m.group(1))
                else:
                    link, raw_title = m.group(2), m.group(3)
                    t_txt = clean_html_tags(raw_title)
                    if not t_txt: continue
                    search_text = f"{t_txt} {current_region}"
                    if check_regions_match(search_text, is_transit=False):
                        code_m = re.search(r'serviceNoticeCode=(\d+)', link)
                        g_txt = f"BCF_{code_m.group(1)}" if code_m else f"BCF_{hash(t_txt)}"
                        d_txt = f"Route/region: {current_region}" if current_region else "Service Notice Active."
                        cur[g_txt] = ("BC Ferries", t_txt, d_txt)
        else:
            skip_clear_sources.add("BC Ferries")
    except Exception as e:
        logging.error(f"BC Ferries notices parse error: {e}")
        skip_clear_sources.add("BC Ferries")

    # NOTE: the old "BC Ferries Capacity Metrics" section called /api/v1/current-conditions, which
    # no longer exists — it returns nothing (moved to per-route pages like /current-conditions/TSA-SWB).
    # Deck-capacity "heavy volume" alerts were removed rather than left silently calling a dead
    # endpoint every minute; rebuilding this would mean scraping dozens of individual route pages.

    # --- BC FERRIES: LIVE DEPARTURE DELAYS ---
    # NOTE: the service-notices scrape above only carries rare, major, agency-announced disruptions
    # (mechanical breakdowns, weather cancellations) — confirmed it produced zero alerts for months
    # even during ordinary day-to-day lateness, which is the actual, common case. Real per-sailing
    # delay data (scheduled vs. actual departure, plus a short human-written status note like "An
    # earlier incident has caused some delays") instead lives on the Departures & Arrivals page,
    # confirmed via a live fetch showing real times/status text server-rendered into the HTML (not a
    # JS-only shell like TransLink/BC Transit's alert pages). This runs alongside the notices scrape
    # above rather than replacing it, since the two sources cover genuinely different things.
    BCF_DELAY_THRESHOLD_MIN = 15  # minimum minutes later than scheduled to count as "delayed" on its own
    BCF_STATUS_SIGNAL_WORDS = ("delay", "incident", "cancel", "problem", "disruption")  # status note counts as a delay regardless of minutes-late if it contains any of these
    _BCF_TIME_RE = r'(\d{1,2}:\d{2}\s*[AP]M)'
    _bcf_row_re = re.compile(
        r'^(?P<ferry>.*?)\s*SCHEDULED:\s*' + _BCF_TIME_RE +
        r'(?:\s*ACTUAL:\s*' + _BCF_TIME_RE + r')?'
        r'(?:\s*(?:ARRIVAL|ETA):\s*(?:' + _BCF_TIME_RE + r'|Variable))?'
        r'\s*(?P<status>.*)$', re.IGNORECASE)

    def _bcf_time_to_minutes(t):
        m = re.match(r'(\d{1,2}):(\d{2})\s*([AP]M)', (t or "").strip(), re.IGNORECASE)
        if not m: return None
        h, mnt, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if ap == "PM" and h != 12: h += 12
        if ap == "AM" and h == 12: h = 0
        return h * 60 + mnt

    try:
        res = _get_with_retry("https://www.bcferries.com/current-conditions/departures", hdrs, timeout=25)
        if res.status_code == 200:
            html_text = res.text
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            current_terminal, current_route = "", ""
            # Terminal headers (<h3>, one per terminal) and each route's table appear in document
            # order, so walking every <h3>/<table> in sequence and remembering the most recent
            # header reconstructs which sailing belongs to which route. Each table's own first row
            # names the route pair (e.g. "Langdale - Horseshoe Bay") before the per-sailing rows.
            for block_m in re.finditer(r'<h3[^>]*>(.*?)</h3>|<table[^>]*>(.*?)</table>', html_text, re.DOTALL | re.IGNORECASE):
                if block_m.group(1) is not None:
                    current_terminal = clean_html_tags(block_m.group(1))
                    continue
                for row_html in re.findall(r'<tr[^>]*>(.*?)</tr>', block_m.group(2), re.DOTALL | re.IGNORECASE):
                    row_txt = " ".join(clean_html_tags(row_html).split())
                    if not row_txt: continue
                    if "sailing duration" in row_txt.lower():
                        current_route = re.split(r'\s*Sailing duration', row_txt, flags=re.IGNORECASE)[0].strip()
                        continue
                    if "SCHEDULED:" not in row_txt.upper(): continue  # column-heading row, or a sailing that hasn't departed yet
                    m = _bcf_row_re.match(row_txt)
                    if not m: continue
                    sched_str, actual_str = m.group(2), m.group(3)
                    sched_min, actual_min = _bcf_time_to_minutes(sched_str), _bcf_time_to_minutes(actual_str)
                    if sched_min is None or actual_min is None: continue  # hasn't actually departed yet, nothing to evaluate
                    late_min = actual_min - sched_min
                    if late_min < -600: late_min += 1440  # departed just after midnight vs. a scheduled time just before it
                    status = (m.group("status") or "").strip()
                    if late_min < BCF_DELAY_THRESHOLD_MIN and not any(w in status.lower() for w in BCF_STATUS_SIGNAL_WORDS): continue
                    search_text = f"{current_route} {current_terminal}"
                    if not check_regions_match(search_text, is_transit=False): continue
                    ferry = m.group("ferry").strip()
                    g_txt = f"BCF_DELAY_{current_route}_{ferry}_{sched_str}_{today}".replace(" ", "_")
                    t_txt = f"{current_route}: {ferry} running ~{late_min} min late" if late_min > 0 else f"{current_route}: {ferry} delay reported"
                    d_txt = f"Scheduled {sched_str}, actual {actual_str}." + (f" {status}" if status else "")
                    cur[g_txt] = ("BC Ferries", t_txt, d_txt)
        else:
            skip_clear_sources.add("BC Ferries")
    except Exception as e:
        logging.error(f"BC Ferries departures parse error: {e}")
        skip_clear_sources.add("BC Ferries")

    # --- REGIONAL TRANSIT SERVICE ALERTS (GTFS-Realtime) ---
    # translink.ca/translink/alerts and alerts.bctransit.com are both JavaScript-only single-page
    # apps — fetching them with plain requests.get() returns literal "Loading..." / "JavaScript
    # Required" placeholders, never real alert text, so no regex against that HTML could ever work.
    # Both agencies publish real GTFS-Realtime alert feeds instead, which is what's used here.
    if not GTFS_RT_AVAILABLE:
        logging.warning("gtfs-realtime-bindings not installed; skipping TransLink/BC Transit alerts. Run: pip install gtfs-realtime-bindings")
    else:
        # BC Transit: free, no API key needed. Each operator feed is already scoped to that
        # operator's own service area (one of the regions this agent deliberately monitors), so
        # region-keyword filtering is skipped here — it could only ever drop a real, wanted alert.
        # important_only=True: same reasoning as TransLink below — BC Transit's 9 regional feeds
        # were producing a steady stream of generic "Service Alert (route)" broadcasts with no real
        # substance beyond a route number. Only service-disrupting alerts go out now.
        for op_id, op_name in BCTRANSIT_OPERATORS.items():
            try:
                res = requests.get(f"https://bct.tmix.se/gtfs-realtime/alerts.pb?operatorIds={op_id}", headers=hdrs, timeout=15)
                if res.status_code == 200:
                    for g_txt, t_txt, d_txt in parse_gtfs_alerts(res.content, f"BCT{op_id}", important_only=True):
                        cur[g_txt] = ("BC Transit", f"{op_name}: {t_txt}", _condense_bct_desc(d_txt))
                else:
                    skip_clear_sources.add("BC Transit")  # one operator's fetch failed; don't clear ANY BC Transit entries this round
            except Exception as e:
                logging.error(f"BC Transit GTFS alert fetch failed for {op_name}: {e}")
                skip_clear_sources.add("BC Transit")

        # TransLink: covers SkyTrain, Bus, SeaBus, and West Coast Express, but requires a free API
        # key (https://developer.translink.ca/Account/Register) that only you can create — I can't
        # sign up for an account on your behalf. Skipped entirely until a key is supplied at startup.
        # important_only=True: TransLink's feed covers every bus route in Metro Vancouver, so it was
        # broadcasting minor/cosmetic notices (elevator outages, small schedule tweaks, detours,
        # temporary closures/moves, etc.) as often as real disruptions. Only service-disrupting alerts
        # (suspensions, no service, major/significant delays, emergencies) go out now — see
        # GTFS_IMPORTANT_EFFECTS/_KEYWORDS/_MINOR_KEYWORDS above if the threshold needs tuning.
        # _translink_mode_passes: further scoped to SkyTrain/SeaBus + only *major* bus issues (see
        # GTFS_RAIL_SEABUS_KEYWORDS/GTFS_MAJOR_BUS_KEYWORDS) — routine bus delays/reroutes that
        # already cleared the general importance bar above are still too frequent to be worth it.
        if TRANSLINK_API_KEY:
            try:
                res = requests.get(f"https://gtfsapi.translink.ca/v3/gtfsalerts?apikey={TRANSLINK_API_KEY}", headers=hdrs, timeout=15)
                if res.status_code == 200:
                    for g_txt, t_txt, d_txt in parse_gtfs_alerts(res.content, "TransLink", important_only=True):
                        if not _translink_mode_passes(t_txt, d_txt): continue
                        cur[g_txt] = ("TransLink", t_txt, d_txt)
                else:
                    logging.error(f"TransLink GTFS alerts request failed: HTTP {res.status_code}")
                    skip_clear_sources.add("TransLink")
            except Exception as e:
                logging.error(f"TransLink GTFS alert fetch failed: {e}")
                skip_clear_sources.add("TransLink")

    await process_scraped_alerts(cur, active_traffic_alerts, skip_clear_sources)

async def scrape_weather_warnings():
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Encoding": "gzip, deflate"}
    cur = {}
    # --- ENVIRONMENT CANADA WEATHER MONITOR ---
    logging.info("Checking Environment Canada Severe Weather streams...")
    for region, profile in WEATHER_LOCATIONS.items():
        # Per request: broadcast one combined message per broad region (Lower Mainland/Vancouver
        # Island/Sunshine Coast) — the same 3-way grouping used everywhere else (daily forecasts,
        # DriveBC region scopes) — rather than one message per individual EC sub-zone/city feed.
        # Each region has several sub-zone feeds (see WEATHER_LOCATIONS), and the same region-wide
        # event (e.g. one rainfall system) shows up on all of them with near-identical text, so this
        # collects the distinct warning "kind" (e.g. "YELLOW WARNING - RAINFALL") active anywhere in
        # the region, deduped, instead of broadcasting each sub-zone's copy separately.
        region_kinds = set()
        for feed_url in profile["feeds"]:
            try:
                res = requests.get(feed_url, headers=hdrs, timeout=10)
                if res.status_code != 200: continue
                decoded_text = res.content.decode('utf-8', errors='ignore')
                # BUG FIX: these battleboard feeds are Atom, not RSS 2.0 — confirmed live — so
                # entries are <entry>/<title>/<summary>/<id>, not <item>/<title>/<description>/
                # <guid>. The old regex below looked for <item>, which never exists in this feed
                # format, so this loop never once matched anything regardless of the URL.
                for entry in re.findall(r'<entry\b[^>]*>(.*?)</entry>', decoded_text, re.DOTALL):
                    title_m = re.search(r'<title\b[^>]*>(.*?)</title>', entry, re.DOTALL)
                    t_txt = clean_html_tags(title_m.group(1)) if title_m else ""
                    # "No alerts in effect, <zone>" is what these feeds return on every single one
                    # of the (much more common) quiet cycles — confirmed live — so it has to be
                    # filtered the same as "no watches"/"statement ended" or every quiet zone would
                    # get counted as if it were itself an active alert.
                    if any(p in t_txt.lower() for p in ("no alerts in effect", "no watches", "statement ended")): continue
                    if not check_regions_match(t_txt, is_transit=False): continue
                    # Title looks like "YELLOW WARNING - RAINFALL, Metro Vancouver - central...";
                    # the part before the comma is the alert type/severity, shared across every
                    # sub-zone under the same region-wide event — that's what gets deduped. The
                    # part after the comma (the individual sub-zone/city name) is deliberately
                    # dropped, per request.
                    kind = t_txt.split(",")[0].strip()
                    if kind: region_kinds.add(kind)
            except requests.exceptions.Timeout:
                logging.warning(f"Server throttle noticed on Environment Canada feed for {region} ({feed_url}). Skipping to protect pipeline.")
            except Exception as e: logging.error(f"Weather warning text regex parse failed for {region} ({feed_url}): {e}")

        if region_kinds:
            kinds_sorted = sorted(region_kinds)
            # BUG FIX: EC's own per-entry <id> embeds the timestamp of that specific bulletin
            # re-issue (confirmed live), which changes every time EC refreshes an ongoing warning's
            # wording — that made the old per-entry-guid approach see a "new" alert and broadcast a
            # spurious CLEARED+NEW pair for a warning that never actually ended. Keying by the
            # region name plus the sorted set of active warning kinds instead is stable across those
            # re-issues (same kinds = same key = no repeat broadcast) while still changing (and
            # re-broadcasting) when a warning is actually added to or dropped from the region.
            g_txt = f"EC_REGION_{region}_{'_'.join(kinds_sorted)}"
            t_txt = "; ".join(kinds_sorted)
            cur[g_txt] = (f"Weather Warning: {region}", t_txt, "")

    await process_scraped_alerts(cur, active_weather_alerts)




async def main():
    global USE_SCOPES, REGION_SCOPES, TRANSLINK_API_KEY
    print("\n=======================================================\n           BC EMERGENCY TRANSPORTATION AGENT           \n=======================================================\n")

    mode = ""
    while mode not in ("usb", "bluetooth"):
        raw = input("\nConnect to the MeshCore node via USB or Bluetooth? (usb/bluetooth): ").strip().lower()
        if raw in ("u", "usb"): mode = "usb"
        elif raw in ("b", "bt", "ble", "bluetooth"): mode = "bluetooth"
        else: print("Please type 'usb' or 'bluetooth'.")

    io.CONNECTION_ARGS = auto_detect_usb_port() if mode == "usb" else auto_detect_ble_device()
    if not io.CONNECTION_ARGS: return

    resolve_channel_indices()  # look up #drivebc/#bcferries/#bctransit/#translink/#weather indices before anything tries to broadcast

    if not GTFS_RT_AVAILABLE:
        print("\n[!] 'gtfs-realtime-bindings' isn't installed, so TransLink/BC Transit alerts will be skipped.")
        print("    Run 'pip install gtfs-realtime-bindings' and restart to enable them.")
    else:
        # Checked first so the key never has to live in this file or be retyped every launch —
        # set it once on this machine (e.g. `setx TRANSLINK_API_KEY your-key`, then open a new
        # terminal) and the prompt below is skipped automatically from then on.
        env_key = os.environ.get("TRANSLINK_API_KEY", "").strip()
        if env_key:
            TRANSLINK_API_KEY = env_key
            logging.info("Using TransLink API key from the TRANSLINK_API_KEY environment variable.")
        else:
            TRANSLINK_API_KEY = input(
                "\nEnter your TransLink API key (free at https://developer.translink.ca/Account/Register)\n"
                "or press ENTER to skip TransLink (SkyTrain/Bus/SeaBus/WCE) alerts: "
            ).strip() or None
            if not TRANSLINK_API_KEY:
                logging.warning("No TransLink API key supplied; SkyTrain/Bus/SeaBus/West Coast Express alerts will be skipped this run.")

    reload_active_alerts_from_log()
    reload_critical_alert_ids_from_log()
    ans = input("\nShould region scopes be used to transmit alerts? (yes/no): ").strip().lower()
    if ans in ['yes', 'y']:
        USE_SCOPES = True
        print("\n--- Enter Scopes for Regions ---")
        for reg in REGION_SCOPES.keys(): REGION_SCOPES[reg] = input(f"Scope for {reg}: ").strip()
    u_in = input("\nEnter target frequency in MHz (e.g., 910.425) or press ENTER to skip: ").strip()
    if u_in:
        ok, detail = set_radio_frequency(u_in)
        (logging.info if ok else logging.error)(detail)
    logging.info(f"Monitoring feeds via {mode.upper()} ({' '.join(io.CONNECTION_ARGS)}).")
    await asyncio.gather(traffic_loop(), weather_loop(), messages_loop())

async def traffic_loop():
    while True:
        # Self-healing: if channel resolution never succeeded (or the node was rebooted/
        # reconfigured mid-run and lost its channel list), keep retrying once a minute instead of
        # requiring a manual restart to notice — see resolve_channel_indices docstring for the
        # incident (5+ days of alerts silently going to Public) this is fixing.
        if not io.CHANNEL_INDEX_BY_NAME:
            resolve_channel_indices()
        await check_tsunami_warnings()  # checked every minute, not on the slower 10-min weather cycle — a tsunami warning is too time-critical to wait on
        await check_earthquake_warnings()  # same cadence/urgency as tsunami — see broadcast_critical_all_channels
        await scrape_traffic_feeds()
        logging.info(f"Traffic scan complete. Tracking {len(active_traffic_alerts)} active items. Sleeping 1 minute...")
        await asyncio.sleep(60)

async def weather_loop():
    while True:
        check_weekly_channel_ad()
        await check_daily_weather_broadcasts()
        await scrape_weather_warnings()
        logging.info(f"Weather scan complete. Tracking {len(active_weather_alerts)} active items. Sleeping 10 minutes...")
        await asyncio.sleep(600)

async def messages_loop():
    while True:
        check_incoming_test_messages()
        await asyncio.sleep(MESSAGE_POLL_SECONDS)

if __name__ == "__main__":
    asyncio.run(main())
