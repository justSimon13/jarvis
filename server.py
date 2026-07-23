"""
J.A.R.V.I.S. WebSocket-Server — läuft auf dem HP EliteDesk (Linux, 24/7).
Sprach-/Raum-Clients teilen sich eine gemeinsame, raumübergreifende Historie;
jeder Web-Tab (jarvis-web) führt seine eigene, isolierte Konversation.
Gesichert via Tailscale (kein eigenes Auth nötig).

Start: python3 server.py
"""
from __future__ import annotations
import asyncio
import json
import os
import signal
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import websockets

import brain
import config
import context
import knowledge
import learning
import protocol as P
import session_memory
import stt
import tracking
from client_manager import ClientManager
from pipeline import JarvisPipeline
from services import alarm as alarm_service
from services import client_music as client_music_service
from services import coding_engine
from services import sleep_coach
from services import proactive as proactive_service
from services.notification_dispatcher import NotificationDispatcher
import local_data

HOST = os.getenv("JARVIS_HOST", "0.0.0.0")
PORT = int(os.getenv("JARVIS_PORT", "8765"))

manager = ClientManager()
dispatcher = NotificationDispatcher(manager)

# api_history: aktuelle Session → geht an Claude API, wird bei Inaktivität geleert
# display_history: vollständiges Protokoll → für Transcript-Ansicht im Dashboard
#
# "voice" (alle Sprach-/Raum-Clients wie der Wohnzimmer-Satellite) teilen sich
# weiterhin EINE gemeinsame Historie — das ist für Ambient-Nutzung gewollt.
# "web" (Dashboard-Rolle, z.B. jarvis-web) ist PRO TAB isoliert: jede Verbindung
# bekommt ihre eigene Historie, keyed über eine stabile tab_id (vom Client in
# client_hello mitgeschickt, in dessen sessionStorage — überlebt Reload/kurze
# Reconnects im selben Tab, aber ein neuer Tab bekommt eine neue ID). Vorher
# teilten sich ALLE Web-Tabs eine einzige Historie: "+ Neuer Chat" in Tab A hat
# dann Tab B's laufendes Gespräch mit gelöscht (2026-07-20 entdeckt — zwei
# gleichzeitig offene jarvis-web-Tabs, einer hat die Session des anderen
# weggerissen).
api_histories: dict[str, list[dict] | dict[str, list[dict]]] = {"voice": [], "web": {}}
display_histories: dict[str, list[dict] | dict[str, list[dict]]] = {"voice": [], "web": {}}
history_lock = threading.Lock()  # ein Lock für alles reicht bei dieser Nutzungsgröße
llm_semaphore = threading.Semaphore(1)


def _category_for_role(role: str) -> str:
    return "web" if role == "dashboard" else "voice"


def _get_api_history(category: str, tab_id: str) -> list[dict]:
    if category == "voice":
        return api_histories["voice"]
    return api_histories["web"].setdefault(tab_id, [])


def _get_display_history(category: str, tab_id: str) -> list[dict]:
    if category == "voice":
        return display_histories["voice"]
    return display_histories["web"].setdefault(tab_id, [])


def _clear_api_history(category: str, tab_id: str) -> None:
    # WICHTIG: in-place .clear(), nicht durch eine neue Liste ersetzen — die
    # Pipeline hält eine Referenz auf das Original-Objekt (shared_history bei
    # der Konstruktion), ein Ersatz-Objekt würde die Pipeline von zukünftigen
    # Lookups über _get_api_history() abkoppeln (stille Divergenz → Datenverlust).
    _get_api_history(category, tab_id).clear()


# Welche Clients haben zur aktuellen Session beigetragen — nur für die Session-
# Liste in jarvis-web (Filterung), NICHT Teil von api_history (das geht 1:1 als
# Messages an die Anthropic API, keine Extra-Keys dort). Für "web" pro tab_id.
_session_clients: dict[str, set[str] | dict[str, set[str]]] = {"voice": set(), "web": {}}


def _get_session_clients(category: str, tab_id: str) -> set[str]:
    if category == "voice":
        return _session_clients["voice"]
    return _session_clients["web"].setdefault(tab_id, set())


def _reset_session_clients(category: str, tab_id: str) -> None:
    if category == "voice":
        _session_clients["voice"] = set()
    else:
        _session_clients["web"][tab_id] = set()


# Welche sessions.db-Zeile gerade zu einem Web-Tab gehört — wird bei jeder
# Nachricht per session_memory.upsert() aktualisiert (nicht erst beim Trennen),
# damit ein einfach geschlossener Tab keine Konversation mehr verschluckt.
# None = noch keine Zeile angelegt (erste Nachricht dieser Session steht noch aus).
_active_session_ids: dict[str, int | None] = {}


def _get_active_session_id(tab_id: str) -> int | None:
    return _active_session_ids.get(tab_id)


def _set_active_session_id(tab_id: str, session_id: int | None) -> None:
    if session_id is None:
        _active_session_ids.pop(tab_id, None)
    else:
        _active_session_ids[tab_id] = session_id


SATELLITE_TIMEOUT = 28800  # 8h Inaktivität → neue Session (nur Voice-Clients)
_last_activity_ts: float = 0.0
_ROLLING_WINDOW = 60       # Max. Nachrichten in api_history

_HISTORY_FILE = Path.home() / ".jarvis" / "history.json"
_HISTORY_KEEP = 200


