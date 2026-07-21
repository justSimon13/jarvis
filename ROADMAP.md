# J.A.R.V.I.S. — Roadmap & Backlog

Letzte Überarbeitung: 2026-07-21

Organisiert nach Priorität.

Technischer Idealplan → `TECHNICAL_PLAN.md`
Gap-Analyse (Plan vs. aktueller Code) → `GAP_ANALYSIS.md`

---

## Vision (Stand 2026-07-16)

JARVIS wird zu einem **lebenden, thematischen Wissenssystem**. Nicht nur Assistent — persönlicher Kontext der wächst. Jedes Gespräch hinterlässt etwas. JARVIS lernt autonom und greift auf sein Wissen proaktiv zu.

**Ökosystem-Ziel:** JARVIS MCP Server + Tailscale = ein Gedächtnis für alle Kontexte.
- Zuhause (persönlich): Claude Code → JARVIS MCP → volles Wissen
- Arbeit (Digital35-Account): Claude Code → JARVIS MCP (work scope) → Programmier-Wissen + Digital35-Kontext
- Unterwegs: iPhone App → JARVIS via Tailscale

### Datenarchitektur (verbindlich)

| Datei/DB | Was gehört rein |
|---|---|
| `knowledge/*.md` | Prose: Pläne, Kontext, Erkenntnisse, Ansätze |
| `brain.db` | JARVIS-Regeln, Config, Module, Routinen, Followups |
| `tracking.db` | Strukturierte Werte: Ziele, Logs, Metriken |
| `brain.memory` (SQLite) | Atomare Micro-Facts als Staging (→ Promotion nach knowledge/) |

**JARVIS schreibt immer nach diesen vier Regeln — kein Graubereich.**

---

## 🔴 Phase A — Knowledge System (Fundament) ✅

Alles andere baut darauf auf. Ohne das kein autonomes Lernen, kein richtiges Web-Interface.

### A1. `knowledge.py` + Verzeichnisstruktur ✅

Neues Modul. Verwaltet `~/.jarvis/knowledge/`.

```
~/.jarvis/knowledge/
├── simon/
│   ├── _core.md          ← immer geladen (~200 Tokens): wer Simon ist, Basics
│   └── details.md        ← on-demand: ausführlicheres Profil
├── sport/
│   ├── _summary.md       ← immer geladen wenn Trainer-Modus aktiv
│   ├── fitnessplan.md
│   └── ernaehrung.md
└── programmierung/
    ├── _summary.md
    └── security.md
```

Markdown-Frontmatter:
```yaml
---
topic: sport
updated: 2026-07-16
tags: [training, krafttraining]
---
```

Funktionen:
- `read(topic, file) -> str`
- `write(topic, file, content)` — überschreibt, erstellt wenn nicht vorhanden
- `append_section(topic, file, heading, content)` — ergänzt Abschnitt
- `list_available() -> list[dict]` — alle Dateien mit Metadaten (für Index)
- `generate_summary(topic)` — auto-generiert `_summary.md` nach jedem Write

### A2. `tracking.py` + `tracking.db` ✅

Neues Modul. Generisches Schema — kein Table-Change wenn neues Thema kommt.

```sql
goals (
  topic TEXT, key TEXT, value REAL, unit TEXT, updated_at TEXT,
  PRIMARY KEY (topic, key)
)

logs (
  id TEXT PRIMARY KEY,
  topic TEXT, date TEXT, key TEXT,
  value REAL, text_value TEXT, unit TEXT, notes TEXT
)
```

Funktionen:
- `set_goal(topic, key, value, unit)`
- `get_goal(topic, key) -> dict`
- `get_goals(topic) -> list`
- `add_log(topic, key, value, text_value, notes, date)`
- `get_logs(topic, key, since_date) -> list`
- `get_progress(topic) -> dict` — Ziel + letzter Log-Wert + Trend

### A3. Knowledge-Index in `knowledge_index.db` ✅

Damit JARVIS schnell findet was er braucht ohne alle Dateien zu lesen.

```sql
knowledge_index (
  path TEXT PRIMARY KEY,    -- "sport/fitnessplan.md"
  topic TEXT,
  tags TEXT,                -- kommagetrennt
  summary TEXT,             -- 1-2 Sätze, auto-generiert
  updated_at TEXT
)
```

