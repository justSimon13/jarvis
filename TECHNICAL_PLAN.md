# J.A.R.V.I.S. — Technischer Idealplan

Letzte Überarbeitung: 2026-05-04

Dieses Dokument beschreibt, wie JARVIS **sein soll** — unabhängig davon, was aktuell implementiert ist.
Es ist die Grundlage für den nächsten Entwicklungsschritt: Gap-Analyse und priorisierter Umbau.

---

## 1. Vision & Kernprinzip

**JARVIS ist kein Chatbot. JARVIS ist ein persönliches Betriebssystem.**

Ein Chatbot wartet auf Eingaben und antwortet. JARVIS handelt proaktiv, kennt Simon besser als Simon sich selbst, erinnert sich zuverlässig und greift ein bevor Simon fragen muss.

### Was das in der Praxis bedeutet

| Eigenschaft | Konkret |
|-------------|---------|
| **Proaktiv** | JARVIS erinnert ohne Anfrage — überfällige Todos, verpasste Schlafzeiten, Termine |
| **Verlässliches Gedächtnis** | Was Simon einmal sagt, gilt. Abmachungen werden nicht vergessen. |
| **Modi** | Assistent (Alltag), Coach (Reflexion/Wachstum), Fokus (ablenkungsarm) |
| **Lokal & privat** | Alles läuft auf dem eigenen Server. Kein Cloud-Zwang außer LLM und Integrationen. |
| **Konsistent** | JARVIS ist immer JARVIS — gleiche Persönlichkeit, gleiche Verlässlichkeit, egal welcher Client |

### JARVIS als Interface zur gesamten Umgebung

JARVIS ist das Interface zu Simons gesamter Welt — digitale Daten (Todos, Kalender, Finanzen) genauso wie physische Umgebung (Licht, Musik, Geräte, Smart Home). Alles läuft über dieselbe Schnittstelle, alles profitiert vom gleichen Kontext.

Smart Home ist kein separates System neben JARVIS — es ist ein weiteres Set an Werkzeugen. Licht an/aus, Musik abspielen, Einkaufsliste, Heizung — alles sind Tool-Calls wie Notion-Write oder Kalender-Abruf. JARVIS kann dabei Kontext nutzen: "Licht aus" beim Schlafen-Gehen ist anders als "Licht aus" beim Film schauen.

### Designprinzipien

1. **Server ist die einzige Wahrheitsquelle** — Clients sind I/O-Geräte
2. **Push, nicht Pull** — JARVIS meldet sich, Simon muss nicht fragen
3. **Gedächtnis first** — Jede Unterhaltung hinterlässt Spuren, die beim nächsten Mal wirken
4. **Kontext kostet** — Nur laden was relevant ist; Token-Budget ist endlich
5. **Robustheit over Features** — Ein zuverlässiger Kern schlägt zehn instabile Extras
6. **Offene Werkzeugkiste** — Jede neue Integration ist ein neues Modul in `services/`; der Kern (Pipeline, LLM, Brain) bleibt unberührt. Keine künstlichen Grenzen.
7. **JARVIS denkt, nicht nur reagiert** — JARVIS verbindet Punkte die Simon gerade nicht im Kopf hat. Er hört zu, merkt sich Beiläufiges, und bringt es zum richtigen Moment wieder.

### JARVIS ist menschlich — der Reflection Loop

Simon sagt beiläufig "ich würde gerne wieder mal Motorrad fahren." Ein Assistent hört das, antwortet höflich, vergisst es. JARVIS schreibt es ins Gedächtnis.

Drei Wochen später: Sonntagmorgen, schönes Wetter, keine Termine → JARVIS verbindet: Motorrad + Wetter + Simon fährt selten raus → *"Perfekter Tag für eine Tour."*

Das ist kein Kalender-Eintrag, kein Todo, kein geplanter Reminder. Das ist JARVIS der *denkt*.

**Architektonisch:** Ein Reflection Loop läuft periodisch (z.B. 1× täglich morgens oder bei bestimmten Triggern wie Wetterwechsel):
```
→ liest brain.memory
→ schaut aktuelle Bedingungen (Wetter, Wochentag, letzte Aktivitäten, Tageszeit)
→ Haiku bewertet: "Gibt es eine sinnvolle, nicht-offensichtliche Verbindung?"
→ wenn ja UND Timing passt UND nicht kürzlich erwähnt → Push via NotificationDispatcher
```

**Gegen Nervigkeit:** Nicht die Verbindungen reduzieren — das Timing und Lernen verbessern:
- Frequency-Cap: dasselbe Thema max. 1× pro Woche
- Feedback-Loop: Simon sagt "erwähn das nicht mehr" → landet in brain.memory als `do_not_surface`
- Stille Stunden: kein Reflection-Push wenn Fokus-Modus aktiv oder nach 22 Uhr

---

## 2. Systemarchitektur — Idealzustand

