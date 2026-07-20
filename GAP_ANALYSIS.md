# J.A.R.V.I.S. — Gap-Analyse

Erstellt: 2026-05-04  
Basis: `TECHNICAL_PLAN.md` vs. aktueller Codestand

Legende: ✅ vorhanden und passt · ⚠️ vorhanden aber falsch/unvollständig · ❌ fehlt komplett

---

## Kurzübersicht

| Komponente | Status | Aufwand |
|---|---|---|
| WebSocket-Basis (STT → LLM → TTS) | ✅ | — |
| Brain-Grundstruktur (7 Sections) | ✅ | — |
| Shared History + Session-Timeout | ✅ | — |
| Server-Driven UI (layout_config) | ✅ | — |
| Modi (assistent/coach/fokus) | ✅ | — |
| Alarm-System (Satellite-lokal) | ✅ | — |
| Brain Memory (Schema) | ⚠️ | M |
| Proactive Daemon | ⚠️ | L |
| Session Memory (Zuverlässigkeit) | ⚠️ | S |
| Mode Playbook (brain.modules.modes) | ⚠️ | M |
| Context Loading (modular) | ⚠️ | L |
| User Rules (brain.rules) | ⚠️ | M |
| NotificationDispatcher | ❌ | L |
| notification_push/ack (Protokoll) | ❌ | S |
| Simon State (Zustandstracking) | ❌ | M |
| Sleep Log | ❌ | S |
| Tool Idempotenz (call_id) | ❌ | S |
| Reflection Loop | ❌ | L |
| Persönliche Wissensdatenbank | ❌ | L |
| Guideline-Versionierung | ❌ | M |
| Audio Keepalive (Satellite) | ❌ | S |
| Client-Settings in brain.config | ❌ | M |
| Notion-Ablösung (lokales SQLite statt PocketBase) | ✅ 2026-07-19 | XL |

Aufwand: S = Stunden · M = 1–2 Tage · L = 3–5 Tage · XL = 1+ Woche

---

## 3.1 Brain & Memory System

**Status: ⚠️ Vorhanden, Schema fehlt komplett**

`brain.py` hat die `memory`-Section (Zeile 6), gibt eine Liste zurück (Zeile 31).

Was fehlt:
- Jeder Eintrag ist ein flaches Dict ohne Schema — kein `id`, kein `ts_iso`, kein `category`, kein `weight`, kein `source`
- Kein Aging (kein `apply_aging()`)
- Kein Max-100-Pruning
- Keine Deduplication beim Write
- Kein sauberes Interface — kein `brain.remember()`, kein `brain.forget()`, kein `brain.get_memory()`

Was tun:
```
brain.py   → remember() / forget() / get_memory() / apply_aging() hinzufügen
             Memory-Write migrieren: altes Format → neues Schema (einmalig)
```

---

## 3.2 Push Notification Layer

**Status: ❌ Existiert nicht**

Kein `NotificationDispatcher`, keine `notifications`-SQLite-Tabelle, kein `notify()`-Interface.

Was stattdessen existiert:
- `overlay_event` (WebSocket-Typ) wird von proactive.py genutzt — aber direkt, ohne Delivery-Tracking
- Kein Retry bei offline Client, keine `expires_at`, keine `delivered_at`

Was fehlen:
- `services/notification_dispatcher.py` (neu)
- SQLite-Tabelle `notifications` (in `~/.jarvis/notifications.db` oder erweitert in `brain.db`)
- `protocol.py`: `NOTIFICATION_PUSH` + `NOTIFICATION_ACK` Konstanten (fehlen)
- Server-seitige Logik: beim Client-Connect pending Notifications nachliefern
- Rate-Limiting (max 3/Stunde)

---

## 3.3 Proactive Daemon

**Status: ⚠️ Vorhanden aber grundlegend falsch**

`services/proactive.py` existiert. Probleme:

**1. Nutzt Pipeline statt Dispatcher:**
```python
# Zeile 64 — aktuell:
pipeline.process_text(text, use_tts=True)
# → Soll sein:
dispatcher.notify(text, channels=["dashboard", "voice"])
```
Das ist der Kern-Fehler. "Proaktive" Erinnerungen sind nur sichtbar wenn JARVIS gerade antwortet — kein echter Push.

**2. fired-State geht bei Neustart verloren:**
```python
fired: set[str] = set()   # Zeile 70 — nur in-memory
```
→ Nach jedem Server-Restart feuern Reminders erneut.

