"""
JARVIS Proactive Service — Kalender-Reminder und VIP-Email-Push.

Konfigurierbar via brain (section='proaktiv'):
  kalender_minuten  → Vorwarnzeit vor Terminen in Minuten (default 15)
  kalender_aktiv    → Kalender-Reminder ein/aus (default true)
  email_intervall   → Email-Check alle N Minuten (default 10)
  email_aktiv       → VIP-Email-Push ein/aus (default true)

Alles einstellbar per Sprachbefehl an JARVIS.
"""
import datetime
import threading
import time

_manager = None
_alarm_service = None
_thread: threading.Thread | None = None


def init(client_manager, alarm_service) -> None:
    global _manager, _alarm_service, _thread
    _manager = client_manager
    _alarm_service = alarm_service
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()


def _get_config() -> dict:
    try:
        import brain
        raw = brain.read(section="proaktiv") or {}
        return {
            "kalender_minuten": int(raw.get("kalender_minuten") or 15),
            "kalender_aktiv": str(raw.get("kalender_aktiv", "true")).lower() != "false",
            "email_intervall": int(raw.get("email_intervall") or 10),
            "email_aktiv": str(raw.get("email_aktiv", "true")).lower() != "false",
        }
    except Exception:
        return {"kalender_minuten": 15, "kalender_aktiv": True, "email_intervall": 10, "email_aktiv": True}


def _is_user_asleep() -> bool:
    """True wenn ein Wecker für heute Morgen noch nicht geklingelt hat."""
    if not _alarm_service:
        return False
    now = datetime.datetime.now()
    for alarm in _alarm_service.list_alarms():
        h, m = alarm.get("hour", 0), alarm.get("minute", 0)
        alarm_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        # Wecker der noch nicht abgefeuert hat und in den letzten 2h / nächsten 4h liegt
        if alarm.get("fire_ts") is None:
            diff = (alarm_time - now).total_seconds() / 60
            if -120 <= diff <= 240:
                return True
    return False


def _send(text: str) -> None:
    pipeline = _manager.get_active_pipeline() if _manager else None
    if not pipeline:
        return
    try:
        pipeline.process_text(text, use_tts=True)
    except Exception as e:
        print(f"[proactive] Fehler: {e}", flush=True)


def _loop() -> None:
    fired: set[str] = set()
    last_email_check = 0.0
    known_email_ids: set[str] = set()
    first_email_run = True

    while True:
        try:
            now = datetime.datetime.now()
            today = now.date().isoformat()
            cfg = _get_config()

            # Um Mitternacht fired-Set leeren
            if not any(k.startswith(today) for k in fired):
                fired.clear()

            if cfg["kalender_aktiv"]:
                _check_calendar(now, today, cfg, fired)

            if cfg["email_aktiv"]:
                if time.time() - last_email_check >= cfg["email_intervall"] * 60:
                    last_email_check = time.time()
                    _check_email(known_email_ids, first_email_run)
                    first_email_run = False

        except Exception as e:
            print(f"[proactive] Loop-Fehler: {e}", flush=True)

        time.sleep(60)


def _check_calendar(now: datetime.datetime, today: str, cfg: dict, fired: set) -> None:
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
            continue  # Ganztages-Events überspringen

        try:
            start_dt = datetime.datetime.fromisoformat(
                start_str.replace("Z", "+00:00")
            ).astimezone().replace(tzinfo=None)
        except Exception:
            continue

        diff_min = (start_dt - now).total_seconds() / 60
        event_id = event.get("event_id", start_str)
        title = event.get("title", "Termin")

        # Haupt-Reminder: X Minuten vorher (±1 Minute Toleranz)
        key_main = f"{today}_{event_id}_main"
        if (reminder_min - 1) <= diff_min <= (reminder_min + 1) and key_main not in fired:
            fired.add(key_main)
            if _is_user_asleep():
                _send(
                    f"[PROAKTIV] Simon schläft noch, aber sein Termin '{title}' ist in "
                    f"{reminder_min} Minuten (um {start_dt.strftime('%H:%M')}). "
                    f"Weck ihn auf und erinnere ihn dringend daran."
                )
            else:
                _send(
                    f"[PROAKTIV] Erinnere Simon kurz und freundlich: "
                    f"In {reminder_min} Minuten hat er '{title}' (um {start_dt.strftime('%H:%M')}). "
                    f"Kein langer Text."
                )

        # Follow-up bei 5 Minuten wenn er noch schläft
        key_followup = f"{today}_{event_id}_followup"
        if 4 <= diff_min <= 6 and key_followup not in fired and _is_user_asleep():
            fired.add(key_followup)
            _send(
                f"[PROAKTIV] DRINGEND: Simon schläft noch und sein Termin '{title}' "
                f"ist in 5 Minuten! Weck ihn sofort auf."
            )


def _check_email(known_ids: set, first_run: bool) -> None:
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
        if first_run:
            continue  # Erste Prüfung: nur bekannte Mails einlesen, nicht melden

        sender = mail.get("from", "Unbekannt")
        subject = mail.get("subject", "(kein Betreff)")
        preview = mail.get("preview", "")[:120]
        _send(
            f"[PROAKTIV] Simon hat eine neue VIP-Email. Informiere ihn kurz sachlich: "
            f"Von: {sender}. Betreff: {subject}. "
            f"{'Vorschau: ' + preview if preview else ''} "
            f"Kurz, kein langer Text."
        )
