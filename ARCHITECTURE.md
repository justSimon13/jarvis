# J.A.R.V.I.S. — Architektur

Dieses Dokument ist die verbindliche Referenz für alle Implementierungsentscheidungen.
Letzte Überarbeitung: 2026-07-24

Für Code-Ebene-Details (Datei-für-Datei, Funktionen, Zeilennummern) siehe `CODE_REFERENCE.md`.
Für die vollständige LLM-Tool-Liste siehe `TOOLS.md`. Für Produktumfang/Grenzen siehe `PRODUCT.md`.

---

## Kernprinzip

**JARVIS ist ein System, nicht mehrere Assistenten.**

- Eine History, ein Brain, ein Kontext — für alle Clients
- Clients sind dumme I/O-Geräte: Audio rein, Audio raus, Events empfangen (Ausnahme: Clients mit `local_exec`-Capability führen auf Anfrage lokale Befehle aus — siehe Coding Engine unten)
- Alle Entscheidungen (Kontext, Wissen, Verhalten) leben auf dem Server
- Ausnahmen nur wo physisch nötig: Alarm-Klingel lokal, Audio-I/O lokal

---

## Physische Topologie

```
HP EliteDesk (24/7, Linux) — jarvis.tail47e1d9.ts.net (Tailscale)
├── server.py             ← WebSocket-Server :8765 (TLS via Tailscale Serve: wss://…:8766)
├── mcp_server.py         ← MCP-Server für Claude-Code-Sessions, :8766 (SSE) oder stdio
├── jarvis-satellite/     ← Wohnzimmer-Client (läuft auch auf dem HP EliteDesk)
├── jarvis-dashboard/     ← Vite Dev Server :5173 (systemd service), iPad-PWA
├── ~/.jarvis/brain.db, sessions.db, alarm_registry.json, local_data.db,
│   knowledge/ + knowledge_index.db, tracking.db, notifications.db,
│   sleep.db, calendar_cache.json, btc_cache.json, coding_worktrees/
└── auto_update.sh (systemd timer, alle 5 Min) — pullt origin/main, restartet
    jarvis.service nur wenn JARVIS erkennbar idle ist

iPad (Browser/PWA)
└── jarvis-dashboard/     ← Vue 3 PWA (Home Screen App), verbindet zu HP via Tailscale

Mac (Entwicklung + Desktop-Client)
├── jarvis-web/           ← Vue 3 (Tauri-Desktop-App), Chat/Wissen/Todos/Kalender/Tracking
│                            meldet `local_exec`-Capability → führt lokale Befehle für JARVIS aus
│                            (z.B. `gh issue list`, nie Server-seitig)
└── main.py                ← Standalone-Modus (CLI, kein Server) im j.a.r.v.i.s.-Repo

Schlafzimmer-Client (geplant)
└── jarvis-satellite/      ← Orange Pi / RPi, gleiches Script
```

Zugriff läuft über Tailscale Serve (`https://jarvis.tail47e1d9.ts.net`, siehe `CLAUDE.md` Deployment-Abschnitt) statt roher Ports — funktioniert auch von unterwegs. LAN-IP (`192.168.0.155`) bleibt zusätzlich nutzbar.

---

## Repos

| Repo | Beschreibung |
|------|-------------|
| `jarvis` (`j.a.r.v.i.s.`) | Server — läuft 24/7 auf HP EliteDesk. Dieses Repo. |
| `jarvis-web` | Desktop-first Vue 3 App (Tauri), Chat + Wissen + Todos/Projekte/Kontakte + Kalender + Tracking. Meldet `local_exec`-Capability für lokale Coding-Engine-Aktionen. |
| `jarvis-dashboard` | iPad PWA — schlankeres, touch-optimiertes Interface ohne Wissens-Editor. |
| `jarvis-satellite` | Headless Audio Client (Lenovo, Raspberry Pi) — Wake Word, VAD, TTS-Playback, alarmfähig auch ohne Serververbindung. |