```
┌─────────────────────────────────────────────────────────────────┐
│                    JARVIS Server (HP EliteDesk)                  │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                     WebSocket :8765                        │   │
│  └──────────┬────────────────────────────┬───────────────────┘   │
│             │                            │                        │
│  ┌──────────▼──────────┐   ┌────────────▼──────────────────┐    │
│  │  JarvisPipeline     │   │  NotificationDispatcher        │    │
│  │  (pro Client)       │   │  (Server-Singleton)            │    │
│  │                     │   │                                │    │
│  │  STT → LLM → TTS   │   │  notify(text, channels)        │    │
│  │  Tool Execution     │   │  Delivery-Queue (SQLite)       │    │
│  │  History (shared)   │   │  Retry bei nächstem Connect    │    │
│  └──────────┬──────────┘   └────────────┬──────────────────┘    │
│             │                            │                        │
│  ┌──────────▼────────────────────────────▼──────────────────┐   │
│  │                    Brain (SQLite)                          │   │
│  │  profile · behavior · memory · followups · events         │   │
│  │  modules · config                                         │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌───────────────────────┐   ┌──────────────────────────────┐   │
│  │  ProactiveDaemon      │   │  ContextLoader               │   │
│  │  (Background-Thread)  │   │  (Modul-basiert)             │   │
│  │                       │   │                              │   │
│  │  CalendarReminder     │   │  base → immer                │   │
│  │  SleepCoach           │   │  todos → bei Check-in        │   │
│  │  TodoReminder         │   │  calendar → bei Termin       │   │
│  │  FollowupReminder     │   │  projects → bei Freelancing  │   │
│  │                       │   │  btc → bei Finanzthema       │   │
│  │  → NotificationDispatcher  └──────────────────────────────┘   │
│  └───────────────────────┘                                        │
└─────────────────────────────────────────────────────────────────┘
            │ ws://...                    │ ws://...
   ┌────────┴────────┐          ┌─────────┴──────────┐
   │ jarvis-satellite │          │  jarvis-dashboard   │
   │  Wake Word, VAD  │          │  Vue 3 PWA (iPad)   │
   │  Audio I/O       │          │  Text-Input         │
   │  Lokale Alarme   │          │  overlay_event UI   │
   └─────────────────┘          └────────────────────┘
```

### Datenflussprinzip

- **Gespräch:** Client → Pipeline → LLM → Tools → Antwort → Client
- **Push:** ProactiveDaemon → NotificationDispatcher → Dashboard (overlay_event) + Voice (idle TTS-Queue)
- **Gedächtnis:** Jedes Gespräch → SessionMemory → Brain.memory (persistiert, gealtert)
- **Kontext:** ContextLoader bestimmt welche Module geladen werden → statischer System-Prompt

---

## 3. Komponentendesign

### 3.1 Brain & Memory System

Das Brain ist das Gedächtnis von JARVIS. Es muss zuverlässig, skalierbar und relevant bleiben.

#### Memory-Section (Idealzustand)

Jeder Memory-Eintrag hat ein vollständiges Schema:

```
{
  "id":       "uuid4",
  "text":     "Simon möchte bis Oktober 3 neue Freelancing-Kunden haben",
  "ts_iso":   "2026-05-04",
  "category": "ziele",       // ziele | abmachungen | vorlieben | kontext | followup
  "weight":   1.0,           // sinkt mit Alter
  "source":   "gespräch"     // gespräch | check-in | nutzer-explicit
}
```

**Aging-Logik:**
- Einträge ≤ 30 Tage: `weight = 1.0`
- 31–90 Tage: `weight = 0.5`
- > 90 Tage: `weight = 0.1` (Kandidat für Archivierung)
- Explizit als `source=nutzer-explicit` markierte Einträge: kein Aging

**Pruning:** Max 100 Einträge. Wenn überschritten: niedrigstes `weight` fliegt raus (Ausnahme: `nutzer-explicit`).

**Deduplication:** Vor jedem Write wird geprüft ob ein ähnlicher Eintrag existiert (Substring-Match auf `text`, gleiche `category`). Existiert einer → Update statt Insert.

**Interface:**
```
brain.remember(text, category, source="gespräch")    # schreibt neuen Eintrag
brain.forget(entry_id)                               # löscht Eintrag
brain.get_memory(top_k=20, min_weight=0.1)           # lädt für Prompt
brain.apply_aging()                                  # weights aktualisieren (täglich)
```

**Phase 2 — RAG:**
Wenn Brain.memory > 80 Einträge: ältere (weight < 0.5) in ChromaDB auslagern.
Beim Prompt-Build: semantische Suche via Embedding statt vollständige Liste laden.

---

### 3.2 Push Notification Layer

JARVIS muss Nachrichten schicken können **ohne dass gerade ein Gespräch läuft**.
Das ist der zentrale Unterschied zu einem Chatbot.

#### NotificationDispatcher

Ein Server-Singleton, unabhängig von jeder Pipeline.

```
NotificationDispatcher
  .notify(text, channels, priority, expires_in_min)
  .get_pending(client_id)          # unzugestellte Notifications
  .mark_delivered(notification_id)
```

**Channels:**
- `dashboard` → sendet direkt als `overlay_event` an alle verbundenen Dashboard-Clients
- `voice` → legt Text in eine Idle-TTS-Queue; wird gesprochen wenn `pipeline.state == idle`
- `persistent` → speichert in SQLite; wird bei nächstem Connect zugestellt

