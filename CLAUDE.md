# J.A.R.V.I.S. — Entwicklungskontext für Claude Code

## Session-Start (IMMER ausführen)

Zu Beginn jeder neuen Konversation diese Dateien lesen:
1. `ROADMAP.md` — aktueller Backlog, Prioritäten, offene Punkte
2. `ARCHITECTURE.md` — verbindliche Architektur-Referenz (bei technischen Fragen)
3. `PRODUCT.md` — Produktumfang: was JARVIS können soll, wie, wo, und explizit was NICHT (bei Scope-/Produktfragen)

Bei technischer Implementierungsarbeit zusätzlich:
4. `CODE_REFERENCE.md` — Datei-für-Datei-Code-Referenz (Funktionen, Zeilennummern, bekannte Ecken)
5. `TOOLS.md` — vollständige LLM-Tool- und MCP-Tool-Referenz
6. `TECHNICAL_PLAN.md` + `GAP_ANALYSIS.md` — **veraltet (Stand 2026-05-04)**, nur noch als historischer Ideal-Entwurf relevant, siehe Hinweis-Banner in den Dateien selbst

Danach kurz bestätigen was der aktuelle Stand ist, bevor mit der Arbeit begonnen wird.

## ⚠️ Aktueller Fokus (Stand 2026-07-24)

Von den ursprünglich fünf Punkten aus `GAP_ANALYSIS.md` ist nur noch einer offen:

1. **Mode Playbook** — `guidelines`/`decision_protocol`/`quality_rules` in `brain.modules.modes` (bisher nur kurzer Prompt-Text pro Modus). Details: `ROADMAP.md`, Abschnitt "Bestehende offene Punkte".

NotificationDispatcher, Proactive-Daemon-Reparatur, Brain-Memory-Schema und Context-Modularisierung sind fertig und verifiziert (siehe `ROADMAP.md` "✅ Fertig", `ARCHITECTURE.md`, `CODE_REFERENCE.md`). Seitdem zusätzlich umgesetzt: Notion-Ablösung, Wissensdatenbank inkl. Wiki-Verlinkung, Coding Engine, Ticket-Integration Phase 1, MCP-Server, jarvis-web als drittes Frontend, Tailscale-Zugriff — vollständige, laufend gepflegte Liste in `ROADMAP.md`.

---

## Was ist JARVIS?

Kein Chatbot. Ein **persönliches Betriebssystem** für Simons Alltag — lokal auf dem HP-Server, mit echtem Gedächtnis, echten Integrationen, vollständig auf Simon zugeschnitten. Konzeptuell **ein Team aus Rollen**: Assistent (Organisation), Coach (Reflexion/Wachstum), Fokus (reduziert aufs Wesentliche). Nicht reaktiv — proaktiv.

---

## Repos

| Repo | Pfad (lokal) | Rolle |
|---|---|---|
| `j.a.r.v.i.s.` | `~/Documents/Arbeit/.../Apps/j.a.r.v.i.s.` | Server/Core — **dieses Repo** |
| `jarvis-web` | `~/Documents/Arbeit/.../Apps/jarvis-web` | Vue 3 (Tauri-Desktop-App) — Chat/Wissen/Todos/Projekte/Kontakte/Kalender/Tracking |
| `jarvis-dashboard` | `~/Documents/Arbeit/.../Apps/jarvis-dashboard` | Vue 3 PWA (iPad) |
| `jarvis-satellite` | `~/Documents/Arbeit/.../Apps/jarvis-satellite` | Headless Audio-Client (Linux) |

---

## System-Topologie

```
JARVIS Server (server.py) — HP EliteDesk, wss://jarvis.tail47e1d9.ts.net:8766 (Tailscale Serve, TLS)
  (roh: ws://jarvis.tail47e1d9.ts.net:8765 oder ws://192.168.0.155:8765 im LAN — beide bleiben nutzbar)
  ├─ Brain (SQLite ~/.jarvis/brain.db) — 7 Sections
  ├─ Shared History (api_history + display_history)
  ├─ LLM Pipeline (Claude Sonnet 4.6 + Prompt-Caching)
  ├─ Proactive Daemon (Kalender-Reminder, VIP-Email)
  └─ WebSocket :8765
       ├─ jarvis-satellite  (Wake Word → WAV → Server → PCM → Lautsprecher)
       └─ jarvis-dashboard  (Vue 3 PWA, iPad, Port 5173)
```

