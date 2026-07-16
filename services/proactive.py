"""
JARVIS Proactive Daemon — echter Push via NotificationDispatcher.

Zwei Typen von Checks:
  Integration Checks  → externe APIs (Kalender, Email, Todos, Followups)
  User Rules          → zeitbasierte Regeln aus brain.rules (via Gespräch konfiguriert)

Kommuniziert ausschließlich über dispatcher.notify() — nie pipeline.process_text().
Fired-State wird in ~/.jarvis/proactive_state.json gespeichert und überlebt Restarts.
"""
import datetime
import json
import threading
import time
from pathlib import Path

_manager = None
_alarm_service = None
_dispatcher = None
_thread: threading.Thread | None = None

_STATE_FILE = Path.home() / ".jarvis" / "proactive_state.json"


def init(client_manager, alarm_service, dispatcher=None) -> None:
    global _manager, _alarm_service, _dispatcher, _thread
    _manager = client_manager
    _alarm_service = alarm_service
    _dispatcher = dispatcher
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()


# ── Fired-State (persistent) ──────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text())
    except Exception:
        pass
    return {"rules": {}, "calendar": {}, "email": {}, "todos": {}, "followups": {}}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
    except Exception as e:
        print(f"[proactive] State-Save Fehler: {e}", flush=True)


# ── Push-Kanal ────────────────────────────────────────────────────────────────

def _notify(text: str, priority: str = "normal", expires_in_min: int = 60) -> None:
    """Sendet Push via NotificationDispatcher. Nie via Pipeline."""
    if _dispatcher:
        _dispatcher.notify(text, channels=["dashboard"], priority=priority, expires_in_min=expires_in_min)
    else:
        print(f"[proactive] (kein Dispatcher) {text}", flush=True)


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _get_config() -> dict:
    try:
        import brain
        raw = brain.read(section="proaktiv") or {}
        return {
            "kalender_minuten": int(raw.get("kalender_minuten") or 15),
            "kalender_aktiv":   str(raw.get("kalender_aktiv",   "true")).lower() != "false",
            "email_intervall":  int(raw.get("email_intervall")  or 10),
            "email_aktiv":      str(raw.get("email_aktiv",      "true")).lower() != "false",
            "todos_aktiv":      str(raw.get("todos_aktiv",      "true")).lower() != "false",
            "followups_aktiv":  str(raw.get("followups_aktiv",  "true")).lower() != "false",
        }
    except Exception:
        return {
            "kalender_minuten": 15, "kalender_aktiv": True,
            "email_intervall": 10,  "email_aktiv": True,
            "todos_aktiv": True,    "followups_aktiv": True,
        }


def _is_user_asleep() -> bool:
    if not _alarm_service:
        return False
    now = datetime.datetime.now()
    for alarm in _alarm_service.list_alarms():
        h, m = alarm.get("hour", 0), alarm.get("minute", 0)
        alarm_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if alarm.get("fire_ts") is None:
            diff = (alarm_time - now).total_seconds() / 60
            if -120 <= diff <= 240:
                return True
    return False


# ── Main Loop ─────────────────────────────────────────────────────────────────

def _loop() -> None:
    state = _load_state()
    last_email_check = 0.0
    last_todo_check = 0.0
    last_followup_check = 0.0
    last_tracking_check = 0.0
    known_email_ids: set[str] = set(state.get("email", {}).keys())
    first_email_run = True

    while True:
        try:
            now = datetime.datetime.now()
            today = now.date().isoformat()
            cfg = _get_config()

            # Um Mitternacht Tages-State leeren
            for section in ("calendar", "todos", "followups", "rules", "tracking"):
                state.setdefault(section, {})
                stale = [k for k in state[section] if not k.startswith(today)]
                for k in stale:
                    del state[section][k]

            if cfg["kalender_aktiv"]:
                _check_calendar(now, today, cfg, state)

            if cfg["email_aktiv"]:
                if time.time() - last_email_check >= cfg["email_intervall"] * 60:
                    last_email_check = time.time()
                    _check_email(state, known_email_ids, first_email_run)
                    first_email_run = False

            if cfg["todos_aktiv"]:
                if time.time() - last_todo_check >= 3600:  # 1× stündlich
                    last_todo_check = time.time()
                    _check_todos(today, state)

            if cfg["followups_aktiv"]:
                if time.time() - last_followup_check >= 3600:
                    last_followup_check = time.time()
                    _check_followups(today, state)

            _check_user_rules(now, today, state)

            # Tracking-Checks 1× täglich um 18:00
            if now.hour == 18 and time.time() - last_tracking_check >= 3600:
                last_tracking_check = time.time()
                _check_training(today, state)

            _save_state(state)

        except Exception as e:
            print(f"[proactive] Loop-Fehler: {e}", flush=True)

        time.sleep(60)


