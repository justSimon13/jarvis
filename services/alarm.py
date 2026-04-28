"""
JARVIS Alarm Service — loopt PCM-Beep auf Ziel-Client bis Dismiss.
Wecker klingelt durch bis Simon explizit sagt "Wecker aus" o.ä.
"""
import datetime
import threading
import time
from typing import Optional

import numpy as np

_active: dict[str, dict] = {}
_scheduled: dict[str, threading.Timer] = {}
_lock = threading.Lock()
_manager = None  # ClientManager, gesetzt via init()

_PCM_RATE = 24000  # muss mit protocol.PCM_SAMPLERATE übereinstimmen


def init(client_manager) -> None:
    global _manager
    _manager = client_manager


# ── Audio-Generierung ─────────────────────────────────────────────────────────

def _beep_pcm() -> bytes:
    sr = _PCM_RATE
    t = np.linspace(0, 0.35, int(sr * 0.35), endpoint=False)
    fade = int(0.02 * sr)
    env = np.ones(len(t))
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    # Zweiklang: 880 Hz + 1320 Hz
    tone = np.sin(2 * np.pi * 880 * t) * 0.6 + np.sin(2 * np.pi * 1320 * t) * 0.4
    return (tone * env * 0.85 * 32767).astype(np.int16).tobytes()


def _silence_pcm(ms: int) -> bytes:
    return np.zeros(int(_PCM_RATE * ms / 1000), dtype=np.int16).tobytes()


# ── Alarm-Loop ────────────────────────────────────────────────────────────────

def _loop(alarm_id: str, target: Optional[str]) -> None:
    beep = _beep_pcm()
    gap = _silence_pcm(800)

    while True:
        with _lock:
            if alarm_id not in _active:
                break

        if _manager:
            if target:
                _manager.send_audio_to_name(target, beep)
                _manager.send_audio_to_name(target, gap)
            else:
                _manager.send_audio_to_active(beep)
                _manager.send_audio_to_active(gap)

        time.sleep(1.3)


# ── Public API ────────────────────────────────────────────────────────────────

def schedule(label: str, hour: int, minute: int,
             target: Optional[str] = None, max_snooze: int = 0) -> tuple[str, str]:
    """Plant Alarm für HH:MM. Gibt (alarm_id, fires_at_str) zurück."""
    now = datetime.datetime.now()
    target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_dt <= now:
        target_dt += datetime.timedelta(days=1)

    alarm_id = f"alarm_{int(time.time() * 1000)}"
    seconds = (target_dt - now).total_seconds()

    def _fire():
        _start(alarm_id, label, target, max_snooze)

    t = threading.Timer(seconds, _fire)
    t.daemon = True
    t.start()

    with _lock:
        _scheduled[alarm_id] = t

    return alarm_id, target_dt.strftime("%H:%M")


def _start(alarm_id: str, label: str, target: Optional[str], max_snooze: int) -> None:
    with _lock:
        _scheduled.pop(alarm_id, None)
        _active[alarm_id] = {
            "label": label,
            "target": target,
            "max_snooze": max_snooze,
            "snooze_count": 0,
            "started_at": time.time(),
        }
    threading.Thread(target=_loop, args=(alarm_id, target), daemon=True).start()


def dismiss(alarm_id: Optional[str] = None) -> bool:
    """Stoppt Alarm sofort. Ohne alarm_id: alle aktiven Alarme."""
    with _lock:
        if alarm_id:
            existed = alarm_id in _active or alarm_id in _scheduled
            if alarm_id in _scheduled:
                _scheduled.pop(alarm_id).cancel()
            _active.pop(alarm_id, None)
            return existed
        count = len(_active) + len(_scheduled)
        for t in _scheduled.values():
            t.cancel()
        _scheduled.clear()
        _active.clear()
        return count > 0


def snooze_alarm(alarm_id: Optional[str] = None, minutes: int = 5) -> tuple[bool, str]:
    with _lock:
        if alarm_id:
            entry = _active.get(alarm_id)
        else:
            entry_id = next(iter(_active), None)
            entry = _active.get(entry_id) if entry_id else None
            alarm_id = entry_id
        if not entry:
            return False, "Kein aktiver Alarm."
        if entry["snooze_count"] >= entry["max_snooze"]:
            return False, f"Kein Snooze erlaubt (Limit: {entry['max_snooze']})."
        entry["snooze_count"] += 1
        snap = dict(entry)
        _active.pop(alarm_id)

    def _restart():
        time.sleep(minutes * 60)
        _start(alarm_id, snap["label"], snap["target"],
               snap["max_snooze"] - snap["snooze_count"])

    threading.Thread(target=_restart, daemon=True).start()
    return True, f"Snooze {minutes} min. ({snap['snooze_count']}/{snap['max_snooze']} genutzt)"


def get_active() -> list[dict]:
    with _lock:
        result = []
        for aid, a in _scheduled.items():
            result.append({"id": aid, "label": a, "state": "geplant"})
        for aid, a in _active.items():
            result.append({"id": aid, "label": a["label"], "state": "klingelt",
                           "snoozes": a["snooze_count"]})
        return result
