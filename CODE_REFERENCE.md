# J.A.R.V.I.S. — Code-Referenz

Datei-für-Datei-Karte des Server-Repos (`/Volumes/jarvis`, Python). Ziel: verstehen wie etwas implementiert ist, ohne den Code selbst lesen zu müssen. Zeilennummern sind ungefähre Anker (Stand 2026-07-24), keine exakten Garantien über künftige Commits hinweg.

Konzeptioneller Überblick → `ARCHITECTURE.md`. LLM-Tools im Detail → `TOOLS.md`. Produktumfang → `PRODUCT.md`.

Laufzeit-Aufruf-Kette, grob: `server.py::main()` → `brain.sync()` + Service-`init()`s → `handle_connection()` pro Socket → `pipeline.JarvisPipeline` → `process_text()` → `context.detect_modules()` + `build_static_prompt`/`build_dynamic_prompt` (liest `brain.py` + `local_data.py` + `knowledge.py` + `services/btc.py` + `services/calendar.py`) → `llm.stream()` (Anthropic SDK) → Tool-Loop ruft `tools.execute()` → Ergebnisse komprimiert via `llm.compress_tool_history`/`compress_attachment_history` → History zurückgeschrieben, persistiert via `session_memory.save()`/`upsert()`. Client-Bookkeeping läuft über `client_manager.ClientManager`, alle Message-`type`-Strings über `protocol.py`-Konstanten.

---

## server.py (~1036 Zeilen) — WebSocket-Server, Einstiegspunkt

Prozess-Einstieg (`python3 server.py`). Hält alles globale mutable State (Historien, Client-Registry, Session-Buchhaltung) und die eine `handle_connection()`-Coroutine, über die jeder Client läuft.

- `api_histories` / `display_histories` (Z. 60-61) — je `{"voice": [...], "web": {tab_id: [...]}}`. `api_histories` geht an Claude, `display_histories` ist das volle UI-Transkript (inkl. `session_break`-Marker, ohne Platzhalter-Text).
- `_get_api_history`/`_get_display_history`/`_clear_api_history` (Z. 70-87) — `_clear_api_history` mutiert die Liste in-place statt neu zuzuweisen, weil `JarvisPipeline` eine direkte Referenz auf dasselbe Objekt hält.
- `_check_satellite_timeout()` (Z. 178) — 8h-Inaktivitäts-Timeout, nur Voice-Kategorie, rotiert Session, ruft `session_memory.save()`.
- `_persist_web_turn()` (Z. 213) — nach jedem Web-Text-Turn `session_memory.upsert(..., finalize=False)` im Executor-Thread.
- `_build_layout_config(mode)` (Z. 270) — berechnet `{cards, quick_actions}` aus `brain.read("modules")`, Fallback `_DEFAULT_QA_IDS`/`_DEFAULT_CARD_IDS`, hängt `alarms`/`followups`-Karten dynamisch an wenn Daten vorhanden. Registrierungen `_QA_REGISTRY` (Z. 234), `_CARD_REGISTRY` (Z. 251).
- `_handle_data_request(resource, ...)` (Z. 303) — Layer-1-Dispatch ("kein LLM-Umweg"): `knowledge_index`, `knowledge_file`, `todos`, `tickets`, `seite`, `calendar`, `alarms`, `followups`, `history`, `weather`, `clients`, `btc`, Coding-Engine-Status, `tracking_*`, `finanzen_overview`/`finanzen_trend` (seit 2026-07-26/27 — dünne Wrapper um `finanzen.compute_overview()`/`compute_trend()`, die eigentliche Logik lebt in `finanzen.py`, nicht hier), `rechnungen`/`ausgaben` (seit 2026-07-27, `local_data.list_rechnungen()`/`list_ausgaben()`), `import_csv` (seit 2026-07-27 — `{resource:"import_csv", kind:"rechnungen"|"ausgaben", csv_text}` → `finanzen_import.import_invoices()`/`import_vouchers()`, Rückgabe `{ok, result}`/`{ok:false, error}`; genutzt von den neuen Rechnungen-/Ausgaben-Seiten in jarvis-web, läuft wie alle anderen Resources in `run_in_executor`), `session_transcript`, `notification_history`.
- `_do_entity_action()` (Z. 492) — direkte, nicht-LLM CRUD via Dispatch-Dicts (`_ENTITY_FIELDS`/`_ENTITY_REQUIRED_FIELD`/`_ENTITY_ADD_FN`/`_ENTITY_UPDATE_FN`/`_ENTITY_DELETE_FN`, ersetzt frühere if/elif-Ketten) auf Todos/Projekte/Kontakte/Seiten/Rechnungen/Ausgaben via `local_data.*`. `_ENTITY_REQUIRED_FIELD` pro Entität: `name` für die meisten, `rechnungsnummer`/`belegnummer` (SevDesks eigene Nummer) für die importierten Typen.
- `GENERATE_DOCUMENT_REQUEST`-Handler (seit 2026-07-26, im Dispatch-Loop, kein eigenes benanntes `def`) — ruft `document_export.generate()` direkt über den Executor auf, Layer 1 DATA wie `_do_entity_action`. Antwort: `DOCUMENT_READY` bei Erfolg, `ERROR` bei `ValueError` (unbekannte Quelle/Format). Gleicher Empfänger-Handler im Frontend wie der LLM-Tool-Pfad (`generate_document` in `tools.py`) — beide münden in dieselbe `document_ready`-Nachricht.
- `_build_dashboard_sync()` (Z. 546) / `_push_dashboard_update()` (Z. 619) — bauen/broadcasten `DASHBOARD_SYNC`/`DASHBOARD_UPDATE`.
- `handle_connection(websocket)` (Z. 633) — kompletter Verbindungs-Lifecycle: wartet bis 1.5s auf `CLIENT_HELLO` (Z. 670), stellt bei unbekannter `tab_id` die Web-Historie aus `sessions.db` wieder her (`session_memory.find_active_session()`, Z. 696 — Post-Restart-Recovery), konstruiert eine `JarvisPipeline`, sendet `DASHBOARD_SYNC` bzw. spielt `pipeline.greet()`, dann Dispatch-Loop über ~20 Message-Typen.
- `_save_all_sessions_on_shutdown()` (Z. 971), `main()` (Z. 1004) — Startup: `brain.sync()`, `knowledge.rebuild_index()`, History laden, STT-Modell laden, alle `services/*`-Singletons initialisieren. Shutdown archiviert offene Sessions.

Auffällig: durchgehend mit datierten "Warum"-Kommentaren zu echten Bugs annotiert (Tab-A-löscht-Tab-B-Historie 2026-07-20, `[tool_result]`-Chatblase-Bug 2026-07-23, History-Lücken bei Tool-lastigen Gesprächen 2026-07-22) — verlässliches In-Code-Changelog.