**Notification-Objekt (SQLite):**
```
{
  "id":           "uuid4",
  "text":         "Erinnerung: Schlafenszeit in 30 Minuten",
  "channels":     ["dashboard", "voice"],
  "priority":     "normal",          // low | normal | high
  "created_at":   "2026-05-04T22:30:00",
  "expires_at":   "2026-05-04T23:30:00",
  "delivered_at": null               // null = ausstehend
}
```

**Delivery-Logik:**
1. `notify()` aufgerufen → in SQLite speichern
2. Für jeden aktiven Dashboard-Client: sofort als `overlay_event` senden → `delivered_at` setzen
3. Für Voice: wenn `pipeline.state == idle` → TTS abspielen → `delivered_at` setzen
4. Beim Connect eines neuen Clients: pending Notifications (nicht abgelaufen) nachliefern
5. Abgelaufene Notifications (past `expires_at`) werden beim nächsten Startup bereinigt

**Rate-Limiting:** Max 3 Notifications/Stunde global (verhindert Spam bei Daemon-Fehler).

---

### 3.3 Proactive Daemon

Der Daemon läuft als Background-Thread und prüft periodisch ob Aktionen nötig sind.
Er kommuniziert **ausschließlich** über `NotificationDispatcher.notify()` — nie direkt in eine Pipeline.

#### Kernprinzip: Proaktive Verhaltensweisen sind Daten, nicht Code

Simon konfiguriert Erinnerungen über Gespräche — nicht über Settings-Menüs oder Code-Änderungen.

*"Erinner mich jeden Tag um 23:30 ans Bett gehen"* → JARVIS schreibt eine Regel ins Brain.
*"Jeden Donnerstag: meine Mutter besuchen"* → JARVIS schreibt eine Regel ins Brain.
Der Daemon liest alle aktiven Regeln und führt sie aus. Kein Code nötig für neue Erinnerungen.

#### Zwei Typen von Checks

**Typ 1 — User Rules (Daten, via Gespräch konfiguriert)**

Zeitbasierte Regeln die Simon JARVIS mitteilt:

```json
{
  "id":       "abc123",
  "description": "Jeden Tag um 23:30 ans Schlafen erinnern",
  "schedule": "daily@23:30",
  "text":     "Zeit fürs Bett",
  "escalate": true,
  "active":   true
}
```

```json
{
  "id":       "def456",
  "description": "Jeden Donnerstag Mutter besuchen",
  "schedule": "weekly@thursday@10:00",
  "text":     "Heute ist Donnerstag — hast du deine Mutter schon besucht?",
  "escalate": false,
  "active":   true
}
```

JARVIS versteht natürliche Sprache und übersetzt sie in Rules. Simon muss kein Schema kennen.
Rules werden in `brain.rules` gespeichert — lesbar, editierbar, löschbar via Gespräch.

`escalate: true` → Tonalität steigt wenn Simon nicht reagiert (soft → direkt → insistent).

**Typ 2 — Integration Checks (Code, externe APIs)**

Kommen von externen Quellen — brauchen API-Client-Code, können nicht rein zeitbasiert konfiguriert werden:

| Check | Quelle | Frequenz |
|---|---|---|
| CalendarReminder | Google Calendar API | alle 5 Min |
| TodoReminder | Notion API | 1× täglich |
| FollowupReminder | brain.followups | Session-Start + 1× täglich |
| VIP Email | IMAP | alle 10 Min |
| BTC Alert | CoinGecko API | bei Schwellwert (konfigurierbar) |

Neue Integrationen = neuer Service in `services/` + neues Check-Modul. Kern bleibt unberührt.

#### Reflection Loop (Typ 3 — kreativ, LLM-basiert)

```
1× täglich morgens (+ optionale Trigger wie Wetterwechsel)
→ liest brain.memory + brain.rules
→ schaut: Wetter, Wochentag, Simon State, letzte Aktivitäten
→ Haiku: "Gibt es eine sinnvolle, nicht-offensichtliche Verbindung?"
→ wenn ja: notify_generated() → Push
→ wenn nein: nichts
```

Frequency-Cap: gleiches Thema max. 1× pro Woche.

#### Fired-State — überlebt Restarts

```json
{
  "rules":       {"abc123": "2026-05-04T23:30:00"},
  "calendar":    {"2026-05-04_zahnarzt": "2026-05-04T09:45:00"},
  "reflection":  {"motorrad": "2026-04-28"}
}
```

---

### 3.4 Context Loading — Modular

Jede Session lädt heute alles: Todos, Projekte, Konzepte, Kalender, BTC, Routinen.
Das kostet Tokens und erzeugt Fehlverhalten (z.B. Check-in-Trigger beim Architektuurgespräch).

#### ContextLoader

Module sind registriert mit Ladebedingungen:

```
MODULE         TRIGGER
─────────────────────────────────────────────
base           immer (Identität, Verhalten, Zeit, Session-Memory)
todos          Check-in erkannt ODER "todo|aufgabe|heute" in erster Message
calendar       "termin|kalender|wann|heute" ODER Check-in
projects       "projekt|freelancing|kunde|auftrag|arbeit"
btc            "bitcoin|btc|kurs|crypto|preis|portfolio"
routines       Check-in erkannt ODER "sport|routine|check"
concepts       "idee|konzept|plan|vorhaben"
followups      immer (klein, wichtig für Verlässlichkeit)
```