# ── Integration Checks ────────────────────────────────────────────────────────

def _check_calendar(now: datetime.datetime, today: str, cfg: dict, state: dict) -> None:
    try:
        from services import calendar as cal_service
        events = cal_service.query(days_ahead=1)
    except Exception as e:
        print(f"[proactive] Kalender-Fehler: {e}", flush=True)
        return

    reminder_min = cfg["kalender_minuten"]

    for event in events:
        start_str = event.get("start", "")
        if not start_str or "T" not in start_str:
            continue

        try:
            start_dt = datetime.datetime.fromisoformat(
                start_str.replace("Z", "+00:00")
            ).astimezone().replace(tzinfo=None)
        except Exception:
            continue

        diff_min = (start_dt - now).total_seconds() / 60
        event_id = event.get("event_id", start_str)
        title = event.get("title", "Termin")

        key_main = f"{today}_{event_id}_main"
        if (reminder_min - 1) <= diff_min <= (reminder_min + 1) and key_main not in state["calendar"]:
            state["calendar"][key_main] = now.isoformat()
            if _is_user_asleep():
                _notify(
                    f"Termin in {reminder_min} Min: '{title}' um {start_dt.strftime('%H:%M')} — Simon schläft noch!",
                    priority="high",
                )
            else:
                _notify(
                    f"Termin in {reminder_min} Min: {title} um {start_dt.strftime('%H:%M')}",
                    priority="normal",
                )

        key_followup = f"{today}_{event_id}_followup"
        if 4 <= diff_min <= 6 and key_followup not in state["calendar"] and _is_user_asleep():
            state["calendar"][key_followup] = now.isoformat()
            _notify(f"Termin in 5 Min: {title} — Simon schläft noch!", priority="high")


def _check_email(state: dict, known_ids: set, first_run: bool) -> None:
    try:
        from services import email as email_service
        import brain
        settings = brain.read(section="settings") or {}
        vip_list = settings.get("contacts", {}).get("email_vip", [])
        if not vip_list:
            return
        emails = email_service.query(limit=10)
    except Exception as e:
        print(f"[proactive] Email-Fehler: {e}", flush=True)
        return

    for mail in emails:
        if mail.get("error") or not mail.get("vip"):
            continue
        uid = f"{mail.get('from', '')}|{mail.get('subject', '')}|{mail.get('date', '')}"
        if uid in known_ids:
            continue
        known_ids.add(uid)
        state["email"][uid] = datetime.datetime.now().isoformat()
        if first_run:
            continue

        sender = mail.get("from", "Unbekannt")
        subject = mail.get("subject", "(kein Betreff)")
        _notify(f"VIP-Email von {sender}: {subject}", priority="high", expires_in_min=120)


def _check_todos(today: str, state: dict) -> None:
    """Einmal täglich: überfällige Todos als Push."""
    key = f"{today}_todos"
    if key in state["todos"]:
        return
    try:
        import context
        conn = context._get_db()
        todos = context._get_cached(conn, "todos") or []
        conn.close()
    except Exception as e:
        print(f"[proactive] Todo-Fehler: {e}", flush=True)
        return

    overdue = [t for t in todos if t.get("datum") and t["datum"] < today]
    if overdue:
        state["todos"][key] = datetime.datetime.now().isoformat()
        count = len(overdue)
        _notify(
            f"{count} überfällige{'r' if count == 1 else ''} Todo{'s' if count > 1 else ''}: "
            + ", ".join(t.get("name", "?") for t in overdue[:3])
            + ("…" if count > 3 else ""),
            priority="normal",
            expires_in_min=480,
        )