---

## pipeline.py (~354 Zeilen) — `JarvisPipeline`

Eine Instanz pro verbundenem Client. Kapselt einen kompletten Turn: Text/Audio rein → Claude (mit Tool-Loop) → gestreamter Text + TTS-Audio raus. Transport-agnostisch.

- `__init__` (Z. 66) — nimmt `shared_history` (dasselbe Listenobjekt wie `server.py`s `api_histories`), gemeinsamen `history_lock`, gemeinsamen `llm_semaphore`. Trackt `_executed_tool_ids` (Dedup) und `_active_modules`. Seit 2026-07-25 zusätzlich `_thinking_enabled: bool` (Default `False`) und `_model: str` (Default `llm.MODEL`) — pro Client/Session einstellbar, nicht global.
- `set_thinking(enabled)` / `set_model(model)` (Z. ~106-113) — setter für obige Felder, von `server.py`s `SET_THINKING`/`SET_LLM_MODEL`-Handlern aufgerufen. `set_model()` validiert gegen `llm.MODEL_CATALOG`, ignoriert unbekannte Werte still (kein Fehler, kein State-Wechsel).
- `greet()` (Z. 101) — "Bereit."-TTS-Ack, umgeht das LLM bewusst.
- `process_audio()` (Z. 129) — WAV → `stt.transcribe()` → Noise-Filter (`_is_noise`, Z. 32) → `process_text()`.
- `process_text(text, use_tts, attachments)` (Z. 153) — Kern-Turn: `llm_semaphore` holen, User-Message anhängen (Attachments über `_attachment_to_block`, Z. 43 — Textdateien werden inline übernommen, Bilder/PDFs base64, **CSV-Dateien seit 2026-07-27 als Sonderfall vor der generischen `text/`-Behandlung**: laufen durch `finanzen_import.detect_and_import()` statt roh als Text in den Kontext zu gehen — Claude bekommt nur die fertige Import-Zusammenfassung inkl. unzugeordneter Rechnungen, siehe `finanzen_import.py`), `context.detect_modules()` **einmal pro Session** (Z. 168, nur beim ersten Turn), `system_static`/`system_dynamic` bauen, `_run_llm()`, dann `final_messages` zurück in `self.history` nach Kompression (Z. 181-190) und Cap bei 150 Einträgen (Z. 196). Schlägt `_run_llm()` komplett fehl, wird die eben angehängte User-Message wieder entfernt (Z. 202-213) — verhindert die "zwei User-Turns hintereinander"-Kaskade (siehe ROADMAP.md, Vorfall 2026-07-22).
- `_run_llm()` (Z. 233) — Tool-Call-Loop: streamt via `llm.stream(..., thinking=self._thinking_enabled, model=self._model)`, puffert Text satzweise für TTS (`SENTENCE_END`-Regex, Z. 25, `TTS_BUFFER_MIN=120`), führt bei `stop_reason=="tool_use"` jeden Block via `tools.execute()` aus (Z. 338), Dedup gegen `_executed_tool_ids` pro `call_id`, komprimiert Tool-History jede Iteration (Z. 346), loopt bis `end_turn`. **Seit 2026-07-25:** `stop_reason=="refusal"` wird wie `end_turn` behandelt (Turn beendet, Platzhaltertext falls `turn_text` leer) — vorher war das der einzige unbehandelte `stop_reason`-Wert, hätte mit unveränderten `client_messages` eine Endlosschleife ausgelöst. Bis dahin nur theoretisch (Sonnet 4.6/5 refust selten), mit Fable 5 als wählbarem Modell (dessen Sicherheitsklassifikatoren auf Cyber-/Bio-nahe Themen ansprechen können) ein reales Risiko.
- `save_session()` (Z. 223) — aus `server.py`s `finally`-Block beim Disconnect (nur Voice).

---

## context.py (~213 Zeilen) — System-Prompt-Builder, Modul-Loading

Baut den zweiteiligen System-Prompt und implementiert das Keyword-basierte, bedingte Kontext-Laden.

`detect_modules(first_message, mode)` (Z. 46) ist real implementiert, nicht nur Idee: `_KEYWORD_MAP` (Z. 20) mappt Keywords auf Module (`todos`, `calendar`, `projects`, `btc`), `_MODE_DEFAULT_MODULES` (Z. 39) gibt jedem Modus eine Baseline auch ohne Treffer, kurze/unklare erste Nachrichten (<8 Zeichen, Z. 53) oder Check-in-Phrasen (Z. 35) laden alles. `pipeline.py` ruft das genau einmal pro Session auf — das Modul-Set ist für das ganze Gespräch fix.

`build_static_prompt(mode, active_modules)` (Z. 72) gated tatsächlich: `local_data.list_todos()` nur `if "todos" in modules` (Z. 110-113), `list_projekte()` nur `if "projects" in modules` (Z. 114-116). `build_dynamic_prompt()` (Z. 178) gated Kalender/BTC analog. **Wissenstopics (`knowledge.read_summary()`, Z. 94-97) und `brain.build_prompt_section()` (Z. 99) sind NICHT gated** — laufen immer, unabhängig vom Modul-Set. Log-Zeile zur Laufzeit-Verifikation: `[context] Module geladen: [...]` (Z. 173).

Tote Imports (harmlos, nur Rauschen): `config` und `session_memory` werden importiert, aber im Dateikörper nicht verwendet.

---

## brain.py (~767 Zeilen) — SQLite-Store, 7 Sections, Memory-Subsystem

JSON-Blob-pro-Section-Store (`~/.jarvis/brain.db`, eine Tabelle `brain(section, data)`).

Memory-Einträge haben vollständiges Schema (`remember()`, Z. 155, baut es Z. 182-189): `{id, text, ts_iso, category, weight, source}`.
- `remember(text, category, source)` (Z. 155) — dedupliziert per Substring-Match innerhalb derselben Kategorie (Update statt Insert, Z. 168-180), pruned bei >100 Einträgen nach niedrigstem `weight` (Z. 193-197, `nutzer-explicit` ausgenommen).
- `forget(entry_id)` (Z. 203) — Delete by id.
- `get_memory(top_k=20, min_weight=0.1)` (Z. 215) — gefiltert, sortiert, genutzt von `build_prompt_section()` (Z. 744).
- `apply_aging()` (Z. 225) — Alters-Decay: ≤30 Tage → 1.0, ≤90 → 0.5, sonst 0.1 (`nutzer-explicit` ausgenommen). **Läuft nur einmalig beim Serverstart** (`sync()`, Z. 343), nicht periodisch — ein lange laufender Server hat bis zum nächsten Neustart veraltete Gewichte.
- `_migrate_memory_schema()` (Z. 269) — einmaliger, idempotenter Upgrade-Pfad für Alt-Einträge ohne Schema.
- `_VALID_CATEGORIES` (Z. 152): `{ziele, abmachungen, vorlieben, kontext, followup, wissen}` — `remember()` fällt bei unbekannter Kategorie still auf `"kontext"` zurück.

