"""Generisches Primitiv: JARVIS lässt etwas lokal auf einem Client mit
'local_exec'-Capability ausführen (aktuell: die Tauri-Desktop-App auf Simons
Mac). Bewusst generisch — 'action' bestimmt WAS läuft (z.B. "gh_issue_list",
später auch "claude_code_cli"), kein Sonderfall für einen bestimmten
Anwendungsfall. Der Zweck: manche Daten/Aufgaben sollen NIE über JARVIS'
eigenen HP-Server laufen (z.B. Quellcode von Arbeitsprojekten) — die
Ausführung passiert komplett lokal, nur das Ergebnis (kurze Zusammenfassung/
Metadaten) kommt zurück."""
from __future__ import annotations
import threading
import uuid

import protocol as P

_manager = None
_lock = threading.Lock()
_pending_events: dict[str, threading.Event] = {}
_pending_results: dict[str, dict] = {}
# Anfragen, deren LOCAL_EXEC_RESPONSE bewusst niemand erwartet (dispatch_nowait) —
# resolve_local_exec verwirft sie still statt "Antwort ohne passende Anfrage" zu loggen.
_fire_and_forget_ids: set[str] = set()
_TIMEOUT_SEC = 60


def init(client_manager) -> None:
    global _manager
    _manager = client_manager


def resolve_local_exec(request_id: str, payload: dict) -> None:
    """Von server.py bei LOCAL_EXEC_RESPONSE aufgerufen."""
    with _lock:
        if request_id in _fire_and_forget_ids:
            _fire_and_forget_ids.discard(request_id)
            return
        _pending_results[request_id] = payload
        event = _pending_events.get(request_id)
    if event:
        event.set()
    else:
        print(f"[local_exec] Antwort ohne passende Anfrage: {request_id}", flush=True)


def dispatch(action: str, timeout: float = _TIMEOUT_SEC, **fields) -> dict:
    """Blockiert den aufrufenden Thread bis der Client antwortet (oder Timeout) —
    NIE auf dem Haupt-Event-Loop aufrufen, immer in einem Hintergrund-Thread/
    Executor (wie coding_engine.py's _request_approval_sync/
    _request_sudo_password_sync). Gibt {"ok": bool, "data": ..., "error": "..."}
    zurück."""
    if not _manager:
        return {"ok": False, "error": "local_exec nicht initialisiert."}

    client_id = _manager.get_client_with_capability("local_exec")
    if not client_id:
        return {"ok": False, "error": "Kein Mac-Client mit lokaler Ausführung verbunden."}

    cb = _manager.get_event_callback(client_id)
    if not cb:
        return {"ok": False, "error": "Kein Mac-Client mit lokaler Ausführung verbunden."}

    request_id = str(uuid.uuid4())
    event = threading.Event()
    with _lock:
        _pending_events[request_id] = event

    payload = {"type": P.LOCAL_EXEC_REQUEST, "id": request_id, "action": action, **fields}
    try:
        cb(payload)
    except Exception as e:
        with _lock:
            _pending_events.pop(request_id, None)
        return {"ok": False, "error": f"Senden an Mac-Client fehlgeschlagen: {e}"}

    got_response = event.wait(timeout)

    with _lock:
        result = _pending_results.pop(request_id, None)
        _pending_events.pop(request_id, None)

    if not got_response or result is None:
        return {"ok": False, "error": "Mac-Client hat nicht rechtzeitig geantwortet (Timeout)."}
    return result


def dispatch_nowait(action: str, **fields) -> dict:
    """Wie dispatch(), aber ohne auf irgendeine Antwort zu warten — kehrt sofort
    zurück, sobald die Nachricht rausgeschickt wurde. Für Aktionen, deren
    Ergebnis über einen eigenen Rückkanal kommt (z.B. claude_code_run →
    CODING_JOB_RESULT): ein Warten auf die Quittierung hier hat real zu
    falschen Timeout-Meldungen geführt ("Es wurde nichts angestoßen"), obwohl
    der Job längst lief — und darüber zu doppelt gestarteten Jobs. Gibt nur
    zurück, ob das SENDEN geklappt hat: {"ok": True} oder
    {"ok": False, "error": "..."} (kein Client verbunden / Senden gescheitert)."""
    if not _manager:
        return {"ok": False, "error": "local_exec nicht initialisiert."}

    client_id = _manager.get_client_with_capability("local_exec")
    if not client_id:
        return {"ok": False, "error": "Kein Mac-Client mit lokaler Ausführung verbunden."}

    cb = _manager.get_event_callback(client_id)
    if not cb:
        return {"ok": False, "error": "Kein Mac-Client mit lokaler Ausführung verbunden."}

    request_id = str(uuid.uuid4())
    with _lock:
        _fire_and_forget_ids.add(request_id)

    payload = {"type": P.LOCAL_EXEC_REQUEST, "id": request_id, "action": action, **fields}
    try:
        cb(payload)
    except Exception as e:
        with _lock:
            _fire_and_forget_ids.discard(request_id)
        return {"ok": False, "error": f"Senden an Mac-Client fehlgeschlagen: {e}"}
    return {"ok": True}
