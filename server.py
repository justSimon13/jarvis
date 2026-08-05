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
import finanzen
import finanzen_import
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
from services import coding_usage
from services import coding_jobs
from services import document_export
from services import import_adapters
from services import import_runner
from services import import_store
from services import import_upload
from services import local_exec
from services import sleep_coach
from services import proactive as proactive_service
from services.notification_dispatcher import NotificationDispatcher
import local_data
import thread_naming

HOST = os.getenv("JARVIS_HOST", "0.0.0.0")
PORT = int(os.getenv("JARVIS_PORT", "8765"))

manager = ClientManager()
dispatcher = NotificationDispatcher(manager)


def _broadcast_web_event(event: dict) -> None:
    """Schickt event an ALLE verbundenen Web-/Dashboard-Clients — für die
    automatische Thread-Benennung (Thread-Umbau Teil B, Schritt 1), die
    anders als THREAD_REASSIGNED (Teil A, dort ändert sich serverseitiger
    Zustand nur für betroffene Tabs) eine reine Anzeige-Aktualisierung ohne
    Zustandsrisiko ist — bei mehreren offenen Fenstern sollen alle sofort den
    neuen Titel sehen, nicht erst beim nächsten eigenen Reconnect. Wird sowohl
    bei jeder JarvisPipeline-Konstruktion durchgereicht als auch dem
    Startup-Sweep übergeben (thread_naming.run_startup_sweep())."""
    for cb, _ in manager.get_dashboard_event_callbacks():
        cb(event)

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


# Welcher Thread (Teil 2, manuelles Etikett — siehe ROADMAP.md) gerade für einen
# Web-Tab aktiv ist. None/fehlend = kein Thread aktiv, Fensterbildung bleibt
# tab-/cursor-basiert wie in Teil 1. Rein in-memory wie _active_session_ids —
# überlebt einen Server-Neustart nicht (dann muss neu gewählt werden), einen
# Client-Reconnect bei laufendem Server aber schon, siehe CLIENT_HELLO unten.
_active_thread_ids: dict[str, int | None] = {}


def _deliver_job_result_to_chat(delivery: dict) -> None:
    """Liefert ein Coding-Job-Ergebnis zusätzlich zur Notification als
    Chat-Nachricht aus — an genau den (category, tab_id), in dem der Job
    gestartet wurde (siehe services/coding_jobs.py::resolve_job_result).
    Bewusst NICHT über RESPONSE_START/CHUNK/DONE (das Muster aus
    pipeline.greet()): diese laufen client-seitig über ein einzelnes
    pendingResponse-Ref, das mit einem echten, gerade laufenden Turn in
    diesem Tab kollidieren würde. Stattdessen direkt über den bestehenden
    coding_job_result-Typ, den jarvis-web jetzt auch eingehend behandelt.

    tab_id ist die vom Browser stabil gehaltene UUID, NICHT dieselbe wie die
    Connection-client_id (server-internes, pro Verbindung neues Handle) — die
    Zustellung läuft deshalb über get_event_callback_for_tab(), die intern
    über die client_manager.py::_tab_to_client-Zuordnung auflöst (Root Cause
    eines echten Bugs, 2026-07-31: hier stand vorher fälschlich
    get_event_callback(tab_id), ein Key der nie registriert wurde — Plan/
    Fortschritt kamen deshalb nie im Chat an, obwohl der Tab verbunden war)."""
    category = delivery["category"]
    tab_id = delivery["tab_id"]
    with history_lock:
        _get_display_history(category, tab_id).append({"role": "assistant", "content": delivery["chat_text_full"]})
        _get_api_history(category, tab_id).append({"role": "assistant", "content": delivery["chat_text_short"]})
    # Läuft außerhalb jeder Pipeline-Runde (der Job kann Minuten/Stunden vorher
    # gestartet worden sein) — hat deshalb kein pipeline._thread_id zur Hand,
    # sondern muss den aktuell aktiven Thread des Ziel-Tabs selbst nachschlagen.
    # Ohne das würde ein Job-Ergebnis, das eintrifft während ein Thread aktiv
    # ist, außerhalb von dessen Fenster landen (build_history_window() liest
    # dann ausschließlich nach thread_id, siehe session_memory.py).
    thread_id = _active_thread_ids.get(tab_id)
    project_id = session_memory.get_thread_project_id(thread_id) if thread_id is not None else None
    # append_or_extend_message() statt append_message(): der Normalfall hier ist "Job gestartet,
    # seither nichts mehr geschrieben" — die letzte Zeile ist dann schon 'assistant', eine
    # zweite in Folge würde die Rollen-Abwechslung brechen (API lehnt mit 400 ab). Ohne diesen
    # Fix wäre das Ergebnis im NORMALFALL nie im Kontext (Thread-Umbau Teil A) — JARVIS wüsste
    # beim awaiting_review-Review im Chat nicht, dass der Job fertig ist.
    session_memory.append_or_extend_message(
        category, tab_id, delivery["chat_text_short"], delivery["chat_text_full"],
        thread_id=thread_id, project_id=project_id,
    )

    cb = manager.get_event_callback_for_tab(tab_id)
    if cb:
        cb({"type": P.CODING_JOB_RESULT, "job_id": delivery.get("job_id"), "result": delivery["chat_text_full"]})


def _relay_job_progress(data: dict) -> None:
    """Leitet ein flüchtiges Fortschritts-Ereignis (siehe protocol.py::
    CODING_JOB_PROGRESS) an den Web-Tab weiter, in dem der Job gestartet
    wurde — KEINE History-Berührung (weder display_history noch api_history),
    Fortschritt ist ausdrücklich nicht persistiert. Kein Ziel bekannt oder Tab
    gerade nicht verbunden → still verwerfen (Design-Entscheidung, ein
    verpasstes Ereignis ist irrelevant), aber GELOGGT statt komplett lautlos —
    sonst nicht von einem echten Bug zu unterscheiden (siehe Diagnose
    "Live-Fortschrittszeile erscheint nicht", 2026-07-31)."""
    job_id = data.get("job_id")
    print(f"[server] coding_job_progress empfangen: job_id={job_id}, text={data.get('text')!r}", flush=True)
    target = coding_jobs.get_job_chat_target(job_id) if job_id is not None else None
    if not target:
        print(f"[server] coding_job_progress verworfen: kein category/tab_id für job_id={job_id} hinterlegt.", flush=True)
        return
    _category, tab_id = target
    cb = manager.get_event_callback_for_tab(tab_id)
    if not cb:
        print(f"[server] coding_job_progress verworfen: Tab {tab_id!r} (job_id={job_id}) gerade nicht verbunden.", flush=True)
        return
    cb({"type": P.CODING_JOB_PROGRESS, "job_id": job_id, "text": data.get("text", "")})
    print(f"[server] coding_job_progress an Tab {tab_id!r} zugestellt (job_id={job_id}).", flush=True)


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
        old_cursor = session_memory.advance_cursor("voice", "")
        if old:
            session_memory.save(
                old, clients=sorted(_session_clients["voice"]), category="voice",
                first_message_id=old_cursor + 1,
            )
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