Weitere zentrale Funktionen: `read()`/`write()` (Z. 506, 521, generischer Dot-Notation-Zugriff, überall im Code genutzt — `write()` hat einen Sonderpfad für `section=="memory"` der roh anhängt, am `remember()`-Interface vorbei), `sync()` (Z. 333, Startup-Orchestrator), `migrate_sections()` (Z. 347), `build_modules_prompt()`/`build_prompt_section()` (Z. 574, 602 — bauen den Großteil des System-Prompts).

**Bekanntes Problem:** `migrate_sections()` (Z. 400) importiert `SYSTEM_PROMPT_BASE` aus `config.py` — das existiert dort nicht mehr (verifiziert per Grep über das ganze Repo). Abgefangen von `except ImportError: pass` (Z. 415-416), also kein Crash, aber der Seed-Schritt für ein frisches `brain.modules` bei einer Neuinstallation ist dadurch stillschweigend ein No-Op.

---

## client_manager.py (~170 Zeilen) — `ClientManager`

In-Memory-Registry verbundener Clients (nicht persistiert, pro Prozess neu aufgebaut). Thread-safe via ein `self._lock`.

- `register()`/`register_event()`/`register_pipeline()` (Z. 20-28, 58) — client_id → Audio-Callback / Event-Callback / `JarvisPipeline`.
- `set_capabilities()`/`get_client_with_capability()` (Z. 74-87) — Basis für `local_exec`: statt eine feste client_id anzusprechen, sucht `services/local_exec.py`/`coding_engine.py` "den einen verbundenen Client mit `local_exec`-Capability" (die Tauri-App). Annahme: höchstens ein solcher Client — ausreichend für ein Ein-Mac-Deployment.
- `set_active()`/`get_active()`/`send_audio_to_active()`/`send_event_to_active()` (Z. 101-142) — "aktiver Client"-Konzept für Raum-gerichtetes Audio-Routing; `server.py` ruft `set_active()` bei jeder eingehenden Nachricht.
- `list_clients()` (Z. 46), `get_dashboard_event_callbacks()` (Z. 158), `unregister()` (Z. 89).

---

## session_memory.py (~287 Zeilen) — Session-Persistenz

Persistiert Gespräche in `~/.jarvis/sessions.db` (Tabelle `sessions`, Spalten inkrementell per `ALTER TABLE` ergänzt: `title`, `transcript`, `clients`, `category`, `tab_id`).

- `_extract_text(content)` (Z. 58) — flacht Content-Blöcke zu Text ab; reine Tool-Turns (kein Text-Block) ergeben einen Platzhalter wie `"[tool_use, tool_result]"` statt leerem String (fixt einen 2026-07-22-Bug, bei dem solche Turns spurlos verschwanden).
- `is_placeholder_text(text)` (Z. 79) — Regex `^\[[a-z_]+(?:, [a-z_]+)*\]$`; `server.py` nutzt das um Platzhalter aus `display_history` fernzuhalten (fixt einen 2026-07-23-Bug: Platzhalter erschienen als eigene Chat-Blase).
- `save(history, clients, category)` (Z. 100) — Insert einer fertigen Session, stößt `learning.process_session()` im Hintergrund-Thread an (`t.join(timeout=10)` beim Shutdown).
- `upsert(session_id, history, clients, category, finalize, tab_id)` (Z. 148) — Insert-oder-Update, laufend für Web-Tabs genutzt. `tab_id` wird nur beim initialen Insert gesetzt.
- `find_active_session(tab_id)` (Z. 214) — `SELECT ... WHERE tab_id=? ORDER BY id DESC LIMIT 1`, der Post-Restart-Reconnect-Mechanismus.
- `list_sessions()`/`get_transcript()`/`delete()` (Z. 232, 259, 273).
- `load_for_prompt(days=3)` (Z. 285) — **explizit tot**, eigene Docstring sagt "Legacy — nicht mehr in context.py verwendet", Body ist nur `return ""`.

Toter Import: `config` wird importiert, nie referenziert.

---

## protocol.py (~96 Zeilen) — WebSocket-Message-Typen

Reine Konstanten, keine Logik, `import protocol as P`, referenziert als `P.XXX`. 45 Konstanten:

| Gruppe | Konstanten |
|---|---|
| Server → alle Clients | `STATE`, `STATUS`, `TRANSCRIPT`, `RESPONSE_START/CHUNK/DONE`, `TOOL`, `ERROR`, `PONG` |
| Client → Server | `TEXT_INPUT`, `PING`, `CLIENT_HELLO`, `ALARM_SYNC`, `ALARM_RINGING`, `ALARM_DISMISSED` |
| Server → Client (Alarm/Musik) | `SET_ALARM`, `CANCEL_ALARM`, `SNOOZE_ALARM`, `PLAY_MUSIC`, `STOP_MUSIC` |
| Server → Dashboard | `DASHBOARD_SYNC`, `DASHBOARD_UPDATE`, `LAYOUT_CONFIG` |
| Drei-Layer-Protokoll | `DATA_REQUEST`, `DATA_RESPONSE`, `SET_MODE`, `SET_THINKING` (seit 2026-07-25 — `{"type":"set_thinking","enabled":bool}`, schaltet Adaptive Thinking pro Client/Session), `SET_LLM_MODEL` (seit 2026-07-25 — `{"type":"set_llm_model","model":"claude-sonnet-5"\|"claude-opus-5"\|"claude-haiku-4-5"\|"claude-fable-5"}`, wechselt das Chat-Modell pro Client/Session). Beide aktuell nur von jarvis-web genutzt (🧠-Toggle + Modell-Select in der Chat-Eingabeleiste), Kontext bleibt beim Wechsel erhalten (gleiche `client_messages`), lediglich der Prompt-Cache wird beim nächsten Call neu geschrieben (anderes Modell = anderer Cache-Namespace). |
| Overlay | `OVERLAY_EVENT` (→Dashboard), `OVERLAY_DISMISS` (→Server) |
| Session-Management | `SESSION_BREAK`, `SESSION_RESET/LIST_REQUEST/DELETE/LOAD` (→Server), `SESSION_LIST_RESPONSE/DELETE_ACK/LOAD_ACK` (→Client) |
| Push-Notifications | `NOTIFICATION_PUSH` (→Client), `NOTIFICATION_ACK` (→Server) |
| Wissensdatenbank | `KNOWLEDGE_SUGGESTION` (→Client), `KNOWLEDGE_CONFIRM`/`KNOWLEDGE_WRITE` (→Server), `KNOWLEDGE_WRITE_ACK` (→Client) |
| Coding Engine | `CODING_APPROVAL_REQUEST/RESPONSE`, `CODING_TASK_STATUS`, `CODING_SUDO_PASSWORD_REQUEST/RESPONSE` (Passwort nie geloggt/gespeichert) |
| Local Exec | `LOCAL_EXEC_REQUEST` (→Client mit Capability), `LOCAL_EXEC_RESPONSE` (→Server) |
| Entity CRUD | `ENTITY_ACTION` (→Server), `ENTITY_ACTION_ACK` (→Client) |
| Dokument-Export | `GENERATE_DOCUMENT_REQUEST` (→Server, seit 2026-07-26 — `{"type":"generate_document_request","quelle_typ":"projekte"\|"todos"\|"kontakte"\|"seite","quelle_id":N,"format":"pdf"\|"docx"}`, Layer 1 DATA ohne LLM-Umweg, z.B. die PDF/Word-Knöpfe in `ProjektItem.vue`/`SeiteView.vue`), `DOCUMENT_READY` (→Client — `{"type":"document_ready","filename":"...","mime":"...","data_base64":"..."}`, Ergebnis von `generate_document` (LLM-Tool-Pfad) ODER `generate_document_request` (Direkt-Pfad), jarvis-web löst daraus direkt einen Browser-Download aus) |