---

## Schlüsseldateien (dieses Repo)

| Datei | Aufgabe |
|---|---|
| `server.py` | Einstieg, WebSocket-Handler, Session-Logik, Layout-Config |
| `pipeline.py` | `JarvisPipeline` — WAV→STT→LLM→TTS pro Client |
| `context.py` | System-Prompt-Builder, Prompt-Caching, SQLite-Cache |
| `llm.py` | Anthropic-Wrapper (Streaming + Cache-Control-Headers) |
| `brain.py` | SQLite-Abstraktion, 7 Sections, dot-notation access |
| `tools.py` | 20+ Tool-Definitionen für Claude |
| `client_manager.py` | Client-Registry, Audio-Routing, Mode-Tracking |
| `session_memory.py` | Session-Zusammenfassung in SQLite |
| `protocol.py` | Alle WebSocket-Nachrichtentypen (Konstanten + Kommentare) |
| `config.py` | Env-Loading, Pfade |
| `local_data.py` | Todos/Projekte/Kontakte — lokales SQLite, ersetzt Notion seit 2026-07-19 |
| `services/` | calendar, email, alarm, timer, btc, weather, search, reminders, music, proactive, sleep_coach |

---

## Brain-Sektionen (`brain.py`)

| Section | Inhalt |
|---|---|
| `profile` | Wer Simon ist, Ziele, Lebenssituation, Interessen |
| `memory` | Was JARVIS aus Gesprächen gelernt hat |
| `followups` | Offene Punkte mit optionalem Fälligkeitsdatum |
| `events` | Routinen (Check-In/Out, Sport), Zeitfenster, Feature-Flags |
| `behavior` | Tonalität, Antwortstil, Erinnerungspräferenzen |
| `modules` | System-Prompt-Sektionen (base identity + modus-spezifisch) |
| `config` | Todos/Projekte-Lade-Parameter, Weather-City, Feature-Flags (technisches) |

Zugriff immer via `brain.read(section)` / `brain.write(section, key, value)`.

---

## WebSocket-Protokoll (Kurzreferenz)

Alle Typen als Konstanten in `protocol.py`. Wichtigste:

**Server → alle Clients:** `state`, `transcript`, `response_start/chunk/done`, `tool`, `error`
**Server → Dashboard:** `dashboard_sync`, `dashboard_update`, `layout_config`, `overlay_event`, `session_break`
**Server → Satellite:** `set_alarm`, `cancel_alarm`, `play_music`, `stop_music`
**Client → Server:** `client_hello`, `text_input` (tts=bool), `ping`, `data_request`, `set_mode`, `overlay_dismiss`
**Satellite → Server:** `alarm_sync`, `alarm_ringing`, `alarm_dismissed`

**Binary:** Client→Server = WAV, Server→Client = PCM 24kHz mono int16

---

## Architektur-Entscheidungen (NICHT ändern ohne Diskussion)

1. **History pro Kategorie:** `api_histories`/`display_histories` sind Dicts mit zwei Kategorien, `"voice"` (alle Sprach-/Raum-Clients — teilen sich weiterhin einen gemeinsamen Live-Kontext, das ist gewollt) und `"web"` (Dashboard-Rolle, z.B. jarvis-web — bewusst isoliert). Ableitung über `_category_for_role()` aus dem `role`-Feld. Bis 2026-07-19 war das ein einziger globaler Puffer für alle Clients; geändert nach wiederholten Vermischungs-Bugs (Sprach-Begrüßung landete in Web-Sessions). Details: `knowledge/programmierung/jarvis_projekt.md`. Session-Timeout weiterhin: `api_histories[kategorie]` leeren, Break-Marker setzen.

2. **Prompt-Caching:** Statischer Prompt (Brain + Session-Memory) mit `cache_control: ephemeral` gecacht. Dynamischer Teil (Zeit, Kalender, BTC) immer frisch. TTL ~5 Min.

3. **Server-driven UI:** `layout_config` (Cards, Quick-Actions) wird server-seitig in `_build_layout_config()` berechnet. Dashboard ist generischer Renderer — keine Business-Logik im Frontend.

