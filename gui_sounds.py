"""Notification sounds (Windows system sounds / beeps / your own .wav), with a short cool-down so a busy mesh doesn't machine-gun you."""
import os
import shutil
import subprocess
import threading
import time

import gui_platform

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
# used when the Windows sound scheme is "No Sounds" (the system sounds are then silent)
FALLBACK = {"SystemAsterisk": [(1000, 90), (1400, 110)], "SystemExclamation": [(800, 120), (800, 120)], "SystemHand": [(400, 250)],
            "SystemQuestion": [(1100, 70), (900, 90)], "SystemDefault": [(900, 100)]}
# macOS and Linux system sounds (the Windows names above are mapped onto them)
MAC_SOUNDS = {"SystemAsterisk": "Glass", "SystemExclamation": "Sosumi", "SystemHand": "Basso", "SystemQuestion": "Pop", "SystemDefault": "Tink"}
LINUX_SOUNDS = {"SystemAsterisk": "message", "SystemExclamation": "dialog-warning", "SystemHand": "dialog-error", "SystemQuestion": "dialog-information",
                "SystemDefault": "bell"}
COOLDOWN = 1.5
_last = [0.0]


def _alias_has_sound(alias):
    """False when the user's Windows sound scheme has nothing assigned to this system sound."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"AppEvents\Schemes\Apps\.Default\{alias}\.Current") as k:
            return bool(winreg.QueryValueEx(k, "")[0])
    except Exception:
        return True      # can't tell: let Windows try


def _beeps(pattern):
    threading.Thread(target=lambda: [winsound.Beep(f, ms) for f, ms in pattern], daemon=True).start()   # Beep() blocks, so keep it off the GUI thread


def _run(cmd):
    try: subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError: return False
    return True


def _play_other(kind, val, bell):
    """macOS / Linux: system sounds through afplay / canberra / paplay, the terminal bell when nothing is available."""
    if kind == "wav":
        player = ("afplay",) if gui_platform.IS_MAC else next(((p,) for p in ("paplay", "aplay") if shutil.which(p)), None)
        if player and os.path.isfile(val) and _run([*player, val]): return
    elif gui_platform.IS_MAC:
        name = MAC_SOUNDS.get(val, "Tink") if kind == "alias" else "Tink"
        if _run(["afplay", f"/System/Library/Sounds/{name}.aiff"]): return
    else:
        name = LINUX_SOUNDS.get(val, "bell") if kind == "alias" else "message"
        if shutil.which("canberra-gtk-play") and _run(["canberra-gtk-play", "-i", name]): return
        for d in ("/usr/share/sounds/freedesktop/stereo",):
            f = os.path.join(d, name + ".oga")
            if shutil.which("paplay") and os.path.isfile(f) and _run(["paplay", f]): return
    if bell: bell()


def play(choice, custom_path="", bell=None):
    """choice: a key of SOUNDS, or 'Custom file' (uses custom_path).  Safe to call from the GUI thread; never raises."""
    try:
        if choice in (None, "None", ""): return
        spec = ("wav", custom_path) if choice == "Custom file" else SOUNDS.get(choice)
        if spec is None: return
        kind, val = spec
        if winsound is None: return _play_other(kind, val, bell)
        if kind == "alias":
            if _alias_has_sound(val): winsound.PlaySound(val, winsound.SND_ALIAS | winsound.SND_ASYNC)
            else: _beeps(FALLBACK.get(val, [(900, 100)]))
        elif kind == "wav" and os.path.isfile(val): winsound.PlaySound(val, winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif kind == "beep": _beeps(val)
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