---

## config.py (~38 Zeilen) — Environment/Config-Loader

`.env`-Loader (`load_dotenv()`) plus abgeleitete Konstanten und Verzeichnis-Layout unter `~/.jarvis`.

Lädt (mit Defaults wo angegeben): `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`/`_VOICE_ID`, `MANUAL_MODE`, `CODING_ENGINE_API_KEY` (Fallback `ANTHROPIC_API_KEY`), `CODING_MANUAL_MODE`, `CODING_TASK_BUDGET_USD` (3.0), `CODING_DAILY_BUDGET_USD` (10.0), `CODING_ENGINE_MODEL` (`"claude-sonnet-5"`), `CODING_ENGINE_MODEL_HIGH` (`"claude-opus-4-8"`), `CHAT_DAILY_BUDGET_USD` (5.0, nur Anzeige, kein Hard-Block), `EMAIL_*`, `WHISPER_MODEL`, `AUDIO_INPUT_DEVICE`, `WEATHER_CITY` (`"Stuttgart"`), `JARVIS_SERVER`. Hardcoded: `VERSION="1.2.0"`, `GITHUB_REPO`. Pfade: `JARVIS_DIR`, `KNOWLEDGE_DIR`, `KNOWLEDGE_INDEX_DB`, `TRACKING_DB`.

**Nicht zentral über config.py geladen** (worth wissend): `JARVIS_HOST`/`JARVIS_PORT` direkt in `server.py`; `NOTION_API_KEY` direkt in `local_data.py` (nur für die einmalige Alt-Migration); `PICOVOICE_ACCESS_KEY` in `.env` vorhanden, aber kein `.py`-Konsument im Server-Repo gefunden (vermutlich Client-seitig konsumiert).

---

## local_data.py (~1050 Zeilen) — SQLite statt Notion

Ersetzt seit 2026-07-19/23 die alte Notion-Integration. Tabellen: `todos`, `projekte`, `kontakte`, `seiten`, seit 2026-07-27 zusätzlich `rechnungen`/`ausgaben`.