def _save_history():
    """Schreibt die letzten _HISTORY_KEEP Einträge der voice-History (inkl. session_break)
    auf Disk. "web" wird bewusst NICHT persistiert — die ist jetzt pro Tab isoliert und hat
    keine stabile Identität über einen Neustart hinweg (jede Verbindung neu ist ein neuer
    Tab aus Server-Sicht); das archivierte Web-Gespräch landet stattdessen in sessions.db
    (siehe _save_all_sessions_on_shutdown)."""
    try:
        with history_lock:
            to_save = {"voice": [
                m for m in display_histories["voice"][-_HISTORY_KEEP:]
                if m.get("content") == "session_break" or isinstance(m.get("content"), str)
            ]}
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_FILE.write_text(json.dumps(to_save, ensure_ascii=False))
    except Exception as e:
        print(f"[server] History-Save Fehler: {e}", flush=True)


def _load_history():
    """Lädt gespeicherte voice-History beim Start in display_histories["voice"] und fügt
    einen Session-Break ein. "web" wird nicht geladen (siehe _save_history)."""
    try:
        if not _HISTORY_FILE.exists():
            return
        data = json.loads(_HISTORY_FILE.read_text())
        if not isinstance(data, dict):
            return
        entries = data.get("voice")
        if not isinstance(entries, list) or not entries:
            return
        display_histories["voice"].extend(entries)
        last = display_histories["voice"][-1]
        if last.get("content") != "session_break":
            display_histories["voice"].append({
                "content": "session_break",
                "role": "system",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })
        print(f"[server] History geladen: {len(entries)} Einträge", flush=True)
    except Exception as e:
        print(f"[server] History-Load Fehler: {e}", flush=True)


