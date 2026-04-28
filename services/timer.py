import threading
import time
import datetime
from typing import Callable

_timers: dict[str, dict] = {}
_lock = threading.Lock()
_speak_callback: Callable[[str], None] | None = None


def set_speak_callback(fn: Callable[[str], None]):
    global _speak_callback
    _speak_callback = fn


def set_timer(label: str, seconds: int) -> str:
    timer_id = f"timer_{int(time.time() * 1000)}"
    fires_at = time.time() + seconds
    t = threading.Timer(seconds, _fire, args=[timer_id, label])
    t.daemon = True
    t.start()
    with _lock:
        _timers[timer_id] = {"id": timer_id, "label": label, "fires_at": fires_at, "timer": t}
    return timer_id


def set_alarm(label: str, hour: int, minute: int) -> tuple[str, datetime.datetime]:
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    seconds = int((target - now).total_seconds())
    timer_id = set_timer(label, seconds)
    return timer_id, target


def cancel(timer_id: str) -> bool:
    with _lock:
        entry = _timers.pop(timer_id, None)
    if entry:
        entry["timer"].cancel()
        return True
    return False


def cancel_by_label(label: str) -> bool:
    with _lock:
        match = next((e for e in _timers.values() if e["label"].lower() == label.lower()), None)
    if match:
        return cancel(match["id"])
    return False


def list_active() -> list[dict]:
    now = time.time()
    with _lock:
        result = []
        for e in _timers.values():
            remaining = int(e["fires_at"] - now)
            result.append({
                "id": e["id"],
                "label": e["label"],
                "fires_in_seconds": max(0, remaining),
                "fires_at": datetime.datetime.fromtimestamp(e["fires_at"]).strftime("%H:%M:%S"),
            })
    return result


def _fire(timer_id: str, label: str):
    with _lock:
        _timers.pop(timer_id, None)
    message = f"Sir, {label}!"
    _notify(label)
    if _speak_callback:
        _speak_callback(message)


def _notify(message: str):
    pass  # Benachrichtigung via TTS-Callback ausreichend
