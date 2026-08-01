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

**Erweiterung ✅ (2026-07-22):** Vier Punkte aus Simons Feedback umgesetzt ("checkt selbst nicht wenn er fertig ist", "Notifikation zu schnell weg", "PR wäre sinnvoller als direkt main", "ich will auch sehen was ich akzeptiere"):
- Fertig-Notification jetzt `priority="high"` (blieb vorher nach 8s unbemerkt weg)
- Freigabe-Anfrage zeigt jetzt den vollen Inhalt (Diff bei Edit, Dateiinhalt bei Write, Befehl bei Bash) statt nur den Pfad — jarvis-web zeigt das in einem vollen Modal statt kleinem Toast
- `check_coding_task_status`-Tool — JARVIS kann im Gespräch nachschauen ob/was der letzte Task gemacht hat
- Echte GitHub-PR-Erstellung (`_create_pull_request`, nutzt `GITHUB_TOKEN`) statt Branch ohne Review
- Notification-Verlauf (Glocke in jarvis-web, `notification_history`-Resource)

Zusätzlich neues Tool `create_project` (gleicher Anlass): legt GitHub-Repo + lokalen Checkout unter `config.PROJECTS_ROOT` (`~/apps`, gleicher Ort wie der bestehende jarvis-web-Checkout) an, ebenfalls immer mit Freigabe-Pflicht.

**Erweiterung ✅ (2026-07-22, zweiter Teil):** Zwei weitere Punkte aus Simons Feedback ("Ja auf jeden fall, dass muss er können" zu Cross-Projekt-Coding, "ich will ihm auch sagen können, dass er das im auto mode einfach runter programmiert - ohne meine Bestätigung"):
- `delegate_coding_task` ist nicht mehr exklusiv auf das `j.a.r.v.i.s.`-Repo beschränkt — neuer optionaler Parameter `project` lässt JARVIS in jedem Git-Repo unter `config.PROJECTS_ROOT` (`~/apps/<name>`) arbeiten, egal ob per `create_project` angelegt oder schon vorher da (z.B. `project="jarvis-web"` funktioniert bereits heute, weil das dort schon liegt). Jedes Ziel bekommt weiterhin einen eigenen isolierten Worktree/Branch und einen eigenen PR im richtigen Repo (`_repo_slug_for()`). `jarvis-satellite` bleibt außen vor — anderes physisches Gerät, kein Zugriff vom Server aus.
- Neuer optionaler Parameter `auto_mode` — wenn Simon das für einen einzelnen Task ausdrücklich verlangt, überspringt der komplette Task jede Freigabe-Rückfrage (auch bei riskanten Aktionen). Der PR am Ende bleibt trotzdem die einzige Instanz, die tatsächlich nach `main` mergt — auto_mode ändert daran nichts.
- Nebenbei behoben: `create_project`s lokaler `git clone` speicherte den `GITHUB_TOKEN` bisher dauerhaft im Klartext in `.git/config` (Standard-Verhalten von `git clone <url-mit-token>`) — Remote-URL wird jetzt direkt danach auf die tokenlose Variante zurückgesetzt.

**Auto-Update ✅ (2026-07-22):** Simon wollte nicht mehr manuell per SSH `git pull` + `sudo systemctl restart jarvis` ausführen müssen, sobald ein PR gemergt ist ("ich will einfach das jarvis up to date ist auf main", "wäre cool wenn ich nicht immer selbst mit ssh rein müsste"). Neu:
- `scripts/auto_update.sh` — läuft alle 5 Minuten (systemd-Timer), prüft `origin/main` per `git fetch` (rührt den Working Tree nie an), pullt nur `--ff-only` wenn der Checkout sauber UND auf `main` ist, startet danach `jarvis.service` neu. Ist der Checkout gerade nicht sauber (z.B. weil Simon oder Claude Code über den SMB-Mount mittendrin sind) oder nicht fast-forward-fähig, wird der Durchlauf übersprungen statt irgendwas zu riskieren.
- `install_auto_update.sh` — einmaliger Installer (wie `install_server.sh`): richtet eine schmal geschnittene, passwortlose sudoers-Regel ein (**nur** `systemctl restart jarvis.service`, kein voller sudo-Zugriff) sowie `jarvis-auto-update.service`/`.timer`. Muss einmal per SSH auf dem HP-Server ausgeführt werden — danach kein SSH mehr nötig für reguläre Updates.
- Bewusst **kein** LLM-Tool/Chat-Befehl dafür — das ist unabhängige Server-Infrastruktur neben JARVIS' eigenem Python-Prozess, kein Coding-Engine-Task der sich selbst neu startet (würde aktive Verbindungen/Sessions killen, wenn ein Gespräch gerade läuft).

**Auto-Update Verfeinerung ✅ (2026-07-22, gleicher Tag):** Zwei Nachbesserungen aus Simons direktem Feedback:
- *"dann unterbricht er doch sowieso, nur mit einem delay von 5min. Dann soll er halt restarten, wenn alle Prozesse abgeschlossen sind"* — der Timer pullt jetzt zwar weiterhin sofort (stört nichts, rührt nur den Checkout an), verschiebt den eigentlichen `systemctl restart` aber bis JARVIS erkennbar idle ist: 0 verbundene Clients UND kein laufender Coding-Task. Dafür schreibt `coding_engine.refresh_idle_status()` bei jedem Client-Connect/-Disconnect (`server.py`) und jeder Coding-Task-Statusänderung eine kleine Datei `~/.jarvis/idle_status.json`, die der Bash-Timer (läuft als eigener Prozess, kennt Python-internen Zustand sonst nicht) ausliest. Ein ausstehender Neustart wird über eine Marker-Datei (`~/.jarvis/jarvis_restart_pending`) durchgehalten, bis JARVIS wieder frei ist — läuft `jarvis.service` gar nicht (z.B. Absturz), wird sofort neu gestartet statt auf "idle" zu warten. Lokal mit einem gemockten `systemctl`/`sudo` gegen alle vier Fälle verifiziert (busy→verschieben, busy bleibt→Marker hält, idle→Neustart+Marker weg, Service inaktiv→sofort statt warten).
- *"jarvis coded, ich merge PR, danach soll er mit aktuellem main weiter coden können. Also muss er pullen können."* — neue Coding-Tasks (`_create_worktree()`) holen jetzt VOR dem Anlegen ihres Worktrees automatisch den aktuellen `origin/main` (`_sync_main_before_task()`, `--ff-only`, nur wenn der Checkout sauber+auf main ist, sonst best-effort übersprungen) — damit ein gerade gemergter PR nicht verpasst wird, egal ob der Auto-Update-Timer schon durchgelaufen ist oder nicht. Braucht dafür **keinen** Neustart, nur der Checkout muss aktuell sein, nicht der laufende Python-Prozess.

**Direktes Pull-Tool ✅ (2026-07-22, gleicher Tag):** Simon wollte JARVIS im Chat direkt "pull jetzt" sagen können, unabhängig von einem Coding-Task oder dem Timer. Neues Tool `sync_project` (`coding_engine.sync_project()`) — ruft direkt den gemeinsamen `_sync_main()`-Kern auf (gleiche Logik wie `_sync_main_before_task`, nur mit lesbarer Ergebnismeldung statt Server-Log), läuft synchron ohne Freigabe-Dialog (reines `git fetch` + `--ff-only`-Pull, nichts Riskantes) und **ohne** eine ganze Coding-Task-Session zu starten — JARVIS hatte vorher fälschlich vorgeschlagen, dafür einen "minimalen Coding-Task der nur git pull ausführt" zu starten, was unnötig Budget verbrannt hätte und wegen `_sync_main_before_task` ohnehin wirkungslos gewesen wäre. Die Zeile "kein LLM-Tool/Chat-Befehl dafür" oben bezieht sich weiterhin nur auf den Neustart-Teil — der bleibt bewusst Timer-only, nur das Pullen selbst ist jetzt on-demand aufrufbar.

**Direktes Commit+Push-Tool ✅ (2026-07-22, gleicher Tag):** Anlass: ein Coding-Task (Tauri-Setup in `jarvis-web`) hatte per Freigabe Dateien direkt im Live-Checkout angelegt/geändert statt im eigenen Worktree — der PR-Flow griff dafür nicht, die Dateien blieben uncommittet liegen. Simon: *"Es fehlt auch push. Wäre cool, wenn wir alle git commands abbilden könnten"*. Bewusst NICHT umgesetzt: ein generisches "führ irgendeinen Git-Befehl aus"-Tool — das würde genau die Sicherheitslücke wieder öffnen, für die das ganze Freigabe-System gebaut wurde. Stattdessen gezielt `commit_and_push` (`coding_engine.commit_and_push()`): committet + pusht uncommittete Änderungen direkt im Live-Checkout, aber IMMER erst nach Freigabe mit vollem Diff (landet ohne PR-Review direkt auf dem ausgecheckten Branch, die Freigabe ist hier also die einzige Kontrollinstanz). Läuft wie `create_project` in einem Hintergrund-Thread (gleicher Deadlock-Grund). Lokal mit einem Test-Repo verifiziert: sauberer Checkout → kein Freigabe-Dialog, direkt "nichts zu committen"; Freigabe abgelehnt → nichts committet; Freigabe erteilt → Commit + Push landet korrekt im Remote, Checkout danach sauber.

**Git-Konventionen ✅ (2026-07-22, gleicher Tag):** Simon: *"Kannst du noch eine schönere Git Conventions für JARVIS definieren. Eine woran er sich immer halten soll als default. Und eventuell noch die Möglichkeit je nach Projekt eine eigene anzulegen."* Vorher baute `_finalize_commit()` Commit-Messages nur aus einem stumpfen `f"JARVIS: {instruction[:72]}"` — kein Typ-Präfix, kein echter Body.
- Neue Datei `GIT_CONVENTIONS.md` (Repo-Wurzel) — Default-Konventionen: Commit-Format `typ: kurze Zusammenfassung` + Body (Typen angelehnt an Conventional Commits: feat/fix/docs/refactor/chore/test/style), keine Emojis, keine Chat-Referenzen im Commit.
- **Pro-Projekt-Override:** legt ein Projekt (unter `config.PROJECTS_ROOT`) eine eigene `GIT_CONVENTIONS.md` in seiner Wurzel an, gilt die für Coding-Tasks in genau diesem Projekt statt der Default-Version (`_load_git_conventions()`, geprüft mit Test-Ordnern: mit Override → eigene Datei, ohne → Fallback auf Default).
- Der Agent committet weiterhin nicht selbst, liefert aber jetzt am Ende seiner Antwort einen `---COMMIT---`/`---END---`-Block mit Typ+Zusammenfassung+Body (System-Prompt verlangt das explizit, Konventionen-Text wird mit reingereicht) — `_finalize_commit()` parst den heraus (`_build_commit_message()`) und nutzt ihn direkt, echte Zusammenfassung des tatsächlichen Diffs statt nur der ursprünglichen Aufgabenbeschreibung gekürzt auf 72 Zeichen. Fehlt der Block (Task bricht vorzeitig ab o.ä.), Fallback auf eine Keyword-Heuristik (`_heuristic_commit_message()`) für den Typ-Präfix.
- `commit_and_push()` bekommt dieselbe Typ-Präfix-Absicherung (`_ensure_conventional_prefix()`) für von Simon/der LLM direkt diktierte Messages, die noch keinen Typ haben.
- Verifiziert: COMMIT-Block wird korrekt geparst, Keyword-Heuristik trifft Typen (fix/docs/refactor/etc.) plausibel, Override-Datei wird gefunden und hat Vorrang, ohne Override greift der Default.

**Direktes Shell-Kommando-Tool ✅ (2026-07-22, gleicher Tag):** Simon: *"Es nervt mich gerade immer für jarvis etwas auf dem HP Server auszuführen. Das soll Jarvis selbst können."* Neues Tool `run_command(command, cwd=None)` (`coding_engine.run_command()`) — führt einen beliebigen Shell-Befehl auf dem Server aus, für alles was kein dediziertes Tool hat (Logs prüfen, Speicherplatz checken, anderen Service neu starten, Paket installieren). IMMER mit Freigabe zuerst (voller Befehl im Dialog) — bei einem beliebigen Befehl lässt sich keine engere, sichere Grenze ziehen wie bei `commit_and_push`/`create_project`, die Freigabe ist hier die einzige Kontrollinstanz. Bekannte Muster aus `_DESTRUCTIVE_PATTERNS`/`_SECRET_PATTERNS` (schon vorhanden für die Coding-Task-Eskalation) lösen zusätzlich eine sichtbare ⚠️-Warnung im Freigabe-Dialog aus, blockieren aber nichts — Simon entscheidet. Läuft wie die anderen direkten Tools in einem Hintergrund-Thread (Deadlock-Vermeidung), Timeout 120s, Ausgabe (stdout+stderr, gekürzt) + Exit-Code kommen per Notification.

**Sudo-Passwort-Popup ✅ (2026-07-22, gleicher Tag):** Simon: *"Mach doch bei sudo ein Pop-up"* — löst die "Bekannte Grenze" oben elegant, statt eine sudoers-Regel pro Befehl zu verlangen. `_execute_with_sudo_support()`: enthält der Befehl `sudo`, wird zuerst `sudo -n` (non-interaktiv) versucht — greift schon eine NOPASSWD-Regel (z.B. für `systemctl restart jarvis.service`), läuft alles wie bisher ohne jede Rückfrage. Scheitert das spezifisch weil ein Passwort nötig ist, fragt ein zweites Dashboard-Popup interaktiv danach (neuer WS-Typ `coding_sudo_password_request`/`_response`, `protocol.py`) und übergibt es via stdin an `sudo -S` — nie geloggt, nie gespeichert, nur für diesen einen Aufruf verwendet. jarvis-web: eigenes kleines Modal mit maskiertem Passwort-Feld (nicht das bestehende Freigabe-Modal wiederverwendet, da hier eine Eingabe statt nur Ja/Nein nötig ist). Abbrechen schickt bewusst ein leeres Passwort statt auf den 600s-Timeout zu warten — sudo lehnt das einfach als falsches Passwort ab, kommt als normal fehlgeschlagener Befehl zurück, kein Sonderfall nötig. Lokal mit einem gemockten `sudo` gegen alle Fälle verifiziert: kein sudo im Befehl (kein Umweg), NOPASSWD-Treffer (kein Popup), richtiges Passwort (Erfolg), falsches Passwort (sudo-Fehler), keine Antwort (Timeout → `None`). Frontend-Rendering + Passwort-Feld-Maskierung per Playwright geprüft.

**Read-Only-Fastpath für run_command ✅ (2026-07-22, gleicher Tag):** Simon testete `run_command` mit `ls ~/projekte/` (falscher Pfad) — JARVIS meldete nur den rohen Fehler per Notification und tat sonst nichts. *"Er schmiert einfach ab ohne irgendwas? Der soll das checken und woanders suchen."* Root Cause: `run_command` lief bis dahin IMMER asynchron (Freigabe + Ausführung im Hintergrund-Thread, Ergebnis nur per Notification) — JARVIS selbst bekam das Ergebnis nie zu sehen, konnte also unmöglich auf einen Fehler reagieren oder von sich aus einen korrigierten Befehl nachschieben, ganz unabhängig davon wie schlau das Modell ist.

Fix: `_is_safe_readonly_command()` — eine enge, bewusst konservative Whitelist (`ls`/`cat`/`pwd`/`head`/`tail`/`wc`/`stat`/`file`/`which`/`df`/`du`/`ps`/`whoami`/`uname`/`date`/`uptime`/`free`/`hostname`, dazu `systemctl status`/`is-active`/`list-*`, `git status`/`log`/`diff`/`show`/`branch`/`remote`, `journalctl` ohne Vacuum/Rotate-Flags — explizit NICHT `find`, wegen `-delete`/`-exec`, und NICHT `curl`/`wget`, schon anderweitig als riskant geflaggt). Nur Befehle die exakt matchen laufen SOFORT synchron in `run_command()` selbst, Ergebnis kommt direkt als Tool-Antwort zurück.

**Nachbesserung, gleicher Tag:** Simon stieß direkt danach auf denselben Effekt bei `ss -tlnp | grep -E '8000|8080|3000|443|80'` — Pipes waren komplett blockiert, das Ergebnis kam wieder nur per Notification. *"Auch hier, wird mir nur angezeigt, jarvis macht aber nix."* Whitelist erweitert um `ss`/`netstat`/`grep`/`sort`/`uniq`/`cut`/`tr`/`column`/`awk`/`sed` (ohne `-i`) und Pipes zwischen sicheren Segmenten jetzt erlaubt — aber über `shlex`-Tokenisierung statt Roh-Text-Split auf `|`, sonst hätte ein Split mittendrin in gequoteten Argumenten wie dem Regex-Pattern oben (`'8000|8080|...'` enthält selbst `|`-Zeichen) die Segmentierung kaputt gemacht. Jedes Pipe-Segment einzeln gegen die Whitelist geprüft, `&&`/`;`/Umleitung/Sub-Shell bleiben harte Blocker unabhängig von Pipes. Lokal mit 25 Testfällen verifiziert, darunter genau das Screenshot-Kommando, `sed -i` (unsicher) vs. `sed` ohne `-i` (sicher), und der Sonderfall dass ein zufällig wie ein Journalctl-Vacuum-Flag aussehender String als reines `grep`-Suchmuster (nicht an `journalctl` selbst gerichtet) trotzdem sicher ist.

**Noch offen:**
- Eskalations-Freigabe-Flow noch nicht mit einer tatsächlich riskanten Aktion live getestet (bisherige Tests waren alle unkritisch)
- ~~Dedizierter `CODING_ENGINE_API_KEY` statt Fallback auf den Haupt-Key~~ — hinfällig, siehe unten: der Weg wird nicht mehr verbessert, sondern durch den Mac-Worker-Ansatz ersetzt.
- Konversationelle Konfigurierbarkeit der Eskalationsregeln
- Nur 1 Coding-Task gleichzeitig (gilt jetzt projektübergreifend — zwei Tasks in unterschiedlichen Projekten können aktuell trotzdem nicht parallel laufen)

**Nicht mehr über Tools erreichbar (2026-07-30):** `delegate_coding_task`/`check_coding_task_status` wurden am selben Tag mehrfach versehentlich statt des neuen Mac-Worker-Wegs (`start_coding_job`, `services/coding_jobs.py`) verwendet — rechnet über `CODING_ENGINE_API_KEY` zu vollen API-Preisen ab, während der Mac-Worker über Simons Abo läuft (`claude` CLI, kein API-Key). Beide Tool-Definitionen + `execute()`-Verdrahtung aus `tools.py` entfernt. `services/coding_engine.py` selbst bleibt vorerst unangetastet — die anderen vier Tools, die es nutzen (`sync_project`, `commit_and_push`, `run_command`, `create_project`), sind davon nicht betroffen, die bleiben. Geprüft: kein Proactive-Daemon/keine Routine ruft `coding_engine` direkt auf — `server.py`s übrige Anbindung (Kosten-Statistik, Idle-Status, Freigabe-/Sudo-Antworten) bleibt technisch intakt, wird nur nie mehr ausgelöst, da kein neuer Task mehr startet.

**Geplant:** der server-seitige Executor wird später nach demselben Muster wie der Mac-Worker neu gebaut — `claude -p` mit `CLAUDE_CODE_OAUTH_TOKEN` (Abo statt API-Preise), ohne Worktrees, dieselbe `jobs`-Tabelle, eigene `client_id` (z.B. `jarvis-server`, ein dritter, immer online laufender Client — siehe `docs-draft/JARVIS-Konzept-2026-07-28.md`, Abschnitt "Der Server als dritter Client"). Sobald das steht, kann `services/coding_engine.py` ersatzlos entfernt werden.

---

## 🔴 Vorfall: Chat komplett eingefroren (2026-07-22) ✅ behoben

**Symptom:** Während ein Coding-Task lief, blieb der Chat für JARVIS-web irgendwann komplett stumm — Nachricht kam an (`[context] Module geladen` im Log), aber nie eine Antwort, kein Fehler, keine Notification. WebSocket-Verbindung blieb dabei "verbunden" (grüner Punkt), war also kein Verbindungsproblem.

**Root Cause:** `server.py`s `llm_semaphore = threading.Semaphore(1)` ist global und wird von JEDER Chat-Nachricht (`pipeline.py: with self._llm_semaphore:`) geteilt — pro Prozess kann nur ein LLM-Call gleichzeitig laufen (FIFO). `llm.py`s Anthropic-Client hatte aber **kein explizites Timeout** — SDK-Default ist `read=600s` (10 Minuten!). Ein einzelner hängender Call (Netzwerk-Stall) hätte also bis zu 10 Minuten lang den globalen Semaphore blockiert — für JEDEN Client, nicht nur den betroffenen. `pipeline.py`s Fehlerbehandlung um `llm.stream()` fängt zwar jede Exception ab und gibt den Semaphore korrekt wieder frei, aber eben nur wenn überhaupt eine Exception kommt — bei einem reinen Hänger ohne Exception half das nichts.

**Fix:** `llm._get_client()` setzt jetzt `timeout=120.0` beim Anthropic-Client — ein hängender Call scheitert jetzt spätestens nach 2 statt bis zu 10 Minuten, `pipeline.py`s bestehende Fehlerbehandlung greift dann normal (Semaphore wird frei, Simon bekommt eine Fehlermeldung statt ewiger Stille). Verifiziert: `anthropic==0.117.1`s SDK-Default ist exakt `Timeout(connect=5.0, read=600, write=600, pool=600)` — bestätigt den Verdacht direkt.

**Noch offen:** Der globale `Semaphore(1)` selbst bleibt ein Single Point of Failure für Verzögerungen (ein langsamer Call blockt weiterhin alle anderen Clients, nur eben nicht mehr unbegrenzt lang) — für später denkbar: pro-Client-Semaphore statt global, oder ein Health-Check/Watchdog der einen festhängenden `_llm_semaphore` erkennt und den Prozess automatisch neu startet.

---

## 🔴 Vorfall: EIN Timeout hat die ganze restliche Session unbrauchbar gemacht ✅ behoben

**Symptom:** Simon: *"Ich laufe immer und immer wieder in diesen Timeout und jarvis antwortet dann einfach nicht mehr. Die Session teilt sich dann auch immer an dem Punkt."* Nach dem ersten Timeout-Fix oben (Semaphore-Vorfall) trat der eigentliche, tiefere Bug zutage: der Timeout selbst war schon behoben, aber EIN einziger fehlgeschlagener Call machte trotzdem die komplette restliche Session unbrauchbar — jede folgende Nachricht scheiterte aus demselben Grund erneut, nicht nur einmalig.

**Root Cause:** `pipeline.py:process_text()` hängt die User-Nachricht SOFORT an `self.history` an (Zeile 163), bevor der LLM-Call überhaupt startet. Scheitert `_run_llm()` komplett (Timeout, API-Fehler, jede Exception) und liefert leeren Response + leere `final_messages`, greift weder der `if final_messages:` noch der `elif response:` Zweig — die eben angehängte User-Nachricht bleibt für immer unbeantwortet in `self.history` stehen. Bei der NÄCHSTEN Nachricht hängt sich eine zweite "user"-Rolle direkt dahinter — zwei aufeinanderfolgende "user"-Turns in Folge, was Anthropics API grundsätzlich mit "roles must alternate" ablehnt. Dieser Fehler landet im selben generischen Exception-Handler, hinterlässt also WIEDER eine unbeantwortete User-Nachricht — ein einziger Erst-Fehler pflanzt sich damit unbegrenzt fort, jede weitere Nachricht in derselben Session scheitert aus genau diesem strukturellen Grund, nicht wegen eines wiederkehrenden Netzwerkproblems.

**Fix:** Neuer `else`-Zweig in `process_text()` — schlägt `_run_llm()` komplett fehl (kein `final_messages`, kein `response`), wird die zuvor angehängte User-Nachricht wieder aus `self.history` entfernt (Rollback auf den Vorzustand). Die nächste Nachricht startet damit wieder sauber alternierend, kein Fortpflanzungseffekt mehr.

Lokal mit vier Szenarien verifiziert (eigene `JarvisPipeline`-Instanz, `_run_llm` gemockt): reiner Fehlschlag → History bleibt leer (kein Orphan); normaler Erfolg → sauberes User/Assistant-Paar; Fehlschlag NACH bestehender guter History → nur die neue Nachricht wird zurückgerollt, der Rest bleibt erhalten; Erfolg direkt nach einem Rollback → volle Alternation über den ganzen Verlauf hinweg wiederhergestellt.

**Zur "Session teilt sich"-Beobachtung:** meine erste Vermutung (Simons eigener "+ Neuer Chat"-Klick) war falsch — Simon hat das explizit verneint, hat keinen neuen Chat erstellt. Die tatsächliche Ursache: siehe nächster Abschnitt.

---

## 🔴 Vorfall: Historie riss nach Neustart große Lücken (Tool-lastige Gespräche) ✅ behoben

**Symptom:** Simon verglich zwei Screenshots (vor/nach einem Neustart) — nach dem Neustart fehlte ein großer Mittelteil eines Tauri/WebSocket-Debugging-Gesprächs komplett, obwohl es sich laut Sidebar (gleicher Titel, nur aktualisierter Zeitstempel) um dieselbe Session handelte, kein neuer Eintrag. *"Fällt dir was auf?"*

**Root Cause, zwei Teile die zusammenwirken:**
1. `pipeline.py`s `self.history` wurde nach jedem Tool-Turn hart auf 40 Einträge gekappt (`del self.history[:-40]`). Ein einzelner Tool-Aufruf kostet dabei bereits 2 Einträge (Assistant-Tool-Use + User-Tool-Result) — ein Gespräch mit mehreren Recherche-/Editier-Schritten (wie die Tauri-WebSocket-Debugging-Session) sprengt das sehr schnell, die ältere Hälfte fiel dabei einfach raus. Das war **kein neuer Bug**, sondern hat schon vorher bestanden — nur unsichtbar, weil der Browser-Tab bis dahin seine eigene, lokale Kopie des kompletten Verlaufs behielt und nie mit dem tatsächlich persistierten (bereits gekappten) Stand abgeglichen wurde.
2. `session_memory.py`s Transcript-Aufbau (für `sessions.db`) extrahierte nur `type: "text"`-Blöcke aus jeder Message — eine Message die AUSSCHLIESSLICH aus `tool_use`/`tool_result`-Blöcken bestand (kein Text) ergab einen leeren String und wurde komplett verschluckt, nicht nur gekürzt.