`knowledge.py` hält den Index synchron bei jedem Write.

### A4. Migration `brain.profile` → `knowledge/simon/_core.md` ✅

`brain.profile` Inhalt wird einmalig in `knowledge/simon/_core.md` migriert.
`brain.profile` Section bleibt danach leer / wird nicht mehr befüllt.
`context.py` lädt `_core.md` statt `brain.profile`.

### A5. Neue Tools für JARVIS ✅

```python
read_knowledge(topic, file)          # Datei lesen
write_knowledge(topic, file, content) # Datei schreiben
search_knowledge(query)              # Index durchsuchen → passende Pfade
set_goal(topic, key, value, unit)    # Ziel setzen/aktualisieren
log_entry(topic, key, value, notes)  # Log-Eintrag schreiben
get_progress(topic)                  # Ziel + Fortschritt abfragen
```

Tool-Beschreibungen explizit formulieren: JARVIS ruft sie **proaktiv** auf wenn das Thema im Gespräch vorkommt — ohne dass Simon es verlangen muss.

### A6. `context.py` Umbau ✅

- Immer laden: `knowledge/simon/_core.md`
- Modus-basiert laden: `knowledge/<topic>/_summary.md` (wenn existiert)
- `brain.profile` aus Prompt entfernen
- `detect_modules()` bleibt für Todos/Kalender/Projekte (Todos/Projekte lokal seit 2026-07-19, siehe `local_data.py`)

---

## 🔴 Phase B — Autonomes Lernen ✅

### B1. Post-Session Extraktion

Nach jeder Session: JARVIS analysiert die Konversation und identifiziert was gelernt wurde.

Drei Pfade:
1. **Micro-Fact** (1 Satz, universell) → direkt in `brain.memory` (Staging)
2. **Thematisches Wissen** (Plan, Kontext, Erkenntnis) → Vorschlag via Notification: *"Soll ich deinen Fitnessplan aktualisieren?"* → bei Bestätigung `write_knowledge()`
3. **Strukturierter Wert** (Ziel, Metrik) → direkt `set_goal()` oder `log_entry()`

Implementierung: neues `learning.py` Modul, läuft als Background-Task nach `session_memory.save()`.

### B2. Staging → Promotion

`brain.memory` sammelt Micro-Facts. Wöchentlich (oder bei Akkumulation >20 gleicher Kategorie) werden sie in die passende knowledge-Datei promoviert und aus brain.memory entfernt.

### B3. Proactive Daemon liest `tracking.db`

`proactive.py` erweitern:
- `_check_training()` — `SELECT max(date) FROM logs WHERE topic='sport' AND key='training'` → Push wenn >2 Tage kein Training
- `_check_goals()` — Goal vs. letzter Log-Wert → Push wenn Ziel weit verfehlt

Kein LLM, kein Markdown-Parse — direkte SQL-Abfrage.

---

## 🟡 Phase F — JARVIS MCP Server (Code-Ökosystem) ✅ mcp_server.py fertig

**Abhängigkeit:** Tailscale (Phase D) für Remote-Zugriff von Arbeit.

### Was das ist
JARVIS exposed sich als MCP-Server. Claude Code verbindet sich beim Start automatisch damit.
Ergebnis: Jede Claude Code Session hat JARVIS's volles Wissen — ohne manuellen Zwischenschritt.

### Scopes
```
personal  →  alles: knowledge/, tracking/, brain.memory
work      →  nur: knowledge/programmierung/, knowledge/digital35/
```

Scope wird beim Connect übergeben:
```bash
# ~/.claude/settings.json (persönlich)
{ "mcpServers": { "jarvis": { "url": "http://hp-elitedesk.tail:8766", "scope": "personal" } } }

# Arbeits-MacBook
{ "mcpServers": { "jarvis": { "url": "http://hp-elitedesk.tail:8766", "scope": "work" } } }
```

### MCP Tools die JARVIS exposed

| Tool | Beschreibung |
|---|---|
| `jarvis_search_knowledge` | Wissensdatenbank durchsuchen |
| `jarvis_read_knowledge` | Datei aus Wissendatenbank lesen |
| `jarvis_write_knowledge` | Erkenntnis aus Coding-Session speichern |
| `jarvis_get_coding_context` | Aktive Projekte + Standards kompakt für Prompt |
| `jarvis_log_work` | Schnelles Work-Update nach Digital35-Session |
| `jarvis_read_project_file` | Datei aus server-seitigem Repo lesen |

