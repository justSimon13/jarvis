"""
JARVIS Alarm Service — Server-Seite.
Leitet SET_ALARM / CANCEL_ALARM / SNOOZE_ALARM als JSON an den Ziel-Satellite weiter.
Die eigentliche Alarm-Logik (PCM, Timer, Snooze-Zähler) läuft auf dem Satellite.
Registry mit Persistenz damit JARVIS aktive Wecker kennt.
"""
import json
import time
from pathlib import Path
import protocol as P

_manager = None
_registry: dict[str, dict] = {}  # alarm_id → {label, hour, minute, target, song, ...}
_STATE_FILE = Path(__file__).parent.parent / "alarm_registry.json"


def init(client_manager) -> None:
    global _manager
    _manager = client_manager
    _load()


def _load() -> None:
    if _STATE_FILE.exists():
        try:
            _registry.update(json.loads(_STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass


def _save() -> None:
    try:
        _STATE_FILE.write_text(json.dumps(_registry, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def schedule(label: str, hour: int, minute: int,
             target: str | None = None, snooze_minutes: int = 9,
             max_snooze: int = 2, song: str | None = None) -> tuple[str, str]:
    alarm_id = f"alarm_{int(time.time() * 1000)}"
    fires_at = f"{hour:02d}:{minute:02d}"
    _registry[alarm_id] = {
        "label": label,
        "hour": hour,
        "minute": minute,
        "fires_at": fires_at,
        "target": target,
        "snooze_minutes": snooze_minutes,
        "max_snooze": max_snooze,
        "song": song,
    }
    _save()
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
    if alarm_id:
        entry = _registry.pop(alarm_id, None)
        target = entry["target"] if entry else None
    else:
        target = None
        _registry.clear()
    _save()
    _route(target, {"type": P.CANCEL_ALARM, "alarm_id": alarm_id})
    return True


def snooze_alarm(alarm_id: str | None = None, minutes: int = 9) -> tuple[bool, str]:
    entry = _registry.get(alarm_id) if alarm_id else None
    target = entry["target"] if entry else None
    _route(target, {"type": P.SNOOZE_ALARM, "alarm_id": alarm_id, "minutes": minutes})
    return True, f"Snooze {minutes} Minuten."


def sync_from_client(client_name: str, alarms: list[dict]) -> None:
    """Überschreibt alle Einträge dieses Clients mit der aktuellen Liste vom Client."""
    # Alte Einträge dieses Clients entfernen
    for aid in [k for k, v in _registry.items() if v.get("target") == client_name or (not v.get("target") and not client_name)]:
        _registry.pop(aid, None)
    for alarm in alarms:
        aid = alarm.get("alarm_id")
        if aid:
            _registry[aid] = {
                "label": alarm.get("label", "Wecker"),
                "hour": alarm.get("hour", 0),
                "minute": alarm.get("minute", 0),
                "fires_at": alarm.get("fires_at", "?"),
                "target": client_name,
                "snooze_minutes": alarm.get("snooze_minutes", 9),
                "max_snooze": alarm.get("max_snooze", 2),
                "song": alarm.get("song"),
            }
    _save()


def list_alarms() -> list[dict]:
    return [{"alarm_id": aid, **entry} for aid, entry in _registry.items()]


def _route(target: str | None, event: dict) -> None:
    if not _manager:
        return
    if target:
        _manager.send_event_to_name(target, event)
    else:
        _manager.send_event_to_active(event)
