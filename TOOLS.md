# J.A.R.V.I.S. — Tool-Referenz

Zwei getrennte Tool-Welten:

1. **LLM-Tools** (`tools.py`) — was Claude während eines Gesprächs (Voice/Chat, über `pipeline.py`) aufrufen kann.
2. **MCP-Tools** (`mcp_server.py`) — was Claude Code in einer Coding-Session (nicht die Voice/Chat-Pipeline) aufrufen kann.

Beide sind vom Drei-Layer-Protokoll aus `ARCHITECTURE.md` zu unterscheiden: Layer-1-`DATA_REQUEST`-Resources (`server.py::_handle_data_request()`) sind reine, LLM-freie Datenabfragen fürs Dashboard/jarvis-web — mehrere LLM-Tools unten lesen dieselben zugrundeliegenden Daten (Todos, Kalender, BTC, Wetter, Tracking-Progress), existieren aber zusätzlich als eigener Tool-Pfad für den Gesprächskontext.

---

## Teil 1 — LLM-Tools (`tools.py`)

### Coding Engine

Ausführlich in `ARCHITECTURE.md` ("Coding Engine"). Backing-Modul: `services/coding_engine.py`.

| Tool | Parameter | Verhalten |
|---|---|---|
| `delegate_coding_task` | `instruction, project?, high_power?, auto_mode?` | Startet die Claude Agent SDK asynchron auf einem isolierten Git-Worktree/Branch (nie `main`). `high_power=True` → `claude-opus-4-8` statt Default `claude-sonnet-5`. Ergebnis kommt per Notification, nicht in der Tool-Antwort. |
| `check_coding_task_status` | — | Status der einen aktuell getrackten Task (nur eine gleichzeitig). |
| `sync_project` | `project?` | Synchrones `git fetch` + `--ff-only pull`. Kein LLM-Sub-Call, schnell, kein Freigabe-Dialog. |
| `commit_and_push` | `project?, message?` | Committet/pusht **direkt auf dem Live-Checkout** (kein Worktree, kein PR) — landet direkt auf dem ausgecheckten Branch. Immer Freigabe mit vollem Diff erforderlich. |
| `run_command` | `command, cwd?` | Whitelisted Read-Only-Befehle (ls/cat/pwd/grep/git status/systemctl status/… — siehe `_SAFE_READONLY_BINARIES`) laufen synchron, Ergebnis kommt direkt zurück. Alles andere: Freigabe-Pflicht, asynchron, Ergebnis per Notification. `sudo`-Befehle lösen ein interaktives Passwort-Popup aus (nie geloggt/gespeichert). |
| `create_project` | `name, description?, private?` | Neues GitHub-Repo + lokaler Checkout unter `~/apps`. Immer Freigabe-Pflicht. |
| `sync_tickets` | — | Holt GitHub-Issues über Simons eigenen `gh`-CLI-Login auf dem Mac (`local_exec.dispatch`, nie Server-Token), upserted als Todos (`source="github"`). GitHub `closed` erzwingt lokal `Erledigt`, `open` überschreibt nie einen lokal gesetzten Status. |

### Todos / Projekte / Kontakte / Unterseiten (`local_data.py`)

| Tool | Parameter | Verhalten |
|---|---|---|
| `data_query` | `database("todos"\|"projekte"), search?, status?, limit?` | Lesen/Filtern. |
| `data_write` | `database, properties` | Neuer Eintrag. |
| `data_update` | `id, database, properties` | Bestehenden Eintrag ändern. |
| `data_delete` | `id, database` | Löschen. |
| `read_seite` | `seite_id` | Lazy-Load des vollen Inhalts einer Unterseite (Listen liefern nur Titel+ID, aus Token-Kostengründen). |
| `create_seite` | `titel, inhalt?, parent_typ?, parent_id?, eltern_seite_id?` | Neue Unterseite — entweder an einem Todo/Projekt/Kontakt oder verschachtelt unter einer bestehenden Seite. |
| `write_seite` | `seite_id, titel?, inhalt?` | Bestehende Seite überschreiben (`inhalt` ersetzt, hängt nicht an). |

### Brain (`brain.py`)

| Tool | Parameter | Verhalten |
|---|---|---|
| `brain_read` | `section, key?` | Liest `profile\|behavior\|memory\|followups\|events\|modules\|config`. |
| `brain_write` | `section, key, value` | Schreibt per Dot-Notation, z.B. `routines.{name}.last_done`, `modes.{modus}.cards`, `contacts.email_vip`, `proaktiv.kalender_minuten`, `schlaf.stunden`. |

### Wissensdatenbank (`knowledge.py`)

| Tool | Parameter | Verhalten |
|---|---|---|
| `read_knowledge` | `topic, file` | **Proaktiv aufrufen** wenn das Gesprächsthema zu einem bekannten Topic passt, ohne dass Simon fragt. |
| `write_knowledge` | `topic, file, content, tags?` | **Sofort aufrufen** — ohne explizite Aufforderung — bei Plänen, Entscheidungen, Erkenntnissen, Präferenzen, Routinen, Meinungen, Zielen als Prosa, Erfahrungen. Nur Prosa/Kontext, keine reinen Zahlenwerte (→ `set_goal`/`log_entry`). Verwandte Inhalte aktiv mit `[[topic/file]]` bzw. `[[topic/file\|Anzeigetext]]` verlinken — das ist die Wiki-Verlinkungskonvention (siehe `ARCHITECTURE.md`), Backlinks werden automatisch berechnet. Vorher `read_knowledge` prüfen, dann ergänzen statt überschreiben. |
| `search_knowledge` | `query` | Vor `write_knowledge` aufrufen um zu prüfen ob die Datei schon existiert. Matcht nur Pfad+Tags+Auto-Summary, nicht den vollen Inhalt. |

