"""Notification sounds (Windows system sounds / beeps / your own .wav), with a short cool-down so a busy mesh doesn't machine-gun you."""
import os
import threading
import time

try:
    import winsound
except ImportError:      # not Windows: fall back to the terminal bell
    winsound = None

SOUNDS = {"None": None,
          "Ding": ("alias", "SystemAsterisk"), "Exclamation": ("alias", "SystemExclamation"), "Hand": ("alias", "SystemHand"),
          "Question": ("alias", "SystemQuestion"), "Default beep": ("alias", "SystemDefault"),
          "Beep high": ("beep", [(1250, 110)]), "Beep low": ("beep", [(550, 160)]),
          "Double beep": ("beep", [(900, 80), (1300, 80)]), "Rising chirp": ("beep", [(700, 60), (900, 60), (1200, 90)])}
EVENTS = {"private": ("Private message arrives", "Ding"), "mention": ("Someone @mentions your node name", "Exclamation"),
          "highlight": ("A highlight word is said", "Question"), "channel": ("Any message in a channel you're not looking at", "None")}
COOLDOWN = 1.5
_last = [0.0]


def play(choice, custom_path="", bell=None):
    """choice: a key of SOUNDS, or 'Custom file' (uses custom_path).  Safe to call from the GUI thread; never raises."""
    try:
        if choice in (None, "None", ""): return
        spec = ("wav", custom_path) if choice == "Custom file" else SOUNDS.get(choice)
        if spec is None: return
        if winsound is None:
            if bell: bell()
            return
        kind, val = spec
        if kind == "alias": winsound.PlaySound(val, winsound.SND_ALIAS | winsound.SND_ASYNC)
        elif kind == "wav" and os.path.isfile(val): winsound.PlaySound(val, winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif kind == "beep":
            threading.Thread(target=lambda: [winsound.Beep(f, ms) for f, ms in val], daemon=True).start()   # Beep() blocks, so keep it off the GUI thread
    except Exception:
        pass


def notify(settings, event, bell=None):
    """Play the configured sound for an event unless sounds are off or one just played."""
    if not settings.get("sounds_enabled", True): return
    now = time.time()
    if now - _last[0] < COOLDOWN: return
    choice = settings.get("sound_" + event, EVENTS[event][1])
    if choice in (None, "None", ""): return
    _last[0] = now
    play(choice, settings.get("sound_custom", ""), bell)
