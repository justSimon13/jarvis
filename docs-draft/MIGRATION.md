---
topic: jarvis
tags: ["migration", "datenmodell", "gap-analyse", "maschinenraum"]
---

# MIGRATION — vom IST-Stand zum Zielbild

**Zielbild:** `JARVIS-Konzept-2026-07-28.md` (Leitbild) + `JARVIS-Datenmodell-und-API.md` (Maschinenraum).
**IST-Stand:** `ARCHITECTURE.md`, `CODE_REFERENCE.md`, `TOOLS.md`, `ROADMAP.md` (Root-Docs) — abgeglichen gegen den tatsächlichen Code (`/Volumes/jarvis`, Stand 2026-07-29). Wo Doku und Code auseinanderliefen, wurde der Code gelesen und gilt; Abweichungen sind unten explizit vermerkt.

**Status:** Reine Analyse, keine Umsetzung. Diese Datei beschreibt nur die Lücke — nichts davon ist beauftragt.

---

## 0. Root-Docs vs. Code — gefundene Abweichungen (Code gewinnt)

Zwei echte Diskrepanzen zwischen dem, was die aktiven Docs behaupten, und dem, was der Code tatsächlich tut:

1. **"Staging → Promotion" existiert nicht.** `ROADMAP.md`s einleitende Datenarchitektur-Tabelle (Zeile 28) und Phase B2 (Zeile 154–156, als ✅ markiert) beschreiben `brain.memory` als Zwischenspeicher, der wöchentlich oder bei Häufung in `knowledge/*.md` "promoviert" und danach aus `brain.memory` entfernt wird. **Das gibt es im Code nicht.** Gelesen: `brain.py` (`remember()`, `apply_aging()`, `forget()`) und `learning.py` (`_run()`, `_extract()`) — `micro_facts` werden direkt und dauerhaft in `brain.memory` geschrieben, `knowledge_updates` werden UNABHÄNGIG davon aus demselben Haiku-Aufruf erzeugt (nicht aus bestehenden `brain.memory`-Einträgen abgeleitet), und nichts entfernt jemals einen Eintrag aus `brain.memory` außer `apply_aging()`s Gewichts-Pruning bei >100 Einträgen. Es gibt keine Promotion, keine wöchentliche Sichtung, keine Entfernung nach Promotion. Relevant fürs Zielbild: das `facts`-Modell im Datenmodell-Dokument (`status: active/achieved/dropped/superseded`, `supersedes_id`) beschreibt etwas Neues, kein bestehendes Verhalten, das nur umgezogen wird.
2. **`brain.apply_aging()` läuft nicht periodisch**, obwohl die eigene Docstring "täglich aufrufen" verlangt — tatsächlich nur einmalig bei `brain.sync()` (Serverstart). Bei einem lange laufenden Prozess bleiben Gewichte zwischen zwei Neustarts veraltet. Für die Migration relevant, falls das neue `facts.status`-Modell zeitbasierte Übergänge (nicht nur Alterung) braucht — die heutige einzige "Zeit vergeht"-Logik im System läuft nur beim Start.

Sonst deckt sich die Root-Doku gut mit dem Code — `CODE_REFERENCE.md`/`TOOLS.md` sind detailliert genug, dass sie direkt als Grundlage für den Abgleich unten dienen konnten, ohne dass an mehr als diesen zwei Stellen nachgelesen werden musste.

---

## 1. Entspricht bereits dem Zielbild

Kein Umbau nötig — Verhalten/Architektur stimmt bereits mit dem Konzept überein, auch wenn Namen/Schema noch abweichen (Namensänderungen stehen in Abschnitt 2).