- `todos` — inkl. Ticket-Spalten `source, external_id, repo, body, labels` — GitHub-Tickets sind einfach `WHERE source='github'`-gefilterte Todos (`list_tickets()`, Z. 135), keine eigene Tabelle.
- `seiten` (Unterseiten, seit 2026-07-23) — `parent_typ`/`parent_id` (Wurzel: Todo/Projekt/Kontakt) XOR `eltern_seite_id` (verschachtelt unter einer anderen Seite).
- CRUD-Tripel pro Entität: `list_/add_/update_/delete_todo` (117-201), analog `_projekt` (206-275), `_kontakt` (280-341).
- `_normalize_fields()` (seit 2026-07-27) — wandelt `""` zu `None` in jedem `**fields`-Dict, angewendet in `update_todo()`/`update_projekt()`/`update_kontakt()`/`write()` (nicht `update_seite()`, dort ist `titel NOT NULL`). Behebt einen echten, live reproduzierten Bug: `TodoItem.vue`s Edit-Formular befüllt ein bisher leeres `datum`-Feld mit `''` statt `None` und schickt das beim Speichern unverändert mit — `list_todos()`s Filter `datum IS NULL OR datum >= cutoff` greift bei `''` auf keinen der beiden Zweige (Leerstring ist lexikographisch kleiner als jeder `YYYY-MM-DD`-String), das Todo verschwand danach dauerhaft aus der Liste, obwohl es in der DB weiter existierte. `_get_db()` (Z. 36) reparariert zusätzlich einmalig bestehende Bestandsdaten (`UPDATE todos SET datum = NULL WHERE datum = ''`, idempotent, läuft bei jedem Connect).
- `projekte.geschaetzter_wert` (REAL, seit 2026-07-26) — geschätzter Auftragswert, speist zusammen mit `tracking.py`s realen `finanzen`/`gewinn`-Logs die Finanzen-Übersicht in `TrackingView.vue` (`finanzen.py`). Nur für nicht abgeschlossene Projekte relevant (Status `Erledigt`/`Archiviert` zählt nicht mit).
- `projekte.erwartetes_abschlussdatum` (TEXT, YYYY-MM-DD, seit 2026-07-27) — für bekannte Pipeline-Projekte, speist den Gewinn-Trend-Chart als "Pipeline"-Balken im jeweiligen Monat (`finanzen.compute_trend()`).
- `query/write/update/delete` (347-409) — generischer Dispatch für die LLM-Tool-Schicht (`todos`/`projekte`/`rechnungen`/`ausgaben`, nicht `kontakte`/`seiten`). `query()`s `limit` ist seit 2026-07-27 `None`-fähig mit pro-Datenbank-Default (`_QUERY_META`-Dict: `cols`/`default_limit`/`search_col`/`status_col`/`unterseiten`, 200 für `projekte`/`rechnungen`/`ausgaben`, 10 für `todos`) statt hart `10` für alle — vorher schnitt `data_query('projekte')` ohne explizites `limit` silently alles über 10 Projekten ab, JARVIS hatte keine Möglichkeit zu merken dass die Liste unvollständig war (Simon: "Ich glaube Jarvis hat keine Möglichkeit alle Projekte zu ziehen").
- `rechnungen`/`ausgaben` (seit 2026-07-27, aus SevDesk-CSV-Exports importiert, siehe `finanzen_import.py`) — je eigenes Identifier-Feld statt `name`: `rechnungsnummer`/`belegnummer` (beide `UNIQUE`, SevDesks eigene stabile Nummer). CRUD-Tripel `list_/add_/update_/delete_rechnung` und `_ausgabe`, plus `upsert_rechnung(rechnungsnummer, **fields)`/`upsert_ausgabe(belegnummer, **fields)` — Insert-oder-Update anhand der Nummer, Kern des idempotenten Imports. `upsert_rechnung()` überschreibt eine bereits gesetzte `projekt_id` **nie** (Feld wird aus `fields` entfernt bevor `update_rechnung()` läuft, falls `existing_projekt_id is not None`) — ein erneuter CSV-Import darf eine manuell/JARVIS-gesetzte Projekt-Zuordnung nicht zurücksetzen. `rechnungen.projekt_id` bewusst nicht automatisch befüllt (siehe `finanzen_import.py`-Docstring) — Zuordnung passiert über die Rechnungen-Seite (jarvis-web, Dropdown) oder `data_query`/`data_update` im Gespräch mit JARVIS.
- `gesperrt`-Feld (`INTEGER DEFAULT 0`, beide Tabellen, seit 2026-07-27) — Simon kann eine einzelne Zeile sperren (🔒-Toggle in `RechnungItem.vue`/`AusgabeItem.vue`, oder `data_update(..., properties={"gesperrt": true})`). `upsert_rechnung()`/`upsert_ausgabe()` überspringen eine gesperrte Zeile beim Import komplett und geben `None` statt der id zurück (statt `int`, damit der Aufrufer "übersprungen" von "neu/aktualisiert" unterscheiden kann) — schützt z.B. eine manuell korrigierte Rechnung davor, dass ein erneuter Import ihre SevDesk-Rohwerte zurückschreibt.
- **`conn`-Parameter (seit 2026-07-27, Bugfix):** `add_rechnung`/`update_rechnung`/`add_ausgabe`/`update_ausgabe`/`upsert_rechnung`/`upsert_ausgabe` akzeptieren jetzt optional eine bestehende `sqlite3.Connection` — ohne sie identisches Verhalten wie vorher (eigene Connection, eigener Commit/Close). Grund: `upsert_rechnung()`/`upsert_ausgabe()` pro Zeile in einer Import-Schleife aufrufen bedeutete pro Zeile eine eigene Connection inkl. voller Schema-Migration (`_get_db()`) + eigenem Commit-fsync — bei 174 Zeilen (Live-Fund) summierte sich das zu über 10s und riss das 10s-Timeout von `store.requestData()` im Frontend, ganz ohne dass irgendwo eine Exception geworfen wurde (daher leere Server-Logs trotz sichtbarem Fehlschlag). `upsert_rechnungen_bulk(entries)`/`upsert_ausgaben_bulk(entries)` (neu) nutzen eine einzige Connection/einen einzigen Commit für den ganzen Batch — von `finanzen_import.import_invoices()`/`import_vouchers()` genutzt, nicht mehr die Einzel-Upserts in einer Schleife. Live gemessen: 174 Zeilen vorher >10s (Timeout), nachher 12ms.
- `create_seite()` (Z. 749) — erzwingt genau eine der beiden Eltern-Varianten. Docstring dokumentiert explizit den Bug, den diese Funktion behoben hat: JARVIS behauptete "in Notion" zu schreiben (existiert seit 2026-07-19 nicht mehr), weil es vorher keine Möglichkeit gab, überhaupt eine neue Unterseite anzulegen.
- `update_seite()`, `get_seite()`, `list_seiten()`, `list_unterseiten()`, `get_seite_breadcrumbs()`, `get_seite_view()`, `read_seite()` (778-890) — Rest der Seiten-API.
- `migrate_stammdaten()`/`backfill_seiten_inhalte()` (471, 683) — einmaliger, guard-geschützter Notion-Import, nur relevant für die historische Migration.

---

## llm.py (~180 Zeilen) — Anthropic-Wrapper

- `MODEL = "claude-sonnet-5"` (Z. 6, hardcoded) — Fallback-Modell, wenn eine Pipeline-Instanz kein/ein ungültiges Modell setzt.
- `MODEL_CATALOG` (Z. 13, seit 2026-07-25) — Dict `model_id → {label, input, output}` (Preis in USD/1M Tokens), vier Einträge: `claude-haiku-4-5` ($1/$5), `claude-sonnet-5` ($3/$15), `claude-opus-5` ($5/$25), `claude-fable-5` ($10/$50). Einzige Quelle für gültige Modell-Strings — `pipeline.set_model()` validiert dagegen, `stream()`/`compute_cost()` schlagen hier den Preis/Fallback nach. jarvis-web spiegelt dieselben vier Einträge (Labels + Hover-Beschreibungen) in `src/stores/jarvis.js::LLM_MODELS` — bei Preisänderungen oder neuen Modellen **beide Stellen** pflegen.
- `_get_client()` (Z. 32) — Lazy-Singleton, `timeout=120.0` explizit gesetzt (schützt den globalen `llm_semaphore` vor einem unbegrenzt hängenden Call, siehe ROADMAP.md Vorfall 2026-07-22).
- `compute_cost(usage, model=None)` (Z. 48, seit 2026-07-25 mit `model`-Parameter) — USD-Schätzung, Preis kommt aus `MODEL_CATALOG[model]` (Fallback `MODEL` falls `model` fehlt/unbekannt) statt fest verdrahtetem Sonnet-Preis — sonst wäre die Kostenanzeige bei Opus/Haiku/Fable falsch. Cache-Write-Multiplikator 2.0×, Cache-Read 0.10×, jeweils auf den Input-Preis des jeweiligen Modells. Speist `tracking.add_log("chat", "cost_usd", ...)`. Thinking-Tokens zählen als normale Output-Tokens, keine separate Abrechnung nötig.
- `compress_tool_history()`/`_compress_one()` (72, 94) — komprimiert alle `tool_result`-Blöcke außer dem des jeweils neuesten Tool-Turns.
- `compress_attachment_history()`/`_compress_attachment()` (108, 131) — analog für Bild-/Dokument-Anhänge.
- `stream(system_static, system_dynamic, messages, tools, thinking=False, model=None)` (Z. 140, `@contextmanager`) — `cache_control: {"type":"ephemeral","ttl":"1h"}` auf den statischen Block und die letzte Tool-Definition. `model` fällt auf `MODEL` zurück falls nicht in `MODEL_CATALOG`. `thinking`: `False` → `{"type":"disabled"}` + `max_tokens=8096` (Default), `True` → `{"type":"adaptive"}` + `max_tokens=16000`. **Sonderfall Fable 5** (Z. 166): erzwingt immer `{"type":"adaptive"}` + `max_tokens=16000`, unabhängig vom `thinking`-Argument — das Modell lehnt `disabled` mit HTTP 400 ab. Gesteuert pro Client/Session über `pipeline.set_thinking()`/`pipeline.set_model()` (WS-Nachrichten `set_thinking`/`set_llm_model`, siehe `protocol.py`) — in jarvis-web als 🧠-Toggle + Modell-Select direkt in der Chat-Eingabeleiste, Default Sonnet 5 + Thinking aus (Latenz für Sprachclients bleibt unverändert schnell). Der Select deaktiviert den Thinking-Toggle clientseitig bei Fable-5-Auswahl.

