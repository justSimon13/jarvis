# J.A.R.V.I.S. — Architektur

Dieses Dokument ist die verbindliche Referenz für alle Implementierungsentscheidungen.
Letzte Überarbeitung: 2026-05-04

---

## Kernprinzip

**JARVIS ist ein System, nicht mehrere Assistenten.**

- Eine History, ein Brain, ein Kontext — für alle Clients
- Clients sind dumme I/O-Geräte: Audio rein, Audio raus, Events empfangen
- Alle Entscheidungen (Kontext, Wissen, Verhalten) leben auf dem Server
- Ausnahmen nur wo physisch nötig: Alarm-Klingel lokal, Audio-I/O lokal

---

## Physische Topologie

```
HP EliteDesk (24/7, Linux) — ws://192.168.0.155:8765
├── server.py            ← WebSocket-Server :8765
├── jarvis-satellite/    ← Wohnzimmer-Client (läuft auch auf HP EliteDesk)
├── jarvis-dashboard/    ← Vite Dev Server :5173 (systemd user service)
├── ~/.jarvis/brain.db   ← Brain-Datenbank
├── ~/.jarvis/sessions.db
└── ~/.jarvis/alarm_registry.json

iPad (Browser/PWA)
└── jarvis-dashboard/    ← Vue PWA (Home Screen App), verbindet zu HP

Mac (Entwicklung)
└── main.py              ← Standalone-Modus (CLI, kein Server)

Schlafzimmer-Client (geplant)
└── jarvis-satellite/    ← Orange Pi / RPi, gleiches Script
```

---

## Repos

| Repo | Beschreibung |
|------|-------------|
| `jarvis` | Server — läuft 24/7 auf HP EliteDesk |
| `jarvis-satellite` | Headless Audio Client (Lenovo, Raspberry Pi) |
| `jarvis-dashboard` | iPad PWA |

---

## Pipeline

Eine `JarvisPipeline`-Instanz pro Client — aber **eine geteilte History und ein geteilter Kontext**.

```
Client A ──→ JarvisPipeline A ──┐
                                 ├──→ shared_history  (in-memory, Server)
Client B ──→ JarvisPipeline B ──┘     shared_context  (brain.db)
```

**Gleichzeitiger Input:** FIFO-Queue — immer nur eine Pipeline schreibt gleichzeitig in die History.

Pipeline pro Client bleibt wegen:
- PCM-Routing ist Client-spezifisch (Audio geht an den Client der gesprochen hat)
- TTS-State ist Client-spezifisch

---

## Voice Pipeline

```
Mikrofon (Client)
    │ lokal auf Client
Wake Word Detection   (openWakeWord)
    │ lokal auf Client
VAD Recording         (RMS-basiert, kein ML — Silero entfernt)
    │ WAV via WebSocket → Server
STT                   (ElevenLabs Scribe)
    │
LLM                   (Claude Sonnet, streaming)
    │ ├── Tool Call → execute() → weiter
    │ PCM via WebSocket → Client
TTS                   (ElevenLabs, entfällt bei tts=false)
    │ auf Client
Lautsprecher
```

TTS läuft parallel zum LLM-Streaming (sobald ein Satz erkannt wird → sofort an ElevenLabs).

---

## Brain DB — Sections

`~/.jarvis/brain.db`, alle Sections als JSON-Blob.

| Section     | Inhalt                                                                  |
|-------------|-------------------------------------------------------------------------|
| `profile`   | Wer Simon ist — freies Key-Value, kein festes Schema                   |
| `behavior`  | Wie JARVIS sich verhalten soll (Tonalität, Priorisierung, Stil)        |
| `memory`    | Was JARVIS über Simon gelernt hat (Liste von Einträgen)                |
| `followups` | Offene Punkte für nächstes Gespräch (mit optionalem `due`-Datum)      |
| `events`    | Routinen: Check-In, Check-Out, Sport, sonstige Zeitfenster             |
| `modules`   | Prompt-Inhalte — Basis-Identität + pro Modus (assistent/coach/fokus)  |
| `config`    | Technisches: Notion-Config, Proaktiv-Einstellungen, Wetter-City etc.   |

> **Migration von alten Sections:**
> `settings` → aufgeteilt in `behavior` + `events` + `config`
> `context_config` → `config`

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

---

### brain.profile — Format

Freies Key-Value-Dict, **kein festes Schema**. JARVIS schreibt/liest beliebige Keys.
Alle Keys erscheinen automatisch im Prompt — kein `ordered_keys`, kein `label_map`.

```json
{
  "name": "Simon Fischer",
  "standort": "München",
  "alter": "28",
  "interessen": "Züge, Bitcoin, Freelancing",
  "btc_bestand": "...",
  "langfristige_ziele": "..."
}
```

---

## Config-Split: .env vs brain.config