**3. Fehlende Check-Module:**
- `TodoReminder` → fehlt komplett
- `FollowupReminder` → fehlt komplett
- `Reflection Loop` → fehlt komplett

**4. User Rules (brain.rules) → fehlt:**
`brain.py` hat kein `rules`-Sektionsschema. `brain.events` hat `checkin_rules` und `ongoing_reminders` — partiell ähnlich, aber anderes Format ohne `schedule`/`escalate`/`active`.

Was tun:
```
proactive.py     → pipeline.process_text() ersetzen durch NotificationDispatcher.notify()
proactive.py     → fired-State als JSON persistent speichern (~/.jarvis/proactive_state.json)
proactive.py     → TodoReminder + FollowupReminder hinzufügen
brain.py         → brain.rules Section mit User-Rules-Schema einführen
proactive.py     → User-Rules-Loader: brain.rules auslesen + schedule auswerten
(Phase 5)        → Reflection Loop hinzufügen (nach brain.memory Schema steht)
```

---

## 3.4 Context Loading — Modular

**Status: ⚠️ Vorhanden aber immer alles geladen**

`context.py` Zeile 187:
```python
modules_prompt = brain.build_modules_prompt(mode)
```
→ `build_modules_prompt()` lädt immer alle aktiven Module — keine Keyword-Erkennung, kein modular bedingtes Laden.

Was fehlt:
- `ContextLoader`-Klasse mit Modul-Registry
- Keyword-Classifier auf erster User-Message
- Pro-Modus definierte Default-Module
- Nachladen mid-conversation

Was schon existiert und passt:
- Modes werden berücksichtigt (assistent/coach/fokus bestimmt welche Sections geladen werden — rudimentär)
- Prompt-Caching-Infrastruktur ist da

Was tun:
```
context.py   → ContextLoader mit KEYWORD_MAP + MODE_DEFAULT_MODULES implementieren
context.py   → detect_modules(first_message, mode) → Liste aktiver Module
context.py   → Fallback: vollständiger Kontext bei kurzer/unklarer erster Message
```

---

## 3.5 Tool Execution — Idempotenz

**Status: ❌ Kein call_id-Tracking, kein Dedup**

`tools.py` und Pipeline haben keine `call_id`-Parameter, kein `_executed_tool_ids`-Set.

Was fehlt:
- `call_id`-Parameter in der Dispatch-Funktion
- Session-Set `_executed_tool_ids` in `JarvisPipeline`
- Notion-Write Dedup-Key (title + database + session_start_ts)
- Einheitliches Return-Format `{"success": bool, "data": Any, "error": str|None}`

Nicht alle Tools geben dasselbe Format zurück — inkonsistent.

---

## 3.6 Simon State — Zustandstracking

**Status: ❌ Existiert nicht**

Kein Zustandstracking, kein `sleep_log`, kein Signal-basiertes State-Update.

`sleep_coach.py` nutzt eine Heuristik basierend auf Wecker-Zeiten statt echtem Signal.

Was fehlt:
- `simon_state.json` in `~/.jarvis/` (State + sleep_log)
- State-Updates in `server.py` wenn Simon "ich schlafe jetzt" sagt
- State-Updates wenn Wecker dismissed wird (Signal existiert bereits via `alarm_dismissed`)
- State-basierter Push-Filter im NotificationDispatcher (kein Push wenn `sleeping`)

---

## 3.7 Session Memory — Zuverlässigkeit

**Status: ⚠️ Grundstruktur da, aber kein Fallback**

`session_memory.py` existiert. Probleme:

Zeile 80 + 123: Single try-except — bei Haiku-Fehler wird `None` zurückgegeben, kein Fallback.

Was fehlt:
- Retry (max 3 Versuche bei Haiku-Fehler)
- Raw-Transcript-Fallback bei totalem Fehlschlag
- System-Prompt erzwingt validiertes JSON (aktuell: kein `kein Markdown, kein Text davor/danach`)

---

## 3.8 Settings & Client-Konfiguration

**Status: ❌ Fehlt**

Keine `available_outputs`/`available_inputs` in `client_hello`.
Keine `brain.config.clients`-Einträge pro Client.
Kein Settings-View im Dashboard.

`.env` macht heute alles — Bluetooth-Adresse, API-Keys und Device-Wahl alles vermischt.

---

## 3.10 Satellite Audio — Keepalive

**Status: ❌ Nicht implementiert**

Kein Keepalive-Loop in `jarvis-satellite/client.py`.

Auto-Power-Off von Bluetooth/USB-Speakern ist bekanntes Problem.