def _check_followups(today: str, state: dict) -> None:
    """Einmal täglich: fällige Followups als Push."""
    key = f"{today}_followups"
    if key in state["followups"]:
        return
    try:
        import brain
        followups = brain.read("followups") or {}
    except Exception as e:
        print(f"[proactive] Followup-Fehler: {e}", flush=True)
        return

    due = []
    for k, v in followups.items():
        due_date = v.get("due") if isinstance(v, dict) else None
        if not due_date or due_date <= today:
            text = v.get("text", k) if isinstance(v, dict) else str(v)
            due.append(text)

    if due:
        state["followups"][key] = datetime.datetime.now().isoformat()
        count = len(due)
        _notify(
            f"{count} offene{'r' if count == 1 else ''} Followup{'s' if count > 1 else ''}: "
            + ", ".join(due[:3])
            + ("…" if count > 3 else ""),
            priority="normal",
            expires_in_min=480,
        )


# ── User Rules ────────────────────────────────────────────────────────────────

def _check_user_rules(now: datetime.datetime, today: str, state: dict) -> None:
    """Liest brain.rules und feuert zeitbasierte Regeln."""
    try:
        import brain
        rules = brain.read("rules")
        if not isinstance(rules, dict):
            return
    except Exception:
        return

    now_time = now.strftime("%H:%M")
    weekday_map = {0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday",
                   4: "friday", 5: "saturday", 6: "sunday"}
    today_weekday = weekday_map[now.weekday()]

    for rule_id, rule in rules.items():
        if not isinstance(rule, dict) or not rule.get("active", True):
            continue

        schedule = rule.get("schedule", "")
        text = rule.get("text", "")
        if not schedule or not text:
            continue

        fire_key = f"{today}_{rule_id}"
        if fire_key in state["rules"]:
            continue

        if _matches_schedule(schedule, now_time, today_weekday):
            state["rules"][fire_key] = now.isoformat()
            priority = "high" if rule.get("escalate") else "normal"
            _notify(text, priority=priority, expires_in_min=120)
            print(f"[proactive] Rule gefeuert: {rule_id}", flush=True)


def _matches_schedule(schedule: str, now_time: str, today_weekday: str) -> bool:
    """
    Unterstützte Formate:
      daily@HH:MM              → jeden Tag um HH:MM
      weekly@weekday@HH:MM     → jeden <weekday> um HH:MM
    Toleranz: ±1 Minute.
    """
    parts = schedule.split("@")
    if not parts:
        return False

    freq = parts[0]

    if freq == "daily" and len(parts) == 2:
        return _time_matches(parts[1], now_time)

    if freq == "weekly" and len(parts) == 3:
        weekday, time_str = parts[1], parts[2]
        return weekday == today_weekday and _time_matches(time_str, now_time)

    return False


def _time_matches(target: str, current: str) -> bool:
    """True wenn current innerhalb ±1 Minute von target liegt."""
    try:
        th, tm = map(int, target.split(":"))
        ch, cm = map(int, current.split(":"))
        target_min = th * 60 + tm
        current_min = ch * 60 + cm
        return abs(target_min - current_min) <= 1
    except Exception:
        return False


# ── Tracking Checks ───────────────────────────────────────────────────────────

def _check_training(today: str, state: dict) -> None:
    """1× täglich um 18 Uhr: prüft ob Training seit >2 Tagen fehlt."""
    key = f"{today}_training"
    if key in state.get("tracking", {}):
        return
    try:
        import tracking
        last = tracking.get_last_log("sport", "training")
    except Exception as e:
        print(f"[proactive] Training-Check Fehler: {e}", flush=True)
        return

    if not last:
        return

    try:
        last_date = datetime.date.fromisoformat(last["date"])
        days_since = (datetime.date.today() - last_date).days
    except Exception:
        return

    if days_since >= 2:
        state.setdefault("tracking", {})[key] = datetime.datetime.now().isoformat()
        last_info = last.get("text_value") or last.get("notes") or last["date"]
        _notify(
            f"Training-Reminder: Letztes Training vor {days_since} Tag{'en' if days_since > 1 else ''}. Zuletzt: {last_info}",
            priority="normal",
            expires_in_min=360,
        )