---

## Pipeline

Eine `JarvisPipeline`-Instanz pro Client — History ist **pro Client-Kategorie geteilt** (`"voice"` vs. `"web"`, seit 2026-07-19, siehe `CLAUDE.md` Architektur-Entscheidungen), Kontext (`brain.db`, `knowledge/`) bleibt für alle geteilt.

Seit der messages/threads-Migration (2026-07-31, Teil 1, siehe ROADMAP.md) ist die `messages`-Tabelle in `sessions.db` die eigentliche Quelle: jede Nachricht wird sofort beim Anhängen persistiert, das Prompt-Fenster wird pro Turn frisch aus SQLite gelesen (`session_memory.build_history_window()`) — überlebt dadurch einen Server-Neustart, auch mitten in einem laufenden Turn. Die `api_histories`-Dicts unten existieren als In-Memory-Struktur weiterhin (füttern u.a. noch die alte `sessions`-Tabelle/Lernextraktion), dienen aber nur noch als temporärer Vergleichswert (`pipeline.py::_verify_reconstruction()`) — Entfernung folgt in einem eigenen, späteren Schritt.

Seit Teil 2 (Threads, 2026-07-31, manuelles Chat-Etikett) kann pro Web-Tab zusätzlich ein Thread aktiv sein (`pipeline._thread_id`, gesetzt über `SET_THREAD`/`client_hello`) — ist einer aktiv, umgeht `build_history_window()` die Tab-Cursor-Fensterbildung komplett und liest stattdessen alle Nachrichten mit passender `thread_id`. Zwei unabhängige, gleichrangige Fensterbildungs-Strategien, keine Hierarchie zwischen beiden. `_verify_reconstruction()` unterdrückt sich bei aktivem Thread selbst (das Vergleichsgerüst kennt Threads nicht).

```
Wohnzimmer   ──→ JarvisPipeline ──┐
Schlafzimmer ──→ JarvisPipeline ──┼──→ messages (SQLite, category='voice')   ← Quelle
                                   │     api_histories["voice"]  (in-memory, nur noch Vergleichswert)
                                   │     shared_context           (brain.db, knowledge/)
jarvis-web   ──→ JarvisPipeline ──┘──→ messages (SQLite, category='web', tab_id)   ← Quelle
                                         api_histories["web"][tab_id]  (in-memory, nur noch Vergleichswert)
```

**Gleichzeitiger Input:** Ein globaler `llm_semaphore = threading.Semaphore(1)` — pro Prozess kann nur ein LLM-Call gleichzeitig laufen (FIFO über alle Clients). Der Anthropic-Client hat ein explizites Timeout (120s, `llm.py`), damit ein hängender Call den Semaphore nicht unbegrenzt blockiert (siehe ROADMAP.md, Vorfall 2026-07-22).

Pipeline pro Client bleibt wegen:
- PCM-Routing ist Client-spezifisch (Audio geht an den Client der gesprochen hat)
- TTS-State ist Client-spezifisch

---

## Voice Pipeline

```
Mikrofon (Client)
    │ lokal auf Client
Wake Word Detection   (openWakeWord, Threshold 0.35)
    │ lokal auf Client
VAD Recording         (RMS-basiert, kein ML — Silero entfernt)
    │ WAV via WebSocket → Server
STT                   (ElevenLabs Scribe)
    │
LLM                   (Claude, streaming, Tool-Loop)
    │ ├── Tool Call → tools.execute() → weiter
    │ PCM via WebSocket → Client (24kHz mono int16)
TTS                   (ElevenLabs, entfällt bei tts=false)
    │ auf Client
Lautsprecher
```

TTS läuft parallel zum LLM-Streaming (sobald ein Satz erkannt wird → sofort an ElevenLabs). Der `greet()`-Ack beim Connect ("Bereit.") umgeht bewusst das LLM komplett (kein Prompt-Cache-Write für ein Wort).

---