| Zielbild-Prinzip | Wo es heute schon so ist |
|---|---|
| Kein Server-Token für Issues, `gh` läuft auf dem Mac | `services/tickets.py` + `local_exec.py` — exakt das beschriebene Client-holt-sich-Arbeit-Modell, nur (noch) ohne persistente `clients`/`queue`-Tabelle |
| Client meldet sich beim Server an, nicht umgekehrt | WebSocket-Verbindungsaufbau ist grundsätzlich schon so (Client verbindet sich zum Server) |
| Prompt-Cache-Reihenfolge "Stabiles vorne, Wechselndes hinten" | `llm.py::stream()` — `cache_control: ephemeral` auf dem statischen Block, dynamischer Teil separat. Exakt das im Datenmodell-Dokument geforderte Muster, schon heute korrekt umgesetzt |
| Eskalationsstufen 1/2/3 (Ablage/Push/gesprochen) | `NotificationDispatcher` + `notifications.db` — Rate-Limit, Zustellung bei Reconnect. Fehlt nur: generische `routines`/`alerts`-Tabellen statt hartcodierter Check-Funktionen (siehe Abschnitt 3) |
| Wiki-Verlinkung `[[topic/file]]`, Backlinks rein berechnet | `knowledge.py` + `knowledge_links` — 1:1 das beschriebene Verhalten |
| SevDesk-Import überschreibt manuell korrigierte/gesperrte Zeilen nie | `gesperrt`-Feld, `upsert_rechnung()`/`upsert_ausgabe()` — funktional identisch zum geforderten `locked`-Flag |
| Projekt-Zuordnung wird nicht geraten, sondern nachgefragt | `finanzen_import.py`-Konvention, `data_query`/`data_update` — entspricht dem Leitprinzip "Zugriff ≠ Gedächtnis" in der Praxis |
| Mail nur lesend als Datenquelle, nicht im Gedächtnis gespeichert | `services/email.py::email_query` — Mails werden abgefragt, nicht persistiert. (Die **Sende**-Fähigkeit weicht ab, siehe Abschnitt 4) |
| E-Mail läuft unabhängig vom Mac-Client, direkt auf dem Server | Schon heute so (`IMAP`/`SMTP` direkt aus `services/email.py`) |

---

## 2. Nur umbenannt oder erweitert

Gleiche Grundidee, gleiche Kardinalität — reines Rename oder additive neue Spalten an einer bestehenden Tabelle, kein struktureller Umbau der Beziehungen.

| Heute | Zielbild | Änderung |
|---|---|---|
| `projekte` | `projects` | Rename + neue Spalten: `data_scope`, `contact_id`, `client_id`, `autonomy`, `path`, `repo`, `estimated_hours`. Bestehende `geschaetzter_wert`→`estimated_value`, `erwartetes_abschlussdatum`→`expected_close_date` |
| `kontakte` | `contacts` | Rename + neue Spalte `company`. `email`/`telefon`/`tags`/`notizen` bleiben inhaltlich (nur `telefon`→`phone`, `notizen`→`notes`) |
| `rechnungen` | `invoices` | Rename + `kunde TEXT` wird durch `contact_id`-FK ersetzt (Freitext bleibt als `customer_name`-Fallback für Importe ohne Treffer), `gesperrt`→`locked`, `data_scope` neu |
| `ausgaben` | `expenses` | Rename + `gesperrt`→`locked`, `data_scope` neu, `project_id` (optional) und `contact_id` neu |
| `knowledge_index` | `documents` (Teil 1) | Rename + neue Spalten `data_scope`, `project_id`/`contact_id`/`todo_id`. **Der vollständige Umbau (Verschmelzung mit `seiten`) ist strukturell, siehe Abschnitt 3** |
| `knowledge_links` | `document_links` | Reines Rename, Schema identisch (`from_path`/`to_path`) |
| `todos` | `todos` | Bleibt, bekommt zusätzlich `project_id`-FK (fehlt heute komplett — Todos sind aktuell nicht mit Projekten verknüpfbar) |
| `logs` | `entries` | Rename + `topic`→`collection_id`-FK. **Setzt voraus, dass `collections` existiert — siehe Abschnitt 3**, daher technisch kein reines Rename, aber konzeptionell die 1:1-Fortsetzung derselben Idee (`tracking.py`s eigener Docstring nennt sich selbst schon "generisch, kein Table-Change nötig") |

**Eine Ungenauigkeit im Zielbild-Dokument selbst, hier korrigiert:** Der Abschnitt *"Bleibt inhaltlich unverändert"* im Datenmodell-Dokument führt `knowledge_suggestions → document_suggestions` als "unverändert übernehmen". Stimmt nicht ganz — das Zielschema für `document_suggestions` ersetzt `topic`/`file`/`heading` durch `document_id`(FK)/`title`/`section` und `status`-Werte wechseln von `pending/applied/rejected` auf `open/applied/rejected`. Gehört damit eigentlich in Abschnitt 3 (strukturell, weil an die `documents`-Verschmelzung gekoppelt), nicht hierher.