---

## knowledge.py (~201 Zeilen, nach Wiki-Umbau) — Wissensdatenbank

Details der Konzept-Ebene → `ARCHITECTURE.md` Abschnitt "Wissensdatenbank". Kern-API:

```python
def read(topic: str, file: str) -> str
def write(topic: str, file: str, content: str, tags: list[str] | None = None)
def append_section(topic: str, file: str, heading: str, content: str)
def list_available() -> list[dict]
def search(query: str, max_results: int = 5) -> list[dict]
def read_summary(topic: str) -> str
def get_links(topic: str, file: str) -> dict          # {"outgoing": [...], "backlinks": [...]}
def move(from_topic, from_file, to_topic, to_file, content=None) -> list[str]   # echtes Verschieben, kein Stub
def delete(topic: str, file: str) -> list[str]        # echtes Löschen, kein Stub
def rebuild_index()
```

`move()`/`delete()` (seit 2026-07-24) geben jeweils die Liste der Backlinks zurück, die noch auf die alte/gelöschte Adresse verweisen — sie schreiben referenzierende Dateien nicht automatisch um, das muss der Aufrufer gezielt nachziehen. Nur über MCP (`jarvis_move_knowledge`/`jarvis_delete_knowledge`) erreichbar, kein LLM-Tool für den Chat/Voice-Pfad.

`search()` matcht ausschließlich gegen `f"{path} {tags} {summary}"` (lowercased, whitespace-getrennte Wörter, reiner Substring-Score) — **nie den vollen Dateiinhalt**, nur die ersten ~150-200 Zeichen (Auto-Summary). `_generate_summary()` läuft unconditionally bei jedem `write()` neu für das ganze Topic (nicht nur die geänderte Datei) — bei größeren Topics ein unnötiger, aber bei der aktuellen Größe irrelevanter Mehraufwand. `_sanitize_segment()` lehnt `topic`/`file`-Werte mit `/`, `..` oder leerem String ab (`read()`/`write()`/`get_links()`) — vorher gab es hier keinerlei Prüfung.

Frontmatter kennt genau drei Felder: `topic`, `updated`, `tags`. Links leben nicht im Frontmatter, sondern als `[[topic/file]]`/`[[topic/file|Anzeigetext]]` im Fließtext, extrahiert von `_extract_links()` und in einer eigenen Tabelle `knowledge_links(from_path, to_path)` gespeichert — Backlinks sind rein berechnet, nie hand-gepflegt.

---

## tools.py (~1320 Zeilen) — LLM-Tool-Definitionen + Dispatch

`DEFINITIONS` (Anthropic Tool-Use-Schema) + `execute(tool_name, tool_input, emit=None)` (Try/Except-Wrapper, gibt bei Fehler `f"Fehler bei {tool_name}: {e}"` zurück). Vollständige, gruppierte Tool-Liste → `TOOLS.md`.

`data_query`/`data_write`/`data_update`/`data_delete` (generische Tools über `local_data.query/write/update/delete`) seit 2026-07-27 auch für `database="rechnungen"`/`"ausgaben"` nutzbar — Tool-Beschreibungen weisen explizit an, `projekt_id` auf Rechnungen nie aus `kunde` zu raten, sondern per `data_query` + Rückfrage an Simon + `data_update` zu klären (siehe `finanzen_import.py`).

`emit`-Parameter (seit 2026-07-26, optional, Default `None`): `pipeline.py`s `_run_llm()` reicht hier `self._emit` durch — für Tools, die dem Client eine eigene Server-Push-Nachricht schicken müssen statt (nur) einen Text-Result für die LLM-Loop zurückzugeben. Aktuell einziger Nutzer: `generate_document` — das erzeugte Dokument geht als `document_ready`-WS-Nachricht raus (siehe `services/document_export.py`), nicht im `tool_result`-Text, der würde sonst als riesiger Base64-Blob dauerhaft im Gesprächsverlauf hängen bleiben (auch nach `compress_tool_history()`, die nur Text >400 Zeichen kürzt, nicht auf Base64-Inhalte prüft).

---

## tracking.py (~185 Zeilen) — Strukturierte Ziele/Logs

SQLite (`config.TRACKING_DB`). Bewusst getrennt von `knowledge.py` (Prose) — hier nur Zahlenwerte: `set_goal()`, `get_goal()`/`get_goals()`, `add_log()`, `get_logs()`, `get_progress()` (Ziel + letzter Log-Wert + Trend). Topic ist reiner Datenwert, kein Schema-Change für neue Themen nötig (z.B. Sport lief schon vor der Finanzen-Übersicht unten produktiv, ganz ohne Code-Änderung).

**Konvention `topic="finanzen", key="gewinn"`** (seit 2026-07-26, **seit 2026-07-27 nicht mehr von `finanzen.py` gelesen** — siehe unten) — ursprünglich realisierte Gewinne als manueller Log, abgelöst durch echten Rechnungen/Ausgaben-Import (`local_data.rechnungen`/`ausgaben`). Die Einträge/das Mechanismus selbst bleiben unverändert nutzbar für andere Topics (Sport etc.) und Ad-hoc-Zwecke. `"finanzen"` ist weiterhin aus dem generischen `tracking_topics`-Ergebnis ausgeschlossen (wie `"coding_engine"` und `"chat"` — `pipeline.py`s `cost_usd`-Logs, dieselbe Quelle wie `store.chatCostToday`).