**Modus bestimmt zwei Dinge gleichzeitig:**

Der aktive Modus (`assistent`, `coach`, `fokus`) steuert sowohl welche Module geladen werden als auch wie JARVIS sich verhält. Beides kommt aus `brain.modules.modes[aktiver_modus]`.

```
assistent → Module: todos, calendar, btc, projects
            Verhalten: direkt, aufgabenorientiert, erinnert an offene Punkte

coach     → Module: memory, followups, progress
            Verhalten: stellt tiefere Fragen, hinterfragt, reflektiert

fokus     → Module: nächster Termin (minimal)
            Verhalten: kein Smalltalk, nur das Wesentliche
```

Neuer Modus = einmal in `brain.modules.modes` definieren: Module + Verhalten. Kein Code nötig.

**Keyword-Classifier auf erster User-Message:**
```python
def detect_modules(first_message: str, mode: str) -> list[str]:
    modules = ["base", "followups"] + MODE_DEFAULT_MODULES[mode]
    text = first_message.lower()
    for keyword, module in KEYWORD_MAP.items():
        if keyword in text:
            modules.append(module)
    if is_checkin(text):
        modules += ["todos", "calendar", "routines", "btc"]
    return list(set(modules))
```

**Nachladen:** Merkt JARVIS mid-conversation dass er ein Modul braucht das nicht geladen ist → kurzer API-Call, Kontext erweitern, weiter. Kein Neustart nötig.

**Fallback:** Kurze oder unklare erste Message → vollständiger Kontext.

**Prompt-Caching:** Statischer Prompt (Base + Brain) gecacht. Dynamische Module (Kalender, BTC) unkecacht.

---

### 3.5 Tool Execution — Idempotent

Claude kann denselben Tool-Call in einer Streaming-Session mehrfach auslösen (Retry bei API-Error, Netzwerkproblem). Schreibende Tools müssen dagegen geschützt sein.

#### Idempotenz-Strategie

Jeder Tool-Call aus der Anthropic-API hat eine eindeutige `tool_use_id`.
Diese ID wird in der Pipeline-Session getrackt:

```python
class JarvisPipeline:
    _executed_tool_ids: set[str]   # geleert bei Session-Start

def execute(tool_name, tool_input, call_id):
    if call_id in _executed_tool_ids:
        return _tool_result_cache[call_id]    # gecachtes Ergebnis zurückgeben
    result = _dispatch(tool_name, tool_input)
    _executed_tool_ids.add(call_id)
    _tool_result_cache[call_id] = result
    return result
```

**Für Notion-Write zusätzlich:** Dedup-Key aus `title + database + session_start_ts`.
Wenn ein Page mit diesem Key bereits in der laufenden Session erstellt wurde → Return der existierenden Page-ID.

**Einheitliches Return-Format:**
```python
{
    "success": True,
    "data":    {...},     # Tool-spezifischer Payload
    "error":   None       # oder Fehlermeldung als String
}
```

---

### 3.6 Simon State — Zustandstracking

JARVIS muss wissen in welchem Zustand Simon sich befindet — nicht raten. Das betrifft Push-Delivery, Schlaf-Tracking und Statistiken.

#### Zustände

```
awake_home     → normaler Betrieb, alle Pushes erlaubt
sleeping       → keine Pushes, Schlaf-Log läuft
away           → nur Dashboard, kein Voice
focused        → reduzierte Pushes (Fokus-Modus aktiv)
do_not_disturb → explizit stumm
```

#### Signale die den State aktualisieren

| Signal | State-Änderung |
|---|---|
| "JARVIS, ich schlafe jetzt" | → `sleeping`, Einschlafzeit geloggt |
| Wecker dismissed | → `awake_home`, Aufstehzeit geloggt |
| Wake Word erkannt während `sleeping` | → `awake_home` |
| "Ich bin unterwegs" | → `away` |
| Fokus-Modus aktiviert | → `focused` |
| Smart Home Presence (Zukunft) | → automatisch |

#### Fehlendes Signal — kein Raten, kein Nerven

Wenn JARVIS kein Schlaf-Signal bekommt:
- Option A: Nächsten Tag kurz nachfragen — *"Wann bist du gestern schlafen gegangen?"*
- Option B: Einfach Lücke in den Statistiken lassen

JARVIS rät nicht ("du warst wahrscheinlich um 23 Uhr schlafen"). JARVIS nervt nicht (fragt höchstens einmal nach, dann Lücke akzeptieren).

#### Persistenz

```json
{
  "state":        "awake_home",
  "since":        "2026-05-04T07:15:00",
  "sleep_log": [
    {"date": "2026-05-04", "slept_at": "23:10", "woke_at": "07:15"},
    {"date": "2026-05-03", "slept_at": null, "woke_at": "08:00"}
  ]
}
```

`slept_at: null` = kein Signal bekommen — ehrliche Lücke, keine Schätzung.

---

### 3.8 Settings & Client-Konfiguration

Einstellungen leben an zwei Orten — sauber getrennt:

**Lokal in `.env` (Hardware-spezifisch, einmalig beim Setup):**
- Server-URL
- API-Keys
- Bluetooth-Adresse

**Zentral in `brain.config.clients` (änderbar via Dashboard & Gespräch):**
- Lautstärke, TTS-Geschwindigkeit, Wake-Word-Empfindlichkeit
- Aktiver Lautsprecher & Mikrofon

#### Audio-Device-Auswahl ohne SSH

Beim Connect meldet der Satellite alle verfügbaren Devices:

```json
{
  "type":             "client_hello",
  "name":             "wohnzimmer",
  "available_outputs": ["Anker PowerConf", "Built-in Audio", "USB Audio"],
  "available_inputs":  ["Blue Yeti", "Built-in Mic"],
  "active_output":     "Anker PowerConf",
  "active_input":      "Blue Yeti"
}
```

Dashboard (EinstellungenView) zeigt pro Client:
```
Wohnzimmer
  Lautsprecher: [Anker PowerConf ▼]
  Mikrofon:     [Blue Yeti ▼]
  Lautstärke:   70%
```

Simon wählt → Server updated `brain.config.clients.wohnzimmer` → pusht Config an Satellite → Satellite wechselt Device ohne Neustart.

Auch via Gespräch: *"JARVIS, nutz im Wohnzimmer das USB-Mikrofon"* → gleicher Weg.

Kein SSH, kein manuelles `.env`-Editieren für Alltagsänderungen.

---

### 3.10 Satellite Audio — Zuverlässigkeit

Stationäre Lautsprecher am Satellite müssen 24/7 verfügbar sein. Portable Bluetooth-Speaker und viele kabelgebundene Boxen haben Auto-Power-Off — sie schalten sich bei Stille ab. Das macht sie für always-on ungeeignet ohne Gegenmaßnahmen.

#### Audio-Keepalive

Alle 4–5 Minuten einen unhörbaren Ton abspielen. Der Speaker "hört" Audio und bleibt aktiv.

```python
# Satellite: Background-Thread
async def _keepalive_loop():
    while True:
        await asyncio.sleep(270)    # alle 4.5 Min
        _play_silent(duration_ms=200)
```

Verhindert Auto-Off bei kabelgebundenen Boxen zuverlässig.
Bei Bluetooth: hilft teilweise — aber Bluetooth + Linux + 24/7 ist grundsätzlich fragil.

#### Hardware-Empfehlung für stationäre Clients

| Option | Zuverlässigkeit | Aufwand |
|---|---|---|
| USB-Lautsprecher (USB-powered) | Hoch — kein Auto-Off, kein BT | Minimal |
| Klinke + Keepalive | Mittel — abhängig vom Modell | Keepalive implementieren |
| Bluetooth | Niedrig — Drops, Reconnect-Logik nötig | Hoch |

Langfristig: USB-Lautsprecher für stationäre Clients. Kein Auto-Off, kein Bluetooth, kein Absturz-Risiko.

---

### 3.7 Session Memory — Zuverlässig

Am Ende jeder Session wird die Konversation zusammengefasst und für zukünftige Sessions verfügbar gemacht.

#### Anforderungen

- **Kein Datenverlust:** Wenn die Zusammenfassung fehlschlägt, wird der Raw-Transcript gespeichert
- **Kein Race Condition:** Save ist synchron oder explizit gequeued mit Acknowledgement
- **Sauberes JSON:** Haiku gibt validiertes JSON zurück — kein nachträgliches Strippping nötig

#### Ablauf

```
Session-Ende (Timeout oder explizit)
    │
    ├── save_session(api_history)
    │       │
    │       ├── summarize_with_haiku(history)   ← sync call, max 10s timeout
    │       │       ├── Erfolg: write {summary, context, follow_ups} to sessions.db
    │       │       └── Fehler: write {raw_transcript: last_20_turns} to sessions.db
    │       │
    │       └── update brain.followups mit neuen follow_ups aus Summary
    │
    └── clear api_history, set session_break marker
```

**System-Prompt für Haiku:**
```
Du analysierst ein Gespräch und gibst exakt dieses JSON zurück (kein Markdown, kein Text davor/danach):
{"summary": "...", "context": "...", "follow_ups": ["...", "..."]}
```

---

## 4. Datenmodelle

### Memory-Eintrag (brain.memory)
```json
{
  "id":       "a1b2c3d4-...",
  "text":     "Simon möchte bis Oktober 3 neue Freelancing-Kunden haben",
  "ts_iso":   "2026-05-04",
  "category": "ziele",
  "weight":   1.0,
  "source":   "gespräch"
}
```

### Notification (notifications.db)
```json
{
  "id":           "e5f6a7b8-...",
  "text":         "Erinnerung: Schlafenszeit in 30 Minuten",
  "channels":     ["dashboard", "voice"],
  "priority":     "normal",
  "created_at":   "2026-05-04T22:30:00",
  "expires_at":   "2026-05-04T23:30:00",
  "delivered_at": null
}
```

### ContextModule (intern, nicht persistiert)
```python
@dataclass
class ContextModule:
    name:             str
    trigger_keywords: list[str]
    trigger_modes:    list[str]    # ["assistent", "coach", "fokus"] oder subset
    always_load:      bool
    loader:           Callable[[], str]   # gibt Prompt-Text zurück
```