---

## 3. Struktureller Umbau nötig

Neue Beziehungen, neue Kardinalität oder ein grundsätzlich anderer Mechanismus — nicht mit einer `ALTER TABLE`-Zeile zu machen.

### 3.1 `brain.db` (JSON-Blob) → echte Tabellen (`facts`, `routines`)

Heute ein Key-Value-Store (`brain(section TEXT PRIMARY KEY, data TEXT)`) — jede Section ein JSON-Blob, nicht abfragbar ohne alles zu laden und im Python-Code zu filtern. Ziel: `memory`/`followups`/`profile` → echte `facts`-Tabelle mit Spalten (`category`, `status`, `due_at`, `data_scope`, `supersedes_id`); `events`/`behavior`, soweit wiederkehrend, → `routines`-Tabelle. `config`/`modules` bleiben laut Zielbild-Dokument selbst bewusst Key-Value (dafür ist das Format richtig).

**Betroffene Dateien:**
- `brain.py` — praktisch komplette Neufassung (`remember`/`forget`/`get_memory`/`apply_aging`/`build_prompt_section`/`build_modules_prompt` alle betroffen)
- `context.py` — `build_static_prompt()` liest heute `brain.build_prompt_section()`
- `tools.py` — `brain_read`/`brain_write` (generischer Dot-Notation-Zugriff) passen nicht mehr zu echten Tabellenspalten; Zielbild schlägt ohnehin `create_fact`/`update_fact` als eigene Tools vor
- `pipeline.py` — Aufrufer von `context.build_static_prompt()`
- `server.py` — `_build_layout_config()` liest `brain.read("modules")`
- `services/proactive.py` — liest `brain.config.proaktiv.*`, `brain.followups`
- `services/sleep_coach.py` — liest `brain.schlaf.*`
- `services/notification_dispatcher.py` — konzeptionell der heutige Vorläufer von `alerts`, aber ohne Routine-Bezug (`subject_ref`, `cooldown_hours` existieren dort nicht)
- `learning.py` — schreibt direkt in `brain.memory`

### 3.2 `sessions` → `messages` + `threads` + `daily_summaries`

Größter Architektur-Bruch im ganzen Zielbild: weg vom Session-als-Behälter-Modell (heute: `api_histories["voice"|"web"][tab_id]`, harte Grenzen zwischen Chats) hin zu einem durchgehenden Strom mit Thread-Etiketten. Das Zielbild-Dokument selbst nennt den technischen Hintergrund korrekt (Anthropic-API ist zustandslos, "Session" ist nur eine eigene DB-Zeile) — der Umbau ist real, aber kein API-Problem, ein Server-internes Umbauproblem.

**Betroffene Dateien:**
- `session_memory.py` — komplette Neufassung (`save`/`upsert`/`find_active_session` durch Thread-/Message-CRUD ersetzt)
- `server.py` — `api_histories`/`display_histories`-Dict-Struktur (aktuell die zentrale In-Memory-Datenstruktur überhaupt), `_persist_web_turn()`, `_check_satellite_timeout()`, `SESSION_BREAK`/`SESSION_RESET`/`SESSION_LIST_REQUEST` komplette Handler-Gruppe
- `pipeline.py` — `self.history` ist heute *die* Turn-Verwaltung; Fenster-Aufbau ("Nachrichten des laufenden Threads + je eine Zeile zu anderen Threads") ist ein anderer Algorithmus als "letzte N Einträge, komprimiert"
- `protocol.py` — `SESSION_*`-Konstanten-Gruppe (7 Stück) wird durch ein Thread-Äquivalent ersetzt oder stark umgebaut
- `learning.py` — `process_session()` wird zu etwas Thread- oder Tages-bezogenem (`daily_summaries` ist im Zielbild explizit der Ersatz für die heutige Session-Zusammenfassung)
- jarvis-web — `ChatView.vue`s Session-Liste/-Panel basiert komplett auf dem Session-Konzept

**Wichtig für die Reihenfolge (siehe Abschnitt 5):** Das Zielbild-Dokument benennt selbst die Cache-Konsequenz — läuft das schief, brechen alle Prompt-Caching-Vorteile weg (teurer, nicht nur ein Bug). Verdient einen eigenen, isoliert testbaren Schritt.