### `jarvis_get_coding_context` — der Schlüssel-Tool
```
Gibt zurück:
- knowledge/programmierung/_summary.md
- knowledge/digital35/_summary.md (nur work scope)
- Aktive Projekte (lokal, `local_data.py`)
- Tech-Stack aus simon/_core.md (nur personal scope)
```

Claude Code ruft das automatisch beim Start auf → hat sofort vollständigen Kontext.

### Neue Datei: `mcp_server.py`
HTTP-Server (FastAPI oder einfaches `http.server`) auf Port 8766.
Implementiert MCP JSON-RPC Protokoll.
Läuft als systemd service neben `server.py`.

### Lernschleife rückwärts
Was Claude Code in einer Session lernt → Simon sagt "JARVIS, merk dir X" → geht via `jarvis_write_knowledge` in die Wissensdatenbank. Kein manueller Schritt mehr.

---

## 🟡 Phase C — Web App (`jarvis-web`) ✅ live auf dem HP-Server (2026-07-20)

Desktop-first Interface. Nicht das Dashboard umbauen — neues Repo mit anderem Fokus.

**Deployment (2026-07-20):** eigenes GitHub-Repo (`justSimon13/jarvis-web`, privat), auf dem
HP-Server als `jarvis-web.service` (systemd, Port 5174, neben dem älteren `jarvis-dashboard.service`
fürs iPad auf Port 5173) — erreichbar unter `http://192.168.0.155:5174`. `VITE_JARVIS_WS_URL`
in `.env` explizit auf die Server-IP gesetzt (Default `ws://localhost:8765` hätte sich sonst
aus Sicht jedes Clients auf dessen eigenen Rechner bezogen, nicht den Server).

**Stack:** Vue 3 + Vite (gleich wie Dashboard, Code-Sharing möglich)

### Layout

```
┌─────────────────────────────────────────────┐
│  Sidebar          │  Hauptbereich            │
│  ─────────────    │  ──────────────────────  │
│  💬 Chat          │                          │
│  📚 Wissen        │  [aktiver Tab]           │
│  ✅ Todos         │                          │
│  📅 Kalender      │                          │
│  📊 Tracking      │                          │
└─────────────────────────────────────────────┘
```

### Tabs

**Chat** — Hauptinterface
- Keyboard-first (Enter zum Senden)
- Tool-Call Anzeige ("JARVIS liest: Fitnessplan")
- Kein Touch-optimiertes Layout

**Wissensdatenbank**
- Themen-Übersicht (alle Topics mit Dateien)
- Markdown-Editor direkt im Browser
- JARVIS kann Änderungen vorschlagen → Diff anzeigen, Simon bestätigt
- Neue Themen anlegen via Button oder Gespräch

**Todos** — lokal (`local_data.py`), Web-UI (Add/Complete/Delete) bereits implementiert

**Kalender** — bereits implementiert

**Tracking**
- Ziele pro Topic (aus `tracking.db`)
- Verlaufs-Graphen (Gewicht, Kalorien, etc.)
- Manueller Log-Eintrag ("Training gemacht", Gewicht eintragen)

### WebSocket-Protokoll Erweiterung

Neue Nachrichtentypen:
- `knowledge_update` — Server → Client: JARVIS hat etwas gelernt, UI aktualisieren
- `knowledge_suggestion` — Server → Client: "Soll ich X speichern?" (Bestätigung nötig)
- `knowledge_confirm` — Client → Server: Bestätigung/Ablehnung
- `tracking_update` — Server → Client: neuer Log-Eintrag, Graphen aktualisieren

---

## 🔴 Phase D — Tailscale ✅ Server + Mac im Tailnet (2026-07-21)

Voraussetzung für Web App remote, iPhone App, MCP von Arbeit.

