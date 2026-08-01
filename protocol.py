"""
WebSocket-Protokoll für JARVIS Server ↔ Client Kommunikation.

Binary frames:
  Client → Server: WAV-Audiodaten (aufgenommene Sprache)
  Server → Client: PCM-Audiodaten (TTS, 24000 Hz, Mono, int16)

JSON frames: alle anderen Nachrichten (State, Text, Events)
"""

# Audio-Format (PCM, Server → Client)
PCM_SAMPLERATE = 24000
PCM_CHANNELS = 1
PCM_DTYPE = "int16"

# ── Server → Client ───────────────────────────────────────────────────────────
STATE = "state"               # {"type": "state", "state": "idle|listening|thinking|speaking|tool_running"}
STATUS = "status"             # {"type": "status", "text": "..."}
TRANSCRIPT = "transcript"     # {"type": "transcript", "text": "..."} — erkannter Text
RESPONSE_START = "response_start"  # {"type": "response_start", "user_message_id": N|null} — id seit Thread-Umbau Teil A, für die Verschieben-Aktion im Chat
RESPONSE_CHUNK = "response_chunk"  # {"type": "response_chunk", "text": "..."}
RESPONSE_DONE = "response_done"    # {"type": "response_done", "text": "...", "assistant_message_id": N|null} — id nur bei abgeschlossenem Turn gesetzt (nicht mitten in einem Tool-Loop), seit Thread-Umbau Teil A
TOOL = "tool"                 # {"type": "tool", "name": "..."}
ERROR = "error"               # {"type": "error", "message": "..."}
PONG = "pong"                 # {"type": "pong"}

# ── Client → Server ───────────────────────────────────────────────────────────
TEXT_INPUT = "text_input"     # {"type": "text_input", "text": "...", "tts": bool, "attachments": [{"filename": "...", "mime_type": "...", "data_base64": "..."}] (optional)}
                              #   tts=True  → LLM + TTS (Voice-Mode, Default)
                              #   tts=False → LLM only, kein Audio (Text-Mode)
PING = "ping"                 # {"type": "ping"}
CLIENT_HELLO = "client_hello" # {"type": "client_hello", "name": "schlafzimmer", "role": "...", "tab_id": "..." (nur role=dashboard — stabile Tab-Identität aus sessionStorage, isoliert die Web-Chat-History pro Tab), "capabilities": ["local_exec"] (optional — nur die Tauri-Desktop-App meldet 'local_exec', ein normaler Browser-Tab nicht, siehe LOCAL_EXEC_REQUEST unten), "worker_id": "..." (optional — nur Tauri-Desktop-App, zufällige lokal persistierte Kennung, noch nicht für Routing genutzt, siehe client_manager.set_worker_id)}
ALARM_SYNC    = "alarm_sync"    # {"type": "alarm_sync", "alarms": [...]} — Client → Server beim Connect
ALARM_RINGING = "alarm_ringing" # {"type": "alarm_ringing", "alarm_id": "...", "label": "..."} — Client → Server wenn Wecker klingelt
ALARM_DISMISSED = "alarm_dismissed" # {"type": "alarm_dismissed", "alarm_id": "...", "snooze_count": N} — Client → Server wenn Wecker aus

# ── Alarm-Steuerung (Server → Client) ─────────────────────────────────────────
SET_ALARM    = "set_alarm"    # {"type": "set_alarm", "alarm_id": "...", "hour": H, "minute": M, "label": "...", "snooze_minutes": N, "max_snooze": N, "song": "..."|null}
CANCEL_ALARM = "cancel_alarm" # {"type": "cancel_alarm", "alarm_id": "..."|null}
SNOOZE_ALARM = "snooze_alarm" # {"type": "snooze_alarm", "alarm_id": "..."|null, "minutes": N}
PLAY_MUSIC   = "play_music"   # {"type": "play_music", "song": "query", "volume": 70, "seek": 0}
STOP_MUSIC   = "stop_music"   # {"type": "stop_music"}

# ── Dashboard (Server → Dashboard-Client) ─────────────────────────────────────
DASHBOARD_SYNC   = "dashboard_sync"   # {"type": "dashboard_sync", "todos": [...], "calendar": [...], "btc": {...}, "clients": [...], "layout_config": {...}}
DASHBOARD_UPDATE = "dashboard_update" # {"type": "dashboard_update", ..., "layout_config": {...}}
LAYOUT_CONFIG    = "layout_config"    # {"type": "layout_config", "cards": [...], "quick_actions": [...]}

