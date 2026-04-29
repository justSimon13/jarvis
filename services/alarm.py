"""
JARVIS Alarm Service — Server-Seite.
Leitet SET_ALARM / CANCEL_ALARM / SNOOZE_ALARM als JSON an den Ziel-Satellite weiter.
Registry mit Persistenz + Schlaf-Tracking in SQLite.
"""
import datetime
import json
import sqlite3
import time
from pathlib import Path
import protocol as P

_manager = None
_registry: dict[str, dict] = {}
_STATE_FILE = Path(__file__).parent.parent / "alarm_registry.json"
_DB_FILE = Path.home() / ".jarvis" / "sleep.db"


def init(client_manager) -> None:
    global _manager
    _manager = client_manager
    _init_db()
    _load()


def _init_db() -> None:
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_FILE) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS sleep_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                alarm_id      TEXT,
                label         TEXT,
                scheduled_time TEXT,
                fire_ts       REAL,
                dismiss_ts    REAL,
                snooze_count  INTEGER DEFAULT 0,
                dismissed_from TEXT,
                weekday       INTEGER
            )
        """)


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


def _auto_max_snooze(hour: int, minute: int) -> int:
    return 3


def schedule(label: str, hour: int, minute: int,
             target: str | None = None, snooze_minutes: int = 9,
             max_snooze: int | None = None, song: str | None = None) -> tuple[str, str]:
    alarm_id = f"alarm_{int(time.time() * 1000)}"
    fires_at = f"{hour:02d}:{minute:02d}"
    effective_max_snooze = max_snooze if max_snooze is not None else _auto_max_snooze(hour, minute)
    _registry[alarm_id] = {
        "label": label,
        "hour": hour,
        "minute": minute,
        "fires_at": fires_at,
        "target": target,
        "snooze_minutes": snooze_minutes,
        "max_snooze": effective_max_snooze,
        "snooze_count": 0,
        "song": song,
        "fire_ts": None,
    }
    _save()
    _route(target, {
        "type": P.SET_ALARM,
        "alarm_id": alarm_id,
        "hour": hour,
        "minute": minute,
        "label": label,
        "snooze_minutes": snooze_minutes,
        "max_snooze": effective_max_snooze,
        "song": song,
    })
    return alarm_id, fires_at


def on_ringing(alarm_id: str) -> None:
    """Wird aufgerufen wenn Satellite meldet dass Wecker klingelt."""
    if alarm_id in _registry:
        _registry[alarm_id]["fire_ts"] = time.time()
        _save()


def dismiss(alarm_id: str | None = None, dismissed_from: str = "") -> bool:
    if alarm_id:
        entries = {alarm_id: _registry.pop(alarm_id, None)}
    else:
        entries = dict(_registry)
        _registry.clear()
    _save()
    for aid, entry in entries.items():
        if entry:
            _log_sleep(aid, entry, dismissed_from)
            target = entry.get("target")
            _route(target, {"type": P.CANCEL_ALARM, "alarm_id": aid})
    return True


def on_dismissed(alarm_id: str, snooze_count: int) -> None:
    """Satellite hat Wecker endgültig beendet (z.B. max_snooze erreicht)."""
    entry = _registry.pop(alarm_id, None)
    if entry:
        entry["snooze_count"] = snooze_count
        _log_sleep(alarm_id, entry, "")
        _save()


def _log_sleep(alarm_id: str, entry: dict, dismissed_from: str) -> None:
    fire_ts = entry.get("fire_ts")
    if not fire_ts:
        return
    try:
        fire_dt = datetime.datetime.fromtimestamp(fire_ts)
        with sqlite3.connect(_DB_FILE) as con:
            con.execute("""
                INSERT INTO sleep_log
                    (alarm_id, label, scheduled_time, fire_ts, dismiss_ts,
                     snooze_count, dismissed_from, weekday)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alarm_id,
                entry.get("label", ""),
                entry.get("fires_at", ""),
                fire_ts,
                time.time(),
                entry.get("snooze_count", 0),
                dismissed_from,
                fire_dt.weekday(),
            ))
    except Exception as e:
        print(f"[alarm] Sleep-Log Fehler: {e}", flush=True)


def snooze_alarm(alarm_id: str | None = None, minutes: int = 9) -> tuple[bool, str]:
    entry = _registry.get(alarm_id) if alarm_id else next(iter(_registry.values()), None)
    if entry:
        entry["snooze_count"] = entry.get("snooze_count", 0) + 1
        _save()
    target = entry["target"] if entry else None
    _route(target, {"type": P.SNOOZE_ALARM, "alarm_id": alarm_id, "minutes": minutes})
    return True, f"Snooze {minutes} Minuten."


def sync_from_client(client_name: str, alarms: list[dict]) -> None:
    for aid in [k for k, v in _registry.items() if v.get("target") == client_name]:
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
                "snooze_count": 0,
                "song": alarm.get("song"),
                "fire_ts": None,
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