- `delete_log(entry_id)` (seit 2026-07-27) — generisches Löschen eines einzelnen Log-Eintrags per id, für jedes Topic (nicht Finanzen-spezifisch). Als LLM-Tool `delete_log_entry` in `tools.py` exponiert.
- `get_logs(topic, key=None, limit=30)` — vollständige Log-Liste mit ids (im Gegensatz zu `get_progress()`, das nur Ziele + letzten Wert zeigt). Als LLM-Tool `list_log_entries` exponiert (seit 2026-07-27) — Vorbedingung für `delete_log_entry`, um gezielt eine id zu finden statt zu raten.
- `get_monthly_series(topic, key, months)` (seit 2026-07-27) — generische Zeitreihen-Aggregation: Summe von `value` pro Kalendermonat (`YYYY-MM`) für eine explizite Liste von Monaten. Bewusst domänen-neutral (kein Finanzen-Bezug), aktuell aber ungenutzt von `finanzen.py` (das liest seit dem Rechnungen/Ausgaben-Import direkt aus `local_data`, nicht mehr aus `tracking`) — weiterhin für andere Topics verfügbar.
- **Einmalige Bereinigung** (`_get_db()`, seit 2026-07-27): 3 identifizierte, pauschale Alt-Gewinneinträge (Datum = Anlage-Tag statt echtes Zahlungsdatum, für knowHere Theme/Ticketsystem/Michelle Webseite) wurden durch feiner aufgeteilte, korrekt datierte Einträge ersetzt — die alten blieben liegen und hätten doppelt gezählt. Löschung per **exaktem id-Match** (drei hartcodierte, aus den echten Live-Daten bestätigte UUIDs), nicht per Datums-/Wert-Kriterium, damit garantiert nie ein unabhängiger künftiger Eintrag mit ähnlichem Datum/Wert betroffen ist. Idempotent, loggt vor jedem Löschen was entfernt wird.

---

## finanzen.py (~170 Zeilen) — Finanzen-Domänenlogik

Eigenes Modul statt inline in `server.py`s Dispatch — kombiniert `local_data.projekte` (geschätzter Wert, erwartetes Abschlussdatum) mit `local_data.rechnungen`/`ausgaben` (aus SevDesk importiert, siehe `finanzen_import.py`). Bewusst getrennt von `tracking.py` (das bleibt domänen-neutral, siehe oben).

**Seit 2026-07-27:** liest nicht mehr aus `tracking.py` (`import tracking` wurde entfernt) — "tatsächlicher Gewinn" kommt jetzt aus echten, bezahlten Rechnungen minus echten, bezahlten Ausgaben statt aus den manuell gepflegten `finanzen`/`gewinn`-Logs (die ohnehin nur eine Abschrift derselben SevDesk-Rechnungen waren, sonst hätte echter Import zu Doppelzählung geführt).

- `_paid_rechnungen()`/`_paid_ausgaben()` — Filter auf `bezahlt_am is not None` (unbezahlte Posten zählen nicht mit).
- `compute_overview()` — geschätzter Gewinn (Summe `geschaetzter_wert` über nicht abgeschlossene Projekte) + tatsächlicher Gewinn (Summe bezahlter `rechnungen.betrag_netto` minus Summe bezahlter `ausgaben.betrag`) + `gesamtpotenzial` (Summe beider) + `tatsaechlich_verlauf` (Liste aller bezahlten Rechnungen/Ausgaben, Ausgaben mit negativem `value`, **absteigend sortiert nach Datum**). Backend für die `finanzen_overview`-Resource.
- `compute_trend(months=12, today=None)` — monatlicher Gewinn-Trend, Fenster **symmetrisch um den aktuellen Monat** (12 → 6 zurück + aktueller Monat + 5 voraus; 24 → 12+12). Vergangene/aktuelle Monate: reale Summe via `_monthly_net()` (Rechnungen positiv, Ausgaben negativ, gruppiert nach `bezahlt_am`-Monat). Zukünftige Monate mit bekanntem `projekte.erwartetes_abschlussdatum` (nicht abgeschlossene Projekte): deren `geschaetzter_wert` als `pipeline`. Übrige Zukunftsmonate: `projected` = Gesamtsumme bisheriger Gewinne ÷ Anzahl Monate seit ältestem bezahlten Beleg (einfacher fortlaufender Schnitt). Zusätzlich laufende `cumulative`-Summe über das gesamte Fenster. `today`-Parameter nur für Tests (sonst `date.today()`). Backend für die `finanzen_trend`-Resource (`months`-Query-Param, nur 12/24 gültig, sonst Fallback 12).

---

## finanzen_import.py (~175 Zeilen, neu seit 2026-07-27) — SevDesk-CSV-Import

Kein SevDesk-API-Zugang (kostet 10€/Monat extra) — Simon exportiert stattdessen manuell als CSV. Zwei eigenwillige Exportformate:
- `invoices.csv` — eine Zeile pro Rechnung, Header gequotet, Datenzeilen nicht (`csv.reader` kommt mit beidem klar).
- `voucher.csv` — **zwei Zeilen pro Beleg**: Kopfzeile (`Belegnummer`/`Status`/`Lieferant`/`Datum`/`Betrag`, `Position`-Feld leer) gefolgt von einer oder mehreren Positionszeilen (gleiche `Belegnummer`, `Position="1"/"2"/..."`). Mehrere Positionen werden zu einem flachen Datensatz zusammengefasst (nur die erste Kategorie/Beschreibung übernommen).

Beide deutsch (Semikolon-getrennt, UTF-8-BOM, `DD.MM.YYYY`, Komma-Dezimaltrennzeichen) — `_parse_date()`/`_parse_amount()` übernehmen die Konvertierung.

- `parse_invoices_csv(text)`/`parse_vouchers_csv(text)` — reine Parser, geben Listen normalisierter Dicts zurück (keine DB-Schreibzugriffe).
- `import_invoices(csv_text)`/`import_vouchers(csv_text)` — Parser + Upsert (`local_data.upsert_rechnung`/`upsert_ausgabe`) in einem, geben `{created, updated, skipped_locked, total, unmatched}` zurück (`unmatched` nur bei Rechnungen: alle ohne `projekt_id`, neue UND bereits bestehende; `skipped_locked` zählt Zeilen, die wegen `gesperrt=1` unangetastet blieben). **Idempotent** — wiederholter Import derselben/aktualisierten Datei erzeugt nie Duplikate (Upsert per `rechnungsnummer`/`belegnummer`), und eine bereits gesetzte `projekt_id` wird nie überschrieben (siehe `local_data.upsert_rechnung()`).
- `detect_and_import(csv_text)` (für den Chat-Upload-Pfad, `pipeline.py`) — erkennt am Header (`"Rechnungs-Nr."` vs. `"Belegnummer"`), welcher der beiden obigen Importe läuft, gibt zusätzlich `kind` zurück. Wirft `ValueError` bei unbekanntem Format.