**`.env` enthält nur Secrets:**
- API Keys (Anthropic, ElevenLabs, Notion, Google, etc.)
- SMTP/IMAP Credentials
- Alles was ein Token, Key oder Passwort ist

**`brain.config` enthält alles andere:**
- `weather_city`
- Notion-Datenbank-IDs und Lade-Parameter
- Proaktiv-Service-Einstellungen
- Server-Port (optional)

Regel: Kann JARVIS es sinnvoll lesen oder ändern? → `brain.config`. Ist es ein Secret? → `.env`.

---

## System-Prompt — Zwei-Schichten-Modell

```
Static Prompt  (Anthropic Prompt Cache, 5 min TTL)
├── brain.modules.base           ← Identität + Grundregeln
├── brain.modules.modes[aktiv]   ← Modus-spezifischer Abschnitt
├── brain.profile                ← Wer Simon ist (alle Keys)
├── brain.behavior               ← Verhaltensregeln
├── brain.events                 ← Aktive Routinen
├── brain.followups              ← Fällige offene Punkte
├── brain.memory                 ← Gelerntes
├── session_memory               ← Letzte 3 Tage Sessions
└── context (Notion-Cache)       ← Todos, Projekte, Konzepte

Dynamic Prompt  (kein Cache, immer frisch)
├── Aktuelle Uhrzeit + Wochentag + Tageszeit
├── Google Calendar (nächste Events)
└── BTC-Preis
```

**Aktiver Modus:** Kommt vom Client per `CLIENT_HELLO` (`mode`-Feld) oder per `set_mode`-Event.
Server wählt `brain.modules.modes[modus]` für den Static Prompt.

---

## WebSocket-Protokoll — Drei Layer

### Layer 1: DATA (kein LLM, keine Token)

Direkte Datenanfragen. Sofortige Antwort vom Server.

```jsonc
// Request:
{"type": "data_request", "resource": "todos"}

// Response:
{"type": "data_response", "resource": "todos", "data": [...]}
```

Verfügbare Resources: `todos`, `calendar`, `alarms`, `followups`, `clients`, `btc`

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

## Dashboard — Server-Driven UI

Der Server definiert was gezeigt wird. Das Frontend ist ein generischer Renderer.

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
- Alarm-Klingel-Logik (muss offline funktionieren)
- Minimale Config: Server-URL, Audio-Device-Name, Client-Name

**Fliegt raus:**
- `SYSTEM_PROMPT_BASE` — hat im Client nichts zu suchen
- Jede Logik die besser auf dem Server aufgehoben ist

---

## Implementierungsstand (Mai 2026)

Alle initialen Aufgaben sind abgeschlossen:

- ✅ Brain-Migration — neue Sections, `brain.py` generisch
- ✅ SYSTEM_PROMPT_BASE → brain.modules, config.py auf Secrets reduziert
- ✅ Shared History — `api_history` + `display_history`, Session-Timeout 15 Min
- ✅ Drei-Layer-Protokoll — `data_request`/`data_response` im Server
- ✅ layout_config — Server berechnet Cards + Quick Actions pro Modus
- ✅ Dashboard — generischer Renderer, Vue 3 PWA, deployed auf HP + iPad
- ✅ Satellite-Cleanup — minimaler Client, RMS-VAD, kein Prompt-Code
- ✅ Proactive Service — Kalender-Reminder + VIP-E-Mail-Push (proactive.py)
- ✅ Overlay Events — Routinen mit Zeitfenster (Check-In/Out), Snooze/Skip
- ✅ Session-Break — Marker in History nach Inaktivität, Dashboard-Push
- ✅ Wetter-Card — weather.py + Dashboard-Integration

**Nächste Schritte → siehe ROADMAP.md**

---

## Verzeichnisstruktur (Ziel)

```
server.py              ← WebSocket-Server
pipeline.py            ← STT → LLM → TTS
client_manager.py      ← Client-Registry + Dashboard-Routing
protocol.py            ← Event-Typen
brain.py               ← Brain DB (generisch, kein hardcoded Schema)
context.py             ← System-Prompt Builder
llm.py                 ← Anthropic Streaming + Prompt Caching
stt.py                 ← ElevenLabs Scribe
tts.py                 ← ElevenLabs TTS
tools.py               ← Tool-Definitionen + execute()
session_memory.py      ← Session-Zusammenfassungen (Haiku)
config.py              ← nur Secrets aus .env
main.py                ← Standalone CLI (unverändert)
services/
  alarm.py             ← Wecker-Registry + Sleep-Log
  calendar.py          ← Google Calendar (cached)
  btc.py               ← CoinGecko (cached)
  notion.py            ← Notion API
  email.py             ← IMAP/SMTP
  proactive.py         ← Kalender-Reminder + VIP-Email-Push
  timer.py             ← Software-Timer
  search.py            ← DuckDuckGo
  sleep_coach.py       ← Schlaferinnerungen
  client_music.py      ← Musik-Routing an Satellite
```