**Erledigt (2026-07-21):** HP-Server + MacBook im selben Tailnet (`justSimon13`). Maschine
auf `jarvis` umbenannt (Hostname `jarvis.tail47e1d9.ts.net`). Zugriff über `tailscale serve`
statt roher Ports/IP:
- `https://jarvis.tail47e1d9.ts.net` → jarvis-web
- `https://jarvis.tail47e1d9.ts.net:8443` → jarvis-dashboard
- `wss://jarvis.tail47e1d9.ts.net:8766` → JARVIS-Backend-WebSocket (TLS, sonst Mixed-Content-
  Block im Browser weil die Web-Apps jetzt über https laufen)

`jarvis-web`s `.env` (`VITE_JARVIS_WS_URL`) auf `wss://jarvis.tail47e1d9.ts.net:8766` gesetzt.
`vite.config.js` beider Web-Apps bräuchte `server.allowedHosts: ['.tail47e1d9.ts.net']`
(Vite blockt sonst unbekannte Host-Header — bei jarvis-web schon erledigt). LAN-IP
`192.168.0.155` bleibt zusätzlich nutzbar (rein additiv, kein Ersatz). Server bindet
schon auf `0.0.0.0`, kein Firewall-/Config-Change am Backend nötig.

**Noch offen:**
- iPhone/iPad ins Tailnet einladen (bisher nur Server + Mac)
- `jarvis-dashboard` (altes iPad-Tool) — `allowedHosts` in dessen `vite.config.js` noch
  nicht ergänzt (wird beim Aufruf über `:8443` vermutlich denselben Vite-Host-Block zeigen)
- `jarvis-satellite` (separates Audio-Client-Gerät, eigenes Repo) — Config nicht in diesem
  Repo, noch nicht angefasst
- Arbeits-Laptop ins Tailnet, damit MCP von Digital35 funktioniert — IT-Policy von
  Digital35 vorher prüfen ob eigene Tailscale-Installation erlaubt ist

---

## 🟡 Phase E — iPhone App

Abhängigkeit: Tailscale (Phase D) muss fertig sein.

**Schritt 1: PWA**
- `jarvis-web` ist responsiv gebaut → auf iPhone als PWA installierbar
- Mikrofon über Safari WebSocket → Spracheingabe möglich
- Quick-Win, kein extra Code

**Schritt 2: Native (wenn PWA nicht reicht)**
- SwiftUI App
- Native Push via APNs (echter Background-Push)
- Siri-Integration möglich
- Nur wenn PWA-Einschränkungen (Audio, Background) problematisch werden

---

## 🔴 Phase G — Coding Engine (JARVIS entwickelt sich selbst) ✅ Grundversion fertig (2026-07-18)

JARVIS kann jetzt über ein neues Tool `delegate_coding_task` selbst Code im `j.a.r.v.i.s.`-Repo schreiben — Simon sagt im Gespräch "JARVIS, bau X", JARVIS delegiert intern an eine eigene Coding-Engine (`services/coding_engine.py`, Claude Agent SDK), arbeitet in einem eigenen Branch (`jarvis/auto-*`, nie `main`), fragt bei riskanten Aktionen über jarvis-web nach Freigabe (`coding_approval_request`/`_response`) und meldet sich per Notification wenn fertig. Details/Design → `knowledge/programmierung/jarvis_coding_architektur.md`.

**Deployment ✅ (2026-07-18):** `pip install claude-agent-sdk` + Smoke-Test + systemd-Neustart erledigt. Erster echter Ende-zu-Ende-Test erfolgreich: Task "lies ROADMAP.md, schreib STATUS.md" lief sauber durch — isolierter Worktree, korrekter Inhalt, sauberer Commit auf eigenem Branch, `main` unangetastet.

Beim Testen gefunden und behoben: fehlende Git-Identität auf dem Server (Commits jetzt mit eigener Identität), gemeinsamer Checkout statt isoliertem Worktree (Sicherheitsrisiko — behoben), `query()` erfordert Streaming-Prompt mit `can_use_tool` (auf `ClaudeSDKClient` umgestellt), `allowed_tools` hebelte `can_use_tool` komplett aus (**Freigabe-System lief bis dahin komplett ins Leere** — behoben durch Entfernen aus `allowed_tools` + `disallowed_tools` für Tools außerhalb des Coding-Scopes), Notification-Rate-Limit kollidierte mit Fortschritts-Updates (Fortschritt läuft jetzt nur noch ins Server-Log). Details → `knowledge/programmierung/jarvis_coding_architektur.md`.