Was tun:
```
jarvis-satellite/client.py → Keepalive-Background-Task: alle 270s silent audio abspielen
```

---

## 3.11 Mode Playbook

**Status: ⚠️ Rudimentär vorhanden, Struktur fehlt**

`brain.modules.modes.assistent` enthält heute nur einen kurzen Prompt-Text.

Was fehlt:
- `guidelines`-Liste pro Modus
- `decision_protocol` (when_to_ask / when_to_act / when_to_check)
- `quality_rules`-Liste

Ohne das kommt JARVIS zu passivem Verhalten: fragt ob ein Todo erledigt ist statt es selbst in Notion nachzuschauen.

Was tun:
```
brain.db   → brain.modules.modes für alle drei Modi mit vollständiger Playbook-Struktur befüllen
context.py → Playbook vollständig in statischen Prompt laden
```

---

## 3.12 Aktives Lernen & Versionierung

**Status: ❌ Fehlt komplett**

Kein aktives Lernen aus Sessions — JARVIS speichert keine Erkenntnisse autonom in brain.memory.
Keine Verhaltens-Retrospektive am Session-Ende.
Keine Guideline-Versionierung, keine `guideline_history`-Tabelle.

---

## 3.13 Persönliche Wissensdatenbank

**Status: ❌ Fehlt komplett**

Keine `knowledge`-Collection, kein TTL/Freshness-System, keine Priorität-Hierarchie beim Antworten.

Notion Konzepte sind nicht dasselbe: kein TTL, kein `evergreen`-Flag, kein semantisches Ranking.

---

## Phase 9 — Notion-Ablösung

**Status: ✅ Erledigt (2026-07-19)** — direktes SQLite (`local_data.py`) statt PocketBase, siehe TECHNICAL_PLAN.md. `notion.py` ist gelöscht.

---

## Was schon wirklich gut ist ✅

- **WebSocket-Protokoll** — sauber, alle Typen in `protocol.py`, drei Layer funktionieren
- **Brain-Struktur** — 7 Sections, `brain.read()` / `brain.write()`, SQLite-Basis solide
- **Shared History** — `api_history` + `display_history`, Session-Timeout 15 Min, korrekt
- **Prompt-Caching** — statisch/dynamisch getrennt, `cache_control` gesetzt
- **Server-Driven UI** — `layout_config` aus `server.py`, Dashboard ist generischer Renderer
- **Alarm-System** — läuft auf Satellite unabhängig, überlebt Server-Restart, `ALARM_SYNC` korrekt
- **Drei-Layer-Protokoll** — data_request (ohne LLM) funktioniert
- **notion.py Isolation** — einzige Stelle mit Notion-Wissen, Ablösung später möglich ohne Refactoring

---

## Empfohlene Umsetzungsreihenfolge

Diese Reihenfolge entspricht TECHNICAL_PLAN.md Phase 1–6, leicht konkretisiert nach aktuellem Stand:

### 1. Notification-Fundament (Phase 1) — ❌ neu bauen
`notification_dispatcher.py` + `protocol.py`-Erweiterung + Dashboard Toast.
Alles andere (Proactive, SleepCoach, TodoReminder) hängt daran.

### 2. Proactive Daemon reparieren (Phase 2) — ⚠️ refactoren
`pipeline.process_text()` → `dispatcher.notify()`.
Fired-State persistieren.
TodoReminder + FollowupReminder ergänzen.

### 3. Brain Memory Schema (Phase 3) — ⚠️ migrieren + erweitern
Schema einführen, `remember()` / `get_memory()` / `apply_aging()` bauen.
Bestehende Einträge einmalig migrieren.

### 4. Context Modularisierung (Phase 4) — ⚠️ context.py umschreiben
ContextLoader + Keyword-Classifier.
Mode Playbook vollständig laden.

### 5. Tool Idempotenz (Phase 5) — ❌ neu
`call_id`-Tracking, Notion-Dedup, einheitliches Return-Format.

### 6. Simon State + Sleep Log — ❌ neu (klein, aber Fundament für SleepCoach)
`simon_state.json`, State-Signale verdrahten.

### 7. Session Memory Hardening — ⚠️ small fix
Retry + Raw-Transcript-Fallback.

### 8. Audio Keepalive — ❌ neu (klein, standalone)
Background-Task in Satellite.

---

*Diese Analyse ist Grundlage für die konkrete Sprint-Planung. Wenn eine Phase fertig ist → ROADMAP.md aktualisieren.*