Der Web-Tab-Verlauf-Fix von vorhin (Restore nach Neustart über `find_active_session()`) hat diese beiden vorbestehenden, stillen Lücken zum ersten Mal sichtbar gemacht — vorher hat die lokale Browser-Kopie das kaschiert, bis zu einem echten Neustart/Reload war der Unterschied nie direkt zu sehen.

**Fix (Simon: "smarter trimmen statt hart abschneiden"):**
- `pipeline.py`: `final_messages` wird vor dem Abspeichern in `self.history` zusätzlich durch `llm.compress_tool_history()` geschickt (bisher lief das nur PRO Live-API-Call innerhalb eines Turns, nie dauerhaft vor dem Speichern) — ältere Tool-Results schrumpfen dauerhaft auf kurze Platzhalter statt für immer in voller Länge liegen zu bleiben. Der jeweils NEUESTE Tool-Result eines Turns bleibt bewusst unkomprimiert (volle Details fürs unmittelbare Modell-Kontext), wird erst beim nächsten Tool-Turn komprimiert — 1-Turn-Verzögerung ist gewolltes Verhalten der bestehenden Funktion.
- Cap dadurch angehoben: 40 → 150 Einträge im Tool-Zweig, 20 → 40 im reinen Text-Zweig — dank der Kompression kostet ein Eintrag jetzt deutlich weniger, es passen spürbar mehr echte Gesprächs-Turns rein bevor überhaupt getrimmt wird.
- `session_memory.py`: neue `_extract_text()`-Hilfsfunktion (ersetzt die inline-duplizierte Logik in `save()` und `upsert()`) — reine Tool-Nachrichten ergeben jetzt einen lesbaren Platzhalter wie `[tool_result]` oder `[tool_use, tool_result]` statt komplett zu verschwinden.

Lokal verifiziert: `_extract_text()` gegen 10 Fälle (Text, gemischt, reine Tool-Blöcke, leer); `upsert()` mit einem 8-Turn-Testgespräch (inkl. zwei reinen Tool-Turns) — alle 8 Einträge bleiben erhalten statt auf 6 zu schrumpfen; `pipeline.py`-Kompression über zwei aufeinanderfolgende Tool-Turns (bestätigt: älterer Tool-Result schrumpft von 1000 auf 311 Zeichen, neuester bleibt bei 1000 — genau das erwartete 1-Turn-Verzögerungsverhalten); neuer Cap mit 60 simulierten Turns (120 Einträge) — bleibt komplett erhalten, wäre vorher bei 40 hart gekappt worden.

---

## 🔴 Web-Tab-Verlauf übersteht jetzt einen Server-Neustart ✅ (2026-07-22)

**Anlass:** Direkte Folge des Semaphore-Vorfalls oben — nach dem Neustart zeigte der offene jarvis-web-Tab weiterhin den alten Chatverlauf (rein clientseitiges Anzeige-Artefakt), aber JARVIS hatte serverseitig keinen Kontext davon mehr. Simon: *"der Chat sollte schon wieder mitgegeben werden"*.

**Root Cause:** `api_histories["web"]` ist pro Tab isoliert (`dict[tab_id, list]`), aber rein in-memory — ein Neustart löscht das komplett. Der laufend geschriebene Verlauf in `sessions.db` (`session_memory.upsert()`, seit 2026-07-20 für genau solche Fälle gedacht) hatte bisher **keine tab_id-Spalte** — es gab also keine Möglichkeit, eine wiederverbindende `tab_id` einer bestehenden Zeile zuzuordnen. Kommentar im Code von damals ("web hat keine stabile Identität über einen Neustart hinweg") war zu dem Zeitpunkt noch bewusst — jetzt behoben.

**Fix:**
- `sessions`-Tabelle bekommt eine `tab_id`-Spalte (Migration wie die anderen Spalten via `ALTER TABLE` + try/except).
- `upsert()` setzt `tab_id` NUR beim allerersten INSERT einer neuen Zeile, rührt sie bei späteren UPDATEs nie wieder an — bestehende Aufrufstellen (SESSION_RESET/SESSION_LOAD/Shutdown), die kein `tab_id` mitgeben, überschreiben dadurch nichts versehentlich mit `None`.
- Neue Funktion `session_memory.find_active_session(tab_id)` — findet die zuletzt aktualisierte Zeile für eine gegebene `tab_id`.
- `handle_connection()` in `server.py`: wenn ein Web-Tab mit einer dem Prozess noch unbekannten `tab_id` verbindet (Neustart-Fall, aber auch ein einfacher Page-Reload trifft denselben Codepfad), wird `find_active_session()` aufgerufen und der Verlauf direkt in `api_histories`/`display_histories` zurückgeschrieben, bevor die Pipeline konstruiert wird — JARVIS hat den Kontext dann sofort wieder, ohne dass Simon "Verlauf laden" anklicken muss.

Lokal mit einer Test-DB verifiziert: INSERT mit tab_id → UPDATE ohne tab_id (bleibt erhalten) → `find_active_session()` findet die richtige Zeile mit vollem Transcript, unbekannte tab_id gibt `None`.

**Nachtrag, 2026-07-31 — Kontextverlust TROTZ Restore, im selben, durchgehend verbundenen Tab.** Simon meldete: nach einem Server-Neustart (zum Deployen des `max_tokens`-Fixes) antwortete JARVIS im selben Chat-Fenster, als hätte das direkt vorangegangene Gespräch (zwei abgeschnittene `write_knowledge`-Versuche) nie stattgefunden — obwohl der sichtbare Chatverlauf im Client lückenlos weiter dastand.

- **Wichtige Klarstellung zuerst:** der lückenlos sichtbare Verlauf im Client ist KEIN Beleg dafür, dass der Server denselben Stand hat — `stores/jarvis.js`s `messages`-Liste lebt rein im Browser-/Tauri-Prozess und wird bei einem WebSocket-Reconnect nie neu vom Server geladen, sie täuscht Kontinuität vor, unabhängig davon was serverseitig tatsächlich passiert ist.
- **Root Cause gefunden:** `server.py::_persist_web_turn()` (der laufende Zwischen-Save nach jedem Web-Turn, Kern des oben gebauten Restore-Mechanismus) schrieb den eigentlichen `sessions.db`-Write bisher NICHT ab, sondern startete ihn fire-and-forget über `asyncio.create_task()`, ohne dass der Aufrufer je darauf wartete. Der tatsächliche SQLite-Write lief dadurch auf einem separaten Task, dessen Ausführung um eine beliebige Zeitspanne (nächster freier Event-Loop-Tick) verzögert sein konnte. Stirbt der Prozess in genau diesem Fenster (Absturz, hartes Kill, oder ein Neustart der nicht auf ausstehende Tasks wartet), geht der zuletzt abgeschlossene Turn nie in `sessions.db` — `find_active_session()` beim Reconnect stellt dann einen veralteten Stand wieder her, dem Modell fehlt genau der zuletzt geführte Gesprächsteil. Alle anderen `session_memory.upsert()`-Aufrufstellen in `server.py` (`SESSION_RESET`/`SESSION_LOAD`) awaiten den Executor-Call bereits direkt — `_persist_web_turn()` war die einzige Ausnahme, vermutlich aus Latenz-Überlegungen so gebaut (aber unnötig: `_run_text_turn()` läuft selbst schon als eigener, von der Nachrichten-Schleife losgelöster Task, siehe `_spawn_turn()` — Awaiten hier verzögert weder die Verbindung noch die längst gestreamte Antwort).
- **Fix:** `_persist_web_turn()` ist jetzt `async def` und wird an ihrer einzigen Aufrufstelle direkt `await`et — der DB-Write ist damit spätestens beim Abschluss eines Turns garantiert abgeschlossen, nicht erst irgendwann danach. Schließt das Zeitfenster fast vollständig (ein SIGKILL exakt WÄHREND des laufenden Writes bleibt ein irreduzibles Restrisiko, aber ein Millisekunden- statt ein unbegrenztes Fenster).
**Nachtrag zwei, gleicher Tag — Simon zurecht misstrauisch: "Das hat vorher immer funktioniert."** Der obige Fix allein passte nicht zur Beobachtung — `_save_all_sessions_on_shutdown()` liest `api_histories["web"]` direkt aus dem Speicher, unabhängig davon ob der `_persist_web_turn()`-Task für den letzten Turn schon gelaufen war. Der eigentliche, vollständigere Befund: `main()` nutzte bisher `async with websockets.serve(...):` — dessen `__aexit__` ruft `server.close()` mit dem WEBSOCKETS-INTERNEN Default `close_connections=True` auf, was VOR dem Rücksprung aus dem `async with`-Block auf JEDEN einzelnen offenen Verbindungs-Handler wartet (`await asyncio.wait(self.handler_tasks)`, verifiziert im `websockets`-Quellcode, Version 17.0). Steckt eine Verbindung dabei gerade in einem lange laufenden `loop.run_in_executor(...)`-Call (ein aktiver LLM-Turn — läuft unabhängig vom WebSocket-Status weiter, auch nachdem der Server dessen Verbindung längst geschlossen hat), blockiert das den KOMPLETTEN Shutdown, bis dieser Call fertig ist — und damit auch `_save_all_sessions_on_shutdown()`, das im alten Code ERST NACH dem `async with`-Block lief. Dauert das länger als systemds `TimeoutStopSec` (Default 90s, kein eigener Wert in `install_server.sh` gesetzt), killt systemd den Prozess hart (SIGKILL) — der Save läuft dann NIE, der komplette laufende Verlauf ist weg, nicht nur der eine in-flight Turn.

Erklärt zwanglos, warum es "vorher immer funktionierte": jeder bisherige erfolgreiche Restore lief über `scripts/auto_update.sh`, das einen anstehenden Neustart bewusst zurückstellt, bis KEIN Client mehr verbunden ist (`connected_clients > 0` in `idle_status.json` → Neustart verschoben). Ein Neustart traf dort also nie eine aktive Verbindung, geschweige denn einen laufenden Turn — dieser Fall (Neustart während eines aktiven Gesprächs, vermutlich weil Simon manuell neu gestartet hat, um den `max_tokens`-Fix sofort zu testen statt auf den nächsten Timer-Durchlauf zu warten) war schlicht ein bisher nie durchlaufener Codepfad.

**Fix:** `main()` nutzt jetzt `server = await websockets.serve(...)` (ohne `async with`) — `_save_all_sessions_on_shutdown()` läuft SOFORT nach dem Shutdown-Signal, VOR `server.close()`/`await server.wait_closed()`. Ein noch laufender Turn kann den vollständigen Shutdown weiterhin verzögern (unvermeidbar, der Executor-Call lässt sich nicht von außen abbrechen), aber nicht mehr den vorherigen Save verhindern.

Empirisch verifiziert (nicht nur gelesen): zwei Vergleichs-Testläufe mit der echten `websockets`-Bibliothek (ein simulierter Client, ein künstlich 1,5s "langsamer" Handler, Shutdown-Signal währenddessen) — altes Muster (`async with`): Save-Aufruf lief NACH Ende des langsamen Calls (Bug bestätigt, nicht nur vermutet). Neues Muster: Save-Aufruf lief SOFORT nach dem Shutdown-Signal, deutlich VOR Ende des langsamen Calls. `py_compile` sauber.

---

## 🔴 Coding-Job-Karte im Chat + Root-Cause-Fix: Plan/Fortschritt kamen nie an (2026-07-31)

Simon meldete: der Plan bei `autonomy='careful'`s `awaiting_review` und die Live-Fortschrittszeile (beide im Abschnitt "Autonomiegrad `careful`" oben als fertig dokumentiert) erscheinen tatsächlich NIE im Chat. Ausdrückliche Vorgabe: erst die Ursache finden, nicht daneben etwas Neues bauen. Zusätzlich sollte die Darstellung ohnehin umgebaut werden: eine einzelne, sich selbst aktualisierende Karte pro Job statt Text-Nachrichten + Toasts.

**Root Cause gefunden (verifiziert, nicht vermutet):** `server.py::handle_connection()` registriert den Event-Callback (`send_json`) ausschließlich unter `client_id` (`str(id(websocket))`, server-internes, PRO VERBINDUNG neues Handle) — `manager.register_event(client_id, send_json)`, einzige Aufrufstelle im Repo. `tab_id` (vom Browser stabil in `sessionStorage` gehalten, per `client_hello` gesendet) ist ein ANDERER Wert, sobald `client_hello` verarbeitet wurde. `_deliver_job_result_to_chat()`/`_relay_job_progress()` riefen aber `manager.get_event_callback(tab_id)` auf — ein Key, der nie registriert wurde. Lookup lieferte für jeden echten Web-Tab `None`, geloggt als "gerade nicht verbunden" (irreführend — der Tab WAR verbunden, nur unter dem falschen Key gesucht). Erklärt beide Symptome vollständig.

**Erster Fix-Entwurf verworfen:** eine zweite `register_event(tab_id, send_json)` direkt in `_event_handlers`. Simons Review: `get_dashboard_event_callbacks()` (einzige Quelle für Toast-/`dashboard_sync`-/`layout_config`-Broadcasts) iteriert zwar heute über `self._clients` (nur `client_id`-Keys), NICHT direkt über `_event_handlers` — mit der zusätzlichen Registrierung wäre es also aktuell nicht zur doppelten Zustellung gekommen, aber ein impliziter, leicht brechender Zusammenhang (ein künftiger, naheliegender Refactor auf direkte `_event_handlers`-Iteration hätte still doppelt zugestellt). Stattdessen sauberer gelöst:

- `client_manager.py`: neues, SEPARATES Dict `self._tab_to_client` (tab_id → client_id), neue Methoden `set_tab_client()`/`get_event_callback_for_tab()` — `_event_handlers` selbst bleibt komplett unverändert, nur unter `client_id`. `unregister(client_id)` entfernt jetzt zusätzlich alle `_tab_to_client`-Einträge mit passendem WERT (nicht Schlüssel!) — race-sicher: überschreibt ein schneller Reconnect die Zuordnung bereits mit einer neuen `client_id`, bevor das verzögerte `unregister()` der alten Verbindung ankommt, schlägt der Wertvergleich fehl, der aktuelle Eintrag bleibt unangetastet.
- `server.py`: `manager.set_tab_client(tab_id, client_id)` an beiden Stellen, an denen `pipeline.set_chat_target()` läuft. `_deliver_job_result_to_chat()`/`_relay_job_progress()` nutzen jetzt `get_event_callback_for_tab(tab_id)`.

**Neue Karte statt Text+Toast:** ein Eintrag `{type: 'coding_job_card', jobId}` in `store.messages` (jarvis-web) statt `role: user/assistant` — landet dadurch chronologisch an der Stelle im Chat, an der der Job erstellt wurde.
- Neuer Protokoll-Typ `CODING_JOB_CREATED` (`protocol.py`), ausgelöst in `coding_jobs.py::start_job()` direkt nach dem DB-Insert, VOR allen drei Rückgabe-Pfaden (sofort dispatcht/hinter anderen Jobs vorgemerkt/kein Worker verbunden) — sonst hätte ein zunächst wartender Job nie eine Karte bekommen. Nur wenn `category`/`tab_id` gesetzt sind. Flüchtig wie `coding_job_progress`, keine History-Persistierung.
- Neue `data_request`-Resource `coding_job` (Singular, per `id`) → `coding_jobs.get_job_status()` (bereits vorhanden). Die Karte behandelt `coding_job_created`/`coding_job_progress`/`coding_job_result` rein als "etwas hat sich geändert, neu laden"-Signal und holt den vollständigen Stand jedes Mal frisch — keine strukturierten Felder durch mehrere WS-Payload-Formen hindurchreichen.
- **Toast-Unterdrückung für den eigenen Tab:** `notification_dispatcher.py::notify()`/`_deliver_now()` bekommen `exclude_tab_id` — überspringt beim Broadcast den zur `tab_id` gehörenden Callback per Objekt-Identität (`get_event_callback_for_tab()` löst zum selben, unter `client_id` abgelegten Objekt auf wie `get_dashboard_event_callbacks()`). `coding_jobs.py::resolve_job_result()`s `_notify(...)`-Aufruf reicht `exclude_tab_id=tab_id` durch — der Tab, der den Job gestartet hat, sieht das Ergebnis bereits live als Karte, andere Tabs/Fenster bekommen den Toast unverändert.
- jarvis-web: `CodingJobCard.vue` (neu) — fünf Zustände (`pending`: wartet auf Worker + Client-Name; `running`: Live-Zeile aus `codingJobProgress` + Laufzeit; `awaiting_review`: voller Plan + Freigeben/Nachbessern-mit-Kommentar/Verwerfen über das bereits vorhandene `codingJobAction()`, keine eigene Logik; `done`: Zusammenfassung/geänderte Dateien/kopierbarer Branch/PR-Link; `failed`/`discarded`: Grund). `JobsView.vue` bleibt unverändert — die zwei reinen Formatierungs-Helfer (`formatDuration`/`formatCost`) plus ein bloßer Zwischenablage-Schreibvorgang wandern in eine neue, geteilte `lib/codingJobFormat.js`, die NUR die Karte importiert.

**Bewusst nicht Teil dieses Durchgangs:** keine Rekonstruktion von Karten aus der History nach Reload/Session-Wechsel (ein wiederhergestellter `awaiting_review`-Job bleibt über die unveränderte Job-Ansicht erreichbar); keine Erweiterung von `codingJobProgress` auf mehrere gleichzeitig sichtbare Fortschrittsanzeigen (bestehende "ein Ref, letztes Ereignis gewinnt"-Einschränkung bleibt).

Verifiziert: `client_manager.py` isoliert getestet (Grundfall, unbekannter tab_id, `get_dashboard_event_callbacks()` liefert weiterhin genau einen Eintrag pro Verbindung — kein Duplikat, Cleanup bei `unregister()`, gezielter Race-Test: `set_tab_client()` gefolgt vom verzögerten `unregister()` der alten Verbindung lässt die neue Zuordnung unangetastet). `notification_dispatcher.py` isoliert getestet (mit/ohne/unbekannten `exclude_tab_id`). `services/coding_jobs.py` isoliert getestet (`CODING_JOB_CREATED` in allen drei `start_job()`-Rückgabepfaden, keins ohne `category`/`tab_id`; `resolve_job_result()` reicht `exclude_tab_id` korrekt durch). `py_compile` sauber. `npm run build` (jarvis-web) sauber. Echter End-zu-End-Test (Karte durchläuft alle fünf Zustände im echten Chat, kein doppelter Toast im eigenen Tab, Toast weiterhin in einem zweiten Fenster) steht noch aus (durch Simon).

---

## 🔴 messages/threads in SQLite — Ersatz für api_histories/display_histories, Teil 1 (2026-07-31)

**Anlass:** Direkte Folge der beiden Kontextverlust-Vorfälle oben (Web-Tab-Verlauf-Restore, `_persist_web_turn`-Await-Fix) — beide behoben Symptome desselben Grundproblems: `api_histories`/`display_histories` sind reine In-Memory-Dicts, jede Session-Grenze (Reset/Timeout/Session laden) ist ein destruktiver Eingriff in denselben Speicher, den die Pipeline gleichzeitig mutiert. Zielbild dazu existiert bereits in `docs-draft/JARVIS-Datenmodell-und-API.md` (`messages`/`threads`/`daily_summaries`, siehe auch `docs-draft/MIGRATION.md` §3.2, dort explizit "der riskanteste Einzelschritt" der ganzen Neuausrichtung). Simons Auftrag: Teil 1 — jede Nachricht wird beim Anhängen in SQLite persistiert und beim Prompt-Aufbau von dort gelesen, direkter Ersatz von Schreib- UND Lesepfad in einem Durchgang (keine gestufte Parallel-Schreib-Phase, die hätte den Kontextverlust-Bug nicht behoben). `thread_id`/`project_id`-Spalten angelegt, aber unbefüllt — kein Thread-Teil in diesem Durchgang.

**Neue Tabellen** (additiv in `sessions.db`, neben der unverändert bestehenden `sessions`-Tabelle): `messages` (`role` NUR `user`/`assistant` — `tool_use`/`tool_result` sind Content-Blöcke innerhalb dieser beiden, kein eigener API-Rollenwert; `content` JSON-encoded, volle API-Fidelity, nie lossy geglättet; `display_text` nullable für die seltenen Fälle wo UI-Anzeige vom API-Inhalt abweicht, z.B. Coding-Job-Ergebnisse; `attachments` JSON für UI-Chips; `client_name` für Voice-Sprecherzuordnung; `category`/`tab_id` als bewusste Zwischenlösung fürs Fenstern bis `thread_id` das übernimmt — bei `category='voice'` wird `tab_id` beim Lesen ignoriert, Voice bleibt absichtlich geräteübergreifend geteilt; `data_scope` Default `'own'`, da im Zielbild-Dokument fehlend, aber laut `docs-draft/CLAUDE.md` zwingend). `history_windows` (Cursor `active_after_id` pro `(category, tab_id)` — Ersatz fürs Leeren/Ersetzen der alten Liste: ein Reset rückt den Cursor vor statt Zeilen zu löschen, echter Append-Only-Strom). `threads`/`daily_summaries` — nur Schema, keine Erzeugungslogik in diesem Durchgang.

**Schreibpfad:** Persistiert an JEDER bestehenden Anhänge-Stelle (`pipeline.py::process_text()` User-Nachricht, `_run_llm()`s drei Stellen: Assistant-Text, Assistant-tool_use-Block, User-tool_result-Block) statt als Diff am Rundenende — deckt sich wörtlich mit "jede Nachricht wird beim Anhängen persistiert". Kompression (`llm.compress_tool_history`/`compress_attachment_history`) wandert vom Schreib- zum Lesepfad (`session_memory.build_history_window()`) — Speicherung bleibt immer volle Fidelity, Kompression ist jetzt ein reiner Read-Time-Transform.

**Rollback bei Totalausfall:** `turn_start_id` wird VOR dem Persistieren der User-Nachricht gemerkt. Schlägt die Runde komplett fehl (Exception ODER leere Rückgabe von `_run_llm()`), löscht `delete_messages_after(category, tab_id, turn_start_id)` ALLES, was diese Runde durabel geschrieben hat — nicht nur die führende User-Nachricht wie beim alten `self.history.pop()`. Notwendig geworden, weil ein Fehlschlag in Runde 2+ eines Tool-Loops sonst einen `assistant(tool_use)/user(tool_result)`-Rest ohne Fortsetzung stehen ließe — der nächste Turn hängt erneut `user` an, exakt der "roles must alternate"-Fehler vom 2026-07-22, nur über SQLite reproduziert (in der alten, rein in-memory gehaltenen Variante war das folgenlos, weil ein Totalausfall nur eine lokale Variable verwarf, nichts Persistiertes).

**Session-Grenzen auf Cursor umgestellt:** `SESSION_RESET`/8h-Voice-Timeout rücken den Cursor auf den aktuellen Höchststand vor. `SESSION_LOAD` behält die Fortsetzbarkeit ("an alte Gespräche anknüpfen", aktiv über die Sidebar genutzt) in reduzierter Form: nur innerhalb desselben `tab_id` (Cross-Tab-Fortsetzung ist ein Thread-Feature, hier bewusst nicht unterstützt — ein Versuch mit fremdem `tab_id` liefert jetzt einen Fehler statt der bisherigen stillen Cross-Tab-Übernahme). Dafür bekommt die alte `sessions`-Tabelle eine neue `first_message_id`-Spalte (befüllt bei Migration UND bei jeder live neu angelegten Zeile), `rewind_cursor_to_session()` setzt den Cursor auf den Punkt davor zurück.

**Migration:** `migrate_sessions_to_messages()` läuft einmalig beim Serverstart (`main()`, vor `_load_history()`), überträgt alle noch nicht migrierten `sessions`-Zeilen (`transcript`, bereits lossy durch `_extract_text()`) als einzelne `messages`-Zeilen, markiert per neuer `migrated_to_messages_at`-Spalte — zweiter Lauf ist ein No-Op. Alte Tabelle/CRUD-Funktionen bleiben unverändert bestehen (Entfernen ist ein eigener, späterer Schritt).

**Vereinfachung:** der Web-Tab-Restore-Block beim Reconnect (`find_active_session()`, siehe Abschnitt oben) ist entfallen — `build_history_window()` liest bei jedem Turn frisch aus SQLite, kein manueller Restore mehr nötig.

**Verifikations-Schritt (Simons Vorgabe, statt einer separaten Parallel-Schreib-Phase):** `self.history`s komplette alte Mutationslogik bleibt unverändert im Code bestehen — dient nur noch als Vergleichswert (`pipeline.py::_verify_reconstruction()`), nicht mehr für die tatsächliche API-Runde. Vor jedem `_run_llm()`-Aufruf: SQLite-Fenster gegen `self.history` vergleichen (Rollen/Inhalte), Abweichung mit `[migration-verify]`-Prefix loggen, nie blockierend. **Bewusst temporäres Gerüst — Entfernung ist ein eigener, späterer Aufräum-Schritt** (gekoppelt an die Entfernung der alten `sessions`-Tabelle/`learning.process_session()`, die `self.history` bis dahin weiterhin speist).

**Bewusst nicht Teil dieses Durchgangs:** `thread_id`/`project_id` befüllen/auswerten; `daily_summaries` befüllen (`learning.py`-Anbindung); Entfernen der alten `sessions`-Tabelle oder der Vergleichs-Scaffold; automatischer Backup-Mechanismus (Simon sichert vor diesem Deploy manuell, der fehlende Mechanismus bleibt ein eigener, überfälliger, nicht blockierender Punkt).

Verifiziert: isoliertes Testskript gegen ein Scratch-`HOME` (Force-Override, nie `setdefault`) deckt `append_message`/`build_history_window`-Roundtrip inkl. Content-Block-Listen, `max_message_id`, `delete_messages_after` (inkl. Tab-Isolation), Cursor (`advance_cursor`/`get_cursor`, Voice ignoriert `tab_id`), Migration (inkl. Idempotenz), `session_belongs_to_tab`/`rewind_cursor_to_session` ab — 27/27 Checks bestanden, reale lokale `~/.jarvis/sessions.db` (54 Zeilen, altes Schema) nachweislich unberührt. `pipeline.py`/`server.py`: `py_compile` sauber, Persistierstellen und Rollback-Pfad per Code-Review gegen die tatsächlichen Zeilen verifiziert — ein End-zu-End-Lauf war auf dem Mac nicht möglich (`.venv` spiegelt die Python-3.10-Umgebung des Linux-Servers, kein lauffähiger Interpreter hier), steht auf dem echten Server noch aus.