### 3.3 `seiten` + `knowledge/*.md` → vereinheitlichte `documents`

Heute zwei parallele Dokumentsysteme mit unterschiedlicher Form: `seiten` hängt polymorph an Todos/Projekten/Kontakten (`parent_typ`/`parent_id` XOR `eltern_seite_id`), `knowledge/` ist frei benannt und Datei-basiert. Ziel: eine Tabelle `documents`, Verschachtelung wird zu `document_links`, `parent_typ`/`parent_id` wird zu drei expliziten, optionalen FK-Spalten (`project_id`/`contact_id`/`todo_id`).

**Betroffene Dateien:**
- `local_data.py` — alle `*_seite*`-Funktionen (`create_seite`, `update_seite`, `list_seiten`, `list_unterseiten`, `get_seite_breadcrumbs`, `get_seite_view`, `read_seite`) fallen weg oder werden zu `documents`-Äquivalenten
- `knowledge.py` — bleibt als Konzept (Wiki-Verlinkung, Frontmatter, Index), aber die Speicher-/Zuordnungsschicht ändert sich grundlegend
- `server.py` — `seite`-Resource und `knowledge_index`/`knowledge_file`-Resourcen verschmelzen konzeptionell
- `tools.py` — `read_seite`/`create_seite`/`write_seite` und `read_knowledge`/`write_knowledge`/`search_knowledge` sind heute sechs separate Tools für zwei Systeme, die zu einem werden sollen
- `services/document_export.py` — nutzt `local_data.get_seite_view()` als zentralen Lookup, direkt betroffen
- jarvis-web — `SeiteView.vue` und `KnowledgeView.vue` sind heute getrennte Komponenten für dasselbe Zielkonzept

### 3.4 GitHub-Ticket-Felder raus aus `todos`, rein in `issue_cache`

Heute liegen `source`/`external_id`/`repo`/`body`/`labels` direkt an der `todos`-Tabelle — Verfallendes (Issue-Snapshot) ist mit Bleibendem (Todo) vermischt. Ziel: eigene `issue_cache`-Tabelle, ein Todo verweist optional per `issue_ref` darauf, statt selbst Issue-Felder zu tragen.

**Betroffene Dateien:**
- `local_data.py` — `list_tickets()` (aktuell ein reiner `WHERE source='github'`-Filter auf `todos`) wird zu einer eigenen `issue_cache`-Abfrage
- `services/tickets.py` — `sync_tickets()` upserted heute direkt in `todos`; Ziel trennt "Cache aktualisieren" von "daraus bewusst ein Todo machen"
- jarvis-web — `TodosView.vue`/`TodoItem.vue` (GitHub-Link/Labels/Ticket-Nummer inline) müsste auf einen Join/Lookup umgestellt werden

### 3.5 Coding-Ausführung: Claude Agent SDK + Worktree → `claude -p`-CLI + `jobs`/`queue`/`clients`

Größter Philosophie-Wechsel im ganzen Zielbild: weg vom eigenen Worktree-pro-Task-Modell mit `can_use_tool`-Callback-Freigabe, hin zu headless `claude -p`-Aufrufen direkt im Arbeitsverzeichnis (kein Worktree — steht explizit auch im Entwurfs-`CLAUDE.md`: *"Keine Worktrees. Direkt im Arbeitsverzeichnis, Branch pro Aufgabe."*), mit persistenten `jobs`/`queue`/`clients`-Tabellen statt In-Memory-Status.

**Wichtiger Fakt, im Code verifiziert:** `services/coding_engine.py` persistiert Task-Status **aktuell überhaupt nicht** — `_current_status`/`get_task_status()` ist ein reines In-Memory-Dict (`_status_lock`), kein `CREATE TABLE` in der ganzen Datei. Die `jobs`-Tabelle ist also keine Migration eines bestehenden Speicherorts, sondern die erste echte Persistenz für Coding-Tasks überhaupt — bisher überlebt kein Task-Status einen Neustart.