4. **Drei-Layer-Protokoll:**
   - Layer 1 DATA: `data_request` → sofortige Antwort ohne LLM
   - Layer 2 ACTION: `text_input` mit Template-Text → LLM verarbeitet
   - Layer 3 CONVERSATION: freies Chat

5. **Clients sind reine I/O** — keine API Keys, keine Logik. Server ist Single Source of Truth.

6. **Kein Docker** (Audio + Docker = Chaos auf Linux).

7. **Alarm läuft auf Satellite unabhängig** — überlebt Server-Trennung. Server kennt Alarme via `ALARM_SYNC` beim Connect.

---

## Modi & Layout-Config

Drei Modi: `assistent` | `coach` | `fokus`

Default-Cards (`_DEFAULT_CARD_IDS` in `server.py`):
- assistent: transcript, btc, weather, todos, calendar
- coach: todos, calendar
- fokus: (leer)

Default-Quick-Actions (`_DEFAULT_QA_IDS`):
- assistent: alarm, todo_add, checkin
- coach: wochenreview, fortschritt, ziel_setzen
- fokus: timer, naechstes_event

Cards und Quick-Actions können per `brain.modules.modes.<modus>` überschrieben werden.

---

## Persistenz

| Datei | Ort | Inhalt |
|---|---|---|
| `brain.db` | `~/.jarvis/` | Brain-Sections (SQLite) |
| `history.json` | `~/.jarvis/` | Letzte 200 display_history Einträge |
| `sessions.db` | `~/.jarvis/` | Session-Summaries |
| `alarm_registry.json` | `~/.jarvis/` | Server-seitige Alarm-Registry |
| `local_data.db` | `~/.jarvis/` | Todos/Projekte/Kontakte (SQLite, ersetzt Notion) |
| `calendar_cache.json` | `~/.jarvis/` | Calendar-Cache (15 Min TTL) |

---

## Deployment

- **Server:** `python3 server.py` als systemd system service (`jarvis.service`) auf HP EliteDesk, Port 8765
- **Dashboard (altes iPad-Tool):** `npm run dev` als systemd service (`jarvis-dashboard.service`), lokal `:5173`
- **jarvis-web:** eigenes Repo (`justSimon13/jarvis-web`, privat), `~/apps/jarvis-web`, systemd service (`jarvis-web.service`), lokal `:5174`, `.env` mit `VITE_JARVIS_WS_URL`
- **Satellite:** `python3 client.py` als systemd system service, `Group=audio`
- **Tailscale (seit 2026-07-21):** Maschinenname `jarvis` (Tailnet `tail47e1d9`), Zugriff über `tailscale serve`
  statt roher Ports/IP — funktioniert auch von unterwegs, nicht nur im Heim-LAN:
  - `https://jarvis.tail47e1d9.ts.net` → jarvis-web (proxy → `:5174`)
  - `https://jarvis.tail47e1d9.ts.net:8443` → jarvis-dashboard (proxy → `:5173`)
  - `wss://jarvis.tail47e1d9.ts.net:8766` → JARVIS-Backend-WebSocket (proxy → `:8765`, TLS nötig
    weil die Web-Apps jetzt über https laufen — Browser blocken sonst unverschlüsseltes `ws://`
    als Mixed Content von einer `https://`-Seite aus)
  - LAN-IP (`192.168.0.155`, rohe Ports) bleibt zusätzlich nutzbar, rein additiv

---

## Roadmap

Vollständige, priorisierte Liste → **ROADMAP.md**

Kurzübersicht (Top 3):
1. **Kontext-Switching** — Brain-Sections modus-basiert laden, nicht alles immer (context.py Umbau)
2. **Push-Reminder** — proactive.py für allgemeine Reminder erweitern (Todos, Followups, nicht nur Kalender)
3. **RAG / Langzeitgedächtnis** — Brain memory-Section mit Vektordatenbank entlasten

---

## Arbeitsworkflow

`ROADMAP.md` ist das lebende Dokument — einzige Wahrheitsquelle für offene und erledigte Arbeit.
- Etwas fertig → sofort `✅` in ROADMAP.md
- Etwas Neues → sofort in ROADMAP.md eintragen
- Kein Punkt als erledigt kommunizieren ohne ROADMAP.md aktualisiert zu haben

## Bericht am Ende eines Laufs

Simon reviewt nicht zeilenweise. Jeder abgeschlossene Auftrag endet mit drei Angaben — knapp, ohne Code-Zitate:

1. **Was geändert wurde, was bewusst nicht, und wo vom Plan abgewichen wurde.** Abweichungen immer nennen, auch wenn sie sinnvoll waren.
2. **Geänderte Dateien mit Umfang** (`git diff --stat`). Dateien, die im Plan nicht vorkamen, ausdrücklich hervorheben.
3. **Testnachweis:** was wurde geprüft, mit welchem Ergebnis. Bei additiven Umbauten gehört dazu, ob der bisherige Weg noch funktioniert.

Temporäre Änderungen an Konfigurationsdateien (Ports, Pfade, Flags) immer erwähnen, auch wenn sie zurückgesetzt wurden.

**Doku im selben Lauf nachziehen.** Wer Code ändert, aktualisiert die betroffene Beschreibung (`ARCHITECTURE.md`, `CODE_REFERENCE.md`, `TOOLS.md`, `ROADMAP.md`). Passt eine Änderung nicht zur bestehenden Doku, ist das im Bericht zu nennen statt stillschweigend zu belassen — sonst driften Code und Doku auseinander (zwei solche Fälle wurden am 29.07. gefunden).

## Konventionen

- Alle Print-Ausgaben mit Prefix `[modulname]` und `flush=True`
- Kommentare auf Deutsch oder Englisch (gemischt ist ok)
- Services sind isoliert — keine Cross-Service-Imports, Server koordiniert
- Neue WebSocket-Typen immer in `protocol.py` als Konstante definieren
- Brain-Änderungen immer via `brain.write()`, nie direktes SQLite
- `api_history` wird MIT `history_lock` modifiziert
- `GIT_CONVENTIONS.md` (Repo-Wurzel) gilt für Commits der Coding-Engine selbst (`delegate_coding_task`/`commit_and_push`) — nicht bindend für Commits von Simon oder Claude Code von Hand. Ein Projekt unter `config.PROJECTS_ROOT` kann eine eigene `GIT_CONVENTIONS.md` in seiner Wurzel anlegen, die dann Vorrang hat.

## Arbeitsweise bei Umbauten

**Branch statt main.** Keine Commits direkt auf main, kein Push ohne Rücksprache.

**Keine Worktrees.** Direkt im Arbeitsverzeichnis, ein Branch pro Aufgabe.

**Schrittweise, nicht auf einmal.** Neue Struktur neben die alte legen, migrieren, alte entfernen. Nach jedem Schritt muss das System lauffähig sein. Das Entfernen der alten Struktur ist immer ein eigener, späterer Schritt.

**Vor jedem Datenbank-Umbau prüfen, ob ein aktuelles Backup existiert.** Wenn nicht, erst darauf hinweisen.

## Ausführung auf dem Mac-Worker

**Keine interaktive Login-Shell.** Nicht `zsh -lic "..."` aufrufen, sondern die Binary direkt mit vollem Pfad und explizit gesetztem `PATH`/`HOME`. Sonst werden die zsh-Startdateien gelesen — und `compinit` stellt dort bei unsicheren Verzeichnisrechten eine interaktive Ja/Nein-Frage, die ein Worker nicht beantworten kann. Der Prozess hängt dann still, ohne Fehlermeldung.

Ursache separat beheben (meist Homebrew-Pfade mit Gruppen-Schreibrecht):

```bash
compaudit | xargs chmod g-w,o-w
# falls nötig:
chmod -R go-w /opt/homebrew/share
```

**Account vor jedem Job prüfen.** `claude auth status` liefert `email` und `orgId`. Vor einem Lauf gegen die erwartete Kennung des Clients abgleichen und bei Abweichung abbrechen — privat: `info@simonfischer.dev`, Arbeit: `digital35 GmbH`. Damit kann ein Arbeitsprojekt nicht über den privaten Account laufen.

**`--bare` nicht verwenden.** Überspringt OAuth und Keychain, verlangt einen API-Key und liest auch `CLAUDE_CODE_OAUTH_TOKEN` nicht — würde die Abrechnung vom Abo auf Token-Preise umstellen.

**Kein `ANTHROPIC_API_KEY` in Umgebungsdateien.** Ist die Variable gesetzt, rechnet Claude Code zu Token-Preisen ab statt über das Abo.

**Bei unbeaufsichtigten Läufen immer `--max-turns` und `--max-budget-usd` setzen.**