**Nachtrag, gleicher Tag — Vorfall: response-only SDK-Felder machten den Chat ab der fünften Nachricht unbenutzbar ✅ behoben.** Simon meldete kurz nach dem Deploy: `messages.5.content.0.text.parsed_output: Extra inputs are not permitted` (400), reproduzierbar in jedem Chat mit mehreren Runden.

- **Root Cause:** `pipeline.py::_serialize_content()` rief `.model_dump()` auf jedem Anthropic-Response-Contentblock auf und speicherte dabei ALLE Pydantic-Felder — inkl. response-only Feldern wie `parsed_output`, die das Request-Schema der API nicht kennt. Beim erneuten Senden einer so gespeicherten Nachricht (jeder Folge-Turn desselben Chats) lehnte die API den Call ab.
- **Fix:** neue zentrale Allowlist `session_memory.clean_content()` (+ `_BLOCK_ALLOWED_FIELDS`, direkt aus dem Request-Schema für `text`/`tool_use`/`thinking`/`redacted_thinking`-Blöcke) — angewendet automatisch in `append_message()`, nicht nur an der einen Aufrufstelle, die den Vorfall verursacht hat (Sicherheitsnetz für jede künftige Persistierstelle). `thinking`-Blöcke behalten bewusst `signature` (bei aktiviertem Thinking für die Fortsetzung eines Tool-Loops zwingend erforderlich, sonst schlägt der NÄCHSTE Call fehl statt nur der aktuelle). `session_memory.clean_stored_content()` — einmaliger, idempotenter Reparaturlauf beim Serverstart — repariert bereits gespeicherte Zeilen von vor diesem Fix, damit ein bereits betroffener Chat nach dem Deploy automatisch wieder funktioniert statt weiter kaputt zu bleiben.
- **Simons Rückfrage eins — greift der `turn_start_id`-Rollback bei einem harten Abbruch?** Nein: der Rollback in `process_text()` fängt nur Python-Exceptions ab — ein SIGKILL (Prozess stirbt hart, z.B. ein Server-Neustart mitten in einem Call) lässt gar keinen `except`-Block mehr laufen. Neue Funktion `session_memory.repair_dangling_turns()` (ebenfalls beim Start, idempotent): scannt bei jedem Serverstart alle Tabs (Voice kategorie-weit über alle Verbindungs-`tab_id`s hinweg, Web pro `tab_id`) nach einem nicht sauber mit einer reinen Assistant-Text-Antwort abgeschlossenen Rest (z.B. `assistant(tool_use)` ohne folgenden `tool_result`, oder ohne folgende Antwort) und schneidet ihn ab — sonst hätte der nächste Turn dort erneut `user` angehängt, dieselbe "roles must alternate"-Klasse wie der Vorfall vom 2026-07-22, nur über SQLite reproduziert.
- **Simons Rückfrage zwei — warum hat `[migration-verify]` das nicht gemeldet?** `_verify_reconstruction()` verglich bisher `self.history`s Inhalt (rohe Anthropic-SDK-Objekte) direkt gegen die aus SQLite gelesenen, reinen Dicts — das ist bei JEDEM `tool_use`-Turn strukturell "verschieden" (Pydantic-Objekt `!=` Dict), unabhängig vom eigentlichen Inhalt. Das echte, informative Signal (der `parsed_output`-Leak) ging in diesem Dauer-Rauschen unter. Fix: beide Seiten laufen jetzt vor dem Vergleich durch dieselbe Normalisierung (`_serialize_content()`).

Verifiziert: Test gegen das ECHTE `pipeline.py` (Import mit gestubbten schweren Abhängigkeiten, kein lauffähiger Interpreter mit den echten Paketen auf diesem Mac) — Fake-SDK-Objekte reproduzieren exakt die gemeldete Struktur. 20/20 Checks: `_serialize_content()` konvertiert nur (filtert nicht mehr selbst), `clean_content()` filtert korrekt je Block-Typ inkl. `signature`-Erhalt bei `thinking`, vollständiger Persist→Lese-Roundtrip enthält nur erlaubte Felder, `append_message()` filtert automatisch auch ohne expliziten Aufruf, `clean_stored_content()` repariert eine vorher kaputt gespeicherte Zeile (idempotent), `repair_dangling_turns()` bereinigt dangling Web-Tabs (mit/ohne vorherigem sauberem Turn) und einen über mehrere Voice-`tab_id`s verteilten Rest, lässt saubere Tabs unangetastet. Ursprüngliches Testskript (27 Checks) erneut grün, keine Regression. Reale lokale `~/.jarvis/sessions.db` nachweislich unberührt. Echter Test auf dem Server (ein zuvor betroffener Chat sollte nach dem Deploy automatisch wieder nutzbar sein) steht noch aus.

---

## 🔴 Threads — manuelle Chat-Themen-Etiketten (Teil 2 des Chat-Umbaus, 2026-07-31)

**Anlass:** Direkte Fortsetzung von Teil 1 — `thread_id`/`project_id` in `messages` existierten dort bereits als Spalten, bewusst unbefüllt ("Kein Thread-Teil in diesem Durchgang"). Simons Auftrag jetzt: rein manuelles Labeling — im Chat ein Projekt auswählen oder ein freies Thema benennen, das wird zum aktiven Thread für neue Nachrichten. Seitenleiste zeigt Threads statt der alten Sessions-Liste. Automatische Themenerkennung ausdrücklich nicht Teil dieses Durchgangs. Bestehender `messages`/`history_windows`/`sessions`-Inhalt wird gelöscht statt migriert (Simon sichert manuell vorher) — die relevanten Altinhalte stehen bereits in der Wissensdatenbank.

**Zwei Design-Fragen, von Simon selbst aufgeworfen und im Plan beantwortet:**
- **Verhältnis Thread ↔ `tab_id`/Cursor:** zwei unabhängige, gleichrangige Fensterbildungs-Strategien statt einer Hierarchie. `history_windows`/Cursor bleiben exakt wie in Teil 1, genutzt NUR ohne aktiven Thread. Ist ein Thread aktiv, umgeht `build_history_window()` Cursor/`tab_id`-Filterung komplett und liest nach `thread_id`. `tab_id` bleibt nötig, aber nur noch als Verbindungs-Routing.
- **Themenwechsel mitten im Gespräch:** nur neue Nachrichten, keine rückwirkende Umsortierung — deckt sich mit Simons eigener Eingangsbeschreibung und dem Anhängen-nie-mutieren-Prinzip des Stroms aus Teil 1.

**Beim Testen selbst gefundener, echter Bug (nicht nur beim Review):** Thread-Nachrichten tragen DENSELBEN `tab_id` wie threadlose (nur zusätzlich `thread_id`) — ohne Gegenmaßnahme hätte das threadlose Fenster (Cursor-basiert, kennt `thread_id` nicht) beim Zurückwechseln auf "Kein Thema" die Nachrichten aus der Thread-Phase wieder mit angezeigt. Fix: `SET_THREAD`-Handler ruft bei JEDEM Wechsel `session_memory.advance_cursor()` — symmetrisch zu `SESSION_RESET`, das denselben Vorstoß schon beim "+ Neuer Chat" macht. Ein isoliertes Testskript hat genau diesen Fall zuerst als (zurecht) fehlgeschlagen gemeldet, bevor der Fix eingebaut wurde.

**Backend (`session_memory.py`):** `create_thread`/`update_thread`/`delete_thread`/`list_threads`/`get_thread_project_id`/`get_thread_messages` — `delete_thread()` löscht NUR die Thread-Zeile, `messages.thread_id` bleibt unangetastet (Threads sind Etiketten, keine Behälter). `append_message()` bekommt `thread_id`/`project_id`, aktualisiert bei gesetztem `thread_id` im selben Aufruf `threads.last_activity_at` (zentral verankert, wie `clean_content()` aus dem Hotfix). `build_history_window()` bekommt `thread_id`-Parameter, der Cursor/`tab_id` komplett umgeht.

**Backend (`pipeline.py`):** `self._thread_id`/`self._project_id` + `set_thread()` (reiner In-Memory-Set, KEINE eigene DB-Arbeit — der Lookup ist Sache von `server.py`, sonst würde entgegen dem `set_mode()`-Muster synchron blockierende SQLite-Arbeit auf dem Event-Loop-Thread laufen). Alle vier `append_message()`-Aufrufstellen + der `build_history_window()`-Lesepfad tragen jetzt `thread_id`/`project_id` durch. `_verify_reconstruction()` unterdrückt sich selbst bei aktivem Thread (`self.history` kennt Threads nicht, ein Vergleich wäre garantiert und bedeutungslos divergent).

**Backend (`server.py`/`protocol.py`):** neue Konstante `SET_THREAD` — Handler macht den `project_id`-Lookup via `run_in_executor` (bewusst NICHT synchron wie `SET_MODE`/`SET_THINKING`, die brauchen keine DB) und rückt danach den Cursor vor (siehe oben). `client_hello` trägt `thread_id` — anders als `extendedThinking`/`llmModel` (bewusst NICHT persistiert) übersteht ein aktiver Thread einen Reconnect wie der Modus, an beiden `CLIENT_HELLO`-Stellen behandelt. `SESSION_RESET` setzt zusätzlich den aktiven Thread zurück. `_do_entity_action()` gibt bei `action=="add"` jetzt die neue Zeilen-`id` zurück (an `ENTITY_ACTION_ACK` als `id`-Feld angehängt) — ohne das hätte das Frontend einen frisch angelegten freien Thread nicht sofort per `SET_THREAD` aktivieren können; andere Entitäten unverändert. `threads`-Eintrag in `_ENTITY_FIELDS`/`_ENTITY_ADD_FN`/... (reine Wiederverwendung des bestehenden generischen Dispatches). Neue `data_request`-Resourcen `threads`/`thread_messages`. `_deliver_job_result_to_chat()` (persistiert außerhalb jeder Pipeline-Runde, für asynchron eintreffende Coding-Job-Ergebnisse) schlägt jetzt selbst den aktiven Thread des Ziel-Tabs nach, sonst wäre ein Job-Ergebnis während eines aktiven Threads außerhalb von dessen Fenster gelandet.

**Frontend (jarvis-web):** Seitenleiste zeigt jetzt eine flache, nach Aktivität sortierte Thread-Liste statt der alten, nach Datum gruppierten Sessions-Liste — kein separater Vorschau-Modus mehr (Threads sind immer live, `selectThread()` setzt den Thread serverseitig UND lädt sofort dessen Nachrichten). Neuer Thread-/Projekt-Picker im Eingabebereich (`<select>`, gleiches visuelles Muster wie die bestehende Modell-Auswahl) — Projekt-Auswahl nutzt Find-or-Reuse (existiert schon ein Thread für dieses Projekt, wird der aktiviert statt ein Duplikat anzulegen). Alte `SESSION_LIST_REQUEST`/`SESSION_LOAD`/`SESSION_DELETE`/`session_transcript`-Nutzung vollständig aus `ChatView.vue`/`jarvis.js` entfernt — die serverseitigen Handler bleiben unverändert bestehen (jarvis-dashboard könnte sie noch nutzen).

**Bewusst nicht Teil dieses Durchgangs:** automatische Themenerkennung; `thread_id`/`project_id`-Auswertung für die Sidebar hinaus; Migration/Backfill der gelöschten Altbestände; Ablösung von `tab_id` durch reine Thread-Zugehörigkeit; Entfernen der alten `sessions`-Tabelle/des `self.history`-Vergleichsgerüsts/der `SESSION_*`-Backend-Handler; Bulk-Löschen/Umbenennen von Threads in der Sidebar-UI (nur Anlegen/Auswählen).

**Löschen des Bestands:** neues, manuelles `scripts/wipe_chat_history.py` (Muster wie `scripts/migrate.py` — NICHT aus `server.py::main()` aufgerufen, verlangt eine getippte Bestätigung). Simon führt es nach seinem eigenen Backup selbst aus.

Verifiziert: isolierte Testskripte gegen Scratch-`HOME` — 20 neue Checks für Thread-CRUD/Fensterbildung/`get_thread_messages()`/Cursor-Vorstoß-Notwendigkeit (inkl. des oben beschriebenen, dabei gefundenen Bugs), 15 neue Checks gegen das ECHTE `pipeline.py` (gestubbte schwere Abhängigkeiten) für `set_thread()`/alle vier Persistierstellen/`_verify_reconstruction()`-Suppression. Beide bestehenden Testsuiten aus Teil 1/Hotfix erneut grün, keine Regression. `_do_entity_action()`s Rückgabewert-Änderung nur per Code-Review verifiziert (voller `server.py`-Import bräuchte 14+ zusätzliche Service-Stubs, wie schon in Teil 1 nicht praktikabel auf diesem Mac). jarvis-web: `npm run build` sauber. Reale lokale `~/.jarvis/sessions.db` nachweislich unberührt. Echter End-zu-End-Test durch Simon (Thema anlegen/wechseln, Sidebar, Reconnect behält aktiven Thread, Coding-Job-Ergebnis im richtigen Thread) steht noch aus.

---

## 🔴 Vorfall: "[tool_result]" erschien als eigene Chat-Blase ✅ behoben (2026-07-23)

**Anlass:** Direkte Nachwirkung des Historie-Truncation-Fixes weiter oben (`_extract_text()` gibt seitdem statt `""` einen Platzhalter wie `"[tool_result]"` zurück, damit tool-lastige Turns nicht mehr spurlos verschwinden). Simon zeigte einen Screenshot aus jarvis-web: nach einer normalen Nachricht ("ok") erschienen zwei weitere Chat-Blasen mit dem wörtlichen Text `[tool_result]` — als hätte er das selbst geschrieben.

**Root Cause:** Der Web-Tab-Restore-Codepfad (siehe Abschnitt oben) verwendet denselben `transcript`-Text aus `sessions.db` für ZWEI verschiedene Zwecke gleichzeitig: `api_hist` (LLM-Kontext, muss auch Platzhalter behalten, sonst bricht die Rollen-Abwechslung beim nächsten Anthropic-Call) und `disp_hist` (die eigentliche Chat-Ansicht, wo ein Platzhalter wie `[tool_result]` nie echter Gesprächsinhalt war — live wurden Tool-Aufrufe nie als Text-Blase gerendert, sondern über einen eigenen `tool`-Nachrichtentyp).

**Fix:** Neue Funktion `session_memory.is_placeholder_text(text)` (Regex gegen das von `_extract_text()` erzeugte `[typ, typ]`-Format). Der Restore-Loop in `server.py` schreibt Platzhalter weiterhin in `api_hist`, überspringt sie aber beim Aufbau von `disp_hist`.

Lokal verifiziert: `is_placeholder_text()` erkennt `"[tool_result]"`/`"[tool_use, tool_result]"` korrekt, lässt echten Text (auch mit eckigen Klammern im Inhalt) unangetastet.

---

## 🔴 Fehlendes Tool: Unterseiten anlegen/bearbeiten ✅ behoben

**Anlass:** Simon zeigte einen Chat-Ausschnitt (Tauri-Desktop-App) — JARVIS behauptete, ein PRD "in Notion" dokumentiert zu haben. Notion existiert in diesem System seit der Ablösung am 2026-07-19 nicht mehr, keine Zeile Code, kein Tool, keine Erwähnung irgendwo im Live-Kontext (geprüft: keine Wissensdatenbank-Treffer, keine Notion-Referenz in irgendeinem Code-Pfad den JARVIS während eines Gesprächs tatsächlich lädt — nur historische Migrations-Doku/Einmal-Skripte, die nie in den System-Prompt geladen werden). Auf Nachfrage gab JARVIS selbst zu: *"Das war meine Annahme, sorry"* — eine Konfabulation ohne erkennbare technische Ursache, vermutlich weil "Doku schreiben" im Trainingsdatensatz stark mit Notion assoziiert ist.

**Der tatsächlich reparable Teil:** JARVIS diagnostizierte im selben Gespräch korrekt, dass es für Unterseiten-Dokumentation kein Schreib-Tool gibt — nur `read_seite` (lesend) existierte, `local_data.py` hatte zwar `update_seite()` für bestehende Zeilen, aber keine Funktion um überhaupt eine neue Seite anzulegen (weder fürs Frontend noch fürs LLM).

**Fix:**
- Neue Funktion `local_data.create_seite(titel, inhalt="", *, parent_typ=None, parent_id=None, eltern_seite_id=None)` — legt eine Seite entweder direkt an einem Todo/Projekt/Kontakt an oder verschachtelt unter einer bestehenden Seite (genau eine der beiden Varianten, sonst `ValueError`).
- Neue LLM-Tools `create_seite` (nutzt die Funktion oben) und `write_seite` (dünner Wrapper um das schon vorhandene `update_seite()`, bisher nur über die UI-eigene `entity_action` erreichbar, nie als Chat-Tool). `data_query`s Beschreibung verweist jetzt auf beide, nicht nur auf `read_seite`.
- Tool-Beschreibung von `create_seite` weist explizit darauf hin, dass es kein Notion gibt — falls das Muster nochmal auftritt, soll JARVIS das nicht wiederholen.

Lokal verifiziert: `create_seite()` direkt (Wurzel-Seite, verschachtelte Unterseite, `list_unterseiten()` findet sie, beide/keine Parent-Variante wird korrekt abgelehnt, ungültiger `parent_typ` wird abgelehnt) sowie über den vollen `tools.execute()`-Dispatch-Pfad (create → write → read bestätigt die Änderung → Fehlerfall gibt saubere Meldung statt Absturz).

**Nachbesserung, direkt danach:** Simon konnte in jarvis-web trotzdem nicht in ein Projekt mit neu angelegter Unterseite reinklicken ("halbautomaten – WordPress Relaunch"). Root Cause: `jarvis-web`s `ProjektItem.vue`/`KontaktItem.vue` machten den Namen nur klickbar wenn `externe_id` gesetzt war — ein Feld das AUSSCHLIESSLICH bei aus Notion migrierten Zeilen befüllt wird. Seit `create_seite()` können aber auch nie-migrierte, ganz normale Projekte/Kontakte Unterseiten bekommen — die Bedingung war seit dem heutigen Feature schlicht falsch, nicht nur unvollständig. `local_data.list_projekte()`/`list_kontakte()` hängen jetzt (wie `query()`, der LLM-Pfad, schon länger) einen `unterseiten`-Hinweis (Liste von `{id, titel}`) an jede Zeile mit tatsächlich vorhandenen Seiten an — Frontend prüft jetzt `unterseiten?.length || externe_id` (additiv, kein Verhaltensverlust für die alten Notion-Zeilen). `TodosView` hat aktuell noch KEIN Klick-durch für Unterseiten, obwohl `create_seite`/`read_seite` auch für Todos funktionieren — nicht Teil dieser Runde, da nicht gemeldet.

Verifiziert: Python-seitig (`list_projekte()` liefert `unterseiten` exakt für Zeilen mit echten Seiten, nicht für andere) sowie live gegen den echten Server (der lokale Dev-Server verbindet sich inzwischen mit der echten Tailscale-Adresse) — "halbautomaten – WordPress Relaunch" zeigte sich dort tatsächlich als nicht-klickbares `<div>`, exakt das gemeldete Symptom, vor dem Deploy des Backend-Fixes.

---

## 🔴 Ticket-Integration + generisches Lokal-Dispatch (2026-07-23) — Phase 1 ✅, Phase 2+3 offen

**Vision:** Simon will JARVIS als aktiven Planungs-/Umsetzungs-Partner für seine Arbeitstickets (GitHub Issues, privates Firmenrepo) — morgendlicher Prioritäts-Hinweis, gemeinsam planen, auf Zuruf umsetzen lassen. Entscheidender Constraint: Quellcode/Diffs dürfen NIE auf JARVIS' HP-Server landen (Datenschutz/Recht am Werk) — nur Ticket-Metadaten. Die eigentliche Code-Arbeit soll lokal auf Simons Mac laufen (Claude Code CLI unter seinem Arbeits-Account, via die Tauri-App), JARVIS delegiert nur, sieht nie den Code. Passt zur schon länger in diesem Dokument stehenden Vision (Zeile 18: "Arbeit (Digital35-Account): Claude Code → JARVIS MCP (work scope)").

Drei bewusst unabhängig überprüfbare Phasen (voller Plan: `/Users/simon/.claude/plans/parsed-mixing-quilt.md`), Phase 3 (lokale Ausführung auf dem Mac) am riskantesten, deshalb separat.

**Nachbesserung noch am selben Tag — zwei Architekturentscheidungen von Simon korrigiert, bevor irgendwas live getestet wurde:**

1. *Kein Server-Token.* Der ursprüngliche Plan (Server holt Issues direkt per eigenem `D35_GITHUB_TOKEN`) hätte einen neuen, fine-grained PAT gebraucht — bei einer Firmen-Org landet sowas typischerweise in einer admin-sichtbaren Freigabe-Warteschlange. Simon ist dort kein Admin und wollte diese Sichtbarkeit ("das sollen die auch nicht wissen, dass ich mir Tickets ziehe via API") explizit vermeiden. Fix: Issues werden jetzt über Simons **eigenen, längst autorisierten `gh`-Login auf dem Mac** geholt — kein neues Credential, keine Org-Sichtbarkeit. Dafür wurde das generische "JARVIS führt etwas lokal auf einem Client aus"-Fundament aus Phase 3 vorgezogen (siehe unten).
2. *Generische Namen, keine Spezialfälle.* Simon: "ich will keine D35 GitHub Issues in meinem Jarvis haben, sondern einfach nur Tickets oder Todos" — kein Repo/Arbeitgeber-Name im Code, mehrere Repos aus unterschiedlichen Projekten möglich (unterschieden über das `repo`-Feld am Ticket, nicht über eine eigene Kategorie). Außerdem: keine separate Tickets-Ansicht — Todos und Tickets sind dasselbe Konzept, GitHub-Issues erscheinen einfach in der normalen Todos-Liste mit Zusatzinfos (Label/Repo-Link), keine zweite Seite.

**Phase 1 (✅ gebaut, noch nicht gegen echte Daten getestet):**
- `services/local_exec.py` (neu) — generisches Primitiv: Server schickt `LOCAL_EXEC_REQUEST` an einen Client mit `local_exec`-Capability (aktuell: die Tauri-Desktop-App), blockiert bis `LOCAL_EXEC_RESPONSE` kommt (oder Timeout). `action`-Feld bestimmt WAS läuft — aktuell nur `gh_issue_list`, später auch die eigentliche Claude-Code-Dispatch aus Phase 3.
- `client_manager.py`: `set_capabilities()`/`get_client_with_capability()` — Browser-Tab und Tauri-App melden sich sonst identisch, das unterscheidet sie.
- jarvis-web: `client_hello` meldet `capabilities: ['local_exec']` nur wenn `isTauri()`. Neues `src/lib/localExec.js` führt `gh issue list --repo ... --json ...` über `@tauri-apps/plugin-shell` aus (neue Cargo-/npm-Dependency, Capability in `capabilities/default.json` eng auf die `gh`-Binary gescopt — Scope-Syntax verifiziert gegen ein echtes GitHub-Repo-Beispiel, nicht geraten).
- `services/tickets.py` (vormals `github_issues.py`) — `sync_tickets()` holt Repos aus `brain.config.ticket_repos` (Liste, per Gespräch gesetzt, nicht fest im Code), dispatched pro Repo über `local_exec`, upserted in `todos` (`source='github'`). Status-Regel weiterhin einseitig: GitHub `closed` erzwingt lokal `Erledigt`, GitHub `open` überschreibt nie einen bereits lokal gesetzten Status.
- `local_data.list_tickets()`, LLM-Tool `sync_tickets` (vormals `sync_arbeit_tickets`), `server.py`-Resourcen `tickets`/`sync_tickets` (vormals `arbeit_tickets`/`sync_arbeit_tickets`).
- jarvis-web: `TodosView.vue`/`TodoItem.vue` erweitert (GitHub-Link, Labels, Ticket-Nummer als Zusatzinfo pro Zeile, "🎫 Sync"-Button) — die separate `ArbeitTicketsView.vue`/`TicketItem.vue`/Route/Nav-Link von der ersten Fassung sind wieder entfernt.

**Offen, bevor Phase 1 live nutzbar ist:** Simon muss lokal `gh auth status` prüfen/`gh auth login` einrichten, per Gespräch `brain.config.ticket_repos` setzen (z.B. `["digital35/xyz"]`), und das echte Priority-Label-Schema mitteilen (`_PRIORITY_LABELS` in `tickets.py` ist aktuell nur ein Platzhalter). Die Tauri-Shell-Capability-Syntax ist gegen ein reales Beispiel verifiziert, aber noch nicht durch einen eigenen CI-Build bestätigt — nächster Tag/Release-Test zeigt das.

**Phase 2 (morgendlicher Prioritäts-Hinweis) und der Rest von Phase 3 (Claude Code lokal starten)** — noch nicht begonnen, Details im Plan-Dokument.

Verifiziert (Phase 1): isolierte Python-Tests (Capability-Routing inkl. Unregister, lokaler Dispatch/Resolve-Roundtrip inkl. Timeout- und Kein-Client-Fall, `sync_tickets()` Upsert-Logik inkl. Status-Einseitigkeit über eine gefakte `local_exec.dispatch`, `tools.execute("sync_tickets", {})` end-to-end), `npm run build` sauber, Playwright-Smoke-Test der zusammengelegten `/todos`-Ansicht (Sync-Button sichtbar, alter Tickets-Nav-Link weg, keine Console-Fehler), Cargo-/npm-Package-Existenz für `tauri-plugin-shell`/`@tauri-apps/plugin-shell` via crates.io/npm-Registry verifiziert.

---

## 🔴 Vollständige Doku + Wiki-Umbau der Wissensdatenbank (2026-07-24) ✅

**Anlass:** Simon wollte das gesamte System nochmal von Grund auf verstehen können, ohne den Code zu lesen — plus die Wissensdatenbank strukturell wie Wikipedia (mit echten Verlinkungen zwischen Dokumenten statt isolierten Dateien).