### SessionSummary (sessions.db)
```json
{
  "date":        "2026-05-04",
  "time":        "09:32",
  "summary":     "Simon hat über Freelancing gesprochen...",
  "context":     "Aktuelle Priorität ist Kundenakquise",
  "follow_ups":  ["Status Angebot Kunde A erfragen", "Bitcoin-Kauf bei nächstem Dip"]
}
```

---

## 5. WebSocket-Protokoll-Erweiterungen

Das bestehende Protokoll (drei Layer) bleibt unverändert. Ergänzungen:

### Neue Message-Typen

**Server → Client: `notification_push`**
```json
{
  "type":     "notification_push",
  "id":       "e5f6a7b8-...",
  "text":     "Überfällige Todos: 3 Einträge",
  "priority": "normal",
  "expires":  "2026-05-04T23:59:00"
}
```
Unterschied zu `overlay_event`: kein Snooze/Skip, keine Routine-Logik — reine Benachrichtigung.

**Client → Server: `notification_ack`**
```json
{
  "type": "notification_ack",
  "id":   "e5f6a7b8-..."
}
```
Server setzt `delivered_at`, schickt nicht erneut.

### Bestehende Typen (unverändert)
`overlay_event` bleibt für Routinen mit Snooze/Skip-Logik.
`notification_push` ist für einfache, einmalige Hinweise ohne Aktionsbuttons.

---

## 6. Umsetzungsreihenfolge

Die Reihenfolge folgt der Architekturlogik — was ist Fundament für was.

### Phase 1 — Notification-Fundament
**Voraussetzung für alles andere (Proactive, SleepCoach, TodoReminder)**

1. `NotificationDispatcher` implementieren
   - SQLite-Tabelle `notifications`
   - `notify()`, `get_pending()`, `mark_delivered()`
   - Integration: beim Client-Connect pending Notifications nachliefern

2. Protokoll: `notification_push` + `notification_ack` in `protocol.py`

3. Dashboard: `notification_push` empfangen und anzeigen (einfaches Toast/Banner)

### Phase 2 — Proactive Daemon Umbau
**Nutzt NotificationDispatcher statt Pipeline**

4. `ProactiveDaemon` refactoren: `pipeline.process_text()` → `dispatcher.notify()`
5. `SleepCoach` refactoren: gleiche Änderung + echten Sleep-Log als Basis nutzen
6. `TodoReminder` hinzufügen (neu)
7. `FollowupReminder` hinzufügen (neu)
8. Fired-State-Persistence für alle Module

### Phase 3 — Brain Memory
**Macht JARVIS zuverlässiger über Zeit**

9. Memory-Schema einführen: Timestamps, category, weight, source
10. Aging-Funktion (`apply_aging()`), täglich ausführen
11. Max-100-Pruning + Deduplication beim Write
12. `brain.remember()` / `brain.forget()` / `brain.get_memory()` als sauberes Interface

### Phase 4 — Context Modularisierung
**Token-Einsparung, weniger Fehlverhalten**

13. `ContextLoader` mit Modul-Registry bauen
14. Keyword-Classifier implementieren
15. `context.py` umschreiben um ContextLoader zu nutzen

### Phase 5 — Tool Idempotenz
**Stabilitäts-Fix für Mehrfach-Writes**

16. `call_id`-Tracking in JarvisPipeline
17. Dedup-Key für Notion-Write
18. Einheitliches Return-Format in `tools.py`

### Phase 6 — Langfristig
19. RAG via ChromaDB (wenn Memory > 80 Einträge)
20. Smoke-Tests pro Service

### Phase 7 — Neue Integrationen & Features

#### Technische Features (neuer Code)

| Feature | Service | Beschreibung |
|---|---|---|
| **Hue Lichtsteuerung** | `hue.py` | Direkte Hue Bridge REST API, lokal, kein Cloud-Zwang |
| **Presence Detection** | `presence.py` | Fritz!Box API alle 2 Min → Simon State automatisch (zuhause/weg) |
| **Google Contacts** | `contacts.py` | Google Contacts API (OAuth bereits vorhanden), Geburtstage automatisch erkennen |
| **RSS News** | `news.py` | RSS-Parser, konfigurierbare Quellen, für Morning Briefing |
| **Morning Briefing Push** | ProactiveDaemon | Automatischer Push: Wetter + Kalender + BTC + News — braucht Phase 1+2 als Fundament |

#### Bereits möglich — nur Konfiguration nötig

| Feature | Was zu tun ist |
|---|---|
| **Einkaufsliste** | PocketBase-Collection (nach Phase 7 Notion-Ablösung) oder Notion-Liste |
| **Geburtstage (manuell)** | Als `brain.rules`: "Jedes Jahr am 15. März: Mutter Geburtstag" |
| **Beziehungs-Erinnerungen** | Als `brain.rules`: "Alle 4 Wochen: bei X melden" |
| **YouTube Musik** | yt-dlp bereits im Satellite, läuft |
| **Morning Briefing (reaktiv)** | Check-in gibt bereits Wetter + Kalender + BTC — heute schon möglich |