## Brain DB — Sections

`~/.jarvis/brain.db`, alle Sections als JSON-Blob (`brain(section TEXT PRIMARY KEY, data TEXT)`), Dot-Notation-Zugriff via `brain.read()`/`brain.write()`.

| Section     | Inhalt                                                                  |
|-------------|-------------------------------------------------------------------------|
| `profile`   | **Leer seit 2026-07-19** — Simons Profil lebt jetzt in `knowledge/simon/_core.md` (siehe unten). Section bleibt aus Kompatibilitätsgründen erhalten, wird nicht mehr befüllt. |
| `behavior`  | Wie JARVIS sich verhalten soll (Tonalität, Priorisierung, Stil)        |
| `memory`    | Micro-Facts über Simon, **strukturiertes Schema**: `{id, text, ts_iso, category, weight, source}`. `remember()` dedupliziert per Substring-Match, `apply_aging()` senkt `weight` mit Alter (≤30 Tage: 1.0, ≤90: 0.5, sonst 0.1 — außer `source="nutzer-explicit"`), Pruning bei >100 Einträgen (niedrigstes `weight` fliegt raus). `apply_aging()` läuft aktuell nur beim Serverstart, nicht periodisch. |
| `followups` | Offene Punkte für nächstes Gespräch (mit optionalem `due`-Datum)      |
| `events`    | Routinen: Check-In, Check-Out, Sport, sonstige Zeitfenster             |
| `modules`   | Prompt-Inhalte — Basis-Identität + pro Modus (assistent/coach/fokus)  |
| `config`    | Technisches: Proaktiv-Einstellungen, Ticket-Repos, Wetter-City, VIP-Kontakte etc. |

> **Migration von alten Sections:**
> `settings` → aufgeteilt in `behavior` + `events` + `config`
> `context_config` → `config`
> `profile` → `knowledge/simon/_core.md` (2026-07-19, Todos/Projekte parallel dazu nach `local_data.db`)

---

### brain.modules — Format

```json
{
  "base": {
    "identity": "Du bist J.A.R.V.I.S., persönlicher Assistent von Simon Fischer.",
    "rules": [
      "Antworte immer auf Deutsch.",
      "Sei direkt und präzise, kein Fülltext."
    ]
  },
  "modes": {
    "assistent": {
      "description": "Standard-Modus: Alltag, Produktivität, Planung",
      "prompt": "Fokus auf Todos, Termine und Projekte. Aktiv auf offene Punkte hinweisen."
    },
    "coach": {
      "description": "Performance-Coach: Ziele, Habits, Wochenbilanz",
      "prompt": "Direkter Coach-Stil. Hinterfragen. Klare Fragen stellen. Keine Ausreden."
    },
    "fokus": {
      "description": "Minimal-Modus: keine Ablenkung",
      "prompt": "Kurze, präzise Antworten. Kein Smalltalk. Nur das Wesentliche."
    }
  }
}
```

`brain.build_modules_prompt()`/`build_prompt_section()` (`brain.py`) setzen daraus zusammen mit Followups, Behavior-Regeln, aktiven Routinen, VIP-Kontakten und der gewichteten Memory-Liste den Großteil des statischen System-Prompts.

---

## Wissensdatenbank (`knowledge.py`)

Simons persönliches Wiki für Prose/Pläne/Kontext/Erkenntnisse — bewusst getrennt von `brain.memory` (Micro-Facts über Simon) und `tracking.db` (strukturierte Zahlenwerte).

**Ablage:** `~/.jarvis/knowledge/<topic>/<file>.md`, YAML-Frontmatter (`topic`, `updated`, `tags`). Ein SQLite-Index (`knowledge_index.db`) hält pro Datei Pfad/Topic/Tags/Auto-Summary für schnelle Suche ohne alle Dateien zu lesen.

