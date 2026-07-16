# JARVIS — Technische Änderungen (Mai/Juni 2026)

Umsetzung aus GAP_ANALYSIS.md, Phasen 1–5.

---

## Phase 1 — NotificationDispatcher

**Problem vorher:** "Proaktive" Nachrichten liefen über `pipeline.process_text()` — d.h. sie erschienen nur wenn gerade eine aktive Konversation lief. Kein echter Push.

**Was gebaut wurde:**

### `services/notification_dispatcher.py` (neu)

Server-Singleton. Unabhängig von Pipeline und LLM.

```python
dispatcher.notify(text, channels=["dashboard"], priority="normal", expires_in_min=60)
```

- Speichert Notification in SQLite (`~/.jarvis/notifications.db`) → überlebt Neustart
- Sendet sofort an alle verbundenen Dashboard-Clients als `notification_push`
- `deliver_pending(client_id)` → beim Client-Connect ausstehende Notifications nachliefern
- `mark_delivered(id)` → setzt `delivered_at`, wird nicht erneut gesendet
- Rate-Limit: max 3 Notifications/Stunde global

**Notification-Schema:**
```json
{
  "id":           "uuid4",
  "text":         "Überfällige Todos: 3 Einträge",
  "channels":     ["dashboard"],
  "priority":     "normal",
  "created_at":   "2026-06-01T09:00:00",
  "expires_at":   "2026-06-01T10:00:00",
  "delivered_at": null
}
```

### `protocol.py`

Zwei neue Konstanten:
- `NOTIFICATION_PUSH` — Server → Client: Push-Nachricht
- `NOTIFICATION_ACK` — Client → Server: Empfangsbestätigung

### `server.py`

- `dispatcher = NotificationDispatcher(manager)` als Modul-Global
- `dispatcher.deliver_pending(client_id)` beim Dashboard-Connect
- Handler für `NOTIFICATION_ACK` → `dispatcher.mark_delivered()`
- `proactive_service.init(..., dispatcher)` — Dispatcher wird übergeben

### `client_manager.py`

- `get_event_callback(client_id)` — neue Methode für gezielte Einzelzustellung

### Dashboard (`jarvis-dashboard`)

- `NotificationToast.vue` — neue Komponente, zeigt Notifications als Toast unten rechts
- Auto-ACK beim Empfang (kein manuelles Bestätigen nötig)
- Auto-Dismiss: normal 8s, high-priority 12s
- Klick schließt Toast sofort

---

## Phase 2 — Proactive Daemon Umbau

**Problem vorher:** `proactive.py` rief `pipeline.process_text()` auf → kein Push, nur reaktiver Output. `fired`-Set war in-memory → nach Neustart erneut gefeuert.

**Was geändert wurde (`services/proactive.py` — komplett neu):**

### Push-Kanal

```python
# Vorher:
pipeline.process_text(text, use_tts=True)

# Nachher:
dispatcher.notify(text, channels=["dashboard"], priority="normal")
```

### Fired-State (persistent)

Zustand wird in `~/.jarvis/proactive_state.json` gespeichert:
```json
{
  "rules":    {"regel_id": "2026-06-01T23:30:00"},
  "calendar": {"2026-06-01_termin_main": "2026-06-01T09:45:00"},
  "todos":    {"2026-06-01_todos": "2026-06-01T08:00:00"},
  "followups": {},
  "email":    {}
}
```

Um Mitternacht: Stale Keys (nicht vom heutigen Tag) werden entfernt.

### Neue Check-Module

**TodoReminder** — 1× stündlich: überfällige Todos aus Notion-Cache als Push.

**FollowupReminder** — 1× stündlich: fällige Followups aus `brain.followups` als Push.

**User Rules** — jede Minute: liest `brain.rules`, wertet Schedule aus.

### User Rules Schema (`brain.rules`)

```json
{
  "schlaf_reminder": {
    "description": "Jeden Tag um 23:30 ans Schlafen erinnern",
    "schedule":    "daily@23:30",
    "text":        "Zeit fürs Bett.",
    "escalate":    true,
    "active":      true
  }
}
```

Unterstützte Schedule-Formate:
- `daily@HH:MM` — jeden Tag
- `weekly@weekday@HH:MM` — z.B. `weekly@thursday@10:00`

Toleranz: ±1 Minute. JARVIS schreibt Rules via Gespräch — kein manuelles Editieren.

---

## Phase 3 — Brain Memory Schema

**Problem vorher:** `brain.memory` war eine flache Liste ohne Schema. Kein Zeitstempel, keine Kategorie, kein Gewicht, keine Deduplication.

**Was geändert wurde (`brain.py`):**

### Neues Schema

```json
{
  "id":       "uuid4",
  "text":     "Simon möchte bis Oktober 3 neue Freelancing-Kunden",
  "ts_iso":   "2026-06-01",
  "category": "ziele",
  "weight":   1.0,
  "source":   "gespräch"
}
```

Kategorien: `ziele | abmachungen | vorlieben | kontext | followup | wissen`
Sources: `gespräch | check-in | nutzer-explicit`

### Neue Funktionen

```python
brain.remember(text, category="kontext", source="gespräch")  # → entry_id
brain.forget(entry_id)                                        # → bool
brain.get_memory(top_k=20, min_weight=0.1)                   # → list[dict]
brain.apply_aging()                                           # → None (täglich)
```

### Aging-Logik