# ── Drei-Layer-Protokoll (Client ↔ Server) ────────────────────────────────────
DATA_REQUEST  = "data_request"   # {"type": "data_request", "resource": "todos"|"calendar"|"alarms"|"followups"|"clients"|"btc"}
DATA_RESPONSE = "data_response"  # {"type": "data_response", "resource": "...", "data": ...}
SET_MODE      = "set_mode"       # {"type": "set_mode", "mode": "assistent"|"coach"|"fokus"}
SET_THINKING  = "set_thinking"   # {"type": "set_thinking", "enabled": bool} — Adaptive Thinking pro Client/Session (Default aus)
SET_LLM_MODEL = "set_llm_model"  # {"type": "set_llm_model", "model": "claude-sonnet-5"|"claude-opus-5"|"claude-haiku-4-5"|"claude-fable-5"}
SET_THREAD    = "set_thread"     # {"type": "set_thread", "thread_id": N|null} — aktiven Thread für diesen Tab setzen/löschen (Teil 2, manuelles Etikett)
MOVE_MESSAGES     = "move_messages"      # Client → Server: {"type": ..., "message_id": N, "mode": "from_here"|"single_round", "target_thread_id": N} — Thread-Umbau Teil A
MOVE_MESSAGES_ACK = "move_messages_ack"  # Server → Client: {"type": ..., "ok": bool, "error": "..."}
MERGE_THREADS     = "merge_threads"      # Client → Server: {"type": ..., "source_thread_id": N, "target_thread_id": N} — Thread-Umbau Teil A
MERGE_THREADS_ACK = "merge_threads_ack"  # Server → Client: {"type": ..., "ok": bool, "error": "..."}
THREAD_REASSIGNED = "thread_reassigned"  # Server → Client: {"type": ..., "old_thread_id": N, "new_thread_id": N} — der gerade aktive Thread dieses Tabs wurde wegzusammengeführt, siehe MERGE_THREADS
THREAD_TITLE_UPDATED = "thread_title_updated"  # Server → ALLE Web-/Dashboard-Clients: {"type": ..., "thread_id": N, "title": "..."} — automatische Benennung (Thread-Umbau Teil B, Schritt 1), reine Anzeige-Aktualisierung ohne Zustandsrisiko, deshalb Broadcast statt nur an den auslösenden Tab wie THREAD_REASSIGNED

# ── Event-Overlay (Server → Dashboard) ───────────────────────────────────────
OVERLAY_EVENT   = "overlay_event"   # {"type": "overlay_event", "event_id": "...", "title": "...", "icon": "...", "send": "...", "snooze_minutes": 10}
OVERLAY_DISMISS = "overlay_dismiss" # {"type": "overlay_dismiss", "event_id": "...", "action": "start"|"skip"|"snooze", "minutes": 10}

# ── Session-Verwaltung (Server → Dashboard) ───────────────────────────────────
SESSION_BREAK         = "session_break"          # {"type": "session_break"} — neue Gesprächssession
SESSION_RESET         = "session_reset"          # Client → Server: neue Session starten (speichert aktuelle)
SESSION_LIST_REQUEST  = "session_list_request"   # Client → Server: Liste vergangener Sessions anfragen
SESSION_LIST_RESPONSE = "session_list_response"  # Server → Client: {"type": ..., "sessions": [{id, date, time, title}]}
SESSION_DELETE        = "session_delete"         # Client → Server: {"type": ..., "session_id": N}
SESSION_DELETE_ACK    = "session_delete_ack"     # Server → Client: {"type": ..., "session_id": N, "ok": bool}
SESSION_LOAD          = "session_load"           # Client → Server: {"type": ..., "session_id": N} — alten Chat fortsetzen
SESSION_LOAD_ACK      = "session_load_ack"       # Server → Client: {"type": ..., "messages": [{role, text}]}

