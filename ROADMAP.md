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
- Dedizierter `CODING_ENGINE_API_KEY` statt Fallback auf den Haupt-Key
- Konversationelle Konfigurierbarkeit der Eskalationsregeln
- Nur 1 Coding-Task gleichzeitig (gilt jetzt projektübergreifend — zwei Tasks in unterschiedlichen Projekten können aktuell trotzdem nicht parallel laufen)

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

**Zur "Session teilt sich"-Beobachtung:** Vermutlich Simons eigener Workaround (manuell "+ Neuer Chat" nach dem Eindruck JARVIS hänge fest) statt eines automatischen Effekts — mit obigem Fix sollte die Ursache dafür aber ohnehin entfallen, ein einzelner Fehlschlag zieht keine Kaskade mehr nach sich.

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