| Alter | Weight |
|---|---|
| ≤ 30 Tage | 1.0 |
| 31–90 Tage | 0.5 |
| > 90 Tage | 0.1 |
| `source=nutzer-explicit` | kein Aging |

### Pruning

Max 100 Einträge. Bei Überlauf: Eintrag mit niedrigstem Weight wird entfernt.
`nutzer-explicit` Einträge sind vom Pruning ausgenommen.

### Deduplication

Vor jedem Write: Substring-Match gegen gleiche Kategorie.
Existiert ähnlicher Eintrag → Update (text + ts_iso + weight reset) statt Insert.

### Migration

`_migrate_memory_schema()` läuft beim Start via `brain.sync()`.
Bestehende Einträge ohne Schema-Felder werden automatisch auf das neue Format gehoben.

### Prompt-Build

`build_prompt_section()` nutzt jetzt `get_memory(top_k=20)` statt roher Liste.
Gewichtete Top-20 statt alle Einträge unkontrolliert.

---

## Phase 4 — Context Modularisierung

**Problem vorher:** `build_static_prompt()` lud bei jedem Turn alles: Todos, Projekte, Konzepte, Kalender, BTC. Check-in-Trigger beim Architektur-Gespräch, verschwendete Tokens.

**Was geändert wurde:**

### `context.py` — `detect_modules()`

```python
modules = context.detect_modules("Kannst du mein nächstes Meeting checken?", mode="assistent")
# → {"todos", "calendar", "btc"}   (calendar durch Keyword "meeting" + Assistent-Defaults)
```

**Mode-Defaults:**
| Modus | Standardmodule |
|---|---|
| assistent | todos, calendar, btc |
| coach | todos, calendar |
| fokus | (leer) |

**Keyword-Trigger (zusätzlich zu Defaults):**
| Keywords | Modul |
|---|---|
| todo, aufgabe, offen, task | todos |
| termin, kalender, meeting, wann, heute, morgen | calendar |
| projekt, freelancing, kunde, auftrag, arbeit | projects |
| bitcoin, btc, kurs, crypto, preis | btc |
| idee, konzept, plan, vorhaben | concepts |

**Checkin-Keywords** (→ alles laden): `check-in`, `checkin`, `guten morgen`, `morgen check`, `morning`

**Fallback:** Message < 8 Zeichen → alles laden.

### `build_static_prompt(mode, active_modules)` / `build_dynamic_prompt(room, active_modules)`

Beide Funktionen akzeptieren jetzt `active_modules: set[str] | None`.
`None` = alles laden (Rückwärtskompatibilität).

### `pipeline.py`

```python
# Beim ersten Turn der Session:
if self._active_modules is None:
    self._active_modules = context.detect_modules(text, self._mode)

# Module für alle Turns der Session verwenden:
system_static  = context.build_static_prompt(mode=self._mode, active_modules=self._active_modules)
system_dynamic = context.build_dynamic_prompt(room=self._room, active_modules=self._active_modules)
```

`set_mode(mode)` setzt `_active_modules = None` → neue Erkennung beim nächsten Turn.

---

## Phase 5 — Tool Idempotenz + Session Memory

### Tool Idempotenz (`pipeline.py`)

Claude gibt jedem Tool-Call eine eindeutige `tool_use_id` (`block.id`).
Die Pipeline trackt ausgeführte IDs per Session:

```python
self._executed_tool_ids: dict[str, str] = {}  # call_id → result
```

Bei Duplikat (z.B. API-Retry): gecachtes Ergebnis zurückgeben statt Tool erneut ausführen.
→ Keine doppelten Notion-Einträge mehr bei Netzwerkproblemen.

Cache lebt per Pipeline-Instanz = per Session. Neuer Connect = neuer Cache.

### Session Memory Hardening (`session_memory.py`)

**Vorher:** Single try-except, bei Haiku-Fehler → `None` → Session-Inhalt verloren.

**Nachher:**
- 3 Retry-Versuche mit Logging pro Versuch
- System-Prompt erzwingt explizit JSON ohne Markdown
- Raw-Transcript-Fallback bei totalem Fehlschlag:
  ```
  [Zusammenfassung fehlgeschlagen] Simon: ... | JARVIS: ...
  ```
  Session-Inhalt ist immer erhalten, auch wenn Haiku nicht erreichbar.

---

## Geänderte Dateien (Übersicht)

| Datei | Art | Beschreibung |
|---|---|---|
| `services/notification_dispatcher.py` | NEU | Push-Layer, SQLite-Delivery |
| `services/proactive.py` | KOMPLETT NEU | Echter Push, persistent state, neue Checks |
| `protocol.py` | ERWEITERT | NOTIFICATION_PUSH, NOTIFICATION_ACK |
| `server.py` | ERWEITERT | Dispatcher-Integration, SET_MODE → pipeline |
| `client_manager.py` | ERWEITERT | get_event_callback() |
| `brain.py` | ERWEITERT | Memory-Schema, remember/forget/get_memory/apply_aging |
| `context.py` | ERWEITERT | detect_modules(), modulares Laden |
| `pipeline.py` | ERWEITERT | set_mode(), _active_modules, Tool-Dedup |
| `session_memory.py` | ERWEITERT | 3 Retries, Raw-Fallback |
| `jarvis-dashboard/src/stores/jarvis.js` | ERWEITERT | notification_push Handler |
| `jarvis-dashboard/src/components/NotificationToast.vue` | NEU | Toast-Komponente |
| `jarvis-dashboard/src/App.vue` | ERWEITERT | NotificationToast eingebunden |