**Verlinkung (Wikipedia-Stil, seit 2026-07-24):** Inline `[[topic/file]]` bzw. `[[topic/file|Anzeigetext]]` im Fließtext. Forward-Links werden beim Schreiben aus dem Body extrahiert und in einer eigenen Tabelle `knowledge_links(from_path, to_path)` gespeichert; **Backlinks werden nie von Hand gepflegt, sondern immer aus derselben Tabelle rückwärts berechnet** (`get_links(topic, file) -> {outgoing, backlinks}`) — analog zu MediaWikis "Linked from". `jarvis_write_knowledge`/`write_knowledge` fordern JARVIS aktiv auf, verwandte Inhalte beim Schreiben zu verlinken, statt Verlinkung zu einer stillen, ungenutzten Fähigkeit zu machen.

**Themen-Konsolidierung — echtes Verschieben/Löschen (seit 2026-07-24):** `move(from_topic, from_file, to_topic, to_file)` und `delete(topic, file)` — kein Verweis-Stub-Workaround mehr. Grund: verstreute Themen (z.B. dasselbe Projekt in 3 verschiedenen Top-Level-Topics) sollen sich zu einem Topic konsolidieren lassen, ohne Karteileichen zu hinterlassen. Beide Funktionen schreiben referenzierende Dateien nicht automatisch um — sie geben die Backlinks zurück, die der Aufrufer gezielt nachziehen muss. Nur über MCP (`jarvis_move_knowledge`/`jarvis_delete_knowledge`) bzw. direkten Code-Zugriff verfügbar, bewusst **kein** LLM-Tool für den Chat/Voice-Pfad — Themen-Umsortierung ist eine kuratierte Aufräum-Aktion, kein Verhalten das JARVIS beiläufig im Gespräch auslösen soll.

**Auto-Summary:** Jeder `write()`-Call regeneriert unconditionally `_summary.md` des betroffenen Topics — eine 150-Zeichen-Kurzfassung pro Datei im Topic-Ordner, geladen von `context.py` für den System-Prompt und von `jarvis_get_coding_context` (MCP).

**Suche:** `search()`/`jarvis_search_knowledge` matchen nur gegen Pfad + Tags + Auto-Summary (nicht den vollen Dateiinhalt) — einfacher Substring-Score pro Suchwort, kein Embedding/RAG (siehe ROADMAP.md, Phase 6 "Langfristig").

**Schreib-Priorität** (aus `knowledge.py`-Modul-Docstring, verbindlich):
```
Prose, Pläne, Kontext, Erkenntnisse  → knowledge.write()
Strukturierte Ziele/Metriken         → tracking.py
JARVIS-Regeln, Config                → brain.db
```

---

## Config-Split: .env vs brain.config

**`.env` enthält Secrets + Hardware-/Deployment-Konstanten** (`config.py` lädt sie): API Keys (Anthropic, ElevenLabs, GitHub, Google-OAuth-Client), SMTP/IMAP-Credentials, `JARVIS_HOST`/`JARVIS_PORT`, Coding-Engine-Budgets (`CODING_TASK_BUDGET_USD`, `CODING_DAILY_BUDGET_USD`), `PROJECTS_ROOT`.

**`brain.config` enthält alles betriebliche, das JARVIS selbst sinnvoll lesen/ändern kann:** `weather_city`, Proaktiv-Service-Intervalle, `ticket_repos`, VIP-Kontakte.

Regel: Kann JARVIS es sinnvoll lesen oder ändern? → `brain.config`. Ist es ein Secret oder eine einmalige Hardware-Einstellung? → `.env`.

---

## System-Prompt — Zwei-Schichten-Modell