**Betroffene Dateien:**
- `services/coding_engine.py` — praktisch komplette Neufassung: `start_task`/`_run_task_thread`/`_create_worktree`/`_finalize_commit`/`_create_pull_request` durch `claude -p`-Subprozess-Aufrufe + `jobs`-Tabellen-Updates ersetzt
- `tools.py` — `delegate_coding_task`/`check_coding_task_status`/`commit_and_push`/`run_command` orientieren sich heute am SDK-Modell, müssten auf Job-Objekte umgestellt werden
- `protocol.py` — `CODING_APPROVAL_REQUEST/RESPONSE`, `CODING_TASK_STATUS` passen zum heutigen Live-Freigabe-Modell, nicht zum Ziel-Modell (Freigabe VOR dem Lauf bei `vorsichtig`, Branch-Review NACH dem Lauf sonst — kein Live-Dialog pro Tool-Call, das Zielbild-Dokument verwirft das explizit: *"Live-Rückfrage pro Tool-Call: bewusst verworfen"*)
- `client_manager.py` — heutige In-Memory-Registry (`local_exec`-Capability, ein Client angenommen) wird durch persistente `clients`+`queue`-Tabellen ersetzt (zwei Mac-Worker gleichzeitig: `mac-private`/`mac-work`)
- jarvis-web — Tauri-App braucht die im Zielbild geforderte UI/Worker-Trennung (Rust-Prozess läuft immer, UI-Fenster nur wenn geöffnet) — heute nicht getrennt
- `services/tickets.py` — `plan_jobs()`/Morgenplanung-Batch-Flow existiert nicht, nur der reine Issue-Sync

### 3.6 Neue, heute nicht existierende Bausteine

Kein "Umbau", weil es nichts Vergleichbares gibt — reine Neu-Infrastruktur:

- **`collections`-Schema-Definition** — `tracking.py`s `logs`/`goals` sind bereits topic-generisch (Daten-Ebene passt), aber es gibt keine Tabelle, die einem Topic ein Feld-Schema zuordnet. Die UI kann heute nicht automatisch aus einer Felddefinition eine Ansicht generieren, weil es diese Definition nirgends gibt.
- **`data_scope`** — existiert in keiner einzigen heutigen Tabelle. Komplett neues Konzept, keine Datenmigration möglich außer "alles bekommt einen sinnvollen Default".
- **Demo-Infrastruktur (`demos`-Tabelle, Container, Reverse Proxy, Health-Check-Loop)** — **Spannung mit einer bestehenden, expliziten Architektur-Entscheidung:** `ARCHITECTURE.md` listet unter den Kernprinzipien wörtlich *"Kein Docker (Audio + Docker = Chaos auf Linux)"*. Das Zielbild-Dokument setzt Container-Isolation für Demo-Projekte explizit voraus (*"Container pro Projekt (empfohlen)"*). Diese beiden Aussagen widersprechen sich direkt — nicht stillschweigend auflösbar, siehe offene Fragen unten.
- **Backup-Mechanismus** — im ganzen Repo kein Backup-Skript/-Cronjob gefunden. Das Zielbild-Dokument selbst nennt das treffend *"kein Konzeptthema, aber bisher nie erwähnt"* — stimmt, es existiert schlicht nicht.
- **REST-API-Schicht (`/api/...`)** — heute **ausschließlich** WebSocket-Nachrichtentypen (`protocol.py`, 45 Konstanten), keine einzige HTTP-Route im ganzen Server. Das Zielbild-Dokument beschreibt wörtlich `POST /api/chat`, `GET /api/projects` etc. Falls das wörtlich (echtes HTTP) gemeint ist, ist das eine zusätzliche Server-Schicht neben dem bestehenden WebSocket-Server, keine Migration eines bestehenden Mechanismus. Siehe offene Fragen.
- **`worker`/`ui`-Trennung im Client** — `client_manager.py` kennt heute nur "verbunden ja/nein" + Capability-Flag, keine Unterscheidung UI-Prozess vs. Hintergrund-Worker.

---

## 4. Fällt ersatzlos weg

