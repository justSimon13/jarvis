"""
JARVIS Alarm Service — Server-Seite.
Leitet SET_ALARM / CANCEL_ALARM / SNOOZE_ALARM als JSON an den Ziel-Satellite weiter.
Die eigentliche Alarm-Logik (PCM, Timer, Snooze-Zähler) läuft auf dem Satellite.
"""
import time
import protocol as P

_manager = None
_alarm_targets: dict[str, str | None] = {}  # alarm_id → target-Name (None = aktiver Client)


def init(client_manager) -> None:
    global _manager
    _manager = client_manager


def schedule(label: str, hour: int, minute: int,
             target: str | None = None, snooze_minutes: int = 9,
             max_snooze: int = 2, song: str | None = None) -> tuple[str, str]:
    alarm_id = f"alarm_{int(time.time() * 1000)}"
    fires_at = f"{hour:02d}:{minute:02d}"
    _alarm_targets[alarm_id] = target
    _route(target, {
        "type": P.SET_ALARM,
        "alarm_id": alarm_id,
        "hour": hour,
        "minute": minute,
        "label": label,
        "snooze_minutes": snooze_minutes,
        "max_snooze": max_snooze,
        "song": song,
    })
    return alarm_id, fires_at


def dismiss(alarm_id: str | None = None) -> bool:
    target = _alarm_targets.pop(alarm_id, None) if alarm_id else None
    _route(target, {"type": P.CANCEL_ALARM, "alarm_id": alarm_id})
    return True


def snooze_alarm(alarm_id: str | None = None, minutes: int = 9) -> tuple[bool, str]:
    target = _alarm_targets.get(alarm_id) if alarm_id else None
    _route(target, {"type": P.SNOOZE_ALARM, "alarm_id": alarm_id, "minutes": minutes})
    return True, f"Snooze {minutes} Minuten."


def _route(target: str | None, event: dict) -> None:
    if not _manager:
        return
    if target:
        _manager.send_event_to_name(target, event)
    else:
        _manager.send_event_to_active(event)