```
Static Prompt  (Anthropic Prompt Cache, TTL 1h — bewusst länger als das 5-Min-Default,
                siehe llm.py-Kommentar zur Kosten/Latenz-Abwägung)
├── brain.modules.base           ← Identität + Grundregeln          [immer]
├── brain.modules.modes[aktiv]   ← Modus-spezifischer Abschnitt     [immer]
├── knowledge/simon/_core.md     ← Wer Simon ist                    [immer]
├── knowledge/<topic>/_summary   ← passende Wissenstopics           [immer, NICHT modul-gated]
├── brain.behavior, brain.events, brain.followups, brain.memory     [immer, NICHT modul-gated]
├── local_data.py: Todos         ← nur wenn "todos" aktives Modul
└── local_data.py: Projekte      ← nur wenn "projects" aktives Modul

Dynamic Prompt  (kein Cache, immer frisch)
├── Aktuelle Uhrzeit + Wochentag + Tageszeit
├── Google Calendar (nächste Events)         ← nur wenn "calendar" aktives Modul
└── BTC-Preis                                ← nur wenn "btc" aktives Modul
```

**Modul-Gating (`context.detect_modules()`):** Ein Keyword-Classifier läuft **einmal pro Session** auf der ersten User-Nachricht (`_KEYWORD_MAP`: "todo"→`todos`, "termin"→`calendar`, "bitcoin"→`btc`, "projekt"→`projects`, u.a.), plus Modus-Default-Module (`_MODE_DEFAULT_MODULES`) und ein Check-in-Sonderfall der alle vier Module lädt. Kurze/unklare erste Nachrichten (<8 Zeichen) laden ebenfalls alles. **Nur `todos`/`calendar`/`btc`/`projects` sind tatsächlich gated** — Wissenstopics, Memory, Followups, Behavior und Routinen werden unabhängig davon immer geladen (kein Token-Sparen dort, aber auch kein Risiko sie zu verpassen).

**Aktiver Modus:** Kommt vom Client per `CLIENT_HELLO` (`mode`-Feld) oder per `SET_MODE`-Event. Bestimmt sowohl das Modul-Set als auch `brain.modules.modes[modus]` für den Static Prompt.

---

## WebSocket-Protokoll — Drei Layer

### Layer 1: DATA (kein LLM, keine Token)

Direkte Datenanfragen. Sofortige Antwort vom Server, `server.py::_handle_data_request()`.

```jsonc
// Request:
{"type": "data_request", "resource": "todos"}

// Response:
{"type": "data_response", "resource": "todos", "data": [...]}
```

Beispiel-Resources: `todos`, `tickets`, `calendar`, `alarms`, `followups`, `clients`, `btc`, `weather`, `knowledge_index`, `knowledge_file`, `seite`, `tracking_*`, `session_transcript`, `notification_history`. Vollständige Liste + alle 44 Message-Typen siehe `protocol.py` / `CODE_REFERENCE.md`.

Direkte, nicht-LLM Mutation von Todos/Projekten/Kontakten/Seiten läuft analog über `ENTITY_ACTION`/`ENTITY_ACTION_ACK` (`server.py::_do_entity_action()`) — genutzt vom Dashboard-UI (z.B. Todo abhaken), nicht vom Gespräch.

### Layer 2: ACTION (durch JARVIS)

User wählt Eingabe → Frontend baut Text aus Template → JARVIS verarbeitet mit vollem Kontext.

```jsonc
{"type": "text_input", "text": "Stell einen Wecker für 07:30 Uhr.", "tts": false}
// → normaler JARVIS-Response-Flow
```

Quick Actions im Dashboard liefern das Text-Template mit `{value}`.
JARVIS bleibt im Loop — kennt Kontext, prüft Konflikte, schreibt History.

### Layer 3: CONVERSATION (freier Chat)

```jsonc
{"type": "text_input", "text": "...", "tts": true}
// oder Audio-Bytes für Voice-Input
```

---

## Proaktives System — NotificationDispatcher, Proactive Daemon, Sleep Coach

JARVIS meldet sich, ohne dass ein Gespräch läuft — der zentrale Unterschied zu einem Chatbot.