- **`email_send`-Tool (echtes Versenden per SMTP).** Zielbild: *"Kein SMTP. JARVIS formuliert einen Entwurf, Simon kopiert ihn per Knopf."* Heute existiert `email_send {to, subject, body}` als Tool (`config.EMAIL_SEND_ENABLED`-gated, verlangt Bestätigung) — das Zielbild will diese Fähigkeit nicht einschränken, sondern vollständig entfernen, explizit als Sicherheitsargument begründet (*"ohne Sendefunktion gibt es keine Aktion, die durch fremden Text ausgelöst werden könnte"*).
- **GitHub-PR-basiertes Merge-Review** für den Coding-Flow. Der heutige Weg (`_create_pull_request`, echter GitHub-PR als Review-Instanz) taucht im neuen Job-Modell nicht mehr auf — Review passiert über Branch+Diff direkt in der Web-App (`/api/jobs/:id/diff`, `/merge`), kein GitHub-Zwischenschritt mehr vorgesehen.
- **Worktree-pro-Task** (`~/.jarvis/coding_worktrees/`) — explizit verworfen zugunsten von Branch-im-Arbeitsverzeichnis.
- **Notion-Migrationsreste** (`local_data.migrate_stammdaten()`, `backfill_seiten_inhalte()`, `externe_id`-Spalten) — einmalige, guard-geschützte Alt-Migrationsfunktionen, im Zielbild nirgends referenziert. Sobald `documents`/`projects`/`contacts` nativ aus der neuen Struktur befüllt sind, ist der historische Notion-Bezug komplett gegenstandslos.
- **`brain.profile`** — laut `ARCHITECTURE.md` bereits seit 2026-07-19 leer/unbefüllt (Nachfolger: `knowledge/simon/_core.md`, im Zielbild Teil von `documents`). Formalisiert nur eine bereits tote Section.
- **`session_memory.load_for_prompt()`** — bereits heute toter Code (eigene Docstring: "Legacy"), fällt mit der `sessions`→`messages`-Ablösung automatisch mit weg.

---

## 5. Reihenfolge — System bleibt nach jedem Schritt lauffähig

> **Überholt durch `JARVIS-Konzept-2026-07-28.md`, Abschnitt "Reihenfolge der Umsetzung" (Stand 29.07.).** Die Priorisierung unten ist eine *Abhängigkeits*-Reihenfolge (welcher Strukturschritt braucht welchen anderen zuerst) — dafür gilt sie weiterhin. Als *Ausführungs*-Reihenfolge zählt ab jetzt nicht mehr sie, sondern das Konzept-Dokument: der Coding-Strang (unten Schritt 8) hängt an nichts anderem hier und wird dort bewusst nach vorne gezogen, zusammen mit Backup (Schritt 0) und additiven Spalten/`data_scope` (Schritt 1) direkt davor. Bei einem Widerspruch zwischen den beiden Reihenfolgen entscheidet das Konzept-Dokument.

Leitprinzip aus dem Entwurfs-`CLAUDE.md` übernommen: *"Neue Struktur neben die alte legen, migrieren, alte entfernen. Nach jedem Schritt muss das System lauffähig sein."* Jeder Schritt unten ist additiv (neue Tabellen/Spalten neben den alten), Entfernen ist immer ein eigener, späterer Schritt.

**0. Vor allem anderen: Backup.** Existiert nicht, sollte vor dem ersten Struktur-Umbau stehen, nicht danach — das Zielbild-Dokument fordert das selbst implizit (*"Vor jedem Datenbank-Umbau: prüfen, ob ein aktuelles Backup existiert"*, Entwurfs-`CLAUDE.md`).

**1. Rein additive Spalten (kleinstes Risiko, kein Rename, keine Downtime):**
`projects.contact_id`, `todos.project_id`, `contacts.company`, `data_scope` an `projekte`/`rechnungen`/`ausgaben` (Default `'own'`). Kein bestehender Code-Pfad bricht, neue Felder sind erst mal einfach ungenutzt.

**2. `collections`-Tabelle einführen**, parallel zu `tracking.py`. Erste Schema-Einträge für bestehende Topics (`sport` etc.) — `tracking.py` selbst bleibt unangetastet, nur eine zusätzliche Tabelle. UI für generische Collections kann parallel zur bestehenden Tracking-Seite entstehen, ersetzt sie noch nicht.

**3. `documents`-Vorbereitung:** Neue Spalten an `knowledge_index` (`data_scope`, `project_id`/`contact_id`/`todo_id`) — nur die Wissensdatenbank, `seiten` bleibt in diesem Schritt unangetastet (der `seiten`-Merge ist Schritt 6, riskanter wegen der polymorphen Verschachtelung).

**4. `facts` parallel zu `brain.memory` aufbauen.** `learning.py` schreibt vorübergehend **doppelt** (alt UND neu). Erst wenn `context.py`s Prompt-Bau nachweislich identische Ergebnisse aus `facts` liefert, wird der `brain.memory`-Lesepfad in einem eigenen, späteren Schritt entfernt.