async def _persist_web_turn(loop, tab_id: str) -> None:
    """Schreibt die aktuelle Web-Tab-Session nach jeder Nachricht in sessions.db durch
    (UPDATE der bestehenden Zeile, kein neuer Eintrag) — läuft im Executor, damit der
    SQLite-Write den Event-Loop nicht blockiert. finalize=False: keine Lernextraktion,
    die läuft nur bei echtem Abschluss (Reset/Wechsel/Shutdown).

    AWAITED seit 2026-07-31 (vorher fire-and-forget über asyncio.create_task, ohne dass
    der Aufrufer je darauf wartete) — Simon meldete Kontextverlust nach einem Server-
    Neustart im selben, weiterhin verbundenen Chat-Tab: der eigentliche Write lief auf
    einem SEPARATEN, nicht abgewarteten Task, dessen Ausführung beliebig lange nach
    Turn-Ende verzögert sein konnte (nächster freier Event-Loop-Tick). Stirbt der Prozess
    (Absturz, hartes Kill, oder ein Neustart der nicht auf den Task wartet) in genau
    diesem Fenster, geht der zuletzt abgeschlossene Turn nie in sessions.db — beim
    Reconnect stellte find_active_session() dann einen veralteten Stand wieder her, dem
    Modell fehlte der Kontext, obwohl der clientseitige Chatverlauf (rein im Browser-/
    Tauri-Zustand, nie neu vom Server geladen) unverändert weiter alles anzeigt und so
    Kontinuität vortäuscht. Alle anderen session_memory.upsert()-Aufrufstellen in dieser
    Datei (SESSION_RESET/SESSION_LOAD) awaiten den Executor-Call bereits direkt — dieser
    hier war die einzige Ausnahme. _run_text_turn() läuft selbst schon als eigener,
    losgelöster Task (siehe _spawn_turn) — das Awaiten hier verzögert weder die
    Nachrichten-Schleife der Verbindung noch die an den Client gestreamte Antwort (die
    steht zu diesem Zeitpunkt längst raus), schließt aber das Zeitfenster fast vollständig.

    Nachtrag (messages/threads-Migration): der Reconnect-Restore über
    find_active_session() (der hier beschriebene Anlass) existiert seitdem gar nicht
    mehr — pipeline.py baut das Prompt-Fenster jetzt bei JEDEM Turn frisch aus
    session_memory.build_history_window() (SQLite), nicht nur beim Reconnect. Dieser
    Fix bleibt trotzdem in Kraft: die Zeile hier aktualisiert weiterhin die (jetzt
    Legacy-)sessions-Tabelle, awaited bleibt awaited."""
    with history_lock:
        hist_snapshot = list(_get_api_history("web", tab_id))
        clients_snapshot = sorted(_get_session_clients("web", tab_id))
    sid = _get_active_session_id(tab_id)

    def _do():
        return session_memory.upsert(sid, hist_snapshot, clients=clients_snapshot, category="web", finalize=False, tab_id=tab_id)

    new_sid = await loop.run_in_executor(None, _do)
    if new_sid is not None:
        _set_active_session_id(tab_id, new_sid)


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
    # Der Entwickler-Modus unterscheidet sich (vorerst) durch Wissen und
    # Werkzeuge, nicht durch die Oberfläche. Eigene Aktionen erst, wenn sich
    # zeigt welche fehlen.
    "entwickler": ["timer", "naechstes_event"],
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
    "coach":      ["todos", "calendar"],
    "entwickler": ["todos", "calendar"],
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
            links = knowledge.get_links(topic, file)
            return {"content": content or "", "topic": topic, "file": file, "links": links}
        except Exception as e:
            return {"error": str(e)}
    if resource == "todos":
        return local_data.list_todos()
    if resource == "coding_jobs":
        try:
            return coding_jobs.list_jobs(status_filter=req_data.get("status"))
        except Exception as e:
            return {"error": str(e)}
    if resource == "coding_job":
        # SINGULAR, ein Job per id — nicht zu verwechseln mit "coding_jobs"
        # (Plural, Liste) oben. Für die selbstaktualisierende Job-Karte im
        # Chat (jarvis-web): coding_job_created/_progress/_result-Ereignisse
        # sind dort nur das Signal "etwas hat sich geändert", die Karte holt
        # den vollständigen Stand danach jedes Mal frisch hierüber — keine
        # strukturierten Felder durch mehrere WS-Payload-Formen hindurchreichen.
        job_id = req_data.get("id")
        if job_id is None:
            return {"error": "id erforderlich"}
        try:
            job = coding_jobs.get_job_status(int(job_id))
            return job if "id" in job else {"error": "Job nicht gefunden"}
        except Exception as e:
            return {"error": str(e)}
    if resource == "coding_job_runs":
        # Die Läufe eines Jobs — BEWUSST eine eigene Ressource und nicht Teil
        # von 'coding_job': die Chat-Karte holt den Job bei jeder Änderung neu
        # und soll dabei nicht die komplette Lauf-Historie mitschleppen. Detail
        # gehört in die Job-Ansicht, der Chat bleibt schmal.
        job_id = (req_data or {}).get("id")
        if job_id is None:
            return {"error": "id erforderlich"}
        try:
            return coding_jobs.list_runs(int(job_id))
        except Exception as e:
            return {"error": str(e)}
    if resource == "imports":
        # Wissens-Importe (services/import_store.py). Reine Leseansicht — das
        # Anlegen und Starten läuft über eigene Nachrichtentypen, nicht hier.
        try:
            return import_store.list_imports(limit=int(req_data.get("limit") or 50))
        except Exception as e:
            return {"error": str(e)}
    if resource == "import_running":
        # Was läuft gerade? Ein frisch geöffnetes Fenster hat die bisherigen
        # import_progress-Ereignisse nicht gesehen und wüsste sonst nichts von
        # einem laufenden Import.
        return import_runner.status()
    if resource == "import":
        # SINGULAR, ein Import per id — gleiches Muster wie coding_job oben:
        # Ereignisse melden nur "etwas hat sich geändert", den vollständigen
        # Stand holt die Ansicht danach frisch hierüber.
        import_id = req_data.get("id")
        if import_id is None:
            return {"error": "id erforderlich"}
        try:
            record = import_store.get(int(import_id))
            return record if record else {"error": "Import nicht gefunden"}
        except Exception as e:
            return {"error": str(e)}
    if resource == "allowed_coding_paths":
        # Für das Coding-Auswahlfeld 'path' in der Projektansicht (jarvis-web) —
        # dieselbe Abfrage wie das LLM-Tool list_allowed_coding_paths
        # (tools.py), hier als Layer-1-Resource ohne LLM-Umweg. client_id
        # bestimmt den zugeordneten Worker (siehe assign_mac_worker); ohne
        # client_id irgendein verbundener local_exec-Client. local_exec.dispatch()
        # blockiert den aufrufenden Thread bis zu 60s — unkritisch, läuft schon
        # via run_in_executor (siehe DATA_REQUEST-Handler), blockiert also nicht
        # den Event-Loop, nur einen Executor-Thread.
        try:
            target_conn_id = coding_jobs.resolve_worker_connection(req_data.get("client_id") or None)
            if not target_conn_id:
                return {"error": "Kein passender Mac-Worker verbunden (Rolle nicht zugeordnet oder nicht verbunden)."}
            result = local_exec.dispatch("list_allowed_paths", target_conn_id=target_conn_id)
            if not result.get("ok"):
                return {"error": result.get("error", "Fehler beim Abfragen der freigegebenen Pfade.")}
            return result.get("data", {})
        except Exception as e:
            return {"error": str(e)}
    if resource == "tickets":
        return local_data.list_tickets()
    if resource == "sync_tickets":
        from services import tickets as tickets_service
        return tickets_service.sync_tickets()
    if resource == "projekte":
        return local_data.list_projekte()
    if resource == "rechnungen":
        return local_data.list_rechnungen()
    if resource == "ausgaben":
        return local_data.list_ausgaben()
    if resource == "import_csv":
        kind = req_data.get("kind", "")
        csv_text = req_data.get("csv_text", "")
        if not csv_text:
            return {"ok": False, "error": "csv_text erforderlich"}
        try:
            if kind == "rechnungen":
                return {"ok": True, "result": finanzen_import.import_invoices(csv_text)}
            if kind == "ausgaben":
                return {"ok": True, "result": finanzen_import.import_vouchers(csv_text)}
            return {"ok": False, "error": f"Unbekannte Art: {kind}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
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
            return coding_usage.get_usage_summary(days=int(req_data.get("days", 14)))
        except Exception as e:
            return {"error": str(e)}
    if resource == "tracking_topics":
        try:
            # coding_engine/chat sind LLM-Verbrauchskosten (chat: pipeline.py's
            # cost_usd-Logs, dieselbe Quelle wie store.chatCostToday) und finanzen
            # hat seine eigene dedizierte Ansicht (finanzen_overview kombiniert es
            # zusätzlich mit Projekt-Schätzungen) — alle drei gehören NICHT in die
            # allgemeine Statistik-Übersicht (Simon: explizite Trennung gewünscht
            # zwischen LLM-Kosten-Tracking und allgemeinen Lebens-Statistiken wie
            # Sport). Kosten-Übersicht ist jarvis-web's eigene "Kosten"-Seite.
            return [t for t in tracking.list_topics() if t not in ("coding_engine", "chat", "finanzen")]
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
    if resource == "finanzen_overview":
        try:
            return finanzen.compute_overview()
        except Exception as e:
            return {"error": str(e)}
    if resource == "finanzen_trend":
        try:
            return finanzen.compute_trend(months=int(req_data.get("months", 12)))
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
    if resource == "threads":
        return session_memory.list_threads()
    if resource == "thread_messages":
        tid = req_data.get("thread_id")
        if tid is None:
            return {"error": "thread_id erforderlich"}
        try:
            return session_memory.get_thread_messages(int(tid))
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
    "projekte":     {"name", "status", "beschreibung", "typ", "notizen", "geschaetzter_wert", "erwartetes_abschlussdatum",
                     "estimated_hours", "path", "repo", "issue_repo", "base_branch", "client_id", "autonomy",
                     "delivery", "coding_doc", "data_scope", "coding_model", "coding_max_budget_usd"},
    "kontakte":     {"name", "email", "telefon", "tags", "notizen"},
    "seite":        {"titel", "inhalt"},
    "rechnungen":   {"rechnungsnummer", "rechnungsdatum", "faellig_am", "bezahlt_am", "betreff",
                     "betrag_netto", "betrag_brutto", "offener_betrag", "kunde", "projekt_id", "notizen",
                     "gesperrt"},
    "ausgaben":     {"belegnummer", "status", "lieferant", "kategorie", "beschreibung", "datum",
                     "faellig_am", "bezahlt_am", "offener_betrag", "betrag", "gesperrt"},
    "threads":      {"title", "project_id"},
}