**`NotificationDispatcher`** (`services/notification_dispatcher.py`, Server-Singleton, unabhängig von jeder Pipeline): `notify(text, channels, priority, expires_in_min)` schreibt in `~/.jarvis/notifications.db` und pusht sofort an alle verbundenen Dashboard-Clients (`notification_push`); nicht zugestellte Notifications werden bei Reconnect nachgeliefert. Rate-Limit: max. 3/Stunde global (verhindert Spam bei Daemon-Fehler).

**`ProactiveDaemon`** (`services/proactive.py`) und **`SleepCoach`** (`services/sleep_coach.py`) laufen als Background-Loops und kommunizieren **ausschließlich** über `dispatcher.notify()` — nie über `pipeline.process_text()`. Das ist eine bewusste, historisch begründete Entscheidung: ein direkter Pipeline-Aufruf hätte eine gefälschte Assistant-Nachricht in den Chat des gerade aktiven Clients injiziert, unabhängig davon wer gerade aktiv ist. Fired-State wird persistiert (`~/.jarvis/proactive_state.json`), überlebt also Neustarts.

Checks: Kalender-Reminder, VIP-Email, Todo-/Followup-Reminder, nutzerdefinierte Regeln (`_check_user_rules`), abendliche Schlafenszeit-Eskalation (SleepCoach, aktives Fenster 20:00–02:00).

---

## Coding Engine — JARVIS entwickelt Code (delegiert, nie selbst auf dem Server)

Simon sagt im Gespräch "JARVIS, bau X" → `delegate_coding_task` startet die Claude Agent SDK (`services/coding_engine.py`) in einem isolierten Git-Worktree/Branch (nie `main`) unter `config.PROJECTS_ROOT` (`~/apps/<projekt>`). Riskante Aktionen (Write/Edit außerhalb des Worktrees, `rm -rf`, `sudo`, Secrets-Dateien) brauchen explizite Freigabe über jarvis-web (`CODING_APPROVAL_REQUEST`/`_RESPONSE`, voller Diff/Befehl sichtbar) — außer bei explizit angefordertem `auto_mode` für eine einzelne Task. Merge nach `main` läuft **immer** über einen echten GitHub-PR, nie direkt. Nur eine Coding-Task gleichzeitig, projektübergreifend.

**Ticket-Sync** (`services/tickets.py`) holt GitHub-Issues nie über einen Server-Token, sondern über Simons eigenen, bereits autorisierten `gh`-CLI-Login auf dem Mac — geroutet über `services/local_exec.py`, ein generisches "führ das auf dem Client mit `local_exec`-Capability aus"-Primitiv. Quellcode/Diffs von Arbeits-Tickets verlassen den Mac nie in Richtung Server — nur Ticket-Metadaten.

Details/Tool-Liste → `TOOLS.md`.

---

## MCP Server — Kontext für Claude-Code-Sessions

`mcp_server.py` exposed JARVIS' Wissen als MCP-Server für Claude Code (nicht für die Voice/Chat-Pipeline). Zwei Scopes über `JARVIS_MCP_SCOPE`:

```
personal  →  alles: knowledge/, tracking/, brain.memory, Projektdateien
work      →  nur: knowledge/programmierung/, knowledge/digital35/ — kein Dateizugriff aufs Repo
```

Deployment: lokal `stdio` (persönlicher Mac) oder remote `sse` über Tailscale, Port 8766 (`jarvis-mcp.service`), für den Arbeits-Laptop unter eigenem Scope registriert. Sechs Tools, Details → `TOOLS.md`.

---

## Dashboard — Server-Driven UI

Der Server definiert was gezeigt wird. Das Frontend ist ein generischer Renderer für Cards/Quick-Actions — gilt für `jarvis-dashboard` (iPad) vollständig; `jarvis-web` nutzt dasselbe `layout_config` auf seiner Startseite, hat aber zusätzlich eigene, direkt per `ENTITY_ACTION`/`data_request` gespeiste CRUD-Views (Todos, Projekte, Kontakte, Wissen, Tracking) die keine `layout_config`-Cards sind.

### layout_config