**Projekt-Zuordnung bewusst nicht automatisch geraten:** an echten Daten bestätigt, dass ein Kunde (Empfänger-Adresse) mehrere, unterschiedlich benannte Projekte haben kann — keine zuverlässige Heuristik. Import lässt `projekt_id` leer, Zuordnung passiert über die Rechnungen-Seite (jarvis-web) oder `data_query`/`data_update` (`tools.py`) im Gespräch mit JARVIS.

---

## mcp_server.py (~265 Zeilen) — MCP-Server für Claude Code

`FastMCP`-basiert, `stdio` oder `sse` (Tailscale, Port 8766). Scope über `JARVIS_MCP_SCOPE` (`personal`/`work`), `_WORK_TOPICS = {"programmierung", "digital35"}`. Sechs Tools, vollständig in `TOOLS.md`.

**Kleine Inkonsistenz:** `jarvis_write_knowledge`/`jarvis_read_knowledge` prüfen `_check_scope()`, `jarvis_search_knowledge` filtert Ergebnisse nachträglich; `jarvis_log_work` ruft `_check_scope()` **nicht** auf — im `work`-Scope könnte ein explizit übergebenes, außerhalb liegendes `topic` trotzdem geschrieben werden (nur der Default-Wert ist sicher).

---

## services/ — Übersicht

| Datei | Zweck | Externe Abhängigkeiten | Caching/Config |
|---|---|---|---|
| `calendar.py` | Google Calendar read/write | Google Calendar API via `google_auth.py` | 15-Min-Dateicache `calendar_cache.json`, invalidiert bei write/delete |
| `email.py` | IMAP/SMTP read/send | `imaplib`/`smtplib` (GMX/IONOS) | VIP/Blacklist aus `brain.settings.contacts`, `config.EMAIL_SEND_ENABLED` |
| `alarm.py` | Satellite-Alarm-Registry + Sleep-Log | keine (WS-Events an Clients) | `alarm_registry.json`, SQLite `sleep.db` |
| `apple_music.py` | Lokale Apple-Music-Steuerung | macOS "Music" App via AppleScript | nur `sys.platform=="darwin"` |
| `btc.py` | Bitcoin-Preis | CoinGecko | 15-Min-Cache `btc_cache.json` |
| `client_music.py` | Musik-Routing an Satellite (mpv+YouTube) | keine | In-Memory Room-Handoff-State |
| `coding_engine.py` | Delegierte Coding-Tasks | Claude Agent SDK, GitHub REST + `git` CLI | Budgets aus `config.CODING_*`, Worktrees unter `~/.jarvis/coding_worktrees` |
| `document_export.py` | PDF/Word aus jeder Seite generieren — Projekt/Todo/Kontakt-Wurzelseite oder einzelne Unterseite, via `local_data.get_seite_view()` (seit 2026-07-26, Markdown-Parser deckt #-Überschriften/Absätze/Bullets/hr/Zitate/GFM-Tabellen/**Bold**/*Kursiv* ab) | `python-docx`, `reportlab` | keine — rein synchron, kein State |
| `google_auth.py` | Gemeinsame Google-OAuth | `google-auth`/`google-auth-oauthlib` | Token `google_token.json` |
| `local_exec.py` | "Führ das auf dem Client aus"-Primitiv | keine (WS-Routing) | 60s Timeout, aktuell nur Action `gh_issue_list` |
| `notification_dispatcher.py` | Push, unabhängig von Pipelines | keine | SQLite `notifications.db`, Rate-Limit 3/h |
| `proactive.py` | Kalender-/Email-/Todo-/Followup-Reminder | `calendar.py`, `email.py`, `local_data`, `tracking`, `brain` | `proactive_state.json`, Intervalle aus `brain.config.proaktiv.*` |
| `reminders.py` | Apple Reminders Einkaufsliste | macOS "Reminders" via AppleScript | nur macOS |
| `search.py` | Websuche | `ddgs` (DuckDuckGo) | keine |
| `sleep_coach.py` | Schlaferinnerung-Eskalation | `alarm_service.list_alarms()` | Fenster 20:00–02:00, `brain.schlaf.*` |
| `tickets.py` | GitHub-Issues → Todos | `local_exec.dispatch("gh_issue_list", ...)` | Repos aus `brain.config.ticket_repos` |
| `timer.py` | Countdown-Timer | keine (`threading.Timer`) | in-memory, überlebt Neustart nicht |
| `weather.py` | Aktuelles Wetter | Nominatim + Open-Meteo | Default-Stadt `config.WEATHER_CITY` |

---

## Bekannte Ecken

Ehrlich dokumentierte Unsauberkeiten — nichts davon ist aktuell ein aktives Problem, aber alle sind gute Kandidaten für spätere Aufräumarbeit:

1. **`brain.py:400`** — `from config import SYSTEM_PROMPT_BASE` schlägt fehl (`config.py` exportiert das nicht mehr), abgefangen von `except ImportError: pass`. Effekt: Der Seed-Schritt für `brain.modules` bei einer Neuinstallation ist ein stiller No-Op.
2. ~~`llm.py` vs. `config.py` Modell-Divergenz~~ — **behoben 2026-07-25**: `llm.py`s `MODEL` wurde von `claude-sonnet-4-6` auf `claude-sonnet-5` angehoben, jetzt identisch mit `config.CODING_ENGINE_MODEL`s Default.
3. **`session_memory.load_for_prompt()`** — explizit als Legacy-Stub markiert (eigene Docstring), Body gibt nur `""` zurück. Nicht aufgerufen.
4. **Tote Imports** — `context.py` importiert `config`/`session_memory` ungenutzt, `session_memory.py` importiert `config` ungenutzt. Harmlos, nur Lesbarkeits-Rauschen.
5. **`brain.apply_aging()`** — läuft nur beim Serverstart (`brain.sync()`), nicht periodisch. Gewichte können zwischen zwei Neustarts veralten.
6. **`mcp_server.py: jarvis_log_work`** — einziges der sechs MCP-Tools ohne `_check_scope()`-Aufruf.
7. **Env-Var-Ladeorte gestreut** — die meisten laufen über `config.py`, aber `JARVIS_HOST`/`JARVIS_PORT` (`server.py`) und `NOTION_API_KEY` (`local_data.py`) werden direkt gelesen. `PICOVOICE_ACCESS_KEY` existiert in `.env`, aber kein Python-Konsument im Server-Repo gefunden.