**Doku (Repo-Root, `j.a.r.v.i.s.`):**
- `ARCHITECTURE.md` aufgefrischt — viele als "Ideal" aus `TECHNICAL_PLAN.md` beschriebene Punkte sind inzwischen implementiert (Brain-Memory-Schema, `detect_modules()`, NotificationDispatcher), war vorher nicht als solches dokumentiert.
- Neu: `CODE_REFERENCE.md` (Datei-für-Datei-Referenz inkl. "Bekannte Ecken" — toter `SYSTEM_PROMPT_BASE`-Import in `brain.py`, Modell-String-Divergenz `llm.py` vs. `config.py`), `TOOLS.md` (vollständige LLM-/MCP-Tool-Referenz), `PRODUCT.md` (Produktumfang inkl. explizitem "was NICHT").
- `TECHNICAL_PLAN.md`/`GAP_ANALYSIS.md` (Stand 2026-05-04, seither weit überholt) mit Veraltet-Banner versehen statt gelöscht.

**Wiki-Verlinkung (`knowledge.py`):** Inline `[[topic/file]]`/`[[topic/file|Anzeigetext]]`-Syntax, beim Schreiben extrahiert und in neuer Tabelle `knowledge_links` gespeichert. `get_links(topic, file)` liefert ausgehende Links direkt aus dem Text und Backlinks rein berechnet (nie von Hand gepflegt, analog MediaWikis "Linked from"). `server.py`s `knowledge_file`-Resource liefert Links mit, jarvis-web (`KnowledgeView.vue`) rendert sie klickbar + zeigt ein Backlinks-Panel. `write_knowledge`-Tool-Beschreibung (Chat + MCP) weist JARVIS aktiv an, beim Schreiben zu verlinken. Zusätzliche Härtung: `_sanitize_segment()` gegen bis dahin ungeprüfte `topic`/`file`-Werte in Pfad-Konstruktion.

**Echtes move()/delete() statt Verweis-Stubs:** Erste Duplikat-Bereinigungen (angular↔programmierung, devops↔infrastruktur) liefen zunächst über Verweis-Stubs, weil `knowledge.py` keine Lösch-Funktion hatte. Auf Simons Feedback ("das soll nicht so verteilt sein") hin durch echtes `move()`/`delete()` ersetzt (Datei+Index+Links werden wirklich entfernt, kein Karteileichen-Muster mehr) — neue MCP-Tools `jarvis_move_knowledge`/`jarvis_delete_knowledge`, bewusst kein Chat-Tool dafür (kuratierte Aufräum-Aktion, kein beiläufiges Gesprächsverhalten).

**Themen-Konsolidierung:** JARVIS-eigener Content war über 4 Topics verstreut (`devops`, `programmierung`, `simon`, `development`) — auf Simons expliziten Wunsch in ein Topic `jarvis/` zusammengeführt (8 Dateien verschoben, `[[...]]`-Links dabei auf die neuen Pfade angepasst). `jarvis/_overview.md` als Übersichtsseite — Unterstrich-Präfix ist eine bestehende Konvention (wie `simon/_core.md`): sortiert alphabetisch immer zuerst, wird von der Frontend-Gruppierungslogik nicht mitgruppiert.

**MCP-Reconnect-Erkenntnis:** Der bekannte `-32602`-SSE-Bug trat innerhalb dieser Session mehrfach erneut auf, auch nach mehreren Server-Neustarts (`jarvis-mcp.service`) — der eigentliche Fix war ein Client-seitiger Reconnect (`/mcp` in Claude Code), nicht der Server-Neustart. Wichtig für nächstes Mal: bei erneutem Auftreten zuerst `/mcp` probieren, bevor der Server mehrfach neu gestartet wird.

Verifiziert: `knowledge.py`-Link-/Move-/Delete-Logik lokal gegen Scratch-Verzeichnisse getestet (Backlinks korrekt, Dedup, Path-Traversal abgelehnt, `move()` löscht Quelle wirklich); `jarvis-web`-Frontend-Änderungen mit `npm run build` verifiziert; Konsolidierung live gegen die echte Wissensdatenbank durchgeführt und per Suche/Read bestätigt (keine `devops`/`infrastruktur`/`development`-Topics mehr, `jarvis/_overview.md` zeigt alle 7 Geschwister-Dateien als Links UND Backlinks).

---

## 🔴 Sonnet-5-Umstieg + umschaltbares Adaptive Thinking (2026-07-25) ✅

**Anlass:** Simon fand die Qualität von Claude Code (Sonnet 5) gut und wollte dieselbe Qualität für JARVIS selbst — bei laufender Diskussion um Token-Verbrauch (siehe Vorfälle weiter oben zu Kosten/Latenz).

**Modell-Umstieg:** `llm.py`s `MODEL` von `claude-sonnet-4-6` auf `claude-sonnet-5` — dieselbe Sticker-Preisklasse ($3/$15 pro 1M Tokens, aktuell sogar Einführungsrabatt bis 2026-08-31), löst nebenbei die bisherige Modell-String-Divergenz zu `config.CODING_ENGINE_MODEL` auf (war schon vorher `claude-sonnet-5`). Zu bedenken: Sonnet 5 nutzt einen neuen Tokenizer (~30% mehr Tokens für denselben Text als 4.6) — nach Ablauf des Rabatts real ca. 30% teurer pro gleichwertigem Gespräch, aktuell durch den Rabatt ungefähr ausgeglichen.

**Wichtigster Fund beim Umstieg:** Sonnet 5 aktiviert Adaptive Thinking automatisch, wenn der `thinking`-Parameter fehlt (bei 4.6 bedeutete das Fehlen "kein Thinking") — ein reiner Modell-String-Swap hätte also für JEDE Antwort (Voice + Web) stillschweigend eine Denkpause vor der Antwort eingeführt, inkl. höherem Tokenverbrauch. Für Voice (STT→LLM→TTS) besonders unerwünscht, da JARVIS bewusst auf ein schnelles Antwortgefühl optimiert ist (`greet()` umgeht deshalb sogar ganz das LLM).

**Lösung — Thinking bleibt standardmäßig aus, aber jetzt einstellbar statt hartcodiert:**
- `llm.stream()` neuer Parameter `thinking: bool` — `False` (Default) → `{"type":"disabled"}` + `max_tokens=8096` (unverändertes Verhalten), `True` → `{"type":"adaptive"}` + `max_tokens=16000` (mehr Headroom, da Thinking-Tokens mit ins `max_tokens`-Limit zählen).
- Neue WS-Nachricht `set_thinking` (`protocol.py`), `pipeline.set_thinking()` hält den Zustand pro Client-Session (analog zu `set_mode`).
- jarvis-web: neuer 🧠-Toggle direkt in der Chat-Eingabeleiste (`ChatView.vue`) — Simons Wunsch war explizit, das *"je nach Situation selbst"* umschalten zu können, nicht nur einen globalen Schalter. Bewusst nur im Web-Chat, nicht in jarvis-dashboard/Voice — dort ist Latenz kritisch, im getippten Web-Chat nicht.

Verifiziert: `llm.py`-Logik lokal gegen einen Fake-Client getestet (thinking=False/True/Default-Fall liefern jeweils korrekte `thinking`-Config und `max_tokens`), `npm run build` für die jarvis-web-Änderungen sauber.

---

## 🔴 LLM-Modellauswahl im Chat (2026-07-25/26) ✅

**Anlass:** Direkte Weiterentwicklung des Sonnet-5-Umstiegs oben. Simon fragte, ob man das LLM auch "on the fly" wechseln könne, ohne Kontextverlust — Antwort: ja, die Anthropic-API ist zustandslos, `client_messages` werden unverändert an das neue Modell weitergereicht, einzige Nebenwirkung ist ein Prompt-Cache-Neuschreib beim ersten Call nach dem Wechsel (anderes Modell = anderer Cache-Namespace). Simons explizite Entscheidung: *"Hau alle rein und mach eine Info text rein on hover"* — alle vier verfügbaren Modelle zur Wahl, mit Hover-Beschreibung statt einem reinen Label.

**Backend:**
- `llm.py`: `MODEL_CATALOG` (Dict `model_id → {label, input, output}`) für alle vier Modelle (Haiku 4.5, Sonnet 5, Opus 5, Fable 5), jeweils eigener Preis. `compute_cost(usage, model=None)` und `stream(..., model=None)` schlagen den Preis/das Modell hier nach statt fest auf Sonnet verdrahtet zu sein — sonst wäre die Kostenanzeige bei den anderen drei Modellen falsch.
- **Sonderfall Fable 5:** lehnt `thinking={"type":"disabled"}` mit HTTP 400 ab (denkt immer). `stream()` erzwingt für dieses Modell immer `{"type":"adaptive"}` + `max_tokens=16000`, unabhängig vom `thinking`-Argument.
- Neue WS-Nachricht `set_llm_model` (`protocol.py`), `pipeline.set_model()` validiert gegen `MODEL_CATALOG` und ignoriert ungültige Werte still (analog zu `set_thinking`).
- **Nebenbei gefundener Bug (nicht von Simon gemeldet, beim Lesen von `_run_llm()` vor dieser Änderung entdeckt):** `stop_reason` kannte bisher nur `end_turn`/`tool_use` — `refusal` (Sicherheits-Ablehnung, bei Fable 5 real möglich) wurde nicht behandelt und hätte die Tool-Loop mit unveränderten `client_messages` endlos denselben Request wiederholen lassen. Jetzt wie `end_turn` behandelt (Turn beendet, Platzhaltertext falls `turn_text` leer). War vorher nur ein theoretisches Risiko, mit Fable 5 als wählbarem Modell real.

**Frontend (jarvis-web):** natives `<select>` neben dem 🧠-Thinking-Toggle in `ChatView.vue`, vier `<option>`s mit `title`-Attribut als Hover-Info (Preis + Charakterisierung je Modell), gespiesen aus `LLM_MODELS` (`stores/jarvis.js`, gleiche vier Einträge wie `llm.py`s `MODEL_CATALOG` — bei Preisänderungen beide Stellen pflegen). Thinking-Toggle wird bei Fable-5-Auswahl deaktiviert (clientseitig, Backend erzwingt es ohnehin serverseitig).

Verifiziert: Fake-Client-Testskript (Fable-5-Override, Opus-5 disabled-thinking, unbekanntes Modell fällt auf Sonnet 5 zurück, `compute_cost()` liefert korrekte, unterschiedliche Kosten pro Modell), `npm run build` für jarvis-web sauber.

---

## 🔴 Dokument-Export: PDF/Word aus Projekten/Seiten (2026-07-26) ✅

**Anlass:** Simon: *"Ich brauche ein Tool für die App. Jarvis sollte das aber allgemein können am besten. Aus den Projekten bzw. den Seiten, muss ich eine PDF oder ein Word Dokument generieren können."* — explizit als generische LLM-Fähigkeit gewollt (jederzeit per Zuruf im Chat auslösbar), nicht als hartkodierter Export-Button in einer einzelnen Frontend-View.