Beim Connect geschickt (zusammen mit Datenpaketen). Nach jeder JARVIS-Antwort neu berechnet und als `dashboard_update` gepushed.

```json
{
  "type": "layout_config",
  "cards": [
    {"id": "todos",     "type": "list",   "title": "Todos heute",   "source": "todos_today"},
    {"id": "calendar",  "type": "agenda", "title": "Heute",         "source": "calendar_today"},
    {"id": "btc",       "type": "metric", "title": "BTC",           "source": "btc"},
    {"id": "followups", "type": "list",   "title": "Offene Punkte", "source": "followups_due"},
    {"id": "alarms",    "type": "list",   "title": "Wecker",        "source": "alarms"}
  ],
  "quick_actions": [
    {
      "id": "alarm",
      "label": "Wecker",
      "icon": "⏰",
      "input": {"type": "time_picker", "label": "Weckzeit"},
      "send": "Stell einen Wecker für {value} Uhr."
    },
    {
      "id": "todo_add",
      "label": "Todo +",
      "icon": "📋",
      "input": {"type": "text", "placeholder": "Was muss erledigt werden?"},
      "send": "Erstelle ein Todo: {value}"
    },
    {
      "id": "checkin",
      "label": "Check-In",
      "icon": "💬",
      "input": null,
      "send": "Mach einen kurzen Morgen Check-In."
    }
  ]
}
```

**Cards sind server-computed:** Alarm-Karte erscheint wenn `alarm_service.list_alarms()` nicht leer.
Followup-Karte wenn fällige Einträge in `brain.followups` vorhanden. Karten verschwinden wenn Datenquelle leer.

JARVIS steuert das Dashboard **indirekt**: Er schreibt in `brain.followups`, setzt Alarme, erstellt Todos → Server leitet daraus die Layout-Config ab.

### Reaktive Updates

Nach jeder JARVIS-Antwort:
1. Server baut `layout_config` + alle Datenpakete neu
2. Pusht `dashboard_update` an alle verbundenen Dashboard-Clients

### Frontend-Mapping

```
source           → store state
─────────────────────────────────
todos_today      → store.todosToday
calendar_today   → store.calendarToday
btc              → store.btc
followups_due    → store.followupsDue
alarms           → store.alarms
clients          → store.clients
```

```
card type  → Vue-Komponente
────────────────────────────
list       → ListCard.vue
agenda     → AgendaCard.vue
metric     → MetricCard.vue
```

```
input type   → Modal-Komponente
───────────────────────────────
time_picker  → TimePickerModal.vue
text         → TextInputModal.vue
null         → direkt senden
```

---

## Satellite — Was bleibt, was geht

**Bleibt lokal (physisch nötig):**
- Audio I/O: Wake-Word, VAD, Aufnahme, Wiedergabe
- Alarm-Klingel-Logik (muss offline funktionieren, `alarm_state.json`)
- Minimale Config: Server-URL, Audio-Device-Name, Client-Name
- Bluetooth-Speaker-Reconnect vor jeder Wiedergabe

**Fliegt raus:**
- `SYSTEM_PROMPT_BASE` — hat im Client nichts zu suchen
- Jede Logik die besser auf dem Server aufgehoben ist

---

## Implementierungsstand (Stand 2026-07-24)

Alle initialen Aufgaben plus die in `TECHNICAL_PLAN.md`/`GAP_ANALYSIS.md` (Mai 2026) noch als offen geführten Kernpunkte sind inzwischen erledigt — siehe ROADMAP.md "✅ Fertig" für die vollständige, laufend gepflegte Liste. Auszug der wichtigsten, seit Mai hinzugekommenen Bausteine:

- ✅ NotificationDispatcher + echter Push (unabhängig von laufendem Gespräch)
- ✅ Proactive Daemon + SleepCoach auf `dispatcher.notify()` umgestellt
- ✅ Brain Memory Schema (id/ts_iso/category/weight/source, Aging, Pruning, Dedup)
- ✅ Context-Modularisierung (`detect_modules()`, siehe oben — teilweise gated)
- ✅ Notion vollständig abgelöst — Todos/Projekte/Kontakte/Seiten lokal in `local_data.db`
- ✅ Wissensdatenbank (`knowledge.py`) inkl. Wikipedia-Stil-Verlinkung (`[[topic/file]]`, automatische Backlinks)
- ✅ Coding Engine — delegierte, freigabepflichtige Code-Arbeit über die Claude Agent SDK
- ✅ Ticket-Integration Phase 1 — GitHub Issues als Todos, nie Server-seitiger Token
- ✅ MCP Server für Claude-Code-Sessions (personal/work Scope)
- ✅ jarvis-web (Desktop, Tauri) als drittes, vollwertiges Frontend neben Dashboard/Satellite
- ✅ Tailscale-Zugriff (`jarvis.tail47e1d9.ts.net`), TLS für alle Web-Clients

**Nächste Schritte → siehe ROADMAP.md**

---

## Verzeichnisstruktur (aktuell)

```
server.py              ← WebSocket-Server
mcp_server.py           ← MCP-Server für Claude Code (personal/work Scope)
pipeline.py             ← STT → LLM → TTS, Tool-Loop
client_manager.py       ← Client-Registry, Capabilities, Audio-Routing
protocol.py             ← Alle WebSocket-Message-Typen (Konstanten)
brain.py                ← Brain DB — Sections, Memory-Schema (remember/forget/get_memory/apply_aging)
context.py              ← System-Prompt-Builder, detect_modules()
knowledge.py            ← Wissensdatenbank — Topics/Dateien, Index, Wikilinks/Backlinks
tracking.py             ← Strukturierte Ziele/Logs (goals, logs)
local_data.py           ← Todos/Projekte/Kontakte/Seiten (SQLite, ersetzt Notion seit 2026-07-19)
tools.py                ← Alle LLM-Tool-Definitionen + execute()-Dispatch
llm.py                  ← Anthropic Streaming + Prompt Caching (1h TTL)
stt.py                  ← ElevenLabs Scribe
tts.py                  ← ElevenLabs TTS
session_memory.py       ← messages/threads (SQLite, Prompt-Quelle seit 2026-07-31, siehe
                           ROADMAP.md) + Legacy sessions-Tabelle (Session-Zusammenfassungen,
                           bleibt vorerst bestehen)
config.py               ← Secrets + Hardware-/Deployment-Konstanten aus .env
main.py                 ← Standalone CLI (unverändert)
services/
  alarm.py              ← Wecker-Registry + Sleep-Log
  apple_music.py        ← Lokale Apple-Music-Steuerung (macOS, AppleScript)
  btc.py                ← CoinGecko (cached)
  calendar.py           ← Google Calendar (cached)
  client_music.py       ← YouTube/mpv-Musik-Routing an Satellite-Clients
  coding_engine.py      ← Delegierte Coding-Tasks, Worktrees, PRs, Freigabe-Flow
  email.py              ← IMAP/SMTP
  google_auth.py        ← Gemeinsame Google-OAuth-Anmeldung
  local_exec.py         ← Generisches "führ das auf dem Client aus"-Primitiv
  notification_dispatcher.py  ← Push-Notifications, unabhängig von Pipelines
  proactive.py          ← Kalender-/Email-/Todo-/Followup-Reminder, Nutzerregeln
  reminders.py          ← Apple Reminders (Einkaufsliste), macOS
  search.py             ← DuckDuckGo (ddgs)
  sleep_coach.py        ← Schlaferinnerungen, Eskalationskette
  tickets.py            ← GitHub-Issues → Todos, über lokalen gh-CLI-Login
  timer.py              ← Software-Timer (in-process)
  weather.py             ← Open-Meteo + Nominatim
```

Vollständige Datei-für-Datei-Referenz mit Funktionen/Zeilennummern → `CODE_REFERENCE.md`.
