"""
JARVIS Sleep Coach — läuft als Daemon-Thread.
Erinnert Simon abends ans Schlafen gehen basierend auf:
  - brain.read("schlaf", "stunden")   → Schlafbedarf (default 8)
  - brain.read("schlaf", "fallback")  → Fallback-Schlafzeit wenn kein Wecker (default "23:30")
Reminder-Kette: 30min vorher (sanft) → pünktlich (direkt) → 30min danach (Konsequenz)

Kommuniziert ausschließlich über dispatcher.notify() — nie pipeline.process_text()
(sonst landet der Reminder als gefälschte Assistant-Nachricht in der History des
gerade "aktiven" Clients, siehe ClientManager.get_active_pipeline() — unabhängig
von Voice/Web-Kategorie. Gleicher Fix wie bei services/proactive.py).
"""
import datetime
import threading
import time

_manager = None
_alarm_service = None
_dispatcher = None
_thread: threading.Thread | None = None


def init(client_manager, alarm_service, dispatcher=None) -> None:
    global _manager, _alarm_service, _dispatcher, _thread
    _manager = client_manager
    _alarm_service = alarm_service
    _dispatcher = dispatcher
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()


def _get_config() -> tuple[int, str]:
    """Gibt (schlaf_stunden, fallback_zeit) zurück."""
    try:
        import brain
        stunden = brain.read("schlaf", "stunden")
        fallback = brain.read("schlaf", "fallback")
        return (int(stunden) if stunden else 8, fallback or "23:30")
    except Exception:
        return 8, "23:30"


def _get_sleep_target() -> datetime.datetime | None:
    """Berechnet Ziel-Schlafzeit für heute Nacht."""
    stunden, fallback = _get_config()
    now = datetime.datetime.now()

    # Ersten Wecker von heute Nacht / morgen früh finden
    alarms = _alarm_service.list_alarms() if _alarm_service else []
    next_fire = None
    for alarm in alarms:
        h, m = alarm.get("hour", 0), alarm.get("minute", 0)
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        if next_fire is None or candidate < next_fire:
            next_fire = candidate

    if next_fire:
        target = next_fire - datetime.timedelta(hours=stunden)
    else:
        # Kein Wecker → Fallback
        parts = fallback.split(":")
        target = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)

    return target


def _notify(text: str, priority: str = "normal", expires_in_min: int = 60) -> None:
    """Sendet Push via NotificationDispatcher. Nie via Pipeline."""
    if _dispatcher:
        _dispatcher.notify(text, channels=["dashboard"], priority=priority, expires_in_min=expires_in_min)
    else:
        print(f"[sleep_coach] (kein Dispatcher) {text}", flush=True)


def _loop() -> None:
    fired_today: set[str] = set()  # welche Reminder heute schon gesendet

    while True:
        try:
            now = datetime.datetime.now()
            today = now.date().isoformat()

            # Um Mitternacht fired_today zurücksetzen
            if not any(k.startswith(today) for k in fired_today):
                fired_today.clear()

            # Nur zwischen 20:00 und 02:00 aktiv
            if not (now.hour >= 20 or now.hour < 2):
                time.sleep(30)
                continue

            target = _get_sleep_target()
            if not target:
                time.sleep(60)
                continue

            diff = (target - now).total_seconds() / 60  # Minuten bis Schlafzeit

            stunden, _ = _get_config()
            alarms = _alarm_service.list_alarms() if _alarm_service else []
            first_alarm = min(
                (f"{a['hour']:02d}:{a['minute']:02d}" for a in alarms),
                default=None,
            )

            key_30 = f"{today}_30min"
            key_0 = f"{today}_0min"
            key_30late = f"{today}_30min_late"

            # 30min vor Schlafzeit: sanfter Hinweis
            if 28 <= diff <= 32 and key_30 not in fired_today:
                fired_today.add(key_30)
                wecker_info = f" Erster Wecker um {first_alarm}." if first_alarm else ""
                _notify(
                    f"In 30 Minuten ist deine Ziel-Schlafzeit ({target.strftime('%H:%M')}). "
                    f"Langsam runterfahren. 🌙{wecker_info}",
                    priority="normal",
                )

            # Pünktlich: direkter Reminder
            elif -2 <= diff <= 2 and key_0 not in fired_today:
                fired_today.add(key_0)
                wecker_info = f" Erster Wecker um {first_alarm}." if first_alarm else ""
                _notify(
                    f"Es ist jetzt deine Schlafzeit ({target.strftime('%H:%M')}).{wecker_info}",
                    priority="normal",
                )

            # 30min zu spät: leichte Konsequenz erwähnen
            elif -32 <= diff <= -28 and key_30late not in fired_today:
                fired_today.add(key_30late)
                knapp_info = (
                    f" Bei {stunden} Stunden Schlaf und Wecker um {first_alarm} wird es knapp."
                    if first_alarm else ""
                )
                _notify(
                    f"Du bist jetzt 30 Minuten nach deiner Schlafzeit ({target.strftime('%H:%M')}) "
                    f"noch wach.{knapp_info}",
                    priority="high",
                )

        except Exception as e:
            print(f"[sleep_coach] Fehler: {e}", flush=True)

        time.sleep(60)