**Neues Tool `generate_document`** (`tools.py`) — `quelle_typ("projekt"|"seite"), quelle_id, format("pdf"|"docx")`. Baut auf `services/document_export.py` (neu):
- Bei `"projekt"`: Beschreibung + Notizen + alle Unterseiten (rekursiv) zu einem Dokument zusammengefasst. Bei `"seite"`: die Seite selbst + ihre Unterseiten.
- Ein gemeinsamer, bewusst einfacher Markdown-Block-Parser (Überschriften/Absätze/Bullet-Listen/**Bold**) speist zwei Renderer — `reportlab` für PDF, `python-docx` für Word. Kein HTML-Zwischenschritt, keine Systemabhängigkeiten (anders als z.B. weasyprint/pandoc) — beide Libraries sind pip-only, passt zum "kein Docker"-Grundsatz.
- Kleiner, beim Testen selbst gefundener Bug: wenn eine Seite ihren Inhalt mit einer Überschrift beginnt, die den eigenen Titel wiederholt (verbreitetes Muster), stand der Titel doppelt im Dokument (einmal als Section-Heading, einmal als erster Content-Block) — `_blocks_for_section()` verwirft eine führende Überschrift, die dem Section-Titel entspricht.

**Auslieferung:** `tools.execute()` bekommt einen neuen optionalen `emit`-Parameter (`pipeline.py` reicht `self._emit` durch) — das generierte Dokument geht als eigene `document_ready`-WS-Nachricht (Base64) direkt an den aufrufenden Client, nicht im `tool_result`-Text (der bliebe sonst als riesiger Blob dauerhaft im Gesprächsverlauf hängen). jarvis-web (`stores/jarvis.js`) löst daraus einen Browser-Download aus (Blob + synthetischer `<a>`-Klick). Funktioniert nur im Web-Chat — Sprach-Clients haben keinen Weg, eine Datei entgegenzunehmen.

**Deployment-Falle gefixt, bevor sie zuschlagen konnte:** `scripts/auto_update.sh` pullte bisher nur `git pull`, ohne je `pip install -r requirements.txt` nachzuziehen — neue Dependencies (hier `python-docx`/`reportlab`) wären auf dem HP-Server nie installiert worden, der nächste Neustart hätte mit `ImportError` den kompletten Server lahmgelegt. Jetzt läuft `pip install -q -r requirements.txt` direkt nach einem erfolgreichen Pull mit.

Verifiziert: Standalone-Testskript gegen monkeypatchte `local_data`-Funktionen (kein Zugriff auf echte Daten) — Projekt- und Seiten-Export für beide Formate, DOCX-Inhalt per `python-docx`-Rückparsen geprüft (alle erwarteten Texte/Abschnitte vorhanden), PDF-Bytes-Header verifiziert und visuell per Rendering geprüft (Überschriften/Bullets/Bold/verschachtelte Unterseiten korrekt, keine doppelten Titel mehr nach dem Fix), Fehlerfälle (unbekanntes Projekt/Format) werfen korrekt `ValueError`. `npm run build` für jarvis-web sauber.

---

## 🔴 Dokument-Export: Markdown-Lücken + Direkt-Button an Projekten (2026-07-26, gleicher Tag) ✅

**Anlass:** Simon testete den frisch gebauten Export mit echtem Seiten-Content (Screenshots eines realen PRD-Dokuments) und meldete zwei Lücken: *"Das Dokument hat aber teilweise noch so Markdown Elemente. Und Tabellen werden auch nicht übernommen."* Sichtbar im PDF: rohe `---`-Zeilen, rohe `> Zitat`-Präfixe, rohe `*kursiv*`-Sternchen, und eine ganze GFM-Pipe-Tabelle als unformatierter `| a | b |`-Text.

**Markdown-Parser erweitert** (`services/document_export.py`, `_parse_blocks`/`_split_inline`):
- Horizontale Linie (`---`/`***`/`___` allein auf einer Zeile) → eigener Block-Typ `hr`, gerendert als `HRFlowable` (PDF) bzw. Absatz mit unterem Rahmen via rohem OOXML (`_add_horizontal_rule`, Word hat kein eingebautes HR-Element).
- Zitat (`> Text`, mehrzeilig zusammengeführt) → Block-Typ `quote`, kursiv + eingerückt (PDF: eigener `ParagraphStyle`; Word: eingebaute `"Quote"`-Formatvorlage, Fallback auf manuellen Einzug falls das Template sie nicht enthält).
- GFM-Pipe-Tabellen (Kopfzeile + `|---|---|`-Trennzeile + Datenzeilen) → Block-Typ `table`, gerendert als echte `reportlab.Table` (PDF, mit grauem Grid + fett/grau hinterlegter Kopfzeile) bzw. `document.add_table()` (Word, Formatvorlage `"Table Grid"`).
- Inline-Kursiv (`*Text*`/`_Text_`) zusätzlich zu **Bold** — `_split_inline()` ersetzt `_split_bold()`, Regex-Alternation prüft `**fett**` immer vor `*kursiv*` (sonst würde ein Bold-Sternchenpaar als zwei Kursiv-Marker fehlinterpretiert).

**Direkter Export-Button an Projekten** (ohne Chat/LLM): Simon wollte zusätzlich *"ein Download direkt bei den Projekten, also ein Knopf"* — nicht jedes Mal erst tippen müssen. Neuer WS-Typ `generate_document_request` (Client→Server, Layer 1 DATA analog zu `entity_action`/`data_request` — `server.py` ruft `document_export.generate()` direkt über den Executor auf, kein LLM-Turn nötig) liefert dieselbe `document_ready`-Antwort wie der Chat-Tool-Pfad, das Frontend braucht dafür keinen zweiten Handler. Zwei neue Hover-Buttons "PDF"/"Word" in `ProjektItem.vue`, `stores/jarvis.js` bekommt `generateDocument(quelleTyp, quelleId, format)`.

Verifiziert: neues Standalone-Testskript mit Content, der die genauen aus den Screenshots gemeldeten Muster nachbildet (Tabelle, Zitat, hr, Inline-Kursiv+Bold gemischt) — DOCX per Rückparsen geprüft (keine rohen `**`/`*`/`>`/`---`-Marker mehr im Text, Tabellenzellen exakt), PDF visuell gerendert und geprüft. Button-Pfad per Playwright gegen den echten laufenden jarvis-web-Dev-Server geklickt (Konsole fehlerfrei) — der volle Server-Roundtrip war zu diesem Zeitpunkt noch nicht live testbar, weil die Backend-Änderungen dafür erst per Auto-Update/manuellem Pip-Install auf den HP-Server müssen (siehe Abschnitt oben).

---

## 🔴 Dokument-Export: Button an jeder Seite statt nur an Projekten (2026-07-26, gleicher Tag) ✅

**Anlass:** Deutliches Feedback von Simon: *"Okay jetzt hast du irgendwie komplett dumm implentiert. Warum nur bei der Projekt übersicht und warum nur PDF? Mach doch einfach IN jeder Seite ein Download Button, sodass ich auch einzelne Seiten herunterladen kann. Und immer Beides, PDF und Word."* Der Button saß bis dahin nur in der Projekte-**Liste** (`ProjektItem.vue`) — eine einzelne Unterseite (z.B. ein PRD unter einem Projekt) ließ sich darüber gar nicht exportieren, nur das ganze Projekt.

**Backend generalisiert** (`services/document_export.py`, `_sections_for()`): nutzt jetzt `local_data.get_seite_view(typ, id)` — exakt dieselbe Funktion, die `SeiteView.vue` im Frontend zum Anzeigen verwendet — statt einer eigenen, nur auf Projekte zugeschnittenen Lookup-Logik. Ein einziger Codepfad für alle vier Quelltypen (`projekte`/`todos`/`kontakte`/`seite`) statt zwei Sonderfällen. `quelle_typ`-Werte umbenannt von `"projekt"` auf `"projekte"` (gleiche Konvention wie überall sonst im System, z.B. `data_query`s `database`-Parameter) — die Vereinheitlichung kam quasi kostenlos mit, weil `get_seite_view()` schon alles abdeckt, worüber das Frontend sowieso navigiert (Todos/Kontakte eingeschlossen, nicht nur Projekte, obwohl nicht explizit gefordert — aber der naheliegende nächste Schritt bei dieser Generalisierung).

**Frontend:** PDF/Word-Buttons jetzt zusätzlich direkt in `SeiteView.vue` (nicht nur in der Projekt-Liste) — funktioniert für jede dort angezeigte Seite: Projekt-Wurzelseite, Todo, Kontakt, oder eine einzelne Unterseite, alles über denselben generischen `exportDoc(format)`, der `page.value.typ`/`page.value.id` direkt weiterreicht. Immer beide Formate als zwei Buttons nebeneinander, nie nur eines. Die Liste-Buttons in `ProjektItem.vue` bleiben zusätzlich bestehen (einzige Möglichkeit, ein Projekt OHNE eigene Unterseiten zu exportieren, das hat ja keinen anklickbaren Link zu `SeiteView.vue`).

Verifiziert: erweitertes Standalone-Testskript deckt jetzt alle vier `quelle_typ`-Werte ab (inkl. Regressionscheck, dass Projekt-Beschreibung weiterhin mit exportiert wird, da `get_seite_view()` sie separat in `meta` statt in `inhalt` liefert). Playwright-Screenshot bestätigt: Buttons erscheinen korrekt sowohl auf der Projekt-Wurzelseite als auch beim Öffnen einer einzelnen Unterseite, sauber rechtsbündig gruppiert (kein CSS-Bug durch mehrere `margin-left:auto`-Elemente im selben Flex-Container). `npm run build` sauber.

---

## 🔴 Finanzen-Übersicht in Tracking (geschätzter vs. tatsächlicher Gewinn) (2026-07-27) ✅

**Anlass:** Simon wollte das Tracking-System noch mal anschauen — *"Ich will Dynamisch Statistiken anzeigen lassen, je nach Thema... ich will eine Statistik haben mit allen Gewinnen und auch mögliche Gewinne basierend auf planende Projekte. Auch sport soll getrackt werden."* Vorab-Check ergab: `tracking.py` ist bereits vollständig topic-generisch (Sport lief schon vor dieser Änderung produktiv, ganz ohne Code-Anpassung — bestätigt live: echte Einträge "5km"/"1.4km" unter `topic="sport"`). Es fehlten nur zwei Dinge: (1) eine Datenquelle für "geschätzte/mögliche Gewinne aus planenden Projekten" — Projekte hatten keinen Wertfeld — und (2) eine Kombination aus geschätzt + tatsächlich in der UI. Auf Simons Bestätigung hin bewusst minimal gehalten (*"Zukünftig kann das ausgebaut werden, aber das reicht"*) — keine allgemeine Chart-Library eingeführt, da laut `dataviz`-Skill-Leitlinie "ein paar Kennzahlen" ein KPI-Stat-Tile-Layout verdienen, keinen Chart.

**Neues Feld `projekte.geschaetzter_wert`** (`local_data.py`, REAL, per `_ensure_column`) — geschätzter Auftragswert. Durchgängig verdrahtet: `list_projekte()`/`query()`/`add_projekt()`/`update_projekt()`/`write()`, `_ENTITY_FIELDS["projekte"]` (server.py, für den `entity_action`-Pfad), `data_write`-Tool-Beschreibung.

**Konvention `topic="finanzen", key="gewinn"`** (`tracking.py`, per `log_entry`) für realisierte Gewinne — Gegenstück zum geschätzten Wert an Projekten.

**Neue Server-Resource `finanzen_overview`** (`server.py::_handle_data_request`) — kombiniert beide Quellen: Summe `geschaetzter_wert` über alle nicht abgeschlossenen Projekte (Status `Erledigt`/`Archiviert` ausgeschlossen, gleiche Konvention wie `list_todos()`/`context.py`, sonst würde ein längst abgerechnetes Projekt doppelt zählen) + Summe/Verlauf der `finanzen`/`gewinn`-Logs. `"finanzen"` deshalb aus dem generischen `tracking_topics`-Ergebnis ausgeschlossen (wie `"coding_engine"`) — eigene dedizierte Ansicht statt generischer Topic-Karte.

**Frontend:** neuer Finanzen-Block in `TrackingView.vue` — zwei Stat-Tiles ("Geschätzter Gewinn"/"Tatsächlicher Gewinn", gleiches Muster wie die bestehenden Chat/Coding-Kosten-Tiles) plus zwei Breakdown-Listen (Projekte mit Schätzung, realisierte Gewinn-Einträge). `ProjektItem.vue`/`SeiteView.vue` zeigen `geschaetzter_wert` zusätzlich als Tag, Edit-Formular bekommt ein neues Zahlenfeld.

**Nebenbei gefundener Bug (nicht von Simon gemeldet, beim Live-Testen der neuen Tracking-View aufgefallen):** `store.requestData()` (jarvis-web) matcht ausstehende Antworten nur über den `resource`-Namen, ohne Request-Id — `TrackingView.vue`s bestehender Code feuerte für mehrere Topics gleichzeitig `Promise.all(topics.map(t => requestData('tracking_progress', {topic: t})))` ab, was sich bei ≥2 Topics gegenseitig im internen Callback-Map überschreibt: eine Anfrage bekommt die falschen Daten, die andere hängt bis zum 10s-Timeout fest. Live reproduziert (Tracking-Seite blieb mit den echten Topics "chat"+"sport" dauerhaft auf "Lädt…" hängen). Gefixt durch sequenzielles statt paralleles Abfragen in `TrackingView.vue` — die naheliegendere generelle Lösung (Request-Ids im Protokoll) wäre eine größere, hier nicht gerechtfertigte Änderung an gemeinsam genutzter Infrastruktur gewesen.

Verifiziert: Standalone-Testskript gegen `local_data`/`tracking` (Projekte mit/ohne Schätzung, Status Erledigt korrekt ausgeschlossen, Aggregation stimmt). Playwright gegen den echten laufenden jarvis-web-Dev-Server: Edit-Feld rendert korrekt, Tracking-Seite lädt nach dem Fix zuverlässig und zeigt Chat+Sport mit korrekt zugeordneten (nicht mehr vertauschten) Daten. Finanzen-Block selbst noch nicht live sichtbar getestet — Backend-Teil braucht wie bei den vorherigen Änderungen erst das Auto-Update/manuelle Pip-Install auf dem HP-Server.

---

## 🔴 data_query('projekte') schnitt Listen silently bei 10 ab (2026-07-27) ✅

**Anlass:** Simon: *"Ich glaube Jarvis hat keine Möglichkeit alle Projekte zu ziehen oder?"* — Verdacht bestätigt: `tools.py`s `data_query`-Ausführung reichte `tool_input.get("limit", 10)` durch, `local_data.query()` hatte ebenfalls einen harten Default von `10` für beide Datenbanken. Bei mehr als 10 Projekten (Simon hat aktuell ~10, war also bereits knapp am Rand) hätte JARVIS bei "zeig mir alle Projekte" oder "wie viele Projekte habe ich" silently nur einen Ausschnitt gesehen, ohne jeden Hinweis dass die Liste unvollständig ist.

**Fix:** `local_data.query()`s `limit` ist jetzt `None`-fähig mit einem pro-Datenbank sinnvollen Default statt einem einzigen harten Wert für beide — `todos` bleibt bei 10 (kann über Jahre auf sehr viele Einträge anwachsen, der niedrige Default ist dort ein bewusster Kosten-Filter), `projekte` bekommt 200 (eine kleine, begrenzte Liste — Simons ~10 aktive Projekte passen locker rein, 200 ist praktisch "alle"). `tools.py` reicht `limit` jetzt unverändert durch (`tool_input.get("limit")`, kein hartes `10` mehr) statt den datenbankspezifischen Default zu überschreiben. Tool-Beschreibung ergänzt: JARVIS soll bei explizitem "wirklich ALLE"-Wunsch trotzdem einen hohen Wert wie 500 setzen, als zusätzliche Absicherung.

Verifiziert: Standalone-Testskript gegen 15 angelegte Test-Projekte + 15 Test-Todos — `query("projekte")` ohne `limit` liefert jetzt alle 15 (vorher wären nur 10 gekommen), `query("todos")` bleibt unverändert bei 10, expliziter `limit`-Wert wird weiterhin respektiert. Einziger Call-Site von `local_data.query()` ist `tools.py` — geprüft, keine weiteren Aufrufer betroffen.

---

## 🔴 Todos verschwanden nach dem Bearbeiten + Kosten/Statistik getrennt (2026-07-27, gleicher Tag) ✅

**Anlass:** Simon: *"Ich kann gewisse Werte nicht updaten oder eintragen, bei eigentlich allen Einträgen auf der App... Bitte gehe einmal nochmal durch, auch Todos etc."* — plus der Wunsch, LLM-Verbrauchskosten und allgemeine Statistiken (Sport etc.) getrennt darzustellen statt beides auf einer Seite.

**Live reproduzierter Bug (Kernursache für "Updates verschwinden"):** Ein Test-Todo ohne Datum anlegen, im Edit-Formular irgendein Feld ändern und speichern (`entity_action_ack` kam korrekt mit `ok:true` zurück) — danach war das Todo aus der Liste komplett verschwunden, obwohl die DB-Zeile weiter existierte. Ursache: `TodoItem.vue`s Edit-Formular befüllt ein bisher leeres `<input type="date">` beim Öffnen mit `''` statt `null` (`form.datum = props.todo.datum || ''`) und schickt das beim Speichern unverändert mit, unabhängig davon welches Feld man eigentlich ändern wollte. `local_data.update_todo()` schrieb dieses `''` bisher unverändert in die Spalte. `list_todos()`s Filter `datum IS NULL OR datum >= cutoff` greift bei `datum=''` auf **keinen** der beiden Zweige (Leerstring ist weder NULL noch `>=` irgendeinem echten `YYYY-MM-DD`-Datum, da lexikographisch kleiner) — die Zeile fiel dadurch aus jedem `list_todos()`-Ergebnis, dauerhaft, bis jemand das `datum`-Feld manuell wieder auf einen echten Wert setzt. Sah aus wie Datenverlust, war aber ein reiner Sichtbarkeits-Bug.

**Fix (`local_data.py`):**
- Neue Funktion `_normalize_fields()` — wandelt `""` zu `None` in jedem Update-Dict. Angewendet in `update_todo()`, `update_projekt()`, `update_kontakt()` und `write()` (LLM-`data_write`-Pfad, als Absicherung falls Claude mal explizit `""` statt das Feld wegzulassen schickt). **Bewusst nicht** in `update_seite()` — `seiten.titel` ist `NOT NULL`, dort würde die Normalisierung einen echten Fehler erzeugen statt einen zu verhindern.
- **Einmalige Reparatur-Migration** in `_get_db()`: `UPDATE todos SET datum = NULL WHERE datum = ''` — repariert bereits bestehende, auf diese Weise unsichtbar gewordene Todos automatisch beim nächsten Connect (idempotent, kein Aufwand nach dem ersten erfolgreichen Lauf).

**Kontrolliert, ob dieselbe Fallenkonstruktion woanders lauert:** `list_projekte()`/`list_kontakte()` haben keine vergleichbare Datums-Cutoff-Filterung — das exakte Katastrophen-Muster (Zeile verschwindet dauerhaft) ist auf `todos.datum` beschränkt. Die `geschaetzter_wert`-Meldung ("Budget/Einkommen nicht eintragbar") hat eine andere, unabhängige Ursache: dieses Feld ist serverseitig noch gar nicht deployed (siehe Deployment-Hinweise weiter oben) — kein Code-Bug, sondern der übliche Auto-Update-Verzug.

**Kosten vs. allgemeine Statistik getrennt** (zweiter Teil der Anfrage): Neue eigene Seite **Kosten** (`KostenView.vue`, Route `/kosten`, Sidebar-Icon 💰) — enthält die bisher auf der Tracking-Seite mitlaufenden LLM-Kosten-Kacheln (Chat/Coding-Engine/Gesamt heute) + `CodingEngineUsage`. Dabei aufgefallen: das generische `tracking_topics`-Ergebnis enthielt zusätzlich einen `"chat"`-Topic mit den rohen `cost_usd`-Log-Einträgen aus `pipeline.py` — dieselbe Datenquelle wie die "Chat heute"-Kachel, nur als Rohliste zwischen Sport & Co. einsortiert. `"chat"` jetzt zusätzlich zu `"coding_engine"`/`"finanzen"` aus dem generischen `tracking_topics`-Ergebnis ausgeschlossen (`server.py`) — die "Tracking"-Seite zeigt jetzt ausschließlich allgemeine Lebens-Statistiken (Sport etc.) plus die Finanzen-Übersicht, LLM-Kosten leben komplett auf der neuen Kosten-Seite.

Verifiziert: Standalone-Testskript reproduziert den Bug gezielt (Todo direkt per Raw-SQL auf `datum=''` korrumpiert, bestätigt Verschwinden aus `list_todos()`), bestätigt dass die Reparatur-Migration es beim nächsten `_get_db()`-Aufruf zurückholt, und dass `update_todo()`/`write()` `datum=''` jetzt korrekt zu NULL normalisieren statt den Bug erneut zu erzeugen. Playwright gegen den echten laufenden jarvis-web-Dev-Server bestätigt: neue Kosten-Seite zeigt korrekt die Kosten-Kacheln, Tracking-Seite zeigt nach dem Split keine LLM-Kosten-Kacheln/Charts mehr (nur noch Topic-Karten + Finanzen-Block), keine Konsolenfehler. Die `"chat"`-Ausschluss-Änderung selbst ist Backend-Code und läuft (wie alle Server-Änderungen heute) erst nach Auto-Update/Pip-Install auf dem HP-Server live — im Test war "Chat" auf der alten, noch nicht aktualisierten Server-Version deshalb weiterhin als Topic-Karte sichtbar, das ist erwartet. `npm run build` sauber.

---

## 🔴 Projekt-Edits raus aus der Liste, rein in die Detail-Seite (2026-07-27, gleicher Tag) ✅

**Anlass:** Simon: *"Warum machst du alle Edits direkt in der Projekt Übersicht??? Mach sie doch IN den Projekten. Alles was man editieren kann raus aus der Liste und rein in die Detail Seite."* — `ProjektItem.vue` hatte bis dahin ein komplettes Inline-Edit-Formular (Name/Status/Typ/Beschreibung/`geschaetzter_wert`), das sich direkt in der Listenzeile aufklappte, während `SeiteView.vue` (die eigentliche Detail-Seite) nur den Freitext-Inhalt (`notizen`) bearbeiten konnte — und war zudem nur für Projekte mit Unterseiten überhaupt anklickbar.

**Frontend-Umbau (jarvis-web, kein Backend betroffen):**
- `ProjektItem.vue`: komplettes Inline-Formular entfernt (kein `editing`-State, kein `startEdit()`/`save()`, kein Bleistift-Button mehr). Der Projektname ist jetzt **immer** ein Link zur Detail-Seite (`/seite/projekte/:id`) — vorher nur wenn das Projekt bereits Unterseiten oder eine `externe_id` hatte, sonst gab es gar keinen Weg zur Detail-Seite. Liste zeigt jetzt nur noch: Name (Link), Status/Typ/Wert-Tags (read-only), Beschreibung, PDF/Word-Export, Löschen — alles Nicht-Editierende bleibt, alles Editierende ist raus.
- `SeiteView.vue`: Bearbeiten-Formular erweitert — bei `page.typ === 'projekte'` erscheinen zusätzlich zum bestehenden Notizen-Textfeld Eingabefelder für Name/Status/Typ/`geschaetzter_wert`/Beschreibung, alle in einem gemeinsamen `entity_action`-Update-Call gespeichert. Für Todos/Kontakte/einzelne Unterseiten unverändert (nur Notizen-Bearbeitung, kein Scope-Creep — nicht angefragt).
- `ProjekteView.vue`: totes `edit()` + `@save`-Wiring entfernt (kein Konsument mehr).

Verifiziert: Playwright-Testlauf gegen den echten laufenden jarvis-web-Dev-Server — Test-Projekt angelegt, bestätigt dass kein Bleistift-Button mehr in der Liste existiert, per Klick auf den Namen zur Detail-Seite navigiert, dort Status/Wert/Beschreibung/Notizen gemeinsam gesetzt und gespeichert, nach vollem Seiten-Reload (nicht nur lokaler State) alle vier Werte weiterhin korrekt sichtbar. `npm run build` sauber.

---

## 🔴 Gewinn-Trend-Chart + Pipeline/Hochrechnung + Datenbereinigung (2026-07-27) ✅

**Anlass:** Feature-Spec kam von JARVIS selbst (Simon hat sie 1:1 weitergereicht) — Trend-Chart für die Finanzen-Übersicht mit echter Pipeline (bekannte Projektabschlüsse) und Hochrechnung für den Rest, plus eine dringende Datenbereinigung (3 doppelt zählende Gewinn-Einträge). Simons ausdrücklicher Zusatz: *"sehe das nicht als spezielles Financing Feature, das soll generell abbildbar sein"* — die generische Zeitreihen-Aggregation muss wiederverwendbar bleiben, nicht Finanzen-spezifisch fest verdrahtet.

**Architekturentscheidung für die Generalisierung:** neues, eigenständiges Modul `finanzen.py` — kombiniert `local_data` (Projekte) mit `tracking.py` (Logs), aber `tracking.py` selbst bleibt komplett domänen-neutral. Neue generische Funktionen dort: `get_monthly_series(topic, key, months)` (Summe pro Kalendermonat für eine beliebige Monatsliste, kein Finanzen-Bezug) und `delete_log(entry_id)` (Log-Eintrag per id löschen, jedes Topic). Die Finanzen-spezifische Komposition (Pipeline aus Projekt-Abschlussdaten, Hochrechnung, kumulierte Summe) sitzt bewusst in `finanzen.py`, nicht in `tracking.py` — jemand könnte `get_monthly_series()`/`delete_log()` morgen genauso gut für einen Sport- oder Ausgaben-Trend nutzen.

**1. Datenbereinigung (zuerst, wie von Simon verlangt):** Live-Daten vor dem Löschen per Read-only-Check exakt verifiziert (nicht blind auf die Beschreibung verlassen) — bestätigt drei pauschale Alt-Einträge (Datum = Anlage-Tag 26.07.2026, nicht echtes Zahlungsdatum): knowHere Theme 6.800€, Ticketsystem 10.900€, Michelle Webseite 0€. Diese wurden durch feiner aufgeteilte, korrekt datierte Einträge ersetzt (knowHere: 4.800€/2025-03-03 + 800€/2025-05-05 + 1.200€/2025-10-08 = 6.800€; Ticketsystem: 10.000€/2025-11-08 + 900€/2025-12-11 = 10.900€ — beide Summen exakt bestätigt). Bereinigung als einmalige, geloggte Migration in `tracking.py`s `_get_db()` — Löschung per **exaktem id-Match** der drei konkreten UUIDs (nicht per Datum/Wert-Kriterium), damit garantiert nie ein unabhängiger künftiger Eintrag mit zufällig gleichem Datum/Wert betroffen ist. Live-Test mit einem extra "unrelated"-Log am selben Datum bestätigt: übersteht die Bereinigung unangetastet.

**2. Neues Feld `projekte.erwartetes_abschlussdatum`** (YYYY-MM-DD) — für bekannte Pipeline-Projekte. Durchgängig verdrahtet wie `geschaetzter_wert` vorher (list/add/update/write/`_ENTITY_FIELDS`/Tool-Beschreibung).

**3. `finanzen.compute_overview()`:** jetzt zusätzlich `gesamtpotenzial` = geschätzter + tatsächlicher Gewinn, neben den beiden bestehenden Einzelwerten.

**4. `finanzen.compute_trend(months, today)`:** Fenster **symmetrisch um den aktuellen Monat** (12 → 6 zurück + aktuell + 5 voraus; 24 → 12+12) — zeigt also immer sowohl Vergangenheit als auch Zukunft. Vergangene/aktuelle Monate: reale Summe. Zukünftige Monate mit bekanntem `erwartetes_abschlussdatum` (nicht abgeschlossene Projekte): deren `geschaetzter_wert` als Pipeline. Übrige Zukunftsmonate: Hochrechnung = Gesamtsumme bisheriger Gewinne ÷ Monate seit ältestem Eintrag. Laufende kumulierte Summe übers ganze Fenster. Neue Server-Resource `finanzen_trend` (`months`-Param, nur 12/24 gültig).

**5. `TrendChart.vue`** (neu, `dataviz`-Skill-Leitlinien angewendet) — hand-gerolltes SVG (kein neuer Chart-Library-Dependency, passt zur "einfacher Deploy"-Linie): ein Balken pro Monat (Ist/Pipeline/Prognose farblich unterschieden, Prognose zusätzlich gestrichelt+abgeschwächt), überlagerte kumulierte Linie (gestrichelt im Prognose-Bereich), **eine** gemeinsame Y-Achse (nie zwei Skalen), sparsame Achsenbeschriftung, Legende, Hover/Fokus-Detailzeile mit allen Werten des angeklickten Monats (kein reines Line-Hover-Gefummel), 12/24-Monats-Umschalter. Als generische, wiederverwendbare Komponente gebaut (Props: `title`, `data`, `months`, `avgPerMonth`), nicht Gewinn-Text hartcodiert im Kern.

**Live-Deployment-Erkenntnis:** der `auto_update.sh`-Timer hatte in der Zwischenzeit tatsächlich bereits mehrere frühere Commits dieser Session automatisch auf den HP-Server gezogen (bestätigt per Live-Read gegen die reale `finanzen_overview`-Resource) — die "Backend braucht erst Deploy"-Einschränkung der letzten Runden ist also kein Dauerzustand, nur eine Verzögerung von einigen Stunden bis zum nächsten Idle-Fenster.

**Erledigt (Nachtrag):** `erwartetes_abschlussdatum` für die zwei echten Pipeline-Projekte live gesetzt (halbautomaten – WordPress Relaunch → 2026-10-15; Digital Mindset → 2026-12-15), per direktem `entity_action`-Write gegen den realen Server verifiziert, `finanzen_trend` zeigt seitdem korrekt beide Projekte in der Pipeline.

Verifiziert: `finanzen.py` per Standalone-Testskript gegen die realen Zahlen nachgebaut und Hochrechnung von Hand nachgerechnet (17.700€ / 17 Monate = 1.041,18€/Monat, exakt bestätigt), 12- und 24-Monats-Fenster-Grenzen geprüft, Erledigt-Projekte korrekt von der Pipeline ausgeschlossen. Bereinigungs-Migration gegen nachgebaute Testdaten verifiziert (inkl. "unrelated same-date entry survives"-Check). `TrendChart.vue` per Playwright mit realistischen Testdaten visuell geprüft (Balken/Linie/Legende/Hover/12↔24-Umschaltung), ein echter Label-Kollisions-Bug dabei gefunden und gefixt (letzte zwei Monatsbeschriftungen überlappten). `npm run build` sauber.

---

## 🔴 SevDesk-Import: Rechnungen & Ausgaben, `list_log_entries`-Tool (2026-07-27) ✅

**Anlass:** Simon exportiert Rechnungen/Ausgaben aus SevDesk manuell als CSV (echte API kostet 10€/Monat extra, daher CSV statt API). Ziel: Import an zwei Stellen (Chat-Upload wie bei Bildern, dedizierte Seite mit voller UI), Rechnungen mit Projekten verknüpft, Statistik (Finanzen-Übersicht) auf echte Zahlen umgestellt. Simons expliziter Qualitätsanspruch: *"Bitte nicht wieder so ein halbes UI, was ich nicht bearbeiten und nutzen kann"* — volles CRUD, kein Read-only-Stub.

**0. `list_log_entries`-Tool** (`tools.py`) — Lücke aus vorheriger Runde geschlossen: `get_progress` zeigt nur Ziele + letzten Wert, kein `get_logs`-Äquivalent mit ids existierte. Jetzt vor `delete_log_entry` aufrufbar, um gezielt eine id zu finden statt zu raten.

**1. Datenmodell** (`local_data.py`) — zwei neue Tabellen `rechnungen`/`ausgaben`, jeweils mit SevDesks eigener stabiler Nummer (`rechnungsnummer`/`belegnummer`, UNIQUE) als Identifier. Generischer `query()`/`_QUERY_META`-Dispatch (für `data_query`-Tool) und `_ENTITY_FIELDS`/`_ENTITY_ADD_FN`/`_ENTITY_UPDATE_FN`/`_ENTITY_DELETE_FN`-Dispatch-Dicts (`server.py`, ersetzt wachsende if/elif-Ketten) beide auf 6 Entitäten erweitert.

**2. `finanzen_import.py`** (neu) — Parser für beide SevDesk-Exportformate (`invoices.csv`: eine Zeile pro Rechnung; `voucher.csv`: Kopfzeile + Positionszeilen pro Beleg, hier zu einem flachen Datensatz zusammengefasst). Import ist **idempotent** (upsert per Rechnungsnummer/Belegnummer — wiederholter Import derselben/aktualisierten Datei erzeugt nie Duplikate) und **überschreibt nie eine bereits gesetzte `projekt_id`** bei erneutem Import.

**3. Projekt-Zuordnung bewusst nicht automatisch geraten:** an echten Daten bestätigt, dass ein Kunde mehrere, unterschiedlich benannte interne Projekte haben kann (z.B. "Halbautomaten Kommunikationsdesign Gmbh" least sowohl "Ticketsystem" als auch "knowHere Theme" als auch das separate "halbautomaten – WordPress Relaunch") — Kundenname → Projekt ist keine zuverlässige Heuristik. Stattdessen zwei Wege: manuelles Dropdown auf der neuen Rechnungen-Seite (immer sichtbar, keine Edit-Mode-Hürde) und `data_query`/`data_update` (beide LLM-Tools auf `rechnungen`/`ausgaben` erweitert) — JARVIS klärt offene Zuordnungen aktiv im Gespräch mit Simon.

**4. `finanzen.py` umgestellt:** "tatsächlicher Gewinn" kommt jetzt aus echten bezahlten Rechnungen minus echten bezahlten Ausgaben (`local_data.rechnungen`/`ausgaben`), nicht mehr aus den manuell per `log_entry` gepflegten `tracking.py`-Einträgen (`topic="finanzen"`) — sonst hätte echter Import zu Doppelzählung geführt (Stichprobe zeigte, dass die alten Log-Einträge exakt dieselben Rechnungen abbildeten). `tracking.py` bleibt unverändert für andere Topics (Sport etc.) nutzbar, nur `finanzen.py` liest nicht mehr daraus.

**5. Zwei Upload-Wege:**
   - **Dedizierte Seite** — neue "Buchhaltung"-Nav-Gruppe (🧾) mit `/rechnungen` + `/ausgaben`, je eigene View + Item-Komponente (voller CRUD: Add-Formular, Inline-Edit, Löschen), CSV-Upload-Button pro Seite. Neue Server-Resource `import_csv` (Layer-1 DATA, `resource`+`kind`+`csv_text`, läuft in `run_in_executor` wie die anderen Resources).
   - **Chat-Upload** — `.csv` zum Attachment-Picker hinzugefügt (`ChatView.vue`, gleicher Mechanismus wie Bilder). Serverseitig (`pipeline.py:_attachment_to_block`) ein Sonderfall **vor** der generischen `text/`-Behandlung: CSV läuft nicht als Rohtext in den LLM-Kontext, sondern direkt durch `finanzen_import.detect_and_import()` (erkennt Format am Header, ruft die passende Importfunktion) — Claude bekommt nur die fertige Zusammenfassung (created/updated/total, bei Rechnungen zusätzlich eine Vorschau unzugeordneter Einträge) und wird instruiert, die Projekt-Zuordnung aktiv mit Simon zu klären statt zu raten.

Verifiziert: Backend-Logik (Parser, Upsert-Idempotenz, `projekt_id`-Erhalt, `finanzen.py`-Neuberechnung, `detect_and_import`) gegen die echten bereitgestellten CSV-Dateien getestet, nicht gegen synthetische Daten (4 Rechnungen, 174 Ausgaben — Belegnummer-Anzahl unabhängig per `awk` gegengeprüft). Frontend per Playwright gegen einen lokalen Test-Server (echtes `local_data.py`/`finanzen_import.py`, isolierte `HOME`) voll durchgeklickt: leerer Zustand, manuelles Anlegen, CSV-Upload (Erfolgsmeldung + Zeilen erscheinen), erneuter Upload derselben Datei (0 neu, alle aktualisiert, keine Duplikate), Projekt-Zuordnung per Dropdown (persistiert, Zähler "ohne Projekt" sinkt korrekt), Inline-Edit (Notiz gespeichert und nach Reload noch da), Löschen — sowohl für Rechnungen als auch Ausgaben. `npm run build` sauber.

**Nachtrag (gleicher Tag): `gesperrt`-Feld auf Rechnungen/Ausgaben.** Simons Anlass: eine Rechnung, die manuell gepflegt wird bzw. nicht in SevDesk auftaucht, sollte durch einen künftigen CSV-Import nie überschrieben werden. Neue Spalte `gesperrt` (`INTEGER DEFAULT 0`) an beiden Tabellen (`_ensure_column`-Migration für bereits existierende Installs). `upsert_rechnung()`/`upsert_ausgabe()` überspringen eine gesperrte Zeile komplett (geben `None` statt der id zurück) — Import lässt sie unangetastet, egal welche Felder die CSV für diese Nummer mitbringt. `import_invoices()`/`import_vouchers()` zählen das separat als `skipped_locked`, beide Frontend-Seiten und der Chat-Upload-Pfad zeigen die Zahl in ihrer Zusammenfassung. UI: 🔒/🔓-Toggle-Button pro Zeile (`RechnungItem.vue`/`AusgabeItem.vue`, immer sichtbar statt nur bei Hover wie Edit/Löschen — Sperr-Status ist wichtiger Dauerzustand, kein spontaner Edit). `data_write`/`data_update` (`tools.py`) kennen `gesperrt` ebenfalls, damit JARVIS eine Zeile auf Zuruf sperren kann.

Wichtig zur Abgrenzung: eine Rechnung, deren `rechnungsnummer` gar nicht in der CSV vorkommt, wäre ohnehin nie betroffen gewesen (Upsert matcht nur Nummern, die im Import auftauchen) — das eigentliche Risiko war der Fall "Zeile existiert in beiden, wurde aber manuell korrigiert" (z.B. Betreff/Beträge von Hand angepasst), den re-importierte, unveränderte SevDesk-Werte sonst stillschweigend zurückgesetzt hätten.

Verifiziert: Playwright-Testlauf — Rechnung gesperrt, Betreff manuell auf einen abweichenden Text geändert, gleiche CSV erneut importiert (Meldung "1 gesperrt übersprungen", Zeile bleibt beim manuellen Text), danach entsperrt und erneut importiert (Zeile kehrt korrekt zum SevDesk-Wert zurück). `npm run build` sauber.

**Zur Frage "wie werden Projekte ohne Rechnungen berücksichtigt":** unabhängig vom ganzen Rechnungen/Ausgaben-System — `compute_overview()`s `geschaetzt_gesamt` zählt den vollen `geschaetzter_wert` jedes nicht abgeschlossenen Projekts, egal ob dafür überhaupt schon eine Rechnung existiert oder verknüpft ist. Die zwei echten Pipeline-Projekte (halbautomaten – WordPress Relaunch, Digital Mindset) zählen also unabhängig von jeglicher Rechnungs-Verknüpfung voll in `geschaetzt_gesamt`/`gesamtpotenzial` mit. Sobald ein Projekt teilweise in Rechnung gestellt und bezahlt ist, aber noch nicht auf "Erledigt"/"Archiviert" gesetzt wurde, zählt sein voller geschätzter Wert weiterhin UND die bereits bezahlten Rechnungen zählen zusätzlich in `tatsaechlich_gesamt` — ein bewusster Trade-off (kein automatisches "Rest-Schätzung minus bereits Bezahltes"), der bei Teil-Abrechnung zu doppelter Zählung in `gesamtpotenzial` führen kann. Sauberster Ausweg: Projekt auf "Erledigt" setzen, sobald es vollständig abgerechnet ist — dann fällt es aus `geschaetzt_gesamt` raus und nur noch die echten Rechnungen zählen.

**Bugfix (gleicher Tag): Ausgaben-Import hing/timeoutete bei echten Datenmengen.** Simon meldete: Rechnungen-Import (4 Zeilen) funktionierte, Ausgaben-Import (174 Zeilen aus der echten SevDesk-CSV) schlug mit generischem "Import fehlgeschlagen." fehl, weder Server-Logs noch Browser-Konsole zeigten einen Fehler. Root Cause per Live-Diagnose gefunden (direkte WS-Verbindung gegen den echten Server, `import_csv`-Resource mit der echten Datei angefragt): keine Exception, sondern ein echter **Timeout** — `upsert_rechnung()`/`upsert_ausgabe()` öffneten pro Zeile eine **eigene** SQLite-Connection (`_get_db()`, das bei jedem Aufruf die volle Schema-Migration mehrerer Tabellen durchläuft) inkl. eigenem Commit/fsync. Bei 174 Zeilen (je nach Neu-/Update-Fall bis zu 2 Connections pro Zeile) summierte sich das zu über 10 Sekunden — genau das Timeout-Limit von `store.requestData()` im Frontend. Kleine CSVs (4 Rechnungen, oder ein synthetischer 1-Zeilen-Testfall) blieben unauffällig schnell, das hat das Problem beim ersten Test mit den kleinen Beispieldateien verdeckt.

**Fix:** `add_rechnung`/`update_rechnung`/`add_ausgabe`/`update_ausgabe`/`upsert_rechnung`/`upsert_ausgabe` (`local_data.py`) haben jetzt einen optionalen `conn`-Parameter — ohne ihn identisches Verhalten wie vorher (eigene Connection, eigener Commit), bestehende Einzel-Aufrufe (manuelles Anlegen/Bearbeiten in der UI) sind unverändert. Zwei neue Funktionen `upsert_rechnungen_bulk(entries)`/`upsert_ausgaben_bulk(entries)` nutzen **eine** Connection und **einen** Commit für den kompletten Batch — `finanzen_import.import_invoices()`/`import_vouchers()` rufen jetzt diese statt der Einzel-Upserts in einer Schleife auf. Live-Messung (174 Ausgaben, isolierte Test-DB): vorher weit über 10s (Timeout), nachher **12ms** für den Erst-Import, **4ms** für einen Re-Import.

Verifiziert: Testrun gegen die reale 174-Zeilen-`voucher.csv` (Standalone-Skript, isolierte `HOME`) bestätigt Zeitmessung. Vollständiger Playwright-Regressionslauf (beide Testsuiten von vorhin — normaler CRUD-Flow inkl. CSV-Upload/Idempotenz/Projekt-Zuordnung, UND der Sperr-Feature-Testlauf) läuft nach dem Refactor unverändert grün. Backward-Compat einzeln geprüft: `add_rechnung`/`update_rechnung`/`add_ausgabe`/`update_ausgabe` ohne `conn`-Argument (wie von allen bestehenden Aufrufern genutzt) verhalten sich exakt wie vorher.

Zusätzliche Lehre für künftige Bulk-Importe: lokale Standalone-Tests gegen eine frische/kleine Test-DB hätten dieses Problem **nie** gezeigt (12ms dort von Anfang an) — nur der Live-Test gegen den echten Server mit einer realistisch großen CSV hat es aufgedeckt. Bei jedem künftigen Feature mit potenziell vielen Datensätzen (nicht nur wenigen Testzeilen) sollte mindestens einmal mit einer Datenmenge in der tatsächlich erwarteten Größenordnung gegen die echte Umgebung getestet werden, nicht nur gegen ein Handvoll synthetischer Beispiele.

---

## 🔴 Vorfall: `stop_reason=max_tokens` löste Endlos-Retry-Schleife aus, kostete zweistellig $ ✅ behoben (2026-07-28)

**Symptom:** Simon meldete live laufende JARVIS-Antworten, die sich in nahezu identischen, sich selbst korrigierenden Gedankengängen wiederholten ("Jetzt seh ich es klar...", "Jetzt hab ich den echten Kontext...", mehrfach fast wortgleich), ohne seine eigentlichen Fragen zu beantworten. Logs zeigten mehrere `[pipeline] LLM fertig: ~75-78s, stop=max_tokens` hintereinander. An diesem Tag bereits über $11 für vergleichsweise wenig normales Chatten verbraucht — bei Hochrechnung auf 30 Tage ein ernstes Kostenproblem.

**Root Cause, per Live-Diagnose gefunden** (direkte WS-Verbindung gegen den echten Server, da MCP zeitweise nicht erreichbar und `/Volumes/jarvis` durch ein anderes gemountetes Volume verdeckt war — Diagnose lief komplett gegen die Live-Instanz): `pipeline.py::_run_llm()`s `while True`-Loop kannte nur drei `stop_reason`-Fälle — `end_turn`/`refusal` (Turn beenden) und `tool_use` (Loop mit Tool-Ergebnissen fortsetzen). **`max_tokens` fiel durch beide Prüfungen durch** und die Schleife sprang einfach zurück an den Anfang — mit **exakt demselben, unveränderten `client_messages`**. Kein Fortschritt, keine Persistierung der (verworfenen) Antwort, keine Abbruchbedingung: der Request wurde identisch wiederholt, potenziell endlos, bis rein zufällig ein Call anders endete. `_run_llm()` gab dabei die Kontrolle nie an `process_text()` zurück, solange das anhielt — erklärt zusätzlich einen zweiten Befund: die serverseitig gespeicherte Session-Historie fror exakt beim letzten erfolgreich abgeschlossenen Turn ein, alle nachfolgenden (aber live gestreamten und abgerechneten!) Turns landeten nie in `sessions.db`.

Konkreter Auslöser in diesem Fall: ein Server-Neustart mitten in einer langen Konzept-Dokumentations-Session (`knowledge/jarvis/konzept_grundsatz.md`) hatte JARVIS fälschlich glauben lassen, Inhalt sei verlorengegangen (tatsächlich war die Datei vollständig intakt — bestätigt per Nachlesen). Beim Versuch, diesen (nicht existenten) Fehler zu korrigieren, geriet JARVIS in die oben beschriebene Schleife — jeder Retry "vergaß" den vorherigen gescheiterten Versuch komplett (der stand ja nie in der History) und leitete dieselbe Erkenntnis frisch neu her. Strukturell dieselbe Bug-Klasse wie der bereits gefixte `refusal`-Fall (Sonnet-5-Umstieg, 2026-07-25/26) — dort wurde derselbe Loop-Typ schon einmal für einen anderen `stop_reason`-Wert gefixt, `max_tokens` wurde dabei offenbar übersehen.

**Fix:** `max_tokens` wird jetzt wie `refusal` behandelt — abgeschnittene Antwort (mit Hinweis auf die Kürzung) an `client_messages`/History anhängen und den Turn sauber beenden, statt sie zu verwerfen und blind zu wiederholen.

Verifiziert mit gemocktem `llm.stream()` (vier Fälle): einzelner `max_tokens`-Treffer beendet den Turn nach genau 1 API-Call (Regressionsschutz gegen erneutes Retry eingebaut — der Test schlägt hart fehl, falls `llm.stream()` ein zweites Mal aufgerufen wird); normaler `end_turn`-Pfad unverändert (1 Call); `tool_use`→`end_turn`-Flow unverändert (2 Calls, bestehende Tool-Loop-Logik nicht angefasst); Edge Case `max_tokens` ganz ohne sichtbaren Text vor dem Limit (z.B. komplett von Thinking-Tokens verbraucht) ebenfalls sauber behandelt statt einer leeren `full_response`, die sonst fälschlich die gerade gestellte User-Frage aus der History gepoppt hätte.

**Noch offen:** Deployment auf den HP-Server (Auto-Update zieht neue Commits nur bei idlem JARVIS, siehe `scripts/auto_update.sh`) sowie die eigentliche Wissensdatenbank-Korrektur (falsche "Inhalt verloren"-Notiz in `jarvis/setup.md` entfernen, Datenhoheit-Punkt eigentlich nach `jarvis/konzept_grundsatz.md` verschieben, verwaiste Duplikat-Datei `jarvis_system/jarvis_konzept.md` bereinigen).

**Nachtrag, 2026-07-31 — zweite Ausprägung: Abbruch WÄHREND eines Tool-Aufrufs, Aufruf wird nie ausgeführt.** Simon meldete reproduzierbar (zweimal an derselben Stelle) eine Antwort, die mit "*(Antwort abgeschnitten — Token-Limit erreicht.)*" endete, statt einen erwarteten `write_knowledge`-Aufruf mit einem längeren Dokument auszuführen — der Aufruf lief nie. Bestätigt: der oben gefixte `max_tokens`-Zweig war bereits korrekt aktiv (die Meldung existiert nur genau dort im Code, ihr Auftreten belegt `stop_reason='max_tokens'` unmittelbar), behandelte den Fall aber wie jeden anderen Abbruch — ohne zu prüfen, ob der Abbruch mitten in einem `tool_use`-Block passierte.

- **Root Cause:** `content` eines `write_knowledge`-Aufrufs zählt gegen dasselbe `max_tokens`-Budget wie sichtbarer Text. `max_tokens` war bei 8096 (ohne Thinking) — knapp für ein längeres Dokument in einem Rutsch. Angehoben auf 24000 (ohne Thinking) / 30000 (mit Thinking bzw. Fable 5) — bleibt unter dem kleinsten unterstützten Modells (Haiku 4.5, max. 32000 Output-Tokens), Sonnet/Opus 5 erlauben deutlich mehr. Höheres `max_tokens` kostet nichts zusätzlich, solange nicht tatsächlich ausgeschöpft (Abrechnung nach generierten, nicht nach maximal erlaubten Tokens).
- **Erkennung nachgerüstet** (`pipeline.py::_find_truncated_tool_call()`): empirisch verifiziert (echter API-Call mit absichtlich sehr niedrigem `max_tokens`), dass die Anthropic-SDK bei einem abgebrochenen `tool_use`-Block nur die VOLLSTÄNDIG übertragenen Felder in `.input` übernimmt — ein noch nicht fertig generiertes Pflichtfeld fehlt einfach komplett (keine Exception, kein kaputter Wert). Ein `tool_use`-Block mit fehlendem Pflichtfeld (Abgleich gegen die jeweilige Tool-Schema aus `tools.DEFINITIONS`) ist damit ein zuverlässiges Signal für "nicht fertig generiert". Bei Erkennung: Tool-Name explizit in der Kürzungs-Notiz genannt ("... während eines Aufrufs von `write_knowledge`. Dieser Aufruf wurde NICHT ausgeführt.") statt der bisherigen generischen Meldung — sowohl für Simon als auch für JARVIS selbst im nächsten Turn eindeutig. Bewusst KEIN automatischer Retry (würde dieselbe Endlosschleifen-Gefahr wie oben zurückbringen, diesmal fürs Fortsetzen eines abgebrochenen JSON-Blobs — die Anthropic-API unterstützt das ohnehin nicht direkt).
- **Strukturelle Entlastung statt nur höherem Limit:** `write_knowledge` erzwang bisher den GESAMTEN Dokumentinhalt in einem einzigen Aufruf — bei einem sehr langen Dokument bleibt das Problem auch bei höherem `max_tokens` grundsätzlich bestehen. `knowledge.append_section()` existierte bereits intern, war aber nicht als LLM-Tool exponiert. Neu: `append_knowledge_section`-Tool (hängt einen Abschnitt an eine Datei an, legt sie an falls nötig).

**Nachtrag zwei, gleicher Tag:** der ursprünglich in `write_knowledge`s eigener Beschreibung ergänzte Hinweis auf `append_knowledge_section` griff in der Praxis kaum — das Modell schätzt die Länge eines Dokuments vorab selten ab, sondern fängt einfach an zu schreiben und läuft ins Limit, der vorbeugende Hinweis kam dann nie zum Tragen. Wieder aus der Tool-Beschreibung entfernt, stattdessen `pipeline.py::_TRUNCATION_HINTS` — ein Dict Tool-Name → konkreter Lösungsvorschlag, das die generische "aufgeteilt auf mehrere Aufrufe"-Formulierung in der Kürzungs-Notiz ersetzt, GENAU wenn der Fall tatsächlich eintritt: bei `write_knowledge` "schreibe zuerst nur einen kurzen Anfang, dann append_knowledge_section pro Abschnitt", bei `append_knowledge_section` selbst "teile den Abschnitt weiter auf". Der Hinweis steht damit im Kontext, in dem er gebraucht wird, statt vorbeugend und wirkungslos in einer Tool-Beschreibung.

Verifiziert: `_find_truncated_tool_call()` isoliert getestet (vollständiger vs. unvollständiger Aufruf, kein `tool_use`-Block, zweites Tool mit anderem Schema) gegen die realen `required`-Felder aus `tools.py`. Nachricht-Zusammenbau für alle drei Fälle (`write_knowledge`/`append_knowledge_section`/unbekanntes Tool) isoliert simuliert und geprüft. AST-Check `tools.py`: 64 Tool-Definitionen (neu: `append_knowledge_section`), keine Duplikate. `py_compile` (`pipeline.py`/`llm.py`/`tools.py`) sauber.

---

## 🔴 Zweiter Mac-Worker: Routing nach worker_id + dateibasierte Allowlist (2026-07-30) ✅ Routing, 🟡 os error 2 noch offen

**Teil 1 — Routing nach worker_id ✅:** Der Mac-Worker-Coding-Weg (`start_coding_job`/`services/coding_jobs.py`) routete Jobs bisher immer an "irgendeinen verbundenen local_exec-Client" (erster Treffer). Für einen zweiten, unabhängigen Worker (Arbeits-Mac) musste `projekte.client_id` tatsächlich bestimmen, welcher Mac einen Job bekommt. Umgesetzt: `client_manager.py` kennt jetzt `worker_id → Rolle`-Zuordnungen (`set_worker_assignment`/`get_connection_for_role`/`list_local_exec_workers`), persistiert in `brain.config.worker_assignments`; `jobs`-Tabelle hat eine `client_id`-Spalte (Migration: bestehende Zeilen auf `mac-private` zurückdatiert); Nebenläufigkeit läuft jetzt pro `(client_id, cwd)` statt global — zwei verschiedene Projekte/Worker dürfen parallel laufen, dasselbe Arbeitsverzeichnis nie doppelt. Neue Tools `list_mac_workers`/`assign_mac_worker`, `list_allowed_coding_paths`/`add_allowed_coding_path` bekamen einen optionalen `client_id`-Parameter zum gezielten Ansprechen eines Workers (PR #14 in `jarvis` server-Repo).

**Teil 2 — dateibasierte Allowlist (`jarvis-web`) ✅:** `PROJECT_ALLOWLIST` war bisher ein hartcodiertes Array im Build. Ersetzt durch `allowlist.json` im Tauri-App-Datenverzeichnis (pro macOS-Account/Installation getrennt, `expected_account`/`base_dir`/`paths`/`binaries`) — kein zweiter Build mehr nötig, wenn der Arbeits-Mac ein anderes Claude-Konto oder andere Pfade hat.

**os error 2 — Marathon-Fehlersuche, noch nicht endgültig bestätigt behoben:** Coding-Jobs aus der installierten App (`/Applications`) scheiterten mit "Unerwarteter Fehler bei der Vorbereitung: No such file or directory (os error 2)", obwohl derselbe Job im Dev-Modus lief. Mehrere Runden:
- PR #14 (`jarvis-web`): Binary-Pfade aus `allowlist.json.binaries` statt fester PATH-Auflösung, `env.PATH`-Override beim Spawn.
- PR #15: `fs:allow-exists`-Scope auf übliche Binary-Verzeichnisse eingegrenzt (war zu weit).
- PR #16: `claude` ist ein Symlink (`~/.local/bin/claude` → `~/.local/share/claude/versions/<version>`) — Tauris `fs`-Plugin kanonisiert vor der Scope-Prüfung, lehnte das Versionsziel als "forbidden path" ab. Gelöst scope-unabhängig über einen eigenen Rust-Command `binary_exists` (App-Commands brauchen laut Tauri-Doku keinen Capability-Eintrag) statt weiterer Scope-Aufweitung.
- PR #17: verbleibende ungefangene `try/catch`-Lücke beim `claude`-Spawn geschlossen, PATH-Wert in Fehlermeldungen.
- PR #18: **Ursachen-Hypothese (im Nachhinein widerlegt, siehe PR #21)** — Annahme war, Rusts `Command` löse einen bloßen Programmnamen über `execvp()` mit dem PATH des ELTERNPROZESSES (dieser App) auf, nicht mit dem per `env()` gesetzten PATH des Kindprozesses. Fix: `std::env::set_var("PATH", ...)` beim App-Start (`extend_app_path()`, `src-tauri/src/lib.rs`) erweitert das PATH des App-Prozesses SELBST, bevor irgendein `Command` läuft.
- PR #19 (`jarvis-web`, gemerged) + PR #16 (`jarvis` server, gemerged): Fehler blieb laut Simon auch nach PR #18 generisch — vollständiger Audit von `local_exec_request` bis zum Spawn fand fünf weitere echte ungefangene Stellen (fehlendes `.catch()` in `jarvis.js`, ungeschütztes `JSON.parse` in `runGhIssueList`, ungeschützter Schreibvorgang in `_writeAllowlistPaths`, ungeschützte Listener-Registrierung + fire-and-forget-Aufruf in `_prepareAndRunJob`/`runClaudeCodeRun`), alle geschlossen. Zusätzlich neues Diagnose-Tool: `diagnose_coding_worker` (Server) → `diagnose_binaries`-Aktion (Worker) liefert ohne Job-Start das PATH des App-Prozesses (neuer `get_env_path`-Rust-Command), das zusammengesetzte Subprozess-PATH, den `allowlist.json`-Inhalt sowie Existenz+`--version`-Probelauf pro Binary.
- PR #20 (`jarvis-web`, gemerged): `npm run deploy:local` — baut lokal ohne Signing-Key (`createUpdaterArtifacts` jetzt standardmäßig `false`, nur `tauri-release.yml` überschreibt es für echte Releases zurück auf `true`), ersetzt/startet `/Applications/JARVIS.app` neu. Auslöser: `--no-bundle` (bisheriger Workaround gegen den Signing-Key-Zwang) erzeugt gar kein `.app`-Bundle — vier Stunden mit einer alten Binary getestet, ohne dass ein neuer Build je installiert war.
- **PR #21 (`jarvis-web`, offen): PR #18 als wirkungslos entlarvt.** Simon bestätigte erneut: identische Binary läuft per Shell-Start durch (Job #27), scheitert per Finder/Spotlight-Start weiterhin mit os error 2. Direkter empirischer Test (`probe_path_resolution()`, spawnt `claude`/`gh` mit `env_clear()` + bewusst ungültigem PATH) beweist am laufenden Binary: die Namensauflösung eines bloßen Programmnamens folgt dem **Command-eigenen, explizit gesetzten PATH** (`tauri-plugin-shell`s `prepare_cmd()` ruft `command.envs(options.env)`, und `localExec.js::_createCommand` setzt bei JEDEM Spawn explizit `env.PATH` aus `_buildSubprocessPath()`/`allowlist.json`) — NICHT dem App-Prozess-eigenen PATH, das `extend_app_path()` (PR #18) setzt. **PR #18 ist damit für die claude/gh/git-Spawns strukturell wirkungslos**, bleibt aber bestehen (harmlos). Neu: `debug_log()` schreibt PATH-Zustände nach `~/Library/Logs/JARVIS/path-debug.log` (sichtbar auch ohne angehängtes Terminal bei Finder-Start) — verifiziert: `allowlist.json`-Lesen über `app_data_dir()` gelingt bei direktem Exec- UND `open()`-Start identisch. Die eigentliche Fehlerursache liegt also bei `_buildSubprocessPath()`/`allowlist.json` zur Laufzeit des tatsächlich fehlschlagenden Jobs, nicht bei der App-Prozess-PATH-Erweiterung.

**Noch offen:** PR #21 muss gemerged/deployed werden — danach den nächsten Finder/Spotlight-Testlauf mit `path-debug.log` UND dem exakten Fehlertext (enthält `verwendeter PATH: '...'`) auswerten, um `_buildSubprocessPath()`s tatsächlichen Laufzeit-Wert beim fehlschlagenden Job zu sehen. Release-Pipeline (`tauri-release.yml`) war zuletzt (`v0.1.11`/`v0.1.12`) wegen eines GitHub-Billing-Problems fehlgeschlagen (nicht code-seitig lösbar) — letzter erfolgreicher Release war `v0.1.10`; Tests laufen seitdem über `npm run deploy:local` (PR #20), nicht über einen offiziellen Release-Build.

**Nebenbefund, gleicher Tag:** Coding-Job-Fertig-Meldungen wurden vom allgemeinen 3/h-Notification-Rate-Limit verworfen (sah aus wie "nichts passiert", führte mehrfach zu Fehldiagnosen) — Fix war bereits vorher gemerged (`services/coding_jobs.py::_notify()` nutzt `bypass_rate_limit=True`, siehe `NotificationDispatcher.notify()`). Simon meldete das Symptom am 2026-07-30 erneut — da der Code-Fix nachweislich schon auf `main` war, deutet das darauf hin, dass der laufende `jarvis.service` auf dem HP-Server seit dem Merge nicht neu gestartet wurde (kein Hot-Reload für den Python-Prozess). Prüfen/Restart durch Simon ausstehend.

---

## 🔴 Autonomiegrad `careful` + Coding-Job-Ergebnisse im Chat + Live-Fortschritt (2026-07-31)

`projekte.autonomy` wurde bisher gespeichert, aber nicht ausgewertet (siehe oben, jetzt entfernter Punkt aus "Bestehende offene Punkte"). Umgesetzt:

**Zweistufiger Ablauf bei `autonomy='careful'`:** erster Lauf read-only (`--allowedTools Read,Grep,Glob --permission-mode dontAsk`, verlangt per Prompt einen Plan statt einer Umsetzung) — Job landet danach auf neuem Status `awaiting_review` statt fertig zu sein, keine Commit/Push/PR, Checkout bleibt auf dem Job-Branch stehen. Neue Tools `approve_coding_job`/`revise_coding_job`/`discard_coding_job`:
- **Freigabe** (`approve_coding_job`): `--resume <session_id>` mit Schreibrechten, setzt den Plan um — danach wie ein normaler Lauf (Commit/Push/PR).
- **Nachbessern** (`revise_coding_job`, Kommentar Pflicht): `--resume` bleibt read-only, Job landet wieder auf `awaiting_review` mit überarbeitetem Plan.
- **Verwerfen** (`discard_coding_job`) — ohne dieses Tool hätte ein nicht freigegebener Plan alle weiteren Jobs für dasselbe Projekt unbegrenzt blockiert (`awaiting_review` zählt als belegt, wie `running`/`pending`). Setzt Checkout zurück, löscht den Job-Branch, gibt den Slot frei.
- **`--resume`-Fallback:** ein wartender Job kann tagelang liegen — ob die Session dann noch fortsetzbar ist, war ungeprüft. Verifiziert (künstlich ungültige Session-ID): Exit-Code 1, letzte NDJSON-Zeile `{"type":"result","subtype":"error_during_execution","is_error":true,"errors":[...]}`. Bei genau diesem Muster EIN automatischer Retry ohne `--resume`, mit dem zuletzt gespeicherten Plantext (neue Spalte `jobs.plan_text`, übersteht im Gegensatz zu `result` eine überschreibende spätere Stufe) — verliert dabei den ursprünglichen Erkundungsverlauf, setzt aber den Plan trotzdem um.
- `sandbox`/`review`/`auto`/nicht gesetzt: unverändert ein einziger schreibender Lauf, wie vor diesem Schritt.

**Coding-Job-Ergebnisse zusätzlich als Chat-Nachricht:** bisher nur Notification (max. 300 Zeichen — für einen mehrere Absätze langen Plan zur Freigabe unlesbar). Jeder Job merkt sich jetzt bei Start `category`/`tab_id` (Herkunft des auslösenden Chat-Turns, neue `JarvisPipeline.set_chat_target()`, durchgereicht über `tools.execute()`) — `resolve_job_result()` liefert damit zusätzlich zur Notification eine Chat-Zustellung an genau diesen Tab: voller Text sichtbar (`display_history`), aber nur eine gekürzte Kurzfassung im Modell-Kontext (`api_history`, dieselbe 300-Zeichen-`summary` wie die Notification) — ein langer Plan/Diff soll nicht jeden folgenden Turn aufblähen und den Prompt-Cache invalidieren; Details bei Bedarf über `check_coding_job_status` nachladbar. Bewusst NICHT über `RESPONSE_START/CHUNK/DONE` (kollidiert mit einem echten, gerade laufenden Turn im selben Tab) — stattdessen der bestehende `coding_job_result`-Typ, jetzt auch Server→Web-Client, jarvis-web behandelt ihn neu eingehend.

**Live-Fortschrittskanal:** `claude -p --output-format stream-json` (Wechsel von `--output-format json`) liefert NDJSON — jede `tool_use`-Zeile wird sofort in eine kurze Statuszeile übersetzt ("liest hello.py", "bearbeitet README.md") und über neuen Typ `coding_job_progress` an denselben Tab weitergeleitet. Bewusst flüchtig: keine History-Berührung (weder `display_history` noch `api_history`), kein eigenes Vorhalten bei Verbindungsabbruch — ein verpasstes Ereignis ist irrelevant, das nächste kommt in Kürze. Eigener, paralleler Mechanismus zum bestehenden `coding_task_status` (server-seitiger `coding_engine.py`-Executor, broadcastet an alle Dashboard-Clients) — bewusst nicht wiederverwendet, andere Lebensdauer/Zielgruppe (dieser hier ist tab-gezielt, nicht Broadcast, wegen der bewussten `web`-History-Isolation pro Tab seit 2026-07-20).

Verifiziert: `claude -p --help` bestätigt `dontAsk` als offiziellen `--permission-mode`-Wert; echter Testlauf mit `--output-format stream-json` bestätigt das angenommene NDJSON-Format (`tool_use`-Blöcke mit `name`/`input`, abschließende `result`-Zeile identisch zum bisherigen `--output-format json`); `--resume`-Fallback-Erkennung an einer künstlich ungültigen Session-ID bestätigt. `services/coding_jobs.py` isoliert getestet (44 Assertions: autonomy-Snapshot, `awaiting_review`-Belegung, Kosten-Summierung über Stufen, `plan_text`-Erhalt, Chat-Delivery-Shape, approve/revise/discard inkl. "kein Worker verbunden"-Pfad, `discarded` gibt den Slot korrekt frei). `local_data.list_coding_projects()` und `tools.execute()`s neue Wiring isoliert verifiziert. `npm run build` (jarvis-web) sauber.

**Nachtrag, gleicher Tag — Job-Ansicht in jarvis-web + worker_id-Fix:** Coding-Jobs liefen bisher nur über den Chat, keine Übersicht — mit dem zweistufigen `careful`-Ablauf oben unübersichtlich geworden. Neu:
- **Job-Ansicht** (`/jobs`, neue `JobsView.vue`): Liste aller Jobs (neueste zuerst, Filter nach Status) mit ID/Titel/Projekt/Status/Laufzeit-oder-Dauer/Kosten/Issue-Nummer/PR-Link. Klick auf eine Zeile öffnet die Detailansicht darunter — bei `awaiting_review` der volle Plantext + drei Aktionen (Freigeben/Nachbessern mit Pflicht-Kommentarfeld/Verwerfen), bei abgeschlossenen Jobs Zusammenfassung + `changed_files` + PR-Link (kein Diff-Viewer, das bleibt GitHub). Laufende Jobs zeigen die Live-Fortschrittszeile aus `coding_job_progress`.
- Die drei Aktionen rufen über einen neuen WS-Typ (`coding_job_action`/`_ack`) **serverseitig direkt** `coding_jobs.approve_job`/`revise_job`/`discard_job` auf — dieselben Funktionen wie die gleichnamigen Tools, keine zweite Implementierung im Frontend.
- Neue `coding_jobs.list_jobs(status_filter)` + gemeinsamer `_enrich_job()`-Helper (mit `get_job_status()` geteilt) — `duration_minutes` für abgeschlossene Jobs (created_at→updated_at, Näherung über alle Stufen hinweg, keine separate Zeiterfassung pro Stufe), `project_name` per Lookup gegen `local_data.list_coding_projects()`.
- **worker_id-Fix:** lag bisher in `localStorage` — das ist pro Web-Origin isoliert, Dev-Build und installierte App haben unterschiedliche Origins und bekamen dadurch real unterschiedliche `worker_id`s, `assign_mac_worker` lief für den jeweils anderen Build ins Leere ("kein Worker verbunden", mehrfach aufgetreten). Jetzt als Datei (`worker_id.txt`) neben `allowlist.json` im Tauri-App-Datenverzeichnis — pro macOS-Account getrennt, aber build-übergreifend gleich. Neue async `getOrCreateWorkerId()` in `localExec.js`, `ws.onopen` in `stores/jarvis.js` awaitet sie jetzt statt der bisherigen synchronen `localStorage`-Konstante.

Verifiziert: `services/coding_jobs.py::list_jobs()` isoliert getestet (8 Assertions: Sortierung, Projekt-Namens-Lookup, `duration_minutes`/`running_since_minutes`, Status-Filter). `py_compile` (server-seitig, beide Python-Versionen) + `npm run build` (jarvis-web) sauber. Echter Test der Job-Ansicht/Aktionen/worker_id-Fix im installierten Build steht noch aus (durch Simon).

**Nachtrag #2, gleicher Tag:**
- **`unassign_mac_worker(worker_id)`** — Gegenstück zu `assign_mac_worker`, hat gefehlt: nach dem worker_id-Speicherort-Wechsel oben (localStorage → Datei) bekommt jede Installation beim ersten Start danach zwangsläufig eine NEUE worker_id, die alte blieb ohne dieses Tool dauerhaft als verwaiste Zuordnung in `list_mac_workers` stehen. `client_manager.py::remove_worker_assignment()` + `coding_jobs.unassign_worker()` (nutzt `brain.write(..., value=None)`, das bereits vorhandene Lösch-Verhalten von `brain.write()` — kein neuer Mechanismus nötig). Isoliert getestet.
- **Live-Fortschritt kam nicht an — Diagnose statt Vermutung:** Simon meldete, die Fortschrittszeile erscheine nie. Vor der Fehlersuche zuerst den ursprünglichen Verifizierungsschritt nochmal geprüft: ein zweiter echter `claude -p --output-format stream-json`-Lauf, diesmal mit den TATSÄCHLICHEN Produktions-Flags (`--allowedTools Read,Edit,Bash --disallowedTools ... --permission-mode acceptEdits`, nicht nur der schmale Read-only-Testfall von vorhin) — bestätigt: das NDJSON-Format war korrekt angenommen, `_summarizeStreamEvent` sollte greifen. Stattdessen beim Nachvollziehen der Zustellkette einen echten Bug gefunden: der ZWEITE `CLIENT_HELLO`-Handler in `server.py` (falls ein Client nach der ersten Begrüßung nochmal einen Hello schickt) aktualisierte `category`/`tab_id` nur lokal, rief aber `pipeline.set_chat_target()` nicht erneut auf — die Pipeline blieb auf dem Stand des ERSTEN Hello stehen. Behoben. Zusätzlich auf allen vier Ebenen Log-Ausgaben ergänzt (Worker: `_spawnClaudeStreaming`/pro-Zeile-Parsing/erkannte `tool_use`-Events; Server: `_relay_job_progress` mit explizitem Grund bei stillem Verwerfen; `coding_jobs.start_job`: `category`/`tab_id` beim Anlegen; Frontend: `coding_job_progress`/`coding_job_result`-Empfang) — damit sich ein evtl. verbleibendes Problem beim nächsten Testlauf eindeutig einer Ebene zuordnen lässt, statt weiter zu raten.
- **PR-Link in der Job-Ansicht nicht klickbar:** war als Text gerendert. Ursache: in Tauri öffnet ein normales `<a target="_blank">` die URL IM App-WebView statt im System-Browser — kein bestehender Opener-Mechanismus im Projekt vorhanden. Neu installiert: `@tauri-apps/plugin-opener` (`npm install`, `tauri-plugin-opener` in `Cargo.toml`, `.plugin(tauri_plugin_opener::init())` in `lib.rs`), Capability eng auf `https://github.com/**` eingegrenzt (alle PR-Links zeigen ausschließlich auf GitHub, keine breitere Freigabe nötig). Neuer, wiederverwendbarer Helper `lib/openExternal.js::openExternalLink()` — in Tauri wird die native `<a>`-Navigation verhindert und stattdessen über den Opener geöffnet, im normalen Browser-Kontext (jarvis-web ohne Tauri) bleibt das native `target="_blank"` unangetastet. `TodoItem.vue`s Ticket-Link hat vermutlich dasselbe Problem, war aber nicht Teil dieser Anfrage — nicht angefasst.

Verifiziert: `client_manager.remove_worker_assignment()`/`coding_jobs.unassign_worker()` isoliert getestet. Zweiter echter `--output-format stream-json`-Lauf mit Produktions-Flags bestätigt das Format erneut. `cargo check`/`cargo test --lib` (7/7) nach dem neuen Opener-Plugin weiterhin grün, `npm run build` + echter `deploy:local`-Durchlauf erfolgreich.

**Nachtrag #3, gleicher Tag — differenzierte gh-Rechte, getrenntes Ticket-Repo, `delivery`, `coding_doc`:**
- **Zwei getrennte gh-Zugriffsebenen, nicht verwechseln:** WORKER-Ebene (`ALLOWED_COMMANDS` in `localExec.js`, für `_ghIssueView`/`runGhIssueList`/`runShellExec` — außerhalb des Modell-Laufs) darf `gh issue view`/`list` und war das immer schon, unverändert. MODELL-Ebene (`_claudeToolFlags()`, die `--allowedTools`/`--disallowedTools` von `claude -p` selbst) braucht gar kein `gh` — der Worker holt den Issue-Inhalt und erstellt einen etwaigen PR jeweils deterministisch selbst, außerhalb des Modell-Laufs. Erster Umsetzungsversuch hatte das verwechselt (eine Denylist einzelner gh-Schreibunterbefehle für die Modell-Ebene gebaut, dabei unvollständig — `gh release`/`workflow run`/`repo edit`/`secret set` u.a. fehlten) — korrigiert: `Bash(gh:*)` ist dem Modell wieder pauschal gesperrt, unabhängig von `delivery`.
- **Empirisch verifiziert** (bleibt relevant für eine künftige gezielte Ausnahme): ein schmales `--allowedTools 'Bash(gh issue list:*)'` überschreibt KEIN breiteres `--disallowedTools 'Bash(gh:*)'` — echter `claude -p`-Testlauf (Scratch-Repo, Fake-`gh`-Binary) verweigerte den Aufruf trotzdem ("Permission ... has been denied"). Deny gewinnt unabhängig von der Spezifität. Falls sich beim Testen zeigt, dass das Modell während eines Laufs wirklich mal ein Issue nachlesen muss, braucht eine gezielte Freigabe (z.B. `Bash(gh issue view:*)`) deshalb einen enger gefassten Disallow (einzelne Schreibunterbefehle statt der Pauschalsperre), nicht nur einen zusätzlichen Allow-Eintrag daneben — bewusst nicht vorsorglich gebaut, siehe oben.
- **`projekte.issue_repo`** (optional): getrenntes Ticket-Repo — bei Simon liegen Issues aller Code-Repos in einem eigenen Repo. Gesetzt, läuft `gh issue view`/`list` für Coding-Jobs (Worker-Ebene) gegen `issue_repo` statt `repo` (Substitution serverseitig in `coding_jobs.py::_try_dispatch()`, reiner Feld-Swap vor dem Dispatch an den Worker — `cwd` bleibt unverändert das Code-Repo). `gh pr create` unberührt (arbeitet ohnehin ohne `--repo` auf dem Git-Remote von `cwd`).
- **`projekte.delivery`** (`local`/`push`/`pr`, Default `pr` = bisheriges Verhalten): unabhängig von `autonomy` — zwei getrennte Achsen (Kontrolle VOR der Ausführung vs. wie weit das Ergebnis geht). `local`: Commit auf dem Job-Branch, kein Push, kein PR, Checkout danach zurück auf `base_branch` (bestehender Code-Pfad unverändert), Branch bleibt bestehen (wird nirgends gelöscht außer bei `discard_job`). `git push`/`gh` sind dem Modell ohnehin in JEDEM `delivery`-Modus komplett gesperrt (siehe oben) — bei `local` lässt der Worker Push/PR-Erstellung im deterministischen Abschluss-Code (`_finishJob`) einfach weg. `push`: Commit + Push, kein PR. Snapshot bei `start_job()` in die `jobs`-Tabelle (wie `autonomy`/`cwd`/`base_branch`) — eine spätere Änderung von `projekte.delivery` betrifft nie einen schon laufenden/wartenden Job, auch nicht über eine `approve_coding_job`-Fortsetzung.
- **`projekte.coding_doc`** (optional, Format `"topic/file"`): Referenz auf ein mit `write_knowledge` angelegtes Wissensdokument, das bei der SCHREIBENDEN Stufe eines Coding-Jobs in den Prompt eingebettet wird (`coding_jobs.py::_build_commit_convention_note()`). Reihenfolge: `GIT_CONVENTIONS.md` im Repo selbst (Prüfung im Prompt ans Modell delegiert — der Server kann die Datei nicht selbst lesen, sie liegt auf dem Mac-Worker, einer anderen Maschine) → `coding_doc`-Inhalt (server-lokal über `knowledge.read()`, direkt eingebettet) → keine Vorgabe. Nicht auf Commit-Konventionen beschränkt — Code-Konventionen oder sonstige projektspezifische Hinweise gehören ebenso hinein. **Zwischenlösung:** im Zielbild (`docs-draft/JARVIS-Datenmodell-und-API.md`) hängen Dokumente über `documents.project_id` am Projekt und werden über eine feste Leseliste pro Auftragstyp geladen — dann entfällt dieses Einzelfeld zugunsten des generischen Dokumentenmodells.

Verifiziert: Scratch-Test zur Permission-Präzedenz (siehe oben). `services/coding_jobs.py`/`local_data.py` isoliert getestet (Schema-Migration, `data_query`/`data_write`/`data_update`-Durchreichung, `_build_commit_convention_note()` in allen drei Fällen, `_build_prompt`/`_build_issue_prompt_parts`/`_build_resume_prompt`/`_build_resume_fallback_prompt` binden die Konvention nur bei der schreibenden Stufe ein, `start_job()` substituiert `issue_repo` korrekt für den Worker, snapshotet `delivery` inkl. Default `'pr'`, `approve_job()` reicht `delivery`/`coding_doc` an den Resume-Aufruf durch). `py_compile` + `node --check` sauber. Echter End-zu-End-Test (Job mit `delivery='local'` gegen ein echtes Repo, `issue_repo` gegen ein echtes Ticket-Repo) steht noch aus (durch Simon).

**Nachtrag #4, 2026-07-31 — Job #35: Modell fragte nach Bestätigung statt zu committen:** echter Job endete mit "Soll ich das committen? … bitte kurz bestätigen" als Zusammenfassung statt einer echten Zusammenfassung — in einem unbeaufsichtigten Lauf kann niemand antworten, der Worker hat trotzdem korrekt committet (das Modell hatte die Änderungen im Arbeitsverzeichnis belassen, wie vorgesehen), aber der `result`-Text war für Simon unbrauchbar. `_WRITE_TAIL` (der Prompt-Baustein der schreibenden Stufe) ergänzt: explizit, dass der Lauf unbeaufsichtigt ist, niemand antwortet, und das Modell selbst entscheiden statt nachfragen soll — plus Verstärkung der bestehenden "kein eigener Commit/Push/PR"-Anweisung ("Änderungen einfach im Arbeitsverzeichnis stehen lassen"). Nebenbefund beim Beheben: `_build_resume_prompt`/`_build_resume_fallback_prompt` (die `approve_coding_job`-Fortsetzung bei `autonomy='careful'`) hatten `_WRITE_TAIL` bisher GAR NICHT eingebunden — die Freigabe-Stufe bekam nie die "kein eigener Commit"/"Zusammenfassung am Ende"-Anweisung, nur die Commit-Konvention. Mit behoben (nur `mode='approve'`, `revise` bleibt read-only und braucht das nicht).

Verifiziert: isolierter Test bestätigt den Hinweis in `_build_prompt`(write)/`_build_resume_prompt`(approve)/`_build_resume_fallback_prompt`(approve), NICHT in `_build_prompt`(plan_only) oder `_build_resume_prompt`(revise) (dort sind Rückfragen im Plan/bei der Nachbesserung weiterhin erwünscht). `py_compile` sauber.

**Nachtrag #5, 2026-07-31 — Coding-Felder in der Projektansicht statt nur im Chat setzbar.** `path`/`repo`/`issue_repo`/`base_branch`/`client_id`/`autonomy`/`delivery`/`coding_doc`/`data_scope` existierten nur in der DB, ausschließlich über den Chat (`data_write`/`data_update`) setzbar. Neuer ausklappbarer Bereich "Coding" in `SeiteView.vue`s Projekt-Bearbeitungsformular:
- `path` als Auswahlfeld statt Freitext, gespeist aus der tatsächlichen Allowlist des in `client_id` gewählten Workers — ein Tippfehler führte vorher nur zu einer nichtssagenden "Ordner nicht freigegeben"-Meldung vom Worker, ohne Hinweis auf die Ursache. Neue `data_request`-Resource `allowed_coding_paths` (`server.py`) ruft denselben Weg wie das LLM-Tool `list_allowed_coding_paths` auf (`coding_jobs.resolve_worker_connection()` + `local_exec.dispatch("list_allowed_paths", ...)`), aber ohne LLM-Umweg — läuft wie jeder `data_request` über `run_in_executor`, blockiert also nur einen Executor-Thread (bis zu `dispatch()`s 60s-Timeout), nicht den Event-Loop. Der aktuell gespeicherte Pfad bleibt immer als Option sichtbar, auch wenn er (noch) nicht in der frisch geladenen Liste steckt (Worker gerade nicht verbunden, oder Allowlist hat sich geändert) — sonst sähe es beim Öffnen des Formulars so aus, als wäre der Wert weg.
- `client_id`/`autonomy`/`delivery`/`data_scope` als Auswahlfelder mit den gültigen Werten (bisher nirgends im Code als Enum durchgesetzt, nur in Prosa dokumentiert — die UI ist damit die erste Stelle, die sie tatsächlich als geschlossene Menge behandelt).
- Zusätzlich neues Feld `projekte.estimated_hours` (REAL) — Gegenstück zu `geschaetzter_wert` in Stunden statt Euro, Grundlage für den effektiven Stundensatz und den Abgleich Schätzung vs. tatsächlichem Aufwand (Zielbild `docs-draft/JARVIS-Datenmodell-und-API.md`: `estimated_value` ist Geld, `estimated_hours` ist Aufwand). Bewusst NICHT im Coding-Bereich, sondern kaufmännisches Feld neben `geschaetzter_wert` im oberen Formularteil — Simons Korrektur einer ursprünglich falschen Einordnung durch mich (die erste Aufzählung hatte `estimated_hours` fälschlich zu den Coding-Feldern gezählt).
- **Blocker gefunden und mit behoben:** `server.py`s `_ENTITY_FIELDS["projekte"]` (Whitelist für den `entity_action`-Sparweg, den die UI für alle Feld-Updates nutzt — NICHT der LLM-Tool-Weg `data_update`) kannte bisher nur `name/status/beschreibung/typ/notizen/geschaetzter_wert/erwartetes_abschlussdatum`. `local_data.update_projekt()`s eigene `allowed`-Menge hatte alle Coding-Felder bereits, aber die WS-Eingangs-Whitelist hätte sie beim Speichern über die UI trotzdem stillschweigend herausgefiltert.

Verifiziert: `local_data.py` isoliert getestet (`add_projekt`/`update_projekt`/`query`/`write`/`get_seite_view` mit `estimated_hours` + allen Coding-Feldern). `py_compile` sauber. `npm run build` (jarvis-web) sauber. Echter Abruf der Worker-Allowlist über `allowed_coding_paths` und ein echter Speichervorgang über die neue UI stehen noch aus (durch Simon).

---

## 🔴 Thread-Umbau Teil A — Bedienung geradeziehen, Nachrichten verschiebbar (2026-08-01)

**Anlass:** Threads (Teil 2, PR #25-28) waren ein manuelles Etikett OBEN AUF dem bisherigen `tab_id`-Cursor-Modell: eine Auswahl im Eingabebereich, "Kein Thema" als Fallback zurück auf die alte Fensterbildung. In der Praxis die falsche Bedienung — Themenwahl gehört nicht in die Eingabezeile, ein optionaler Fallback bedeutet uneinheitliche Nachrichten. Simons Auftrag: Threads werden für jarvis-web das EINZIGE Modell, die Seitenleiste die alleinige Navigations-/Verwaltungsstelle, und Nachrichten lassen sich nachträglich zwischen Threads verschieben.

**Grundprinzip:** der Server erzwingt "jede Nachricht hat einen Thread" NICHT strukturell — `build_history_window()`s threadloser Cursor-Fallback bleibt bestehen (`category="voice"` hat kein Thread-Konzept, `category="web"` wird laut `_category_for_role()` allein über `role="dashboard"` vergeben, nicht exklusiv jarvis-web — ein separates, hier nicht einsehbares jarvis-dashboard-Repo könnte sich denselben Client-Typ teilen und liefe ohne `SET_THREAD` unverändert im alten Modus weiter). Stattdessen sorgt jarvis-web SELBST per Bootstrap dafür, dass für seine Verbindungen immer ein Thread aktiv ist.

**Zwei Korrektheitslücken zuerst behoben (Review deckte sie vor der Umsetzung auf) — beide waren Voraussetzung, damit die neue Rundenerkennung (Punkt 5) sich auf "jede Runde ist in sich abgeschlossen" verlassen kann:**
- `pipeline.py::_run_llm()`, `stop_reason=="end_turn"` mit leerem `turn_text`: persistierte bisher GAR NICHTS (anders als `refusal`/`max_tokens`, die schon einen Nicht-leer-Platzhalter erzwangen) — die Runde hinterließ keinen sauberen Abschluss. Fix: `end_turn` bekommt denselben Platzhalter-Schutz (`"(keine Antwort)"`).
- `server.py::_deliver_job_result_to_chat()`: persistierte eine `assistant`-Nachricht außerhalb jeder Pipeline-Runde, ohne zu prüfen was die zuletzt gespeicherte Zeile ist — startet ein Nutzer einen Coding-Job und schreibt bis zur Fertigstellung nichts (der Normalfall, nicht der Randfall), entstehen zwei aufeinanderfolgende `assistant`-Zeilen (API lehnt mit 400 ab, eigenständiger, jetzt blockierender Bug). Fix (nach Simons Korrektur eines ursprünglich falschen "einfach überspringen"-Entwurfs — hätte bedeutet, dass ein Job-Ergebnis im Normalfall NIE im Kontext landet): neue `session_memory.append_or_extend_message()` hängt den Ergebnistext an die letzte `assistant`-Zeile an (`UPDATE`) statt eine neue zu schreiben, sofern die letzte Zeile bereits `assistant` mit reinem String-`content` ist — sonst wie bisher ein neuer Insert.

**Umgesetzt (Backend, `session_memory.py`/`pipeline.py`/`server.py`/`protocol.py`/`client_manager.py`):**
- `threads.created_at` (neu, einmalig gesetzt, ändert sich anders als `last_activity_at` nie wieder) — Grundlage für "Unbenannt · HH:MM" in der Sidebar. `create_thread(title=None, ...)` — Titel optional, `_ENTITY_REQUIRED_FIELD` hat für `threads` keinen Eintrag mehr.
- Rundenerkennung: `get_round_bounds()` — eine Runde beginnt bei `role='user'` (kein reiner `tool_result`-Block) und endet bei der nächsten `role='assistant'`-Zeile mit reinem String-`content` (derselbe Test wie `repair_dangling_turns()`). Jede abgeschlossene Runde ist dadurch ein in sich stimmiger Block — Verschieben eines beliebigen Teils solcher Blöcke erhält die Rollen-Abwechslung IMMER, unabhängig vom Zielort. Mathematische Grundlage für "nur ganze Runden verschieben, nie einzelne Zeilen".
- `move_round(message_id, target_thread_id)` ("nur diese Runde") / `move_from_here(message_id, target_thread_id)` (Standard, "ab hier alles", filtert nur über `thread_id`, nicht `tab_id` — ein Thread kann geräteübergreifend fortgesetzt werden). Beide sortieren den Zielort automatisch nach `id` (chronologisch einsortiert, nicht ans Ende angehängt) und aktualisieren `threads.last_activity_at` für Quelle UND Ziel.
- `merge_threads(source_id, target_id)` — überträgt alle Nachrichten, löscht den Quell-Thread. Neue WS-Typen `MOVE_MESSAGES`/`MOVE_MESSAGES_ACK`, `MERGE_THREADS`/`MERGE_THREADS_ACK`. Betroffene, GERADE VERBUNDENE Tabs (auch der auslösende selbst) werden serverseitig umgehängt (`_active_thread_ids`, `pipeline.set_thread()` über neues `client_manager.get_pipeline_for_tab()`) und bekommen `THREAD_REASSIGNED` — sonst würde die nächste dort geschriebene Nachricht unter einer gelöschten `thread_id` landen.
- Message-IDs im Frontend: `RESPONSE_START` trägt jetzt `user_message_id`, `RESPONSE_DONE` trägt `assistant_message_id` (nur bei echtem Turn-Abschluss, `None` bei einem Tool-Loop-Zwischenschritt) — ohne echte DB-`id` ließe sich eine laufende, noch nicht neu geladene Konversation nicht als Verschieben-Anker adressieren. `get_thread_messages()` liefert zusätzlich `id` pro Eintrag.

**Umgesetzt (Frontend, `jarvis-web`):**
- `ChatView.vue`s Thread-/Projekt-`<select>` im Eingabebereich (Teil 2/PR #27/#28) vollständig entfernt inkl. `window.prompt()`-Restlogik — Themenwahl läuft nur noch über die Seitenleiste.
- Bootstrap statt Fallback: `activeThreadId` primär in `sessionStorage` (wie `tab_id` — NICHT reinem `localStorage`, das wäre tab-übergreifend: zwei offene Fenster würden sonst im selben Thread landen und parallel hineinschreiben, ohne die Nachrichten des jeweils anderen live zu sehen), `localStorage` nur als Startwert für einen frisch geöffneten Tab. Nach jedem `requestThreads()`: ist `activeThreadId` nicht (mehr) in der Liste (nie gesetzt, oder inzwischen gelöscht/gemergt), wird automatisch ein neuer unbenannter Thread angelegt und aktiv geschaltet (`newThread()`) — deckt ersten Start UND einen ungültig gewordenen Alt-Zustand einheitlich ab.
- Sidebar: Threads mit Projektbezug gruppiert vor freien Threads (Projektname als Badge), Inline-Bearbeitungsformular pro Zeile (Titel + Projekt-Zuordnung, `updateThread()`) statt `window.prompt()`, plus ein Zusammenführen-Auswahlfeld im selben Panel (`mergeThreads()`).
- Verschieben-UI: Hover-Button an jeder User-Bubble mit gesetzter `id` öffnet ein kleines Menü (Modus "Ab hier alles"/"Nur diese Runde", Ziel-Thread oder "+ Neuer Thread") → `moveMessages()`. Bei Erfolg: Threads-Liste + aktuell angezeigtes Fenster neu geladen (verschobene Runden verschwinden sichtbar aus der laufenden Ansicht).
- `thread_reassigned`-Handling bereits aus dem Merge-Handler abgedeckt (kein Extra-Code in `ChatView.vue` nötig) — der Server pusht das an JEDEN betroffenen Tab, auch den auslösenden selbst.
- `resetSession()`/`_parseHistoryChunks()`/`restoreCurrentMessages()`/`sessionChunks` vollständig entfernt (durch Threads ersetzt, keine Zwischenschicht mehr nötig).

**`history_windows`/Cursor bewertet, bleibt bestehen** — nicht Teil dieses Durchgangs, s.o. (Grundprinzip). Was tatsächlich entfällt, ist nur die NUTZUNG durch jarvis-web (`SESSION_RESET` wird von dort nicht mehr gesendet, Handler bleibt serverseitig für andere `role="dashboard"`-Clients bestehen).

**Bewusst nicht Teil dieses Durchgangs:** automatische Themenerkennung (Teil B); Entfernen von `history_windows`/Cursor/`SESSION_RESET`; Änderungen an jarvis-dashboard; Drag-and-Drop fürs Zusammenführen (einfaches Auswahlfeld reicht vorerst).

Verifiziert: isolierte Testskripte gegen Scratch-`HOME` — 35 neue Checks für `session_memory.py` (Rundenerkennung inkl. Tool-Loops und dem gefixten `end_turn`-Leerstring-Fall, `move_round`/`move_from_here` inkl. Einfügen mitten in eine bestehende Sequenz, `merge_threads`-Reihenfolge nach `id`, `append_or_extend_message` in beiden Fällen), 15 neue Checks gegen das ECHTE `pipeline.py` (gestubbte schwere Abhängigkeiten) für den `end_turn`-Fix und die `RESPONSE_START`/`RESPONSE_DONE`-Message-IDs. Teil 2s bestehende 20-Checks-Testsuite erneut grün nach Anpassung an das neue `id`-Feld in `get_thread_messages()` (keine Regression, nur eine stale Assertion). `npm run build` (jarvis-web) sauber. Echter End-zu-End-Test durch Simon (Bootstrap bei frischem Tab, Umbenennen/Projektzuordnung, Verschieben in beiden Modi inkl. laufender Konversation, Zusammenführen inkl. eines zweiten verbundenen Tabs) steht noch aus.

**Nachtrag, 2026-08-01 — "+ Neuer Chat" legte immer einen neuen Thread an, freie Threads unlöschbar (jarvis-web PR #30):** Simon meldete anhand eines Screenshots: ein leerer "Unbenannt"-Thread war entstanden, weil "+ Neuer Chat" bisher IMMER einen neuen Thread anlegte, auch wenn der gerade aktive noch komplett leer war (kein Datenverlust-Bug — der Thread war tatsächlich leer, nur redundant angelegt). Fix: `newChat()` prüft jetzt vorher `store.messages.length` — ist der aktive Thread bereits leer, bleibt er einfach aktiv. Dabei zusätzlich zwei UI-Wünsche umgesetzt: freie Threads (ohne Projektbezug) lassen sich jetzt aus dem Bearbeiten-Panel löschen (Zwei-Klick-Bestätigung statt `window.confirm()`, Backend-Weg `entity_action`/`threads`/`delete` existierte bereits seit Teil 2, keine Server-Änderung nötig); Projekt-Threads zeigen nur noch den Projektnamen als einzelne Zeile statt zusätzlich denselben Text als Titel darunter zu wiederholen (wirkte auf dem Screenshot wie zwei redundante Zeilen). **Prozess-Fehler dabei:** der erste Fix-Commit landete versehentlich direkt auf `main` (lokaler Checkout war zwischenzeitlich, nach dem PR-#29-Merge, auf `main` gewechselt worden, vor dem Commit nicht geprüft) — per `git revert` auf `main` rückgängig gemacht und stattdessen sauber über `git cherry-pick` auf einen neuen Branch + PR #30 übertragen.

---

## 🔴 Thread-Umbau Teil B, Schritt 1 — automatische Benennung unbenannter Threads (2026-08-01)

**Anlass:** Seit Teil A zeigt ein unbenannter Thread "Unbenannt · HH:MM" in der Seitenleiste — das manuelle Umbenennen bleibt in der Praxis oft aus, "+ Neuer Chat" ist bewusst niedrigschwellig. Simons Auftrag: ein unbenannter Thread bekommt automatisch einen zum Inhalt passenden Kurztitel ("Steuern", nicht der Wortlaut der ersten Frage), ermittelt über einen billigen Hintergrund-Call mit einem kleinen Modell, NACH dem Turn — die Chat-Antwort geht wie bisher sofort raus. Ausdrücklich NICHT Teil dieses Schritts: Drift-Erkennung (laufende Neubewertung ob eine Nachricht noch zum aktuellen Thread passt) — das bleibt ein späterer, eigener Schritt, falls überhaupt.

**Wann versucht wird:** nicht nach der ersten Nachricht (oft noch nicht erkennbar — "Hi", "kurze Frage"), sondern bei Runde 2, 4 und 8 (Simons explizite Vorgabe, statt bei jedem Turn ab Runde 2 — deckt spät erkennbare Themen genauso ab, bei weniger als der Hälfte der Aufrufe). Neue `session_memory.count_rounds(thread_id)` zählt dafür echte Rundenanfänge (`role='user'`, keine `tool_result`-Fortsetzung — dieselbe Definition wie `get_round_bounds()` aus Teil A). Ist das Thema bei Runde 8 immer noch nicht erkennbar, gibt der Live-Hook auf — Nachholtermin ist der Startup-Sweep (siehe unten).

**Nie überschreiben:** neue `session_memory.set_auto_title(thread_id, title)` — atomares `UPDATE threads SET title = ? WHERE id = ? AND title IS NULL`, gibt zurück ob tatsächlich geschrieben wurde. Race-sicher gegenüber einer parallelen manuellen Umbenennung oder einem inzwischen gelöschten/gemergten Thread: die WHERE-Bedingung macht ein verlorenes Rennen zu einem stillen No-Op statt eines Clobberns — kein separates "erst lesen, dann schreiben" nötig.

**Bestehende Threads werden mitgegeben, damit dasselbe Thema denselben Titel bekommt:** neue `session_memory.list_named_titles(limit=300)` — bewusst NICHT `list_threads()` wiederverwendet (die ist nach `last_activity_at DESC` sortiert und auf 50 gedeckelt, für die Sidebar gedacht — ein seit Monaten inaktiver "Steuern"-Thread würde dort nach genug neueren Threads aus dem Fenster fallen und dem Namensgeber nie mehr als Wiederverwendungs-Kandidat angeboten, genau der von Simon befürchtete Fall). Der Prompt weist das Modell an, bei erkennbar gleichem Thema GENAU den vorhandenen Titel zu übernehmen statt eine Variante zu erfinden. **Bewusste Entscheidung zu Duplikaten:** kein Auto-Merge, keine erzwungene Eindeutigkeit (z.B. "Steuern (2)") — zwei Threads mit identischem Titel sind das gewollte, sichtbare Signal "gleiches Thema" und über die in Teil A gebaute "Zusammenführen"-Funktion jederzeit manuell konsolidierbar. Ein automatisches Zusammenführen ohne Rückfrage wäre ein größerer Eingriff (Konversationen ungefragt verschmelzen) als angefragt und ginge Richtung der bewusst zurückgestellten Drift-Erkennung. Die eigentliche Sorge ("vier Steuer-Threads nach drei Monaten") wird stattdessen dadurch adressiert, dass die Titel bei gleichem Thema KONSISTENT sind — das macht Duplikate auf einen Blick erkennbar statt sie durch uneinheitliche Benennung ("Steuern"/"Steuererklärung"/"Finanzamt") zu verschleiern.

**Broadcast an alle Web-Clients, nicht nur den auslösenden Tab:** anders als `THREAD_REASSIGNED` (Teil A, dort ändert sich serverseitiger Zustand nur für betroffene Tabs) ist eine Titel-Änderung eine reine Anzeige-Aktualisierung ohne Zustandsrisiko. Simon hat regelmäßig mehrere Fenster offen — neue `server.py::_broadcast_web_event()` schickt an ALLE über `client_manager.get_dashboard_event_callbacks()` registrierten Web-/Dashboard-Clients. `pipeline.py` konnte diese Funktion bisher nicht erreichen (`manager`-Singleton lebt in `server.py`, das bereits `pipeline.py` importiert — ein Reimport wäre zirkulär) — gelöst über Dependency Injection: neuer optionaler Konstruktor-Parameter `JarvisPipeline(..., broadcast_web_event=...)`, genau wie `on_event`/`on_audio` bereits injiziert werden. `main.py`s lokaler Voice-Only-Entrypoint lässt den Parameter einfach weg (Default `None`, harmlos — Voice-Pipelines setzen nie `self._thread_id`).

**Startup-Sweep für bereits bestehende unbenannte Threads:** der Live-Hook läuft nur bei einem NEUEN Turn — ein Thread, in dem längst nicht mehr geschrieben wird, hätte sonst nie einen Titel bekommen, obwohl genug Inhalt für einen da wäre. Neue `session_memory.list_unnamed_thread_ids()` + `thread_naming.run_startup_sweep()`: beim Serverstart, in einem eigenen Hintergrund-Thread (blockiert den Start nicht), werden alle unbenannten Threads mit ≥2 Runden NACHEINANDER (gedrosselt, 3s Pause zwischen Aufrufen — kein Burst gleichzeitiger API-Calls) verarbeitet. Läuft bei jedem Neustart erneut über die dann noch verbleibenden unbenannten Threads — akzeptierter Kompromiss statt einer zusätzlichen "nur einmal pro Thread jemals"-Sperre, bei den hier abgeschätzten Kosten pro Aufruf vernachlässigbar.

**Neues Modul `thread_naming.py`** (Top-Level, nicht unter `services/` — Turn-Abschluss-Logik wie `context.py`, kein isolierter Service): `_attempt_naming()` als gemeinsame Kernfunktion, zwei gegatete Einstiegspunkte (`maybe_name_thread_on_turn()` für den Live-Hook mit dem Runde-2/4/8-Check, `maybe_name_thread_once()` für den Sweep mit nur einer Untergrenze). Neue `llm.complete()` — minimaler, NICHT-streamender Einzel-Call (`_get_client().messages.create()`), da `stream()` immer streamt, einen festen 24000-Token-Deckel hat und einen zweiteiligen Cache-Control-System-Prompt baut — für einen einzelnen kurzen Hintergrund-Call unpassend. Neuer Protokolltyp `THREAD_TITLE_UPDATED`.

**Kosten:** Haiku 4.5 ($1/$5 pro 1M Tokens), typischer Call ~$0.0007-0.001, ungünstigster Fall (später Versuch, langer Verlauf, auf ~4000 Zeichen gekappt) ~$0.002-0.003. Laufender Betrieb bei realistischer Einzelnutzung geschätzt ~0.005-0.015 $/Tag (~0.15-0.45 $/Monat). Einmaliger Startup-Sweep über den aktuell angesammelten Rückstand (grob 30-50 unbenannte Threads) einmalig ~0.03-0.15 $. Bewusst NICHT in die sichtbare `chatCostToday`-Anzeige eingerechnet (die soll tatsächliches Gesprächsbudget widerspiegeln, nicht unsichtbare Hintergrund-Haushaltsführung) — nur Logging mit Prefix `[thread_naming]`.

**Bewusst nicht Teil dieses Schritts:** Drift-Erkennung; automatisches Zusammenführen gleichnamiger Threads; Sidebar-Gruppierung/Sortierung nach Titel; Einbindung der Benennungs-Kosten in die sichtbare Kosten-Anzeige.

Verifiziert: isolierte Testskripte gegen Scratch-`HOME` — 19 neue Checks für `session_memory.py` (Rundenzählung inkl. Tool-Loop-Ausschluss, `set_auto_title()`-Race-Sicherheit inkl. simulierter manueller Umbenennung während des Hintergrund-Laufs, `list_named_titles()` inkl. Regressionstest für den vom Review gefundenen `list_threads(limit=50)`-Recency-Bug, `list_unnamed_thread_ids()`), 34 neue Checks für `thread_naming.py` (gestubbtes `llm.complete` — Rundenzahl-Gates für beide Einstiegspunkte, Prompt-Inhalt, `UNCLEAR`-Pfad, Sanitizing, Exception-Abfangen, Startup-Sweep über mehrere Threads), 12 neue Checks gegen das ECHTE `pipeline.py`/`llm.py` (gestubbte schwere Abhängigkeiten — `llm.complete()`s SDK-Aufrufform, Hook-Gate: feuert nur bei aktivem Thread + gesetztem `broadcast_web_event` + echtem Rundenabschluss, nicht bei einem Tool-Use-Zwischenschritt). Alle drei bestehenden Thread-Testsuiten aus Teil 1/2/A erneut grün, keine Regression. `ast.parse` auf allen geänderten Python-Dateien sauber, `npm run build` (jarvis-web) sauber. Ein unabhängiger Plan-Review (Hintergrund-Agent) bestätigte den `list_threads()`-Recency-Bug unabhängig und schlug außerdem exponentiellen Backoff statt fester Runde 2/4/8 sowie Push nur an den auslösenden Tab statt Broadcast vor — beide Vorschläge bewusst NICHT übernommen, da sie Simons expliziten Vorgaben widersprochen hätten (er hatte genau diese beiden Punkte in seiner Korrektur des ersten Plan-Entwurfs festgelegt). Echter End-zu-End-Test durch Simon (Thread anlegen, 2-3 Nachrichten schreiben, Titel beobachten; zweites Fenster offen halten und prüfen dass es live mitzieht; manuellen Titel gegen Überschreiben testen; Server neu starten und den Backlog-Sweep beobachten) steht noch aus.

**Nachtrag, gleicher Tag — Sidebar-Politur + zwei UX-Fixes (jarvis-web, bewusst in denselben Branch/PR gebündelt statt separat):** anhand mehrerer Screenshots gemeldet:
- **Blaue `border-left` an Projekt-Threads entfernt** — wirkte "hart hässlich" (Simons Wortlaut). `.project-badge`-Textfarbe von akzentuiert-blau auf `var(--text-dim)` neutralisiert, ebenfalls auf Wunsch ("Farbe raus").
- **Projekt-Zeilen zeigen den Titel nur noch, wenn er sich vom Projektnamen unterscheidet** (neue `hasDistinctTitle()`): viele bestehende Projekt-Threads haben `title === project_name` (alte Find-or-Reuse-Anlage aus Teil 2) — zwei identische Zeilen sahen redundant aus. Ein späterer Thread mit eigenem, vom Projektnamen abweichendem Titel zeigt weiterhin beide Zeilen (echte Zusatzinfo, keine Dopplung).
- **Löschen war fälschlich auf freie Threads beschränkt** — generalisiert auf beliebige Threads (Projekt-Zuordnung bleibt beim Löschen unberührt, nur die `threads`-Zeile verschwindet).
- **Löschen des gerade AKTIVEN Threads wirkte wie ein Fehlschlag:** `confirmDeleteThread()` rief danach `store.newThread()` auf, was bei einem unbenannten Thread eine optisch identische "Unbenannt · HH:MM"-Ersatzkachel anlegte — sah aus als hätte sich nichts geändert. Fix: bevorzugt zu einem bestehenden Thread wechseln (wie ein Browser-Tab, der beim Schließen zum nächsten springt), nur bei wirklich leerer Liste neu anlegen.
- **"+ Neuer Chat" ignorierte den Leer-Check bei Projekt-Threads:** die in Punkt "+ Neuer Chat" legte immer einen neuen Thread an" (s.o.) eingebaute Abkürzung ("aktiver Thread schon leer → einfach dabei bleiben") galt fälschlich auch für Projekt-Threads — die dürfen aber leer sein (gerade erst angelegt/wiederverwendet), Simon will trotzdem einen neuen freien Chat öffnen können. Abkürzung gilt jetzt nur noch, wenn der aktive Thread sowohl leer ALS AUCH `project_id == null` ist.

Verifiziert: `npm run build` nach jeder Einzeländerung sauber. Echter Test durch Simon steht aus.

---

## 🔴 Coding-Jobs: max-turns/Modell konfigurierbar, Abbruch-Zusammenfassung, incomplete-Status (2026-08-01)

**Anlass:** Job #39 (erstes echtes Arbeitsticket, nicht mehr nur ein Test) scheiterte mit `terminal_reason: max_turns` ("Reached maximum number of turns (20)"), obwohl der Worker fünf Dateien/82 Zeilen sauber committet hatte. Deckte drei unabhängige Lücken im Mac-Worker-Coding-Weg auf, die bei trivialen Testläufen nie auffielen.

**1. `--max-turns` von 20 auf 50** — war an drei Stellen in `jarvis-web`s `localExec.js` hartcodiert (frischer Lauf + Resume + Resume-Fallback), jetzt eine gemeinsame Konstante `_MAX_TURNS`. `--max-budget-usd` bleibt die eigentliche Bremse (Simons Vorgabe), nur jetzt zusätzlich pro Projekt konfigurierbar (s.u.), nicht mehr fest `5.00`.

**2. Modell + Budget pro Projekt konfigurierbar** — neue `projekte`-Spalten `coding_model` (Default `claude-sonnet-5` — Job #39 lief auf `claude-opus-5`, dem Team-Plan-Default, weil nie `--model` gesetzt wurde, kostete $2.38 für einen einzigen Lauf) und `coding_max_budget_usd` (Default `5.00`, weiterhin überschreibbar für größere Tickets). Beide werden bei `start_job()` validiert (`coding_model` gegen `llm.MODEL_CATALOG`, ungültige Werte fallen still auf Sonnet zurück, gleiches Prinzip wie `pipeline.py::set_model()`) und wie `autonomy`/`delivery` in die `jobs`-Zeile gesnapshottet (eine spätere Projekt-Änderung betrifft nie einen laufenden/wartenden Job). Neue Dispatch-Felder `model`/`max_budget_usd` durch `_try_dispatch()`/`_resume_job()`, worker-seitig durch die komplette Aufrufkette bis in alle drei `claude`-CLI-Aufrufe. UI: neue Felder im "Coding"-Bereich der Projektansicht (`SeiteView.vue`) — Modell-Auswahl nutzt die bereits bestehende, mit `llm.py` synchron gehaltene `LLM_MODELS`-Konstante (`ChatView.vue`s Modell-Picker), keine dritte Kopie derselben vier Modelle.

**3. Abbruch-Zusammenfassung statt Roh-Dump, Rohstream ins Log:** kommt nie eine finale `result`-Zeile im `stream-json`-Output an (genau Job #39s Fall), landete bisher der komplette rohe NDJSON-Mitschnitt (157 KB) direkt in `jobs.result` — unbrauchbar in der Job-Ansicht. `_spawnClaudeStreaming` sammelt jetzt zusätzlich `toolActions` (dieselbe Distillation, die für die Live-Fortschrittszeile ohnehin schon berechnet wird, bisher nach dem Senden verworfen) und erfasst `capturedSessionId` aus dem ERSTEN Event mit einer `session_id` (typischerweise schon im initialen `system`-Event, nicht erst in der finalen `result`-Zeile) — Voraussetzung dafür, dass ein Abbruch ohne `result`-Zeile trotzdem eine Session zum Fortsetzen hat (siehe Punkt 5). Neue `_buildFallbackSummary()` (Abbruchgrund per Best-Effort-Substring-Suche, letzte ~10 Aktionen) ersetzt den Rohdump in `_finishJob`/`_finishPlanStage`; der volle Rohstream geht stattdessen per neuer `_appendJobRawLog()` nach `coding-job-logs/job-<id>-raw.log` im App-Datenverzeichnis (gleiches `@tauri-apps/plugin-fs`-Muster wie `worker_id.txt`/`allowlist.json`).

**4. Neuer Status `incomplete`:** ein Job, der bereits committet hat, ist nicht im Sinne von "nichts passiert" gescheitert. Kernbeobachtung: sobald `_finishJob` den Commit-Fehlschlag-Zweig passiert hat, steht bereits fest dass committet wurde — die verbleibenden `runOk`-Verzweigungen (delivery=local, Push/PR) wurden von `'failed'` auf `'incomplete'` umgestellt (ein fehlgeschlagener `git push` bleibt bewusst `'failed'` — Infrastrukturproblem, keine Lauf-Unvollständigkeit). Kleiner benachbarter Korrektheits-Fix: der "keine Änderungen"-Zweig war bisher hartcodiert `'done'` unabhängig von `runOk` — ein Max-Turns-Abbruch VOR jeder Änderung sah dadurch fälschlich wie ein Erfolg aus, jetzt `runOk ? 'done' : 'failed'`. Server: `_TERMINAL_STATUSES` + eigener Label-Zweig ("⚠️ unvollständig — Änderungen committet, Fortsetzung möglich"). Frontend: `incomplete` in `JobsView.vue`/`CodingJobCard.vue`s Filtern/Labels/CSS/Detail-Ansicht aufgenommen.

**5. Fortsetzen-Tool für `incomplete`-Jobs:** Status + Hinweistext allein helfen nicht ohne einen Weg, tatsächlich fortzusetzen — Job #39 war laut Simon fast fertig, ein Fortsetzen hätte gereicht. Neue, eigenständige `continue_job(job_id)` in `services/coding_jobs.py` — bewusst NICHT als dritter `_resume_job()`-Modus, da dessen Prompt-Bau einen zuvor gebilligten Plan voraussetzt ("approve"/"revise"); für eine reine Fortsetzung nach Turn-Limit gibt es keinen Plan, nur die ursprüngliche `instruction`. Neue Prompt-Bausteine `_build_continue_prompt()`/`_build_continue_fallback_prompt()` (knappes "Setze die Arbeit fort", kein Plan-/Kommentar-Konzept). Neues LLM-Tool `continue_coding_job(id)` (`tools.py`, TOOLS.md nachgezogen — die ganze Mac-Worker-Coding-Jobs-Tool-Familie fehlte dort bisher komplett, jetzt mit ergänzt). Protokoll: keine neue Konstante nötig, `CODING_JOB_ACTION`s freies `action`-Feld um `"continue"` erweitert. Frontend: "▶ Fortsetzen"-Knopf sowohl in `JobsView.vue` als auch in der Chat-Karte `CodingJobCard.vue`, beide nutzen den bereits generischen `store.codingJobAction(id, action, comment)` — keine Store-Änderung nötig.

**Bewusst nicht Teil dieses Durchgangs:** eine tiefere strukturelle Root-Cause-Analyse aller möglichen `stream-json`-Abbruchformen (der Best-Effort-Substring-Treffer deckt den konkret gemeldeten Fall ab); Live-Validierung von `coding_model` gegen einen echten API-Call (nur gegen die statische `MODEL_CATALOG`-Liste).

Verifiziert: 20 neue isolierte Checks gegen das ECHTE `services/coding_jobs.py` (Scratch-DB, `anthropic` gestubbt — das Modul importiert seit diesem Umbau zusätzlich `llm.py` für die Modell-Validierung, betrifft ab jetzt auch bestehende Testskripte für dieses Modul) — Default-Auflösung, Snapshot, ungültiges Modell/Budget fällt still zurück, `incomplete`-Status inkl. `_TERMINAL_STATUSES`, `continue_job()`s drei Gates (falscher Status, fehlende Session-ID, Erfolg inkl. korrektem Dispatch/Prompt). Bestehende `test_start_job.py`/`test_projekte_migration.py` erneut grün (mit demselben `anthropic`-Stub), keine Regression. `ast.parse`/`py_compile` auf allen geänderten Python-Dateien sauber, `node --check` auf `localExec.js`, `npm run build` (jarvis-web) sauber. `localExec.js`s eigentliche Kernlogik (`_buildFallbackSummary`/`_finishJob`/`_spawnClaudeStreaming`) lässt sich in dieser Umgebung nicht gegen einen echten `claude`-Prozess testen (kein Tauri-Runtime, keine echten API-Calls) — nur per Code-Review verifiziert. Echter End-zu-End-Test durch Simon (ein Ticket mit angepasstem Modell/Budget starten, einen Lauf bewusst ans Turn-Limit laufen lassen und Zusammenfassung/Log/`incomplete`-Status/Fortsetzen-Knopf prüfen) steht noch aus.

---

## 🔴 jarvis-web: Dark-HUD-Redesign — Cyan-Glow-Akzent, Strichicons, Bento-Grid (2026-08-01)

**Anlass:** Simon fand die Optik von jarvis-web "ziemlich hässlich", wollte einen modernen, dunklen, leicht "cyfy" Look. Erst ein Artifact-Mockup (Sidebar + Dashboard) zur Abstimmung, nach Zustimmung 1:1 in `jarvis-web` umgesetzt — eigener Branch `design/hud-dark-redesign` (PR [#32](https://github.com/justSimon13/jarvis-web/pull/32)), nicht `main`.

**Farbtokens (`style.css`):** bestehende CSS-Variablennamen behalten (kein Umbau jeder View nötig), nur Werte auf eine Cyan-Glow-HUD-Palette umgestellt (`--accent: #3ed6e8` statt Blau, dunkleres Blau-Schwarz statt neutralem Grau) — wirkt dadurch automatisch auf jede View, die `var(--accent)`/`var(--surface)`/etc. nutzt, nicht nur auf die zwei explizit umgebauten Dateien. Neue `--border-glow`-Variable und eine globale `.chamfer`-Utility-Klasse (gekappte Ecke per `clip-path`, bewusst sparsam auf Instrumenten-Flächen wie Eingabezeile/Budget-Panel/Kennzahlen-Kacheln, nicht auf jeder Karte).

**Icons:** neue `src/components/Icon.vue` — eigenes Strichicon-Set (14 Icons, inline SVG-Pfade) ersetzt die Emoji-Icons im Sidebar-Nav. Modals/Toasts/serverseitig gelieferte Quick-Action-Icons (`layout_config`) bewusst nicht angefasst — außerhalb des abgestimmten Scopes.

**Sidebar-Footer (`App.vue`):** Budget-Anzeige von reinem Text zu einem Panel mit Fortschrittsbalken (`costPct`-Computed, neu), Status-Zeile mit pulsierendem Punkt, Modus-Umschalter zu einem echten Segmented Control. Kontrastfix: weißer Text auf hellem Cyan (Badge, Bestätigen-Buttons) war kaum lesbar, jetzt dunkler Text auf Akzentfarbe.

**Dashboard (`DashboardView.vue`):** Kartenraster von gleichförmigem `auto-fit`-Grid auf ein Bento-Grid umgestellt (Todos/Kalender als 2-zeiliger Anker, Kennzahlen-Kacheln kompakt daneben), responsiver Fallback auf eine Spalte unter 640px.

**Bewusst nicht Teil dieses Durchgangs:** andere Views (Chat, Todos, Kalender, Buchhaltung, Tracking, …) — übernehmen nur die neue Farbpalette automatisch über die CSS-Variablen, keine strukturellen Änderungen; Emoji in Modals/Toasts/Sudo-Popup; kein Light-Mode (App war schon immer reines Dark-Theme, blieb so).

Verifiziert: `npm run build` sauber. Dev-Server gestartet, Dashboard- und Todos-Route per Playwright-Screenshot visuell geprüft (Sidebar-Icons, Bento-Grid mit echten Daten, Budget-Balken, Modus-Umschalter) — dabei einen echten Layout-Bug gefunden und behoben (Modus-Label "Fokus" lief bei 212px Sidebar-Breite mit Emoji+Text über den Rand hinaus, `.layout`s `overflow:hidden` schnitt es sichtbar ab; Emoji entfernt, Buttons textzentriert, jetzt wie im abgestimmten Mockup). Browser-Konsole auf Errors geprüft — keine. Echte End-to-End-Prüfung durch Simon (PR-Review) steht noch aus.

---

## 🟡 Bestehende offene Punkte (weiterhin gültig)

### Große Neuausrichtung — Entwurfsphase (seit 2026-07-28)
Simon arbeitet an einem grundlegenden Redesign (Datenmodell, API-Schnitt, Personas, Gedächtnis, Coding-Executor-Modell) — Entwürfe liegen in [`docs-draft/`](docs-draft/) (`JARVIS-Konzept-2026-07-28.md`, `JARVIS-Datenmodell-und-API.md`, `CLAUDE.md`), bewusst getrennt von den aktiven Root-Docs. **Noch nicht umgesetzt, wird laut Simon noch weiter überarbeitet** — dieser Abschnitt hier (ROADMAP.md) und der übrige Code beschreiben weiterhin den tatsächlichen IST-Stand, nicht den Entwurf. Vor jeder größeren Struktur-/Datenbankänderung erst prüfen, ob sie zufällig schon Teil dieser Neuausrichtung ist, um nicht gegen die spätere Richtung zu bauen.

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