**5. `messages`/`threads` parallel zu `sessions` — der riskanteste Einzelschritt.** `server.py`/`pipeline.py` schreiben zusätzlich in die neuen Tabellen, `api_histories`/`sessions.db` läuft unverändert weiter. Cache-Verhalten (Prompt-Caching-Trefferquote) muss explizit gemessen werden, bevor `process_text()` umgestellt wird — genau die Stelle, die laut Zielbild-Dokument selbst technisch am heikelsten ist.

**6. `seiten` + `knowledge` wirklich zu `documents` verschmelzen.** Erst jetzt, nachdem `documents` (Schritt 3) schon die Bezugsfelder hat. Jede aktuell erreichbare Seite muss danach über den neuen Pfad weiterhin erreichbar sein (Regressionscheck, nicht optional).

**7. `todos`-GitHub-Felder → `issue_cache`.** Setzt `todos.project_id` (Schritt 1) voraus. Alte Spalten (`source`/`external_id`/`repo`/`body`/`labels`) bleiben vorerst bestehen, aber ungenutzt, bis der neue Pfad im Frontend verifiziert ist.

**8. Coding-Executor-Wechsel — eigener Block, folgt der im Zielbild-Dokument selbst vorgeschlagenen Reihenfolge unverändert:**
   1. Praxistest (`claude -p` im Arbeitsprofil, `claude auth status` in beiden Profilen) — laut Zielbild-Dokument die "letzte offene Annahme", blockiert alles Weitere in diesem Block
   2. Kanal (Client-Anmeldung + `gh issue list` als harmloser erster Auftrag)
   3. Ein einzelner `claude -p`-Lauf über denselben Kanal
   4. Diff-Ansicht mit drei Knöpfen
   5. Batch mit Plan-Freigabe (Morgenplanung)

   Technisch unabhängig von Schritt 1–7 (eigene neue Tabellen `jobs`/`queue`/`clients`), kann parallel laufen — aber wegen eigenem Umfang und der explizit offenen Praxistest-Frage bewusst als eigener Strang geführt, nicht verzahnt.

**9. Aufräumen — für jede Migration ein eigener, separater letzter Schritt:** Alte Tabellen/Spalten erst löschen, nachdem der jeweilige neue Pfad produktiv UND verifiziert ist (`brain.memory` erst nach 4, `sessions`/`api_histories` erst nach 5, `seiten` erst nach 6, `todos`-GitHub-Spalten erst nach 7, Worktree-Verzeichnis erst nach 8). Nie im selben Schritt wie das Anlegen der neuen Struktur.

**10. Zuletzt, wie im Zielbild-Dokument selbst gefordert** (*"Danach erst: Server-Client, Demo-Automatik, Personas, Sprache"*): Personas (Werkzeug-Vorauswahl braucht ein stabiles `facts`/`routines`-Fundament aus Schritt 4), Demo-Infrastruktur (siehe offene Frage unten), REST-API-Frage klären.

---

## Offene Fragen — nicht auflösbar ohne Simons Entscheidung

- **Docker-Widerspruch:** `ARCHITECTURE.md` schließt Docker explizit aus, das Zielbild setzt Container-Isolation für Demos voraus. Beide Dokumente können nicht gleichzeitig gelten.
- **REST-API wörtlich oder konzeptionell?** Wenn wörtlich: komplett neue HTTP-Server-Schicht neben dem bestehenden WebSocket-Server, nicht im Zielbild-Dokument selbst mit einer Umsetzungsreihenfolge versehen (nur der Coding-Executor-Teil hat eine).
- **`tracking.goals` hat im Zielbild kein klares Zuhause.** Persönliche Ziele ("will in 2 Jahren ausziehen") passen als `facts.category="goals"` — aber pro-Topic-Zielwerte (`topic="sport", key="kalorien_ziel", value=2800`) sind etwas anderes und werden im `collections`/`entries`-Modell nirgends erwähnt.
- **`layout_config` (Cards/Quick-Actions, server-driven Dashboard-UI)** wird im ganzen Zielbild-Dokument nicht erwähnt — weder als zu ersetzen noch als zu erhalten. Bleibt unadressiert, nicht stillschweigend als "bleibt" oder "fällt weg" angenommen.