# 'add' braucht pro Entität einen Pflicht-Bezeichner — bei den meisten 'name', bei
# den aus SevDesk importierten Typen die jeweilige SevDesk-eigene Nummer. Kein
# Eintrag (z.B. "threads", seit Thread-Umbau Teil A) = kein Pflichtfeld — "+ Neuer
# Chat" legt Threads seitdem immer unbenannt an, Umbenennen ist ein separater,
# späterer Schritt (Seitenleiste), kein Teil des Anlegens mehr.
_ENTITY_REQUIRED_FIELD = {
    "todos": "name", "projekte": "name", "kontakte": "name",
    "rechnungen": "rechnungsnummer", "ausgaben": "belegnummer",
}
_ENTITY_ADD_FN = {
    "todos": local_data.add_todo, "projekte": local_data.add_projekt, "kontakte": local_data.add_kontakt,
    "rechnungen": local_data.add_rechnung, "ausgaben": local_data.add_ausgabe,
    "threads": session_memory.create_thread,
}
_ENTITY_UPDATE_FN = {
    "todos": local_data.update_todo, "projekte": local_data.update_projekt, "kontakte": local_data.update_kontakt,
    "seite": local_data.update_seite, "rechnungen": local_data.update_rechnung, "ausgaben": local_data.update_ausgabe,
    "threads": session_memory.update_thread,
}
_ENTITY_DELETE_FN = {
    "todos": local_data.delete_todo, "projekte": local_data.delete_projekt, "kontakte": local_data.delete_kontakt,
    "rechnungen": local_data.delete_rechnung, "ausgaben": local_data.delete_ausgabe,
    "threads": session_memory.delete_thread,
}