**Noch offen:**
- Eskalations-Freigabe-Flow noch nicht mit einer tatsächlich riskanten Aktion live getestet (bisherige Tests waren alle unkritisch)
- Dedizierter `CODING_ENGINE_API_KEY` statt Fallback auf den Haupt-Key
- GitHub-Push/PR-Automation (`GITHUB_TOKEN` bleibt vorerst ungenutzt)
- Konversationelle Konfigurierbarkeit der Eskalationsregeln
- Nur `j.a.r.v.i.s.`-Repo als Ziel — dashboard/satellite/web noch nicht angebunden
- Nur 1 Coding-Task gleichzeitig

---

## 🟡 Bestehende offene Punkte (weiterhin gültig)

### Mode Playbook (brain.modules.modes)
Pro Modus: `guidelines` + `decision_protocol` + `quality_rules` in brain.db speichern. Bisher nur kurzer Text.

### Simon State
Zustände: `awake_home`, `sleeping`, `away`, `focused`. NotificationDispatcher nutzt State. Kein Push wenn `sleeping`.

### Coach-Modus ausbauen
Abend-Coaching mit Eskalation, Weekly Review, Freelancing-Pipeline-Reminder.

### Reflection Loop
JARVIS läuft 1×/Stunde, wertet Brain + History aus, entscheidet selbst ob Hinweis sinnvoll.

---

## 🟢 Niedrige Priorität / Ideen

- **Hardware-Client (ESP32-S3)** — Plan in `HARDWARE_CLIENT_PLAN.md`, deprioritized
- **Schlafzimmer-Client** — Orange Pi Zero 2W
- **Lichtsteuerung** — Philips Hue Bridge API
- **SevDesk & Steuer** — Erinnerungsschicht + API
- **Multi-Room Audio** — hängt an Schlafzimmer-Client

---

## ✅ Fertig (Referenz)

- Brain-Migration (neue Sections, generische brain.py)
- Shared History (api_history + display_history, Session-Timeout 15 Min)
- Drei-Layer-Protokoll (data_request/response)
- Server-Driven Layout-Config (Cards + Quick Actions pro Modus)
- Dashboard Vue 3 PWA (iPad + HP EliteDesk)
- Satellite-Cleanup (RMS-VAD, kein Prompt-Code)
- Proactive Service (Kalender-Reminder + VIP-E-Mail-Push)
- Overlay Events (Routinen-Zeitfenster, Snooze/Skip)
- Session-Break (History-Marker + Dashboard-Push)
- Wetter-Card (weather.py + Dashboard)
- HP-Server Einrichtung (Server + Wohnzimmer-Satellite)
- Alarm-System (Satellite-lokal, ALARM_SYNC, Snooze, Musik-Wecker)
- NotificationDispatcher + Push-Reminder (Phase 1+2)
- Brain Memory Schema mit Aging, Dedup, Pruning (Phase 3)
- Context Modularisierung mit detect_modules() (Phase 4)
- Tool Idempotenz + Session Memory Hardening (Phase 5)
- Notion-Ablösung — Todos/Projekte/Kontakte lokal in SQLite (`local_data.py`), Konzepte-Feature gestrichen (2026-07-19)
- Notion-Seiten-Migration — rekursiver Seitenbaum (Unterseiten, Breadcrumbs) statt flachem Textblob, `read_seite`-Tool für lazy-Load im Chat, "notion"-Naming komplett aus Schema/Code/Frontend entfernt (2026-07-20)
- Kosten-Tracking — `compute_cost()` in llm.py, Chat-Kosten-Log pro Turn, Sidebar-Widget + Tracking-Ansicht, 1h-Cache-TTL, kostenlose Begrüßung ohne LLM-Call (2026-07-20)
- Graceful Shutdown — Sessions werden bei Server-Neustart (SIGTERM) archiviert statt spurlos verloren zu gehen (2026-07-20)
- Web-Chat-History pro Tab isoliert (stabile tab_id via sessionStorage) — vorher teilten sich alle offenen jarvis-web-Tabs eine Historie (2026-07-20)
- sleep_coach.py auf dispatcher.notify() umgestellt (gleicher Fix wie proactive.py) — Reminder landeten vorher als gefälschte Assistant-Nachricht im Chat des gerade aktiven Clients (2026-07-20)