# ── Push Notifications (Server → Client, Client → Server) ─────────────────────
NOTIFICATION_PUSH = "notification_push"  # {"type": "notification_push", "id": "...", "text": "...", "priority": "normal", "expires": "..."}
NOTIFICATION_ACK  = "notification_ack"   # {"type": "notification_ack", "id": "..."} — Client bestätigt Empfang

# ── Wissensdatenbank (Server → Client, Client → Server) ───────────────────────
KNOWLEDGE_SUGGESTION = "knowledge_suggestion"  # {"type": "knowledge_suggestion", "id": "...", "topic": "...", "file": "...", "preview": "..."} — JARVIS schlägt vor zu speichern
KNOWLEDGE_CONFIRM    = "knowledge_confirm"     # {"type": "knowledge_confirm", "id": "...", "confirmed": bool} — Nutzer bestätigt/lehnt ab
KNOWLEDGE_WRITE      = "knowledge_write"       # {"type": "knowledge_write", "topic": "...", "file": "...", "content": "..."} — Web App schreibt direkt
KNOWLEDGE_WRITE_ACK  = "knowledge_write_ack"  # {"type": "knowledge_write_ack", "ok": bool, "topic": "...", "file": "...", "error": "..."}

# ── Coding Engine (Server → Client, Client → Server) ──────────────────────────
CODING_APPROVAL_REQUEST  = "coding_approval_request"   # {"type": "coding_approval_request", "id": "...", "text": "..."} — JARVIS fragt vor riskanter Aktion nach
CODING_APPROVAL_RESPONSE = "coding_approval_response"  # {"type": "coding_approval_response", "id": "...", "approved": bool} — Nutzer entscheidet
CODING_TASK_STATUS = "coding_task_status"  # {"type": "coding_task_status", "active": bool, "branch": "...", "model": "...", "instruction": "...", "started_at": "...", "last_action": "..."} — Live-Fortschritt, kein Rate-Limit (anders als notification_push)
CODING_SUDO_PASSWORD_REQUEST  = "coding_sudo_password_request"   # {"type": "coding_sudo_password_request", "id": "...", "text": "..."} — run_command() braucht ein sudo-Passwort (keine passende NOPASSWD-Regel)
CODING_SUDO_PASSWORD_RESPONSE = "coding_sudo_password_response"  # {"type": "coding_sudo_password_response", "id": "...", "password": "..."} — nur einmalig verwendet, nie serverseitig gespeichert/geloggt

# ── Lokale Ausführung auf einem Client mit 'local_exec'-Capability (z.B. die Tauri-Desktop-App) ──
# Generisches Primitiv — 'action' bestimmt WAS lokal läuft ("gh_issue_list", "shell_exec",
# "claude_code_run"), nicht auf einen einzelnen Anwendungsfall festgelegt.
LOCAL_EXEC_REQUEST  = "local_exec_request"   # {"type": "local_exec_request", "id": "...", "action": "...", **action-spezifische Felder}
LOCAL_EXEC_RESPONSE = "local_exec_response"  # {"type": "local_exec_response", "id": "...", "ok": bool, "data": ..., "error": "..."}

# "claude_code_run" läuft fire-and-forget: der Server wartet auf KEINE
# LOCAL_EXEC_RESPONSE (local_exec.dispatch_nowait — der Client schickt zwar
# weiterhin eine sofortige Quittierung, die wird server-seitig still verworfen).
# Das Ergebnis kommt Minuten später ausschließlich als eigene, unabhängige
# CODING_JOB_RESULT-Nachricht — auch alle Vorbereitungs-Fehler auf dem Mac
# (Allowlist, Konto, Git) kommen so, als status="failed".
CODING_JOB_RESULT = "coding_job_result"  # {"type": "coding_job_result", "job_id": int, "ok": bool, "status": "done"|"failed"|"awaiting_review"|"discarded", "session_id": "...", "cost_usd": float, "result": "...", "changed_files": "...", "denials": [...], "branch": "...", "pr_url": "..."|None} — Mac-Worker → Server, unaufgefordert (siehe services/coding_jobs.py). NEU auch Server → Web-Tab-Client: server.py leitet ein Ergebnis mit gesetztem category/tab_id zusätzlich zur Notification an genau diesen Tab weiter (_deliver_job_result_to_chat), Payload dann nur {"type", "job_id", "result", "status"} — jarvis-web rendert das als Chat-Nachricht.