---

### 3.11 Modus-Arbeitshandbuch (Mode Playbook)

JARVIS braucht mehr als einen kurzen Modus-Beschreibungstext. Jeder Modus braucht ein explizites **Arbeitshandbuch** — Guidelines, Entscheidungsprotokoll, Qualitätsstandards. Das ist der Unterschied zwischen einem LLM der antwortet und einem Assistenten der arbeitet.

#### Problem heute

`brain.modules.modes.assistent.prompt` enthält nur:
```
"Fokus auf Todos, Termine und Projekte. Aktiv auf offene Punkte hinweisen."
```

Das führt zu Verhalten wie: JARVIS fragt beim Check-in ob ein Todo erledigt ist — obwohl er es selbst in Notion nachschauen könnte.

#### Neue Struktur: brain.modules.modes

```json
{
  "assistent": {
    "description": "Standard-Modus: Alltag, Produktivität, Planung",
    "prompt": "...",
    "guidelines": [
      "Bevor du nach dem Status einer Aufgabe fragst: prüfe ihn selbst in Notion",
      "Beim Check-in: lade aktuelle Daten, stelle keine Fragen die du selbst beantworten kannst",
      "Verbinde verwandte Informationen: offene Rechnung + bevorstehendes Meeting mit gleichem Kunden",
      "Präsentiere nicht alles — filtere auf was heute relevant ist"
    ],
    "decision_protocol": {
      "when_to_ask":   "Nur wenn Information nicht verfügbar oder Aktion irreversibel",
      "when_to_act":   "Bei klarem Intent und reversiblen Aktionen",
      "when_to_check": "Immer bevor du über den Status von etwas spekulierst"
    },
    "quality_rules": [
      "Keine doppelten Todos erstellen — erst prüfen",
      "Datum in der Vergangenheit oder Feiertag → erwähnen",
      "Nach Write-Operationen: Erfolg bestätigen oder Fehler melden"
    ]
  },
  "coach": {
    "guidelines": [
      "Nicht voreilig lösen — Simon selbst denken lassen",
      "Hinterfragen statt bestätigen",
      "Fortschritt zu früheren Zielen und Abmachungen aktiv verfolgen"
    ],
    "decision_protocol": {
      "when_to_ask": "Oft — gute Fragen sind das Werkzeug des Coaches",
      "when_to_act": "Selten — Coach löst nicht, Coach begleitet"
    }
  },
  "fokus": {
    "guidelines": [
      "Niemals unterbrechen ohne triftigen Grund",
      "Keine Zusatzinfos, keine Smalltalk",
      "Nur das direkt Gefragte — nichts mehr"
    ],
    "decision_protocol": {
      "when_to_ask":   "Fast nie — im Zweifelsfall die naheliegendste Aktion nehmen",
      "when_to_act":   "Sofort und direkt"
    }
  }
}
```

#### Wer schreibt das Handbuch

- **Initial:** von Simon und Claude gemeinsam definiert (wie jetzt)
- **Erweiterbar:** Simon kann via Gespräch neue Guidelines hinzufügen — *"JARVIS, merk dir: beim Check-in immer zuerst den Kalender zeigen"* → landet in `guidelines`
- **Lernend:** JARVIS schlägt neue Guidelines vor wenn er ein Muster erkennt — Simon bestätigt oder verwirft

#### Wie es in den Prompt kommt

`ContextLoader` lädt `brain.modules.modes[aktiver_modus]` vollständig — Guidelines, Protokoll und Qualitätsregeln landen im statischen System-Prompt. Claude folgt ihnen wie Instruktionen.

---

### 3.12 Aktives Lernen & Verhaltens-Retrospektive

JARVIS lernt aktiv aus jeder Session — nicht nur durch Zusammenfassungen, sondern durch destilliertes Wissen und reflektiertes Verhalten.

#### Drei Lerntypen

**1. Faktisches Lernen** → brain.memory
Beiläufig Erwähntes das JARVIS über Simon weiß: Präferenzen, Lebensumstände, Gewohnheiten.
Schreibt JARVIS autonom — low risk, jederzeit korrigierbar.

**2. Wissens-Extraktion** → Wissensdatenbank
Am Session-Ende analysiert Haiku: *"Haben wir hier Wissen erarbeitet das über diese Session hinaus nützlich ist?"*
```
Riester-Rente-Analyse → speichern (ttl: 180 Tage)
BTC Markteinschätzung → speichern (ttl: 90 Tage)
Smalltalk über Wetter  → nichts speichern
```
Schreibt JARVIS autonom mit `source: session-extract` — Simon sieht was gespeichert wurde.

**3. Verhaltenslernen** → Mode Playbook / brain.behavior
Am Session-Ende Retro: gab es Korrekturen? Frustration? Muster?
→ JARVIS **schlägt vor**, ändert nie autonom:
*"Ich habe bemerkt dass du dreimal nachgefragt hast ob ich den Todo-Status selbst prüfen kann. Soll ich das als Guideline aufnehmen?"*
→ Simon bestätigt → Guideline wird geschrieben
→ Simon lehnt ab → verworfen

**Faustregel:**
- Fakten & Wissen → JARVIS kann autonom speichern
- Verhalten & Guidelines → immer Simon-Bestätigung