### Tracking / Ziele (`tracking.py`)

| Tool | Parameter | Verhalten |
|---|---|---|
| `set_goal` | `topic, key, value, unit?, label?` | Strukturiertes Ziel — nur Zahlenwerte mit Einheit, keine Prosa. |
| `log_entry` | `topic, key, value?, text_value?, unit?, notes?, date?` | **Sofort aufrufen**, parallel zur Antwort, bei jedem messbaren Ereignis (Training, Gewicht, Kalorien, Schlaf, Ausgaben) — nie erst um Erlaubnis fragen. |
| `get_progress` | `topic` | Ziel + letzter Log-Wert + Trend. |

### Kalender (`services/calendar.py`)

`calendar_query {days_ahead?}`, `calendar_write {title, start_iso, end_iso, description?}`, `calendar_delete {event_id}` — Google Calendar API, 15-Min-Cache, invalidiert bei Write/Delete.

### Email (`services/email.py`)

`email_query {filter?, limit?}` (VIP/Blacklist aus `brain.settings.contacts`), `email_send {to, subject, body}` (Tool-Beschreibung verlangt explizite Bestätigung durch Simon vor dem Senden, zusätzlich serverseitig gated über `config.EMAIL_SEND_ENABLED`), `sync_email_vip {}`.

### Alarme / Timer (`services/alarm.py`, `services/timer.py`)

`alarm_start {hour, minute, label, target?, snooze_minutes?, max_snooze?, song?}`, `alarm_list {}`, `alarm_snooze {minutes?}` (Default-Reaktion auf "Wecker aus", außer explizit "endgültig"), `alarm_dismiss {alarm_id?}` (nur bei explizitem "wirklich aus"). `timer_set {label, minutes?, seconds?}`, `timer_list {}`, `timer_cancel {label?, id?}` — reiner In-Process-Timer, überlebt keinen Neustart.

### Musik

Lokale Apple Music (`services/apple_music.py`, macOS-only): `music_current`, `music_play_pause`, `music_stop`, `music_next`, `music_previous`, `music_volume {level}`, `music_search {query}`, `music_play_track {query, index}`.
Satellite-Musik (`services/client_music.py`, mpv+YouTube, andersartig als Apple Music): `client_music_play {song, target?, volume?}`, `client_music_stop {target?}`.

### Suche / Wetter / BTC

`web_search {query, max_results?}` (DuckDuckGo via `ddgs`, kein API-Key), `get_weather {city?}` (Nominatim + Open-Meteo, beide kostenlos), `btc_price {}` (CoinGecko, 15-Min-Cache).

### Einkaufsliste (`services/reminders.py`)

`shopping_add {items[], list_name?}`, `shopping_get {list_name?}`, `shopping_remove {item, list_name?}` — Apple Reminders via AppleScript, macOS-only, Sync via iCloud aufs iPhone.

---

## Teil 2 — MCP-Tools (`mcp_server.py`, für Claude-Code-Sessions)

Scope-gesteuert über `JARVIS_MCP_SCOPE` (`personal` Default, oder `work`). `_WORK_TOPICS = {"programmierung", "digital35"}`.

| Tool | Parameter | Verhalten | Scope-Check |
|---|---|---|---|
| `jarvis_get_coding_context` | — | Proaktiv am Sessionstart aufrufen. Liefert: relevante Tech-Stack-Zeilen aus `knowledge/simon/_core.md` (nur personal), `knowledge.read_summary("programmierung")` + `("digital35")` (beide Scopes), offene `brain.followups` (nur personal). | — |
| `jarvis_search_knowledge` | `query` | Wrapper um `knowledge.search()`. | Ergebnisse nachträglich auf `_WORK_TOPICS` gefiltert (work) |
| `jarvis_read_knowledge` | `topic, file` | Wrapper um `knowledge.read()`. | ✅ `_check_scope()` |
| `jarvis_write_knowledge` | `topic, file, content, heading?` | Wrapper um `knowledge.write()` (überschreibt) bzw. `knowledge.append_section()` (wenn `heading` gesetzt). | ✅ `_check_scope()` |
| `jarvis_move_knowledge` | `from_topic, from_file, to_topic, to_file, content?` | Verschiebt eine Datei wirklich (kein Verweis-Stub) — für Themen-Konsolidierung. `content` optional zum Anpassen von `[[...]]`-Links vor dem Schreiben. Schreibt fremde Dateien nicht automatisch um, Antwort listet Backlinks die manuell nachgezogen werden müssen. | ✅ `_check_scope()` auf Quelle und Ziel |
| `jarvis_delete_knowledge` | `topic, file` | Löscht eine Datei wirklich — für Duplikate/veraltete Seiten. Antwort listet verbleibende Backlinks. | ✅ `_check_scope()` |
| `jarvis_log_work` | `summary, topic="digital35"` | Hängt einen `## Work-Log {datum}`-Abschnitt an `knowledge/{topic}/worklog.md`. | ⚠️ **kein** `_check_scope()`-Aufruf — siehe `CODE_REFERENCE.md` "Bekannte Ecken" |
| `jarvis_read_project_file` | `path` | Liest eine Datei direkt aus dem `j.a.r.v.i.s.`-Repo (Path-Traversal-geprüft, 8000-Zeichen-Cap). | **Nur personal Scope** — im `work`-Scope sofortige Ablehnung |

Deployment: lokal `stdio` (persönlicher Mac, `scope=personal`) oder remote über Tailscale `sse` auf Port 8766 (`jarvis-mcp.service`), für den Arbeits-Laptop als eigene MCP-Server-Registrierung mit `scope=work` — dieselbe Codebasis, unterschiedliche Env-Variable, nicht zwei getrennte Implementierungen.