# Flüchtiger Live-Fortschritt WÄHREND ein claude_code_run/claude_code_resume
# noch läuft — aus claude -p --output-format stream-json's tool_use-Events
# destilliert (Worker), NICHT persistiert (weder display_history noch
# api_history) und NICHT über lange Ausfälle hinweg nachgeliefert — ein
# verpasstes Ereignis ist irrelevant, das nächste kommt in Kürze. Client →
# Server (Mac-Worker) UND Server → Client (Web-Tab, nur wenn der Job beim
# Start ein category/tab_id erfasst hat, siehe services/coding_jobs.py::
# get_job_chat_target).
CODING_JOB_PROGRESS = "coding_job_progress"  # {"type": "coding_job_progress", "job_id": int, "text": "liest hello.py"}

# Server → Web-Tab-Client, unaufgefordert: ein neuer Job wurde angelegt (siehe
# services/coding_jobs.py::start_job(), direkt nach dem DB-Insert, nur wenn
# category/tab_id gesetzt sind — gleiche Bedingung wie bei CODING_JOB_RESULTs
# Chat-Zustellung). Flüchtig wie CODING_JOB_PROGRESS, KEINE History-Persistierung
# — für jarvis-webs selbstaktualisierende Job-Karte im Chat: das Signal, an
# genau dieser Stelle im Gesprächsverlauf eine Karte einzufügen, die sich
# danach über CODING_JOB_PROGRESS/CODING_JOB_RESULT (als reine "neu laden"-
# Signale, siehe data_request "coding_job") selbst aktualisiert.
CODING_JOB_CREATED = "coding_job_created"  # {"type": "coding_job_created", "job_id": int}

# Job-Ansicht in jarvis-web (Freigeben/Nachbessern/Verwerfen-Knöpfe) — Client →
# Server, ruft DIESELBEN services/coding_jobs.py-Funktionen wie die Tools
# approve_coding_job/revise_coding_job/discard_coding_job (server.py ruft sie
# direkt auf, keine eigene/zweite Logik). "result" in der Ack ist derselbe
# menschenlesbare Text, den auch das jeweilige Tool zurückgeben würde — kein
# strukturiertes ok/fail für den fachlichen Ausgang (z.B. "Job wartet nicht
# auf Freigabe"), das Frontend zeigt den Text und lädt die Liste neu, der
# tatsächliche Status danach ist die verlässliche Quelle.
CODING_JOB_ACTION     = "coding_job_action"      # {"type": "coding_job_action", "action": "approve"|"revise"|"discard"|"continue", "id": int, "comment": "..."|None} — "continue" (seit 2026-08-01) setzt einen 'incomplete'-Job (Turn-Limit erreicht, bereits committet) per --resume fort, kein comment nötig
CODING_JOB_ACTION_ACK = "coding_job_action_ack"  # {"type": "coding_job_action_ack", "id": int, "action": "...", "result": "..."}

# ── Todos/Projekte/Kontakte (Client → Server, Server → Client) — direkte local_data-Mutation ohne LLM ─
ENTITY_ACTION     = "entity_action"      # {"type": "entity_action", "entity": "todos"|"projekte"|"kontakte"|"threads", "action": "add"|"update"|"delete"|"complete", "id": ..., ...Felder}
ENTITY_ACTION_ACK = "entity_action_ack"  # {"type": "entity_action_ack", "ok": bool, "entity": "...", "action": "...", "error": "...", "id": ... (nur bei action=="add" und Erfolg)}

# ── Dokument-Export (Client → Server, Server → Client) ────────────────────────
GENERATE_DOCUMENT_REQUEST = "generate_document_request"  # {"type": "generate_document_request", "quelle_typ": "projekt"|"seite", "quelle_id": int, "format": "pdf"|"docx"} — Client → Server, Layer 1 DATA (kein LLM-Umweg, z.B. Export-Knopf in ProjekteView.vue). Antwort kommt als document_ready (Erfolg) oder error (Fehlschlag).
DOCUMENT_READY = "document_ready"  # {"type": "document_ready", "filename": "...", "mime": "...", "data_base64": "..."} — Ergebnis von generate_document (tools.py, LLM-Pfad) ODER generate_document_request (Direkt-Pfad), Client löst Browser-Download aus