#### Versionierung & History

Vor jeder Änderung an Mode Playbook oder brain.behavior → Snapshot der aktuellen Version speichern.

```
guideline_history
  section       → "modules.modes.assistent.guidelines"
  version       → 3
  content       → [...]
  changed_at    → "2026-05-05T14:30:00"
  change_reason → "Simon bestätigt: Todo-Status selbst prüfen statt fragen"
```

JARVIS behält die letzten 10 Versionen pro Section. Rücksetzen via Gespräch:
*"JARVIS, setz die Assistent-Guidelines auf den Stand von letzter Woche zurück."*

**Was versioniert wird:** Mode Playbook, brain.behavior, Wissensdatenbank-Einträge
**Was nicht versioniert wird:** brain.memory (append-only reicht), brain.followups

---

### 3.13 Persönliche Wissensdatenbank

Simons persönliche Wikipedia — Frameworks, Strategien, Referenzwissen. JARVIS konsultiert sie bevor er das Internet fragt. JARVIS kann sie selbst erweitern.

Verschieden von:
- `brain.memory` → persönliche Fakten über Simon
- Notion Konzepte → Ideen & Projekte
- Internet → aktuell aber ungeprüft

#### Schema

```json
{
  "id":            "btc_market_phases",
  "title":         "BTC Marktphasen-Einschätzung",
  "content":       "...(Markdown)...",
  "tags":          ["bitcoin", "investing", "market"],
  "last_verified": "2026-03-01",
  "ttl_days":      90,
  "evergreen":     false,
  "source":        "simon-explicit"
}
```

#### Freshness-Logik

- `evergreen: true` → gilt immer (persönliche Werte, grundlegende Strategien, Fakten)
- `evergreen: false` + TTL überschritten → JARVIS nutzt den Eintrag, erwähnt aber: *"Diese Info ist X Monate alt — noch aktuell?"*
- Reflection Loop prüft 1× wöchentlich auf veraltete Einträge → Push an Simon

#### Priorität beim Antworten

```
1. Wissensdatenbank  (persönlich, vertrauenswürdig)
2. brain.memory      (Fakten über Simon)
3. Internet          (aktuell, aber ungeprüft)
```

#### Wer schreibt

- Simon explizit: *"JARVIS, merke dir: ..."* → `source: simon-explicit`, kein Aging
- JARVIS nach Recherche: *"Soll ich das für dich speichern?"* → nach Bestätigung → `source: jarvis-research`
- JARVIS autonom (bei klaren Fakten): schreibt direkt, informiert Simon kurz

#### Speicherort

PocketBase-Collection `knowledge` (nach Phase 9) oder eigene SQLite-Tabelle bis dahin.
Markdown-Content wird als Text gespeichert — Dashboard rendert es.

---

### Phase 9 — Strategisch: Notion-Ablösung

**✅ Erledigt (2026-07-19)** — abweichend vom hier skizzierten PocketBase-Ansatz: direktes SQLite (`local_data.py`), konsistent mit brain.db/sessions.db/tracking.db, kein neuer Server-Prozess. Konzepte wurden komplett gestrichen statt migriert. Details: `knowledge/programmierung/jarvis_projekt.md`.

**Ziel (ursprünglich):** Notion durch eigenes Backend auf dem JARVIS-Server ersetzen. Keine externe API-Abhängigkeit, kein Rate Limit, kein Caching-Problem.

**Was ersetzt wird:**

| Notion DB | Schema | Inhalt |
|---|---|---|
| Todos | Name, Status, Priorität, Aufwand, Bereich, Datum | Strukturierte Daten → SQLite |
| Konzepte | Name, Status, Typ, Bereich, Notiz + Seiteninhalt | Markdown-Body → SQLite + Datei |
| Projekte | Name, Status, Typ, Zeitraum, Kunden, Umsatz | Strukturierte Daten + Relations → SQLite |
| Kunden | Name + Metadaten | Strukturierte Daten → SQLite |

**Kein Canvas — nur Markdown.** Konzeptseiten werden als Markdown gespeichert. Dashboard rendert sie (Markdown-Parser existiert bereits).

**Technologie: PocketBase**
- Single Binary auf dem HP-Server, SQLite darunter
- Eingebaute Admin-UI (Simon editiert Todos/Projekte direkt)
- REST API → JARVIS nutzt sie statt Notion API
- Real-time Subscriptions → Dashboard hört auf Änderungen
- Neue Collections via Admin-UI ohne Code-Änderungen
- JARVIS kann via Gespräch neue Collections anlegen lassen → dynamisches Schema

**Architekturprinzip jetzt:** `notion.py` ist die einzige Datei die Notion-spezifischen Code enthält. Kein anderer Code darf direkt Notion-Objekte kennen. Wenn der Austausch kommt → `notion.py` wird durch `pocketbase.py` ersetzt, nichts anderes.

**Aufwand MVP:** ~1 Woche (PocketBase Setup + JARVIS-Integration + Migration + Dashboard-Views).

---

*Dieser Plan beschreibt den Sollzustand. Die Gap-Analyse (aktueller Code vs. dieser Plan) ist der nächste Schritt.*