def _check_satellite_timeout() -> bool:
    """Nur für Voice-Clients: 8h Inaktivität → api_history (Kategorie 'voice') leeren.
    Gibt True zurück wenn eine neue Session gestartet wurde."""
    global _last_activity_ts
    now = time.time()
    if _last_activity_ts > 0 and (now - _last_activity_ts) > SATELLITE_TIMEOUT:
        with history_lock:
            old = list(api_histories["voice"])
            api_histories["voice"].clear()
            last = display_histories["voice"][-1] if display_histories["voice"] else None
            if not (last and last.get("content") == "session_break"):
                display_histories["voice"].append({
                    "content": "session_break",
                    "role": "system",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
        if old:
            session_memory.save(old, clients=sorted(_session_clients["voice"]), category="voice")
        _session_clients["voice"] = set()
        hours = (now - _last_activity_ts) / 3600
        print(f"[server] Neue Session nach {hours:.1f}h Inaktivität (Satellite)", flush=True)
        _last_activity_ts = now
        return True
    _last_activity_ts = now
    return False


def _trim_api_history(category: str, tab_id: str):
    """Rolling Window: History auf _ROLLING_WINDOW Nachrichten kürzen."""
    with history_lock:
        hist = _get_api_history(category, tab_id)
        if len(hist) > _ROLLING_WINDOW:
            del hist[:len(hist) - _ROLLING_WINDOW]


def _persist_web_turn(loop, tab_id: str):
    """Schreibt die aktuelle Web-Tab-Session nach jeder Nachricht in sessions.db durch
    (UPDATE der bestehenden Zeile, kein neuer Eintrag) — läuft im Executor, damit der
    SQLite-Write den Event-Loop nicht blockiert. finalize=False: keine Lernextraktion,
    die läuft nur bei echtem Abschluss (Reset/Wechsel/Shutdown)."""
    with history_lock:
        hist_snapshot = list(_get_api_history("web", tab_id))
        clients_snapshot = sorted(_get_session_clients("web", tab_id))
    sid = _get_active_session_id(tab_id)

    def _do():
        return session_memory.upsert(sid, hist_snapshot, clients=clients_snapshot, category="web", finalize=False, tab_id=tab_id)

    async def _run():
        new_sid = await loop.run_in_executor(None, _do)
        if new_sid is not None:
            _set_active_session_id(tab_id, new_sid)

    asyncio.create_task(_run())


_QA_REGISTRY = {
    "alarm":          {"id": "alarm",          "label": "Wecker",     "icon": "⏰", "input": {"type": "time_picker", "label": "Weckzeit"},              "send": "Stell einen Wecker für {value} Uhr."},
    "todo_add":       {"id": "todo_add",       "label": "Todo +",     "icon": "📋", "input": {"type": "text", "placeholder": "Was muss erledigt werden?"}, "send": "Erstelle ein Todo: {value}"},
    "checkin":        {"id": "checkin",        "label": "Check-In",   "icon": "💬", "input": None,                                                       "send": "Mach einen kurzen Morgen Check-In."},
    "wochenreview":   {"id": "wochenreview",   "label": "Review",     "icon": "📊", "input": None,                                                       "send": "Gib mir eine Wochenübersicht."},
    "fortschritt":    {"id": "fortschritt",    "label": "Fortschritt","icon": "📈", "input": None,                                                       "send": "Wie ist mein Fortschritt diese Woche?"},
    "ziel_setzen":    {"id": "ziel_setzen",    "label": "Ziel setzen","icon": "🎯", "input": {"type": "text", "placeholder": "Was ist dein Ziel?"},      "send": "Setze ein neues Ziel: {value}"},
    "timer":          {"id": "timer",          "label": "Timer",      "icon": "⏱", "input": None,                                                       "send": "Stell einen 25-Minuten-Timer."},
    "naechstes_event":{"id": "naechstes_event","label": "Nächstes",   "icon": "📅", "input": None,                                                       "send": "Was ist das nächste Event heute?"},
}

_DEFAULT_QA_IDS = {
    "assistent": ["alarm", "todo_add", "checkin"],
    "coach":     ["wochenreview", "fortschritt", "ziel_setzen"],
    "fokus":     ["timer", "naechstes_event"],
}

_CARD_REGISTRY = {
    "transcript": {"id": "transcript", "type": "chat",   "title": "Letztes Gespräch"},
    "btc":        {"id": "btc",        "type": "metric", "title": "BTC"},
    "weather":    {"id": "weather",    "type": "metric", "title": "Wetter"},
    "weather_btc":{"id": "weather_btc","type": "metric", "title": "Wetter & BTC"},
    "todos":      {"id": "todos",      "type": "list",   "title": "Todos heute"},
    "calendar":   {"id": "calendar",   "type": "agenda", "title": "Kalender heute"},
    "alarms":     {"id": "alarms",     "type": "list",   "title": "Wecker"},
    "followups":  {"id": "followups",  "type": "list",   "title": "Offene Punkte"},
    "clients":    {"id": "clients",    "type": "chips",  "title": "Clients"},
}

_DEFAULT_CARD_IDS = {
    "assistent": ["transcript", "btc", "weather", "todos", "calendar"],
    "coach":     ["todos", "calendar"],
    "fokus":     [],
}


def _build_layout_config(mode: str = "assistent") -> dict:
    """Berechnet server-seitig welche Cards und Quick Actions für den Modus gezeigt werden."""
    modules = brain.read("modules") or {}
    mode_cfg = (modules.get("modes") or {}).get(mode, {}) or {}

    # Quick Actions: aus brain.modules lesen, Fallback auf defaults
    action_ids = mode_cfg.get("quick_actions") or _DEFAULT_QA_IDS.get(mode, [])
    quick_actions = [_QA_REGISTRY[qid] for qid in action_ids if qid in _QA_REGISTRY]

    # Cards: JARVIS hat explizit konfiguriert → exakt diese; sonst Defaults + dynamisch
    if "cards" in mode_cfg:
        cards = [_CARD_REGISTRY[cid] for cid in mode_cfg["cards"] if cid in _CARD_REGISTRY]
    else:
        cards = [_CARD_REGISTRY[cid] for cid in _DEFAULT_CARD_IDS.get(mode, []) if cid in _CARD_REGISTRY]
        # Dynamisch: alarms/followups hinzufügen wenn Daten vorhanden
        try:
            if alarm_service.list_alarms():
                cards.append(_CARD_REGISTRY["alarms"])
        except Exception:
            pass
        try:
            followups_raw = brain.read("followups") or {}
            if isinstance(followups_raw, dict):
                today_iso = date.today().isoformat()
                if any(v and (not isinstance(v, dict) or not v.get("due") or v.get("due") <= today_iso)
                       for v in followups_raw.values()):
                    cards.append(_CARD_REGISTRY["followups"])
        except Exception:
            pass

    return {"cards": cards, "quick_actions": quick_actions}


def _handle_data_request(resource: str, req_data: dict | None = None, category: str = "web", tab_id: str = ""):
    req_data = req_data or {}
    if resource == "knowledge_index":
        try:
            return knowledge.list_available()
        except Exception as e:
            return {"error": str(e)}
    if resource == "knowledge_file":
        topic = req_data.get("topic", "")
        file  = req_data.get("file", "")
        if not topic or not file:
            return {"error": "topic und file erforderlich"}
        try:
            content = knowledge.read(topic, file)
            return {"content": content or "", "topic": topic, "file": file}
        except Exception as e:
            return {"error": str(e)}
    if resource == "todos":
        return local_data.list_todos()
    if resource == "arbeit_tickets":
        return local_data.list_arbeit_tickets()
    if resource == "sync_arbeit_tickets":
        try:
            from services import github_issues
            return github_issues.sync_arbeit_tickets()
        except RuntimeError as e:
            return {"error": str(e)}
    if resource == "projekte":
        return local_data.list_projekte()
    if resource == "kontakte":
        return local_data.list_kontakte()
    if resource == "seite":
        typ = req_data.get("typ", "")
        item_id = req_data.get("id")
        if not typ or item_id is None:
            return {"error": "typ und id erforderlich"}
        try:
            page = local_data.get_seite_view(typ, int(item_id))
            return page or {"error": "Seite nicht gefunden"}
        except Exception as e:
            return {"error": str(e)}
    if resource == "calendar":
        try:
            from services import calendar as cal_service
            return cal_service.query_cached(days_ahead=7)
        except Exception:
            return []
    if resource == "alarms":
        try:
            return alarm_service.list_alarms()
        except Exception:
            return []
    if resource == "followups":
        try:
            return brain.read("followups") or {}
        except Exception:
            return {}
    if resource == "history":
        with history_lock:
            snapshot = list(_get_display_history(category, tab_id)[-120:])
        result = []
        for m in snapshot:
            if m.get("content") == "session_break":
                result.append({"role": "system", "type": "session_break", "timestamp": m.get("timestamp", "")})
            elif isinstance(m.get("content"), str):
                result.append({"role": m["role"], "text": m["content"], "client": m.get("client")})
        return result
    if resource == "weather":
        try:
            from services import weather as weather_service
            return weather_service.get_weather()
        except Exception:
            return {}
    if resource == "clients":
        return manager.list_clients()
    if resource == "btc":
        try:
            from services import btc as btc_service
            return btc_service.get_price()
        except Exception:
            return {}
    if resource == "coding_engine_usage":
        try:
            return coding_engine.get_usage_summary(days=int(req_data.get("days", 14)))
        except Exception as e:
            return {"error": str(e)}
    if resource == "coding_task_status":
        try:
            return coding_engine.get_task_status()
        except Exception as e:
            return {"error": str(e)}
    if resource == "tracking_topics":
        try:
            # coding_engine hat seine eigene dedizierte Ansicht — hier nicht doppelt zeigen
            return [t for t in tracking.list_topics() if t != "coding_engine"]
        except Exception as e:
            return {"error": str(e)}
    if resource == "tracking_progress":
        topic = req_data.get("topic", "")
        if not topic:
            return {"error": "topic erforderlich"}
        try:
            progress = tracking.get_progress(topic)
            progress["logs"] = tracking.get_logs(topic, limit=10)
            return progress
        except Exception as e:
            return {"error": str(e)}
    if resource == "session_transcript":
        sid = req_data.get("session_id")
        if sid is None:
            return {"error": "session_id erforderlich"}
        try:
            return session_memory.get_transcript(int(sid))
        except Exception as e:
            return {"error": str(e)}
    if resource == "notification_history":
        try:
            return dispatcher.list_recent(int(req_data.get("limit", 30)))
        except Exception as e:
            return {"error": str(e)}
    return None


def _build_overlay_events() -> list[dict]:
    """Gibt das erste fällige Overlay-Event zurück (Routine im aktiven Zeitfenster)."""
    try:
        events_data = brain.read("events") or {}
        routines = events_data.get("routines", {})
        if not isinstance(routines, dict):
            return []
    except Exception:
        return []

    now = datetime.now()
    today_iso = now.date().isoformat()
    now_time = now.strftime("%H:%M")
    _DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    today_day = _DAYS[now.weekday()]

    for rname, rcfg in routines.items():
        if not isinstance(rcfg, dict) or not rcfg.get("active", True):
            continue
        if rcfg.get("last_done") == today_iso:
            continue
        days = rcfg.get("window", {}).get("days", [])
        if days and today_day not in days:
            continue
        window_from = rcfg.get("window", {}).get("from", "00:00")
        window_to   = rcfg.get("window", {}).get("to",   "23:59")
        if not (window_from <= now_time <= window_to):
            continue
        deferred = rcfg.get("deferred_until")
        if deferred and now_time < deferred:
            continue
        return [{
            "type": P.OVERLAY_EVENT,
            "event_id": rname,
            "title": rcfg.get("label", rname.replace("_", " ").title()),
            "icon": rcfg.get("icon", "💬"),
            "send": rcfg.get("send", "Mach einen Morgen Check-In."),
            "snooze_minutes": int(rcfg.get("snooze_minutes", 10)),
        }]
    return []


def _handle_overlay_dismiss(event_id: str, action: str, minutes: int) -> None:
    """Aktualisiert brain.events nach Overlay-Dismiss (start/skip/snooze)."""
    try:
        events_data = brain.read("events") or {}
        routines = events_data.get("routines", {})
        if event_id not in routines:
            return
        if action in ("start", "skip"):
            routines[event_id]["last_done"] = date.today().isoformat()
            routines[event_id].pop("deferred_until", None)
        elif action == "snooze":
            until = (datetime.now() + timedelta(minutes=minutes)).strftime("%H:%M")
            routines[event_id]["deferred_until"] = until
        brain.write("events", "routines", routines)
    except Exception as e:
        print(f"[server] Overlay-Dismiss Fehler: {e}", flush=True)


_ENTITY_FIELDS = {
    "todos":        {"name", "status", "datum", "prioritaet", "bereich", "aufwand", "notizen",
                     "source", "external_id", "repo", "body", "labels"},
    "projekte":     {"name", "status", "beschreibung", "typ", "notizen"},
    "kontakte":     {"name", "email", "telefon", "tags", "notizen"},
    "seite":        {"titel", "inhalt"},
}


def _do_entity_action(entity: str, action: str, data: dict) -> None:
    """Blockierende Todos/Projekte/Kontakte(/Seiten)-Mutation — Layer 1 DATA, kein LLM-Umweg."""
    if entity not in _ENTITY_FIELDS:
        raise ValueError(f"Unbekannte Entität: {entity}")
    fields = {k: v for k, v in data.items() if k in _ENTITY_FIELDS[entity]}

    if action == "add":
        if entity == "seite":
            raise ValueError("Seiten werden nur über die Migration angelegt")
        name = (fields.get("name") or "").strip()
        if not name:
            raise ValueError("Name erforderlich")
        fields["name"] = name
        if entity == "todos":
            local_data.add_todo(**fields)
        elif entity == "projekte":
            local_data.add_projekt(**fields)
        else:
            local_data.add_kontakt(**fields)
    elif action == "update":
        item_id = data.get("id")
        if not item_id:
            raise ValueError("id erforderlich")
        if entity == "todos":
            local_data.update_todo(item_id, **fields)
        elif entity == "projekte":
            local_data.update_projekt(item_id, **fields)
        elif entity == "kontakte":
            local_data.update_kontakt(item_id, **fields)
        else:
            local_data.update_seite(item_id, **fields)
    elif action == "complete":
        if entity != "todos":
            raise ValueError(f"'complete' gibt es nur für todos, nicht {entity}")
        item_id = data.get("id")
        if not item_id:
            raise ValueError("id erforderlich")
        local_data.complete_todo(item_id)
    elif action == "delete":
        if entity == "seite":
            raise ValueError("Seiten können hier nicht gelöscht werden")
        item_id = data.get("id")
        if not item_id:
            raise ValueError("id erforderlich")
        if entity == "todos":
            local_data.delete_todo(item_id)
        elif entity == "projekte":
            local_data.delete_projekt(item_id)
        else:
            local_data.delete_kontakt(item_id)
    else:
        raise ValueError(f"Unbekannte Aktion: {action}")


def _build_dashboard_sync() -> dict:
    todos = local_data.list_todos()
    btc_data: dict = {}
    try:
        from services import btc as btc_service
        btc_data = btc_service.get_price()
    except Exception:
        pass
    weather_data: dict = {}
    try:
        from services import weather as weather_service
        weather_data = weather_service.get_weather()
    except Exception:
        pass
    cal_events: list = []
    try:
        from services import calendar as cal_service
        cal_events = cal_service.query_cached(days_ahead=7)
    except Exception:
        pass
    alarms: list = []
    try:
        alarms = alarm_service.list_alarms()
    except Exception:
        pass
    followups: dict = {}
    try:
        followups = brain.read("followups") or {}
    except Exception:
        pass
    return {
        "type": P.DASHBOARD_SYNC,
        "todos": todos,
        "calendar": cal_events,
        "btc": btc_data,
        "weather": weather_data,
        "clients": manager.list_clients(),
        "alarms": alarms,
        "followups": followups,
        "chat_cost_today": _chat_spend_today(),
        "coding_cost_today": _coding_spend_today(),
        "chat_daily_budget": config.CHAT_DAILY_BUDGET_USD,
    }


def _coding_spend_today() -> float:
    """Gleiche Zahl wie die Coding-Engine-Kosten-Grafik, nur fürs Dashboard/Sidebar-Widget."""
    try:
        return coding_engine.get_usage_summary(days=1).get("today_usd", 0.0)
    except Exception:
        return 0.0


def _chat_spend_today() -> float:
    """Für die kleine Kosten-Anzeige in jarvis-web — Summe aller Chat-Turns (voice+web) heute."""
    try:
        today = date.today().isoformat()
        logs = tracking.get_logs("chat", key="cost_usd", since_date=today, limit=2000)
        return round(sum((l["value"] or 0.0) for l in logs if l["date"] == today), 4)
    except Exception:
        return 0.0


def _activate_client(client_id: str):
    """Setzt aktiven Client und triggert Musik-Raumwechsel falls nötig."""
    prev_active = manager.get_active()
    manager.set_active(client_id)
    if prev_active != client_id:
        new_name = manager.get_name(client_id)
        if new_name:
            client_music_service.on_room_change(new_name)


async def _push_dashboard_update():
    loop = asyncio.get_event_loop()
    try:
        base = await loop.run_in_executor(None, _build_dashboard_sync)
        base["type"] = P.DASHBOARD_UPDATE
        for cb, mode in manager.get_dashboard_event_callbacks():
            try:
                cb({**base, "layout_config": _build_layout_config(mode)})
            except Exception:
                pass
    except Exception as e:
        print(f"[server] Dashboard-Update Fehler: {e}", flush=True)


async def handle_connection(websocket):
    loop = asyncio.get_event_loop()
    client_id = str(id(websocket))
    addr = websocket.remote_address
    print(f"[server] Client verbunden: {addr} ({client_id})")

    _capture = [False]  # Wird nach Greeting/Dashboard-Init auf True gesetzt
    category = "voice"  # Default bis Rolle bekannt ist; send_json() bindet spät (Closure)
    tab_id = client_id  # Für "web": stabile Tab-Identität vom Client (client_hello); sonst Fallback

    def send_json(event: dict):
        if _capture[0]:
            etype = event.get("type")
            client_name = manager.get_name(client_id) or client_id
            if etype == P.TRANSCRIPT and event.get("text"):
                with history_lock:
                    _get_display_history(category, tab_id).append({"role": "user", "content": event["text"], "client": client_name})
                _get_session_clients(category, tab_id).add(client_name)
            elif etype == P.RESPONSE_DONE and event.get("text"):
                with history_lock:
                    _get_display_history(category, tab_id).append({"role": "assistant", "content": event["text"], "client": client_name})
        asyncio.run_coroutine_threadsafe(
            websocket.send(json.dumps(event, ensure_ascii=False)), loop
        )

    def send_audio(pcm: bytes):
        asyncio.run_coroutine_threadsafe(websocket.send(pcm), loop)

    manager.register(client_id, send_audio)
    manager.register_event(client_id, send_json)
    coding_engine.refresh_idle_status()
    send_json({"type": P.STATE, "state": "idle"})

    # Warte kurz auf CLIENT_HELLO um Role und Raumname zu erkennen
    role = "client"
    pending_msgs = []
    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=1.5)
        pending_msgs.append(first_raw)
        if isinstance(first_raw, str):
            first_data = json.loads(first_raw)
            if first_data.get("type") == P.CLIENT_HELLO:
                name = first_data.get("name", "")
                role = first_data.get("role", "client")
                tab_id = first_data.get("tab_id") or client_id
                if name:
                    manager.set_name(client_id, name)
                    manager.set_role(client_id, role)
                    print(f"[server] Client {addr} heißt: {name!r} (role={role})")
                pending_msgs.clear()
    except asyncio.TimeoutError:
        pass

    # Erst jetzt ist die Kategorie bekannt — Pipeline bekommt die passende History
    # zugewiesen (vorher konstruiert hätte sie fälschlich immer "voice" bekommen).
    category = _category_for_role(role)

    # Web-Tab reconnected mit einer tab_id, die der Prozess noch nicht kennt (z.B.
    # nach einem Server-Neustart) — Verlauf aus sessions.db zurückholen, statt bei
    # null anzufangen. Der Tab hatte bisher über einen Neustart hinweg keine stabile
    # Identität, obwohl der Verlauf durch die laufenden Upserts längst in der DB lag
    # (2026-07-22: 'der Chat sollte schon wieder mitgegeben werden').
    if category == "web" and tab_id not in api_histories["web"]:
        restored = session_memory.find_active_session(tab_id)
        if restored:
            api_hist = _get_api_history(category, tab_id)
            disp_hist = _get_display_history(category, tab_id)
            for msg in restored["transcript"]:
                text = msg.get("text", "")
                if not text:
                    continue
                role_ = msg.get("role")
                # api_hist braucht den Eintrag auch bei einem reinen Tool-Platzhalter
                # (sonst bricht die Rollen-Abwechslung beim nächsten LLM-Call), aber
                # disp_hist zeigt sowas nie als eigene Chat-Blase — Platzhalter wie
                # "[tool_result]" waren nie echter Gesprächsinhalt.
                api_hist.append({"role": role_, "content": text})
                if not session_memory.is_placeholder_text(text):
                    disp_hist.append({"role": role_, "content": text, "client": "jarvis-web"})
            _set_active_session_id(tab_id, restored["id"])
            print(f"[server] Web-Tab {tab_id[:8]}: {len(api_hist)} Nachrichten aus Session {restored['id']} wiederhergestellt.", flush=True)

    pipeline = JarvisPipeline(
        client_id=client_id,
        on_event=send_json,
        on_audio=send_audio,
        shared_history=_get_api_history(category, tab_id),
        history_lock=history_lock,
        llm_semaphore=llm_semaphore,
    )
    if manager.get_name(client_id):
        pipeline.set_room(manager.get_name(client_id))
    manager.register_pipeline(client_id, pipeline)

    if role == "dashboard":
        init_mode = "assistent"
        manager.set_mode(client_id, init_mode)
        sync = _build_dashboard_sync()
        sync["layout_config"] = _build_layout_config(init_mode)
        send_json(sync)
        for overlay in _build_overlay_events():
            send_json(overlay)
        dispatcher.deliver_pending(client_id)
        learning.deliver_pending(client_id)
        print("[server] Dashboard-Sync gesendet.", flush=True)
    else:
        # Begrüßung für Voice-Clients — läuft über pipeline.greet(), NICHT über
        # process_text()/den LLM: kein echter Gesprächsinhalt, braucht keine
        # Intelligenz, und ein LLM-Call hätte bei kaltem Cache unnötig den vollen
        # System-Prompt gekostet nur für ein Wort. Fasst History gar nicht erst an.
        print("[server] Starte Greeting…", flush=True)
        try:
            await loop.run_in_executor(None, pipeline.greet, "Bereit.")
            print("[server] Greeting fertig.", flush=True)
        except Exception as e:
            print(f"[server] Greeting Fehler: {e}", flush=True)

    _capture[0] = True  # Ab jetzt Gespräche in display_history aufnehmen

    try:
        # Ausstehende Nachrichten (falls CLIENT_HELLO noch nicht verarbeitet) zuerst
        for raw in pending_msgs:
            if isinstance(raw, bytes):
                manager.set_active(client_id)
                await loop.run_in_executor(None, pipeline.process_audio, raw)
            else:
                try:
                    data = json.loads(raw)
                    if data.get("type") == P.CLIENT_HELLO:
                        name = data.get("name", "")
                        r = data.get("role", "client")
                        tab_id = data.get("tab_id") or client_id
                        if name:
                            manager.set_name(client_id, name)
                            manager.set_role(client_id, r)
                except Exception:
                    pass

        async for message in websocket:
            if isinstance(message, bytes):
                _activate_client(client_id)
                if role != "dashboard":
                    # Ein Sprach-Timeout betrifft nur die "voice"-Kategorie, nicht
                    # die (unbeteiligte) Web-Live-Ansicht.
                    _check_satellite_timeout()
                await loop.run_in_executor(None, pipeline.process_audio, message)
                _last_activity_ts = time.time()
                _trim_api_history(category, tab_id)
                _save_history()
                asyncio.create_task(_push_dashboard_update())
            else:
                data = json.loads(message)
                if data.get("type") == P.TEXT_INPUT:
                    _activate_client(client_id)
                    use_tts = data.get("tts", True)
                    text = data["text"]
                    attachments = data.get("attachments") or []
                    if role != "dashboard":
                        _check_satellite_timeout()
                    client_name = manager.get_name(client_id) or client_id
                    with history_lock:
                        display_entry = {"role": "user", "content": text, "client": client_name}
                        if attachments:
                            display_entry["attachments"] = [
                                {"filename": a.get("filename"), "mime_type": a.get("mime_type")}
                                for a in attachments
                            ]
                        _get_display_history(category, tab_id).append(display_entry)
                    _get_session_clients(category, tab_id).add(client_name)
                    await loop.run_in_executor(None, pipeline.process_text, text, use_tts, attachments)
                    _last_activity_ts = time.time()
                    _trim_api_history(category, tab_id)
                    _save_history()
                    if category == "web":
                        _persist_web_turn(loop, tab_id)
                    asyncio.create_task(_push_dashboard_update())
                elif data.get("type") == P.CLIENT_HELLO:
                    name = data.get("name", "")
                    role = data.get("role", "client")
                    category = _category_for_role(role)
                    tab_id = data.get("tab_id") or client_id
                    if name:
                        manager.set_name(client_id, name)
                        manager.set_role(client_id, role)
                        print(f"[server] Client {addr} heißt jetzt: {name!r} (role={role})")
                    if role == "dashboard":
                        mode = data.get("mode", "assistent")
                        manager.set_mode(client_id, mode)
                        sync = _build_dashboard_sync()
                        sync["layout_config"] = _build_layout_config(mode)
                        send_json(sync)
                        await loop.run_in_executor(None, learning.deliver_pending, client_id)
                elif data.get("type") == P.SET_MODE:
                    new_mode = data.get("mode", "assistent")
                    manager.set_mode(client_id, new_mode)
                    pipeline.set_mode(new_mode)
                    send_json({"type": P.LAYOUT_CONFIG, **_build_layout_config(new_mode)})
                elif data.get("type") == P.DATA_REQUEST:
                    resource = data.get("resource", "")
                    result = await loop.run_in_executor(None, _handle_data_request, resource, data, category, tab_id)
                    send_json({"type": P.DATA_RESPONSE, "resource": resource, "data": result})
                elif data.get("type") == P.KNOWLEDGE_WRITE:
                    topic   = data.get("topic", "")
                    file    = data.get("file", "")
                    content = data.get("content", "")
                    if topic and file and content:
                        try:
                            await loop.run_in_executor(None, knowledge.write, topic, file, content)
                            send_json({"type": P.KNOWLEDGE_WRITE_ACK, "ok": True, "topic": topic, "file": file})
                        except Exception as e:
                            send_json({"type": P.KNOWLEDGE_WRITE_ACK, "ok": False, "error": str(e)})
                    else:
                        send_json({"type": P.KNOWLEDGE_WRITE_ACK, "ok": False, "error": "topic, file und content erforderlich"})
                elif data.get("type") == P.ENTITY_ACTION:
                    entity = data.get("entity", "")
                    action = data.get("action", "")
                    try:
                        await loop.run_in_executor(None, _do_entity_action, entity, action, data)
                        send_json({"type": P.ENTITY_ACTION_ACK, "ok": True, "entity": entity, "action": action})
                        asyncio.create_task(_push_dashboard_update())
                    except Exception as e:
                        send_json({"type": P.ENTITY_ACTION_ACK, "ok": False, "entity": entity, "action": action, "error": str(e)})
                elif data.get("type") == P.ALARM_SYNC:
                    client_name = manager.get_name(client_id) or ""
                    alarm_service.sync_from_client(client_name, data.get("alarms", []))
                    print(f"[server] Alarm-Sync von {client_name!r}: {len(data.get('alarms', []))} Wecker")
                elif data.get("type") == P.ALARM_RINGING:
                    alarm_service.on_ringing(data["alarm_id"])
                    print(f"[server] Wecker klingelt: {data.get('label')!r} auf {manager.get_name(client_id)!r}")
                elif data.get("type") == P.ALARM_DISMISSED:
                    client_name = manager.get_name(client_id) or ""
                    alarm_service.on_dismissed(data["alarm_id"], data.get("snooze_count", 0))
                elif data.get("type") == P.OVERLAY_DISMISS:
                    await loop.run_in_executor(
                        None,
                        _handle_overlay_dismiss,
                        data.get("event_id", ""),
                        data.get("action", "skip"),
                        int(data.get("minutes", 10)),
                    )
                elif data.get("type") == P.NOTIFICATION_ACK:
                    dispatcher.mark_delivered(data["id"])
                elif data.get("type") == P.CODING_APPROVAL_RESPONSE:
                    coding_engine.resolve_approval(data["id"], bool(data.get("approved")))
                elif data.get("type") == P.CODING_SUDO_PASSWORD_RESPONSE:
                    coding_engine.resolve_sudo_password(data["id"], data.get("password", ""))
                elif data.get("type") == P.KNOWLEDGE_CONFIRM:
                    if data.get("confirmed"):
                        learning.apply_suggestion(data["id"])
                    else:
                        learning.reject_suggestion(data["id"])
                elif data.get("type") == P.SESSION_RESET:
                    with history_lock:
                        old_history = list(_get_api_history(category, tab_id))
                        _clear_api_history(category, tab_id)
                        disp = _get_display_history(category, tab_id)
                        last = disp[-1] if disp else None
                        if not (last and last.get("content") == "session_break"):
                            disp.append({
                                "content": "session_break",
                                "role": "system",
                                "timestamp": datetime.utcnow().isoformat() + "Z",
                            })
                    # Nur an diesen einen Client (Tab/Raum) — bei "web" ist die History
                    # jetzt pro Tab isoliert, andere Tabs sind von diesem Reset nicht betroffen.
                    send_json({"type": P.SESSION_BREAK})
                    if old_history:
                        clients_snapshot = sorted(_get_session_clients(category, tab_id))
                        if category == "web":
                            # Zeile ist durch die laufenden Upserts schon aktuell — hier nur
                            # noch finalisieren (Lernextraktion) statt blind neu einzufügen.
                            await loop.run_in_executor(
                                None, session_memory.upsert, _get_active_session_id(tab_id),
                                old_history, clients_snapshot, category, True,
                            )
                            _set_active_session_id(tab_id, None)
                        else:
                            await loop.run_in_executor(
                                None, lambda: session_memory.save(old_history, clients=clients_snapshot, category=category)
                            )
                    _reset_session_clients(category, tab_id)
                    print(f"[server] Session ({category}) manuell zurückgesetzt.", flush=True)
                elif data.get("type") == P.SESSION_LIST_REQUEST:
                    sessions = await loop.run_in_executor(None, session_memory.list_sessions, 30)
                    send_json({"type": P.SESSION_LIST_RESPONSE, "sessions": sessions})
                elif data.get("type") == P.SESSION_DELETE:
                    sid = data.get("session_id")
                    ok = await loop.run_in_executor(None, session_memory.delete, sid)
                    send_json({"type": P.SESSION_DELETE_ACK, "session_id": sid, "ok": ok})
                elif data.get("type") == P.SESSION_LOAD:
                    sid = data.get("session_id")
                    # Aktuelle Session (eigener Tab/Raum) abschließen falls nicht leer
                    with history_lock:
                        old = list(_get_api_history(category, tab_id))
                    if old:
                        clients_snapshot = sorted(_get_session_clients(category, tab_id))
                        if category == "web":
                            await loop.run_in_executor(
                                None, session_memory.upsert, _get_active_session_id(tab_id),
                                old, clients_snapshot, category, True,
                            )
                        else:
                            await loop.run_in_executor(
                                None, lambda: session_memory.save(old, clients=clients_snapshot, category=category)
                            )
                    _reset_session_clients(category, tab_id)
                    # Transcript laden und als neue api_history (eigener Tab/Raum) setzen —
                    # weitere Nachrichten in diesem Tab schreiben ab jetzt die GELADENE
                    # Zeile fort (statt eine neue anzulegen), das ist ja "fortsetzen".
                    transcript = await loop.run_in_executor(None, session_memory.get_transcript, sid)
                    with history_lock:
                        hist = _get_api_history(category, tab_id)
                        hist.clear()
                        for msg in transcript:
                            hist.append({"role": msg["role"], "content": msg["text"]})
                        # Rolling Window anwenden
                        if len(hist) > _ROLLING_WINDOW:
                            del hist[:len(hist) - _ROLLING_WINDOW]
                    if category == "web":
                        _set_active_session_id(tab_id, sid)
                    send_json({"type": P.SESSION_LOAD_ACK, "messages": transcript})
                elif data.get("type") == P.PING:
                    await websocket.send(json.dumps({"type": P.PONG}))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        manager.unregister(client_id)
        coding_engine.refresh_idle_status()
        if role != "dashboard":
            pipeline.save_session()
        print(f"[server] Client getrennt: {addr}")


def _save_all_sessions_on_shutdown():
    """Beim Server-Shutdown (z.B. systemctl restart) laufende Sessions archivieren:
    die eine geteilte voice-Session, plus JEDEN offenen web-Tab einzeln (seit der
    Pro-Tab-Isolation 2026-07-20 ist api_histories["web"] ein dict tab_id -> history).
    Ohne das verschwindet eine Konversation bei jedem Neustart mitten im Gespräch
    spurlos: _load_history() lädt nur "voice" zurück, und der Disconnect-Handler
    pro Verbindung speichert für Web-/Dashboard-Clients bewusst nicht (sonst gäbe
    es bei jedem Tab-Wechsel eine neue Session) — ein Server-weiter Shutdown ist
    aber kein trivialer Disconnect, das verdient eine echte Archivierung. Web-Tabs
    laufen inzwischen ohnehin schon laufend über session_memory.upsert() mit — hier
    nur noch ein finaler Upsert (finalize=True, Lernextraktion) auf die schon
    bekannte Zeile, kein blinder Neu-Insert (sonst Duplikat der schon gespeicherten
    Zeile)."""
    with history_lock:
        voice_hist = list(api_histories["voice"])
        voice_clients = sorted(_session_clients["voice"])
        web_tabs = {tab_id: list(hist) for tab_id, hist in api_histories["web"].items()}
        web_clients = {tab_id: sorted(_get_session_clients("web", tab_id)) for tab_id in web_tabs}

    if voice_hist:
        t = session_memory.save(voice_hist, clients=voice_clients, category="voice")
        if t:
            t.join(timeout=10)
    for tab_id, hist in web_tabs.items():
        if not hist:
            continue
        session_memory.upsert(
            _get_active_session_id(tab_id), hist, clients=web_clients[tab_id],
            category="web", finalize=True,
        )
    print("[server] Laufende Sessions vor Shutdown gesichert.", flush=True)


async def main():
    brain.sync()
    knowledge.rebuild_index()
    _load_history()
    stt.load_model()
    alarm_service.init(manager)
    client_music_service.init(manager)
    sleep_coach.init(manager, alarm_service, dispatcher)
    proactive_service.init(manager, alarm_service, dispatcher)
    coding_engine.init(manager, dispatcher)
    learning.init(manager)
    print(f"[server] J.A.R.V.I.S. bereit — ws://{HOST}:{PORT}")

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    async with websockets.serve(
        handle_connection, HOST, PORT,
        ping_interval=30,
        ping_timeout=120,
        max_size=20 * 1024 * 1024,  # Default 1MiB reicht nicht für Bild-/PDF-Anhänge im Chat
    ):
        await stop_event.wait()

    print("[server] Shutdown-Signal empfangen — sichere Sessions...", flush=True)
    await loop.run_in_executor(None, _save_all_sessions_on_shutdown)


if __name__ == "__main__":
    asyncio.run(main())