def _do_entity_action(entity: str, action: str, data: dict) -> int | None:
    """Blockierende Mutation für Todos/Projekte/Kontakte/Seiten/Rechnungen/Ausgaben/
    Threads — Layer 1 DATA, kein LLM-Umweg. Gibt bei action=="add" die neue
    Zeilen-id zurück (sonst None) — seit Teil 2 (Threads) gebraucht: das Frontend
    muss einen frisch angelegten freien Thread sofort per SET_THREAD aktivieren
    können, ohne extra über die Liste danach suchen zu müssen. Für die anderen
    Entitäten ändert sich dadurch nichts (ihre Add-Funktionen gaben die id schon
    vorher zurück, nur bisher ungenutzt)."""
    if entity not in _ENTITY_FIELDS:
        raise ValueError(f"Unbekannte Entität: {entity}")
    fields = {k: v for k, v in data.items() if k in _ENTITY_FIELDS[entity]}

    if action == "add":
        if entity == "seite":
            raise ValueError("Seiten werden nur über die Migration angelegt")
        required_field = _ENTITY_REQUIRED_FIELD.get(entity)
        if required_field is not None:
            required_val = fields.get(required_field)
            if isinstance(required_val, str):
                required_val = required_val.strip()
            if not required_val:
                raise ValueError(f"{required_field} erforderlich")
            fields[required_field] = required_val
        return _ENTITY_ADD_FN[entity](**fields)
    elif action == "update":
        item_id = data.get("id")
        if not item_id:
            raise ValueError("id erforderlich")
        _ENTITY_UPDATE_FN[entity](item_id, **fields)
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
        _ENTITY_DELETE_FN[entity](item_id)
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
        return coding_usage.get_usage_summary(days=1).get("today_usd", 0.0)
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

    # Ein Turn (process_text/process_audio) lief bisher INLINE im async-for
    # unten (await run_in_executor direkt in der Schleife) — blockierte damit
    # auch das Lesen JEDER weiteren Nachricht auf DERSELBEN Verbindung, bis der
    # Turn fertig war. Kein Problem, solange nichts anderes auf dieser
    # Verbindung eine Antwort braucht — bricht aber zusammen, sobald ein Tool
    # innerhalb des Turns per local_exec.dispatch() auf eine Antwort GENAU
    # DIESES Clients wartet (Mac-Worker chattet über dieselbe Verbindung, über
    # die er auch local_exec bedient): die local_exec_response steckt dann bis
    # zum dispatch()-Timeout (60s) im ungelesenen Rest der Schleife fest, real
    # beobachtet als ~63s-Verzögerung + "Antwort ohne passende Anfrage"
    # (2026-07-30). Fix: Turn läuft als eigener Task, die Schleife liest sofort
    # weiter — _turn_lock hält die bisherige Serialisierung (ein Turn nach dem
    # anderen, keine zwei gleichzeitig) auf dieser Verbindung aufrecht.
    _turn_lock = asyncio.Lock()
    _pending_turn_tasks: set[asyncio.Task] = set()

    def _spawn_turn(coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        _pending_turn_tasks.add(task)
        task.add_done_callback(_pending_turn_tasks.discard)
        return task

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
    send_json({"type": P.STATE, "state": "idle"})

    # Warte kurz auf CLIENT_HELLO um Role und Raumname zu erkennen
    role = "client"
    pending_msgs = []
    initial_thread_id = None  # aus client_hello, siehe SET_THREAD-Reconnect-Fall unten
    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=1.5)
        pending_msgs.append(first_raw)
        if isinstance(first_raw, str):
            first_data = json.loads(first_raw)
            if first_data.get("type") == P.CLIENT_HELLO:
                name = first_data.get("name", "")
                role = first_data.get("role", "client")
                tab_id = first_data.get("tab_id") or client_id
                initial_thread_id = first_data.get("thread_id")
                if name:
                    manager.set_name(client_id, name)
                    manager.set_role(client_id, role)
                    manager.set_capabilities(client_id, first_data.get("capabilities", []))
                    manager.set_worker_id(client_id, first_data.get("worker_id"))
                    if "local_exec" in (first_data.get("capabilities") or []):
                        coding_jobs.on_worker_connected()
                    print(f"[server] Client {addr} heißt: {name!r} (role={role})")
                pending_msgs.clear()
    except asyncio.TimeoutError:
        pass

    # Erst jetzt ist die Kategorie bekannt — Pipeline bekommt die passende History
    # zugewiesen (vorher konstruiert hätte sie fälschlich immer "voice" bekommen).
    category = _category_for_role(role)

    # Ein Web-Tab-Reconnect (z.B. nach einem Server-Neustart) braucht keinen
    # manuellen Verlaufs-Restore mehr: pipeline.py baut das Prompt-Fenster jetzt
    # bei jedem Turn frisch aus session_memory.build_history_window() (SQLite,
    # überlebt einen Neustart) statt aus diesem In-Memory-Dict — der frühere
    # Restore-Block (fand statt über session_memory.find_active_session()) ist
    # damit überflüssig geworden, nicht nur redundant, siehe ROADMAP.md/
    # Migrationsplan messages/threads. api_histories["web"] startet für einen
    # reconnectenden Tab entsprechend leer und dient nur noch als temporäres
    # Vergleichs-Gerüst (siehe pipeline.py::_verify_reconstruction).

    pipeline = JarvisPipeline(
        client_id=client_id,
        on_event=send_json,
        on_audio=send_audio,
        shared_history=_get_api_history(category, tab_id),
        history_lock=history_lock,
        llm_semaphore=llm_semaphore,
        broadcast_web_event=_broadcast_web_event,
    )
    if manager.get_name(client_id):
        pipeline.set_room(manager.get_name(client_id))
    pipeline.set_chat_target(category, tab_id)
    manager.set_tab_client(tab_id, client_id)
    manager.register_pipeline(client_id, pipeline)

    # Reconnect-Fall für Threads (Teil 2): anders als _thinking_enabled/_model
    # (bewusst NICHT persistiert, siehe pipeline.py) übersteht ein aktiver
    # Thread einen Reconnect wie der Modus — ein aktiver Thread ist eine
    # inhaltliche Zuordnung laufender Nachrichten, kein Session-lokaler
    # UI-Toggle, ihn bei jedem WLAN-Hänger unbemerkt zu verlieren wäre der
    # überraschendere, schlechtere Default. client_hello trägt ihn mit (siehe
    # jarvis.js), hier derselbe Lookup wie im SET_THREAD-Handler.
    if initial_thread_id is not None:
        initial_thread_project_id = await loop.run_in_executor(
            None, session_memory.get_thread_project_id, initial_thread_id
        )
        _active_thread_ids[tab_id] = initial_thread_id
        pipeline.set_thread(initial_thread_id, initial_thread_project_id)

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
                            manager.set_capabilities(client_id, data.get("capabilities", []))
                            manager.set_worker_id(client_id, data.get("worker_id"))
                            if "local_exec" in (data.get("capabilities") or []):
                                coding_jobs.on_worker_connected()
                except Exception:
                    pass

        async for message in websocket:
            if isinstance(message, bytes):
                _activate_client(client_id)
                if role != "dashboard":
                    # Ein Sprach-Timeout betrifft nur die "voice"-Kategorie, nicht
                    # die (unbeteiligte) Web-Live-Ansicht.
                    _check_satellite_timeout()

                async def _run_audio_turn(message=message):
                    async with _turn_lock:
                        try:
                            await loop.run_in_executor(None, pipeline.process_audio, message)
                            _last_activity_ts = time.time()
                            _trim_api_history(category, tab_id)
                            _save_history()
                            asyncio.create_task(_push_dashboard_update())
                        except Exception as e:
                            print(f"[server] Unerwarteter Fehler bei Audio-Turn ({client_id}): {e}", flush=True)

                _spawn_turn(_run_audio_turn())
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

                    async def _run_text_turn(text=text, use_tts=use_tts, attachments=attachments):
                        async with _turn_lock:
                            try:
                                await loop.run_in_executor(None, pipeline.process_text, text, use_tts, attachments)
                                _last_activity_ts = time.time()
                                _trim_api_history(category, tab_id)
                                _save_history()
                                if category == "web":
                                    await _persist_web_turn(loop, tab_id)
                                asyncio.create_task(_push_dashboard_update())
                            except Exception as e:
                                print(f"[server] Unerwarteter Fehler bei Text-Turn ({client_id}): {e}", flush=True)

                    _spawn_turn(_run_text_turn())
                elif data.get("type") == P.CLIENT_HELLO:
                    name = data.get("name", "")
                    role = data.get("role", "client")
                    category = _category_for_role(role)
                    tab_id = data.get("tab_id") or client_id
                    # pipeline.set_chat_target() muss hier MIT aktualisiert werden —
                    # sonst bleibt die Pipeline (self._category/self._tab_id, siehe
                    # pipeline.py) auf dem Stand des ERSTEN client_hello stehen, ein
                    # danach gestarteter Coding-Job würde sein Ergebnis/Fortschritt
                    # an den falschen (oder gar keinen) Tab zugestellt bekommen.
                    # Bisher übersehen (gefunden bei der "Live-Fortschritt kommt
                    # nicht an"-Diagnose, 2026-07-31).
                    pipeline.set_chat_target(category, tab_id)
                    manager.set_tab_client(tab_id, client_id)
                    # Reconnect-Fall für Threads (Teil 2) — derselbe Lookup wie
                    # bei der Erstverbindung oben, siehe dortiger Kommentar.
                    resend_thread_id = data.get("thread_id")
                    if resend_thread_id is not None:
                        resend_thread_project_id = await loop.run_in_executor(
                            None, session_memory.get_thread_project_id, resend_thread_id
                        )
                        _active_thread_ids[tab_id] = resend_thread_id
                        pipeline.set_thread(resend_thread_id, resend_thread_project_id)
                    if name:
                        manager.set_name(client_id, name)
                        manager.set_role(client_id, role)
                        manager.set_capabilities(client_id, data.get("capabilities", []))
                        manager.set_worker_id(client_id, data.get("worker_id"))
                        if "local_exec" in (data.get("capabilities") or []):
                            coding_jobs.on_worker_connected()
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
                elif data.get("type") == P.SET_THINKING:
                    pipeline.set_thinking(bool(data.get("enabled")))
                elif data.get("type") == P.SET_LLM_MODEL:
                    pipeline.set_model(data.get("model", ""))
                elif data.get("type") == P.SET_THREAD:
                    # Bewusst NICHT wie SET_MODE/SET_THINKING rein synchron —
                    # die brauchen keine DB, dieser Lookup schon (project_id
                    # zum Thread, damit pipeline.py beides denormalisiert auf
                    # jede Nachricht schreiben kann). run_in_executor wie bei
                    # jedem anderen DB-Zugriff in dieser Schleife.
                    new_thread_id = data.get("thread_id")
                    thread_project_id = None
                    if new_thread_id is not None:
                        thread_project_id = await loop.run_in_executor(
                            None, session_memory.get_thread_project_id, new_thread_id
                        )
                    # Thread-Nachrichten tragen DENSELBEN tab_id wie threadlose
                    # (nur zusätzlich thread_id) — ohne diesen Cursor-Vorstoß bei
                    # JEDEM Wechsel würde das threadlose Fenster (Cursor-basiert,
                    # kennt thread_id nicht) beim Zurückwechseln auf "Kein Thema"
                    # die Nachrichten aus der Thread-Phase wieder mit anzeigen,
                    # obwohl sie fachlich dem Thread gehören (gefunden beim Testen
                    # von build_history_window()). Symmetrisch zu SESSION_RESET,
                    # das denselben Vorstoß schon beim "+ Neuer Chat" macht.
                    await loop.run_in_executor(None, session_memory.advance_cursor, category, tab_id)
                    _active_thread_ids[tab_id] = new_thread_id
                    pipeline.set_thread(new_thread_id, thread_project_id)
                elif data.get("type") == P.IMPORT_UPLOAD:
                    # Legt einen Import an, schreibt die Quelldateien und liefert
                    # die Plan-Vorschau zurück. Startet bewusst KEINEN Lauf —
                    # erst sieht Simon, was der Import kosten würde und wie die
                    # Struktur erkannt wurde. Läuft komplett im Executor, weil
                    # Base64-Dekodierung und Plattenzugriff den Event-Loop sonst
                    # für die Dauer eines mehrere Megabyte großen Uploads
                    # blockieren würden.
                    def _do_import_upload(payload: dict):
                        title = (payload.get("title") or "").strip()
                        topic = (payload.get("topic") or "").strip()
                        if not title or not topic:
                            return {"ok": False, "error": "Titel und Topic sind erforderlich."}
                        new_id = import_store.create(
                            source_path="", source_type="unbekannt", title=title, topic=topic,
                            model=payload.get("model"), max_budget_usd=payload.get("budget_usd"),
                        )
                        try:
                            stored = import_upload.store_files(new_id, payload.get("files") or [])
                            plan = import_adapters.plan(Path(stored["path"]))
                            if not plan.get("adapter"):
                                # Kein Kopf erkannt: der Import bleibt bestehen,
                                # die Dateien auch — aber der Grund steht in der
                                # Antwort, statt beim Start zu überraschen.
                                import_store.update(
                                    new_id, source_path=stored["path"], status="failed")
                                return {"ok": False, "id": new_id, "plan": plan,
                                        "error": plan.get("problem")}
                            import_store.update(
                                new_id, source_path=stored["path"],
                                source_type=plan["adapter"], status="planned",
                                section_count=plan["section_count"],
                                lecture_count=plan["lecture_count"],
                            )
                            return {"ok": True, "id": new_id, "plan": plan, "files": stored["count"]}
                        except Exception as exc:
                            import_upload.discard(new_id)
                            import_store.update(new_id, status="failed")
                            return {"ok": False, "id": new_id, "error": str(exc)}

                    try:
                        upload_result = await loop.run_in_executor(None, _do_import_upload, data)
                    except Exception as e:
                        upload_result = {"ok": False, "error": str(e)}
                    send_json({"type": P.IMPORT_UPLOAD_ACK, **upload_result})
                elif data.get("type") == P.IMPORT_ACTION:
                    # start: Hintergrundlauf anstoßen. stop: kooperativ anhalten.
                    # Fortschritt und Abschluss gehen per Broadcast an ALLE
                    # Web-Clients (wie THREAD_TITLE_UPDATED) — reine Anzeige,
                    # und bei mehreren offenen Fenstern soll jedes mitziehen.
                    action_id = data.get("id")
                    action = data.get("action")
                    try:
                        if action == "start":
                            def _on_progress(update: dict):
                                _broadcast_web_event({"type": P.IMPORT_PROGRESS, **update})

                            def _on_finished(result: dict):
                                _broadcast_web_event({"type": P.IMPORT_FINISHED, **result})

                            res = await loop.run_in_executor(
                                None,
                                lambda: import_runner.start(int(action_id), _on_progress, _on_finished),
                            )
                            send_json({"type": P.IMPORT_ACTION_ACK, "id": action_id,
                                       "action": action, **res})
                        elif action == "stop":
                            stopped = import_runner.request_stop(int(action_id))
                            send_json({"type": P.IMPORT_ACTION_ACK, "id": action_id,
                                       "action": action, "ok": stopped,
                                       "error": None if stopped else "Dieser Import läuft gerade nicht."})
                        else:
                            send_json({"type": P.IMPORT_ACTION_ACK, "id": action_id,
                                       "action": action, "ok": False,
                                       "error": f"Unbekannte Aktion: {action}"})
                    except Exception as e:
                        send_json({"type": P.IMPORT_ACTION_ACK, "id": action_id,
                                   "action": action, "ok": False, "error": str(e)})
                elif data.get("type") == P.MOVE_MESSAGES:
                    # Thread-Umbau Teil A — verschiebt eine oder mehrere vollständige
                    # Runden (nie einzelne Zeilen, siehe session_memory.py::
                    # get_round_bounds) in einen anderen Thread. mode="single_round"
                    # (nur diese eine Runde) vs. der Standard "from_here" (ab hier
                    # alles). fn wird VOR dem run_in_executor-Aufruf ausgewählt statt
                    # als Lambda mit if/else darin — reine Übersichtlichkeit.
                    move_message_id = data.get("message_id")
                    move_target = data.get("target_thread_id")
                    move_mode = data.get("mode", "from_here")
                    move_fn = session_memory.move_round if move_mode == "single_round" else session_memory.move_from_here
                    try:
                        moved_ok = await loop.run_in_executor(None, move_fn, move_message_id, move_target)
                        if moved_ok:
                            send_json({"type": P.MOVE_MESSAGES_ACK, "ok": True})
                        else:
                            send_json({"type": P.MOVE_MESSAGES_ACK, "ok": False,
                                       "error": "Nachricht ist kein gültiger, abgeschlossener Rundenanfang."})
                    except Exception as e:
                        send_json({"type": P.MOVE_MESSAGES_ACK, "ok": False, "error": str(e)})
                elif data.get("type") == P.MERGE_THREADS:
                    merge_source = data.get("source_thread_id")
                    merge_target = data.get("target_thread_id")
                    try:
                        merged_ok = await loop.run_in_executor(None, session_memory.merge_threads, merge_source, merge_target)
                        if merged_ok:
                            # Betroffene, gerade verbundene Tabs umhängen — sonst würde die
                            # nächste dort geschriebene Nachricht unter einer inzwischen
                            # gelöschten thread_id landen (kein pipeline._thread_id-Update
                            # ohne das). Nur DIESE Tabs bekommen den Push, alle anderen sind
                            # von einem Merge zweier Threads unbeteiligt.
                            for affected_tab_id, active_id in list(_active_thread_ids.items()):
                                if active_id == merge_source:
                                    _active_thread_ids[affected_tab_id] = merge_target
                                    affected_pipeline = manager.get_pipeline_for_tab(affected_tab_id)
                                    if affected_pipeline:
                                        affected_pipeline.set_thread(merge_target, session_memory.get_thread_project_id(merge_target))
                                    affected_cb = manager.get_event_callback_for_tab(affected_tab_id)
                                    if affected_cb:
                                        affected_cb({"type": P.THREAD_REASSIGNED, "old_thread_id": merge_source, "new_thread_id": merge_target})
                            send_json({"type": P.MERGE_THREADS_ACK, "ok": True})
                        else:
                            send_json({"type": P.MERGE_THREADS_ACK, "ok": False, "error": "Threads nicht gefunden oder identisch."})
                    except Exception as e:
                        send_json({"type": P.MERGE_THREADS_ACK, "ok": False, "error": str(e)})
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
                        new_id = await loop.run_in_executor(None, _do_entity_action, entity, action, data)
                        ack = {"type": P.ENTITY_ACTION_ACK, "ok": True, "entity": entity, "action": action}
                        if new_id is not None:
                            ack["id"] = new_id
                        send_json(ack)
                        asyncio.create_task(_push_dashboard_update())
                    except Exception as e:
                        send_json({"type": P.ENTITY_ACTION_ACK, "ok": False, "entity": entity, "action": action, "error": str(e)})
                elif data.get("type") == P.GENERATE_DOCUMENT_REQUEST:
                    # Layer 1 DATA, kein LLM-Umweg — z.B. der Export-Knopf direkt in
                    # ProjekteView.vue. Gleiche document_ready-Antwort wie der LLM-Tool-Pfad
                    # (generate_document in tools.py), damit das Frontend nur EINEN Handler braucht.
                    try:
                        filename, mime, data_b64 = await loop.run_in_executor(
                            None, document_export.generate,
                            data.get("quelle_typ", ""), data.get("quelle_id"), data.get("format", ""),
                        )
                        send_json({"type": P.DOCUMENT_READY, "filename": filename, "mime": mime, "data_base64": data_b64})
                    except ValueError as e:
                        send_json({"type": P.ERROR, "message": str(e)})
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
                elif data.get("type") == P.LOCAL_EXEC_RESPONSE:
                    local_exec.resolve_local_exec(data["id"], data)
                elif data.get("type") == P.CODING_JOB_RESULT:
                    delivery = coding_jobs.resolve_job_result(data)
                    if delivery:
                        _deliver_job_result_to_chat(delivery)
                elif data.get("type") == P.CODING_JOB_PROGRESS:
                    _relay_job_progress(data)
                elif data.get("type") == P.CODING_JOB_ACTION:
                    # Job-Ansicht in jarvis-web (Freigeben/Nachbessern/Verwerfen) —
                    # ruft DIESELBEN Funktionen wie die gleichnamigen Tools
                    # (approve_coding_job/revise_coding_job/discard_coding_job in
                    # tools.py), keine zweite Logik fürs Frontend.
                    job_action = data.get("action", "")
                    job_id_arg = data.get("id")
                    if job_action == "approve":
                        action_result = await loop.run_in_executor(None, coding_jobs.approve_job, job_id_arg, data.get("comment"))
                    elif job_action == "revise":
                        action_result = await loop.run_in_executor(None, coding_jobs.revise_job, job_id_arg, data.get("comment"))
                    elif job_action == "discard":
                        action_result = await loop.run_in_executor(None, coding_jobs.discard_job, job_id_arg)
                    elif job_action == "continue":
                        # Fortsetzen eines 'incomplete'-Jobs (Turn-Limit erreicht,
                        # aber bereits committet) — kein comment-Konzept wie bei
                        # approve/revise, siehe coding_jobs.py::continue_job().
                        action_result = await loop.run_in_executor(None, coding_jobs.continue_job, job_id_arg)
                    elif job_action == "answer":
                        # Antwort auf eine Rückfrage — der Lauf hat sich
                        # unterbrochen, weil eine Entscheidung fehlte. Nutzt
                        # dasselbe comment-Feld wie approve/revise, damit das
                        # Frontend keinen eigenen Nachrichtentyp braucht.
                        action_result = await loop.run_in_executor(
                            None, coding_jobs.answer_job, job_id_arg, data.get("comment") or "")
                    else:
                        action_result = f"Unbekannte Aktion: {job_action}"
                    send_json({"type": P.CODING_JOB_ACTION_ACK, "id": job_id_arg, "action": job_action, "result": action_result})
                elif data.get("type") == P.KNOWLEDGE_CONFIRM:
                    if data.get("confirmed"):
                        learning.apply_suggestion(data["id"])
                    else:
                        learning.reject_suggestion(data["id"])
                elif data.get("type") == P.SESSION_RESET:
                    # "+ Neuer Chat" landet immer im threadlosen Grundzustand
                    # (Teil 2) — No-Op für Clients, die nie SET_THREAD senden
                    # (_active_thread_ids[tab_id] ist dann ohnehin schon None).
                    _active_thread_ids[tab_id] = None
                    pipeline.set_thread(None)
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
                    # Ersatz für "Liste leeren" im messages-Strom: Cursor auf den
                    # aktuellen Stand vorrücken statt Zeilen zu löschen (siehe
                    # session_memory.advance_cursor). Vor dem Vorrücken gelesen,
                    # damit old_cursor die Grenze VOR dieser (jetzt beendeten)
                    # Session markiert — an save()/upsert() weitergereicht, damit
                    # eine spätere SESSION_LOAD-Fortsetzung den richtigen Punkt kennt.
                    old_cursor = await loop.run_in_executor(None, session_memory.advance_cursor, category, tab_id)
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
                                old_history, clients_snapshot, category, True, tab_id, old_cursor + 1,
                            )
                            _set_active_session_id(tab_id, None)
                        else:
                            await loop.run_in_executor(
                                None, lambda: session_memory.save(
                                    old_history, clients=clients_snapshot, category=category,
                                    first_message_id=old_cursor + 1,
                                )
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
                    # Cross-Tab-Fortsetzung ist ein Thread-Feature und hier bewusst nicht
                    # unterstützt (siehe ROADMAP.md/Migrationsplan messages/threads) — VOR
                    # jeder Änderung geprüft, damit ein fremder tab_id-Versuch komplett
                    # folgenlos bleibt statt einen halben Zustand zu hinterlassen.
                    allowed = await loop.run_in_executor(None, session_memory.session_belongs_to_tab, sid, category, tab_id)
                    if not allowed:
                        send_json({"type": P.ERROR, "message": "Diese Session gehört zu einem anderen Tab und kann hier nicht geladen werden."})
                    else:
                        # Aktuelle Session (eigener Tab/Raum) abschließen falls nicht leer
                        with history_lock:
                            old = list(_get_api_history(category, tab_id))
                        old_cursor = await loop.run_in_executor(None, session_memory.get_cursor, category, tab_id)
                        if old:
                            clients_snapshot = sorted(_get_session_clients(category, tab_id))
                            if category == "web":
                                await loop.run_in_executor(
                                    None, session_memory.upsert, _get_active_session_id(tab_id),
                                    old, clients_snapshot, category, True, tab_id, old_cursor + 1,
                                )
                            else:
                                await loop.run_in_executor(
                                    None, lambda: session_memory.save(
                                        old, clients=clients_snapshot, category=category,
                                        first_message_id=old_cursor + 1,
                                    )
                                )
                        _reset_session_clients(category, tab_id)
                        # Cursor auf den Punkt vor der geladenen Session zurücksetzen — das
                        # messages-Fenster (künftige LLM-Runden) umfasst danach automatisch
                        # genau diese alte Session plus alles, was seither in diesem Tab
                        # dazukam ("fortsetzen"). Kann False liefern (Alt-Session ohne
                        # first_message_id) — dann bleibt der Cursor unverändert, nur die
                        # Legacy-Anzeige unten wird trotzdem geladen.
                        rewound = await loop.run_in_executor(None, session_memory.rewind_cursor_to_session, category, tab_id, sid)
                        if not rewound:
                            print(f"[server] SESSION_LOAD: Session {sid} hat keine first_message_id — messages-Fenster bleibt unverändert.", flush=True)
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
        if _pending_turn_tasks:
            print(f"[server] Warte auf {len(_pending_turn_tasks)} laufende(n) Turn(s) vor Disconnect-Cleanup ({addr})...", flush=True)
            await asyncio.gather(*_pending_turn_tasks, return_exceptions=True)
        manager.unregister(client_id)
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
    session_memory.migrate_sessions_to_messages()
    # Beide Läufe idempotent, immer bei jedem Start (nicht nur einmalig) — siehe
    # jeweilige Docstrings. clean_stored_content() repariert bereits gespeicherte
    # Zeilen mit response-only Feldern (Vorfall 2026-07-31, "Extra inputs are not
    # permitted"). repair_dangling_turns() räumt Turn-Reste auf, die ein harter
    # Prozess-Abbruch (SIGKILL mitten in einem Call) hinterlassen haben kann —
    # der turn_start_id-Rollback in pipeline.py greift dort nicht, weil kein
    # Python-Exception-Handler mehr läuft.
    session_memory.clean_stored_content()
    session_memory.repair_dangling_turns()
    _load_history()
    stt.load_model()
    alarm_service.init(manager)
    client_music_service.init(manager)
    sleep_coach.init(manager, alarm_service, dispatcher)
    proactive_service.init(manager, alarm_service, dispatcher)
    coding_jobs.init(manager, dispatcher)
    local_exec.init(manager)
    learning.init(manager)
    # Einmaliger Nachhol-Durchlauf für bereits bestehende unbenannte Threads
    # (Thread-Umbau Teil B, Schritt 1) — der Live-Hook in pipeline.py läuft nur
    # bei einem NEUEN Turn, ein Thread in dem längst nicht mehr geschrieben
    # wird würde sonst nie einen Titel bekommen. Eigener Hintergrund-Thread,
    # blockiert den Server-Start nicht.
    threading.Thread(target=thread_naming.run_startup_sweep,
                      args=(_broadcast_web_event,), daemon=True).start()
    print(f"[server] J.A.R.V.I.S. bereit — ws://{HOST}:{PORT}")

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    # Bewusst NICHT "async with websockets.serve(...):" — dessen __aexit__ ruft
    # server.close() mit dem Default close_connections=True auf, was VOR dem
    # eigentlichen Sichern (unten) auf jeden einzelnen offenen Verbindungs-
    # Handler wartet (websockets' eigene Server._close()-Logik). Steckt eine
    # Verbindung dabei gerade in einem lange laufenden LLM-Call (loop.
    # run_in_executor in _run_text_turn/_run_audio_turn — läuft unabhängig vom
    # WebSocket-Status weiter, auch nachdem der Server die Verbindung selbst
    # bereits geschlossen hat), blockiert das den KOMPLETTEN Shutdown, bis
    # dieser Call fertig ist. Dauert das länger als systemds TimeoutStopSec
    # (Default 90s, kein eigener Wert in install_server.sh gesetzt), killt
    # systemd den Prozess hart (SIGKILL) — _save_all_sessions_on_shutdown()
    # läuft dann NIE, der komplette laufende Verlauf (nicht nur der eine
    # in-flight Turn) ist weg. Erklärt einen von Simon gemeldeten Kontext-
    # verlust nach einem Neustart MITTEN in einem aktiven Gespräch (2026-07-31)
    # — alle vorherigen erfolgreichen Restores liefen über scripts/
    # auto_update.sh, das einen Neustart bewusst zurückstellt bis KEIN Client
    # mehr verbunden ist (siehe dort), weshalb dieser Fall vorher nie auftrat.
    # Jetzt umgekehrte Reihenfolge: erst der schnelle, synchrone Snapshot unter
    # history_lock (braucht keine geschlossenen Verbindungen), danach erst
    # server.close()/wait_closed() — ein noch laufender Turn kann den
    # Shutdown weiterhin verzögern, aber nicht mehr den vorherigen Save
    # verhindern.
    server = await websockets.serve(
        handle_connection, HOST, PORT,
        ping_interval=30,
        ping_timeout=120,
        max_size=20 * 1024 * 1024,  # Default 1MiB reicht nicht für Bild-/PDF-Anhänge im Chat
    )
    await stop_event.wait()

    print("[server] Shutdown-Signal empfangen — sichere Sessions...", flush=True)
    await loop.run_in_executor(None, _save_all_sessions_on_shutdown)

    server.close()
    await server.wait_closed()
    print("[server] Shutdown abgeschlossen.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
