topic: jarvis
updated: 2026-07-29
tags: ["datenmodell", "api", "architektur", "maschinenraum"]

# JARVIS — Datenmodell und API-Schnitt

**Ebene:** Maschinenraum. Das Leitbild steht im Grundsatz-Konzept; dieses Dokument ist die verbindliche Struktur darunter.

**Zweck:** Vorlage für Claude Code. Ohne festes Modell erfindet jede Sitzung neue Strukturen (`project_id` vs. `projectId`, Fakt mit oder ohne Projektbezug). Mit diesem Dokument im Repo gibt es eine Grundlage, gegen die gebaut wird.

**Sprache:** Alle Bezeichner — Tabellen, Spalten, Enum-Werte, Endpunkte, Werkzeuge — sind englisch. Erklärungen bleiben deutsch.

**Nicht enthalten:** bestehende Bereiche, die nicht neu entworfen wurden — Todos, Kontakte, Kalender, Rechnungen, Ausgaben, Tracking. Was dort ergänzt werden muss, steht unter *Änderungen an bestehenden Tabellen*.

---

## Leitregeln für das Modell

1. **Alles, was dauerhaft gespeichert wird, trägt einen `data_scope`** (`own` / `customer` / `employer`). Die einzige Spalte, die sich nachträglich nicht befüllen lässt.
2. **Das Projekt ist die zentrale Einheit.** Client, Autonomie und Scope hängen dort — nicht global, nicht am Einzeleintrag.
3. **Cache und Gedächtnis sind getrennt.** Was verfällt, kommt nicht in dieselbe Tabelle wie das, was bleibt.
4. **snake_case durchgehend**, auch in JSON-Antworten. Keine Umbenennung an der Grenze.
5. **Zeitstempel immer ISO-8601 mit Zeitzone**, Spaltenname endet auf `_at`.
6. **`client` heißt immer Gerät, `customer` immer Kunde.** Nie vermischen.

---

## Tabellen

### `projects`

Die Steuerzentrale. Alles Weitere erbt von hier.

| Spalte | Typ | Werte / Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | |
| `description` | TEXT | |
| `status` | TEXT | `planning` / `in_progress` / `done` / `backlog` |
| `type` | TEXT | `demo` / `customer` / `product` / `freelance` |
| `data_scope` | TEXT | `own` / `customer` / `employer` |
| `contact_id` | INTEGER FK NULL | der Kunde |
| `client_id` | TEXT FK | wer ausführt: `mac-private`, `mac-work`, `jarvis-server` |
| `autonomy` | TEXT | `sandbox` / `auto` / `review` / `careful` |
| `path` | TEXT | freigegebener Ordner auf dem Client |
| `repo` | TEXT | z. B. `owner/name` für `gh` |
| `estimated_value` | REAL | Angebotssumme |
| `estimated_hours` | REAL | für den Schätzungs-Kreislauf |
| `expected_close_date` | TEXT | für die Pipeline im Trend |
| `created_at`, `updated_at` | TEXT | |

**Regel:** `autonomy = 'sandbox'` ist nur zulässig, wenn JARVIS das Repo selbst angelegt hat **und** `data_scope = 'own'`. Bei allem anderen wird beim Anlegen auf `review` heruntergesetzt.

### `facts`

Was immer mitfährt. Klein halten.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `text` | TEXT | ein Satz |
| `category` | TEXT | `preferences` / `goals` / `agreements` / `followup` / `context` |
| `status` | TEXT | `active` / `achieved` / `dropped` / `superseded` |
| `due_at` | TEXT NULL | nur bei `followup` |
| `data_scope` | TEXT | |
| `project_id` | INTEGER FK NULL | optional |
| `source` | TEXT | `user_explicit` / `learning` / `system` |
| `supersedes_id` | INTEGER FK NULL | zeigt auf den überholten Vorgänger |
| `created_at`, `updated_at` | TEXT | |

**Auswahl für den Prompt:** `status = 'active'`, sortiert nach Kategorie — nicht nach Alter. `preferences`, `goals`, `agreements` immer; `followup` solange `due_at` nicht überschritten; `context` nur die jüngsten.

**Widerspruch statt Duplikat:** Ein überholter Fakt wird nicht gelöscht, sondern auf `superseded` gesetzt, und der neue trägt `supersedes_id`. Damit bleibt die Historie ("seit wann weiß JARVIS das"), ohne dass zwei widersprüchliche Sätze im Prompt stehen.

### `documents`

Die Wissensdatenbank. Wird gesucht, nicht mitgeschickt.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `title` | TEXT | erscheint im Index |
| `path` | TEXT | Datei unter `knowledge/` |
| `summary` | TEXT | ein bis zwei Sätze, für Suchtreffer |
| `data_scope` | TEXT | |
| `project_id` | INTEGER FK NULL | |
| `contact_id` | INTEGER FK NULL | |
| `todo_id` | INTEGER FK NULL | |
| `tags` | TEXT | JSON-Array |
| `created_at`, `updated_at` | TEXT | |

Der Inhalt liegt als Markdown-Datei, nicht in der Spalte — dann funktionieren Git-Historie und Diffs.

**Index:** `SELECT title FROM documents` liefert das Inhaltsverzeichnis, das immer mitfährt.

### `document_links`

Wiki-Verlinkung, Backlinks werden daraus berechnet.

| Spalte | Typ |
|---|---|
| `from_path` | TEXT |
| `to_path` | TEXT |

PK über beide Spalten.

### `document_suggestions`

Der Bestätigungsschritt für Wiki-Änderungen aus dem Lernlauf.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | TEXT PK | |
| `document_id` | INTEGER FK NULL | NULL = neues Dokument |
| `import_id` | INTEGER FK NULL | NULL = aus dem Gesprächs-Lernlauf, gesetzt = aus einem Import |
| `title`, `section` | TEXT | |
| `content`, `preview` | TEXT | |
| `status` | TEXT | `open` / `applied` / `rejected` |
| `created_at` | TEXT | |

### `imports`

Große Wissensquellen einlesen: Kurstranskript, PDF, Buch. **Eigenständiges Objekt mit Lebensdauer — nicht im Chat**, gleiche Begründung wie bei `jobs`. Ein Anhang lebt nur im Prompt; ein fortsetzbarer Lauf braucht eine Ablage.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | Adresse für "Import fortsetzen" |
| `source_path` | TEXT | Rohdatei im Archiv, **nicht** unter `knowledge/` |
| `source_type` | TEXT | Adapter, z. B. `udemy_transcript` — der austauschbare Kopf |
| `title` | TEXT | "SEO-Kurs" |
| `status` | TEXT | `planned` / `running` / `paused` / `done` / `failed` |
| `data_scope` | TEXT | Pflicht vor dem Start, siehe unten |
| `cost_usd`, `max_budget_usd` | REAL | wie bei `jobs` |
| `created_at`, `updated_at` | TEXT | |

**Drei Schritte, das Modell erst im dritten: normalisieren → schneiden → destillieren.** Die ersten beiden sind deterministisch und kostenlos. Quellenspezifisch ist nur dieser vordere Teil — Destillieren, Schreiben, Verlinken, Fortschritt, Budget sind für alle Quellen gleich. Also eine Pipeline mit austauschbarem Kopf, kein Universal-Importer. Findet sich für eine Quelle kein Kopf, fragt der Import nach, statt zu raten.

**Kein Fortschrittszähler, keine Chunk-Offsets.** Weil der Schnitt deterministisch ist, lässt sich die Abschnittsliste jederzeit kostenlos aus der Quelldatei neu erzeugen. "Wo waren wir?" ist ein Abgleich: neu schneiden, gegen die vorhandenen `document_suggestions` mit dieser `import_id` halten, fehlende Abschnitte abarbeiten. Gespeichert wird nur, was sich nicht rekonstruieren lässt.

**`data_scope` muss vor dem ersten Aufruf feststehen** und wird an jedes erzeugte Dokument durchgereicht — ein Kurs über den Arbeitgeber erzeugt Dokumente mit `employer`. Es ist die einzige Angabe, die beim Start erzwungen wird (siehe Leitregel: das Einzige, was sich nachträglich nicht reparieren lässt).

**Freigabe gebündelt, nicht pro Abschnitt.** Der Lauf produziert durch, der Review passiert danach über `document_suggestions`. Ein Lauf, der nach jedem Abschnitt auf Bestätigung wartet, ist über 18 Abschnitte unbenutzbar.

**Der Rohtext wird kein Dokument.** Gesprochener Kursinhalt ist überwiegend Füllwort; er würde den Index verstopfen, dessen Zusammenfassung ohnehin auf ein bis zwei Sätze kappt. In die Wissensdatenbank kommt nur das Destillat, die Rohdatei bleibt Archiv.

**Beispiel eines Kopfes** (`udemy_transcript`): zweistufige Marker im Text — `=== Sektion ===`, `--- Lektion ---`. Sektion wird ein Dokument, Lektionen werden Abschnitte darin, der Kurs eine Indexseite mit Links. Destilliert wird pro Sektion mit den Lektionsüberschriften im Block, nicht pro Lektion — sonst viele kleine Aufrufe mit vollem Prompt-Overhead je Lektion.

### `personas`

Rollendefinition als Objekt. **Abweichung von der Fassung vom 28.07.**, die Personas bewusst als reine Konfiguration führte: sobald eine Rolle einen Index-Ausschnitt und eine Werkzeug-Vorauswahl hat, ist sie adressierbar — eine wachsende Konfigurationsstruktur wäre dieselbe Sache in unehrlicher Form.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | TEXT PK | `assistant`, `coach`, `focus` |
| `name`, `description` | TEXT | |
| `tools` | TEXT | JSON-Array: Vorauswahl, keine Berechtigung |
| `index_scope` | TEXT | JSON: welcher Ausschnitt des Dokument-Index mitfährt |
| `document_id` | INTEGER FK NULL | Zeiger auf das Arbeitsweise-Dokument |

**Eine Persona lädt kein Wissen, sie filtert den Index.** Das folgt aus Leitregel 2 (Dokumente werden gesucht, nicht mitgeschickt) — eine Rolle, die Dokumente vorlädt, arbeitet gegen die zentrale Kostenregel. Der SEO-Coach sieht die SEO-Dokumente im Inhaltsverzeichnis und sucht sich, was er braucht. Die feste Leseliste bleibt dem **unbeaufsichtigten Lauf** vorbehalten, wo Suchen zu unzuverlässig ist.

**Die Arbeitsweise ist ein Dokument, keine Spalte.** Damit ist sie lesbar, im Gespräch änderbar, hat eine Historie und kann verlinkt werden — siehe "Wissen steuert Ausführung" im Konzept.

**Der Lernstand gehört nicht hierher.** Er ist eine Eigenschaft des Imports und wird auf der Persona-Seite nur angezeigt (Verknüpfung über `index_scope`). Sonst stünde derselbe Fortschritt an zwei Orten, sobald zwei Rollen dasselbe Thema führen.

### `messages`

Ein Strom. Keine Sessions.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `role` | TEXT | `user` / `assistant` / `tool` |
| `text` | TEXT | |
| `thread_id` | INTEGER FK NULL | Themen-Etikett |
| `project_id` | INTEGER FK NULL | |
| `client_id` | TEXT | woher sie kam — **Gerät** (`mac-private`, `jarvis-web`, …) |
| `tab_id` | TEXT NULL | **Zwischenlösung:** isoliert mehrere gleichzeitig offene Web-Fenster. Entfällt, sobald `thread_id` die Fensterbildung übernimmt — dann wird nach Thema gefenstert, nicht pro Tab. |
| `data_scope` | TEXT | Default `own` |
| `created_at` | TEXT | |

`data_scope` gilt ebenso für `threads` und `daily_summaries` — Nachrichten enthalten Arbeitsinhalte, sobald über ein Arbeitsprojekt gesprochen wird.

### `threads`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `title` | TEXT | vom Modell vergeben |
| `project_id` | INTEGER FK NULL | |
| `collection_id` | TEXT FK NULL | Gruppierung für Themen ohne Projekt |
| `last_activity_at` | TEXT | |
| `summary` | TEXT NULL | wenn verdichtet |

**Fensteraufbau:** Nachrichten des laufenden Threads + je eine Zeile zu anderen Threads desselben Tages. Nicht "die letzten N".

**Warum `collection_id` neben `project_id`:** ein Projekt ist die einzige Gruppierungsachse, die es sonst gäbe — für ein Thema wie "Steuern" gibt es kein sinnvolles Projekt, und ein Bereich als Projektzeile getarnt würde Projektliste, Finanzansicht und Stundenschätzung mit einem Ding verseuchen, das keines davon ist. `collections` existiert im Modell ohnehin ("hat es Logik → feste Tabelle, sind es nur Daten → Collection"); für die reine Gruppierung reichen `id`/`name`/`icon`, ohne `fields`/`entries`.

**Bewusst ein einzelner FK, keine n:m-Zuordnung.** Ein Thread liegt in genau einer Gruppe — Ordner, keine Etiketten. Soll ein Thread unter mehreren Kategorien erscheinen, ist das ein anderes Konzept mit anderer Oberfläche und wird dann als solches entschieden, nicht durch eine stillschweigend hinzugefügte Zwischentabelle.

### `daily_summaries`

Tagesrückblick statt Session-Zusammenfassung.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | `YYYY-MM-DD`, unique |
| `text` | TEXT | worum ging es, was entschieden, was offen |
| `created_at` | TEXT | |

Die letzten Tage erscheinen als eine Zeile im Index, damit das Modell weiß, dass es etwas zu holen gibt.

### `jobs`

Coding-Aufträge. Eigenständige Objekte mit Lebensdauer — **nicht** im Chat.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `project_id` | INTEGER FK | |
| `issue_number` | INTEGER NULL | GitHub |
| `title` | TEXT | |
| `plan` | TEXT | aus dem Planungslauf |
| `base_branch` | TEXT | wovon abgezweigt wird |
| `branch` | TEXT | Zielbranch |
| `risk` | TEXT | `low` / `medium` / `high` — steuert Sortierung im Review |
| `status` | TEXT | `planned` / `approved` / `running` / `awaiting_review` / `done` / `failed` |
| `session_id` | TEXT NULL | Claude-Code-Session, für `--resume` |
| `cost_usd` | REAL NULL | aus `total_cost_usd` |
| `result` | TEXT NULL | Zusammenfassung: was geändert, was bewusst nicht, wo vom Plan abgewichen |
| `changed_files` | TEXT NULL | JSON: Datei + Zeilenzahl (`git diff --stat`) — Grundlage für den Plan-Abgleich |
| `test_evidence` | TEXT NULL | was geprüft wurde, mit Ergebnis |
| `denials` | TEXT NULL | `permission_denials` — Abbruchgrund |
| `created_at`, `updated_at` | TEXT | |

**`base_branch` ist Pflicht bei abhängigen Aufträgen.** Zeigt er auf einen Vorgänger und der scheitert, stoppt die Kette; bei unabhängigen läuft sie weiter.

**Keine Migration, sondern die erste Persistenz überhaupt.** `coding_engine.py` hält Task-Status heute in einem In-Memory-Dict — kein Coding-Task überlebt einen Neustart. `jobs` ersetzt also nichts, es füllt eine Lücke.

### `clients`

Geräte, nicht Kunden.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | TEXT PK | `mac-private`, `mac-work`, `jarvis-server`, `jarvis-web` |
| `type` | TEXT | `worker` / `ui` / `speaker` |
| `expected_account` | TEXT NULL | `email` oder `orgId` aus `claude auth status` |
| `online` | INTEGER | |
| `last_seen_at` | TEXT | |

**`expected_account` ist die Absicherung der Kontotrennung.** Der Worker ruft vor jedem Job `claude auth status` auf und vergleicht — bei Abweichung Abbruch statt Ausführung. Damit hängt die Trennung nicht allein an der korrekten Client-Zuordnung des Projekts.

### `queue`

Gehört zum Client, nicht zum System.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `client_id` | TEXT FK | |
| `kind` | TEXT | `command` / `coding_run` / `issue_refresh` |
| `payload` | TEXT | JSON |
| `status` | TEXT | `open` / `running` / `done` / `failed` |
| `created_at`, `completed_at` | TEXT | |

### `issue_cache`

Verfällt, gehört nicht ins Gedächtnis.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `project_id` | INTEGER FK | |
| `issue_number` | INTEGER | |
| `title`, `body`, `state` | TEXT | |
| `fetched_at` | TEXT | wird in der UI als "Stand: …" angezeigt |

### `demos`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `project_id` | INTEGER FK | |
| `url` | TEXT | |
| `container` | TEXT | |
| `status` | TEXT | `running` / `stopped` / `failed` |
| `expires_at` | TEXT | Default +30 Tage, verlängerbar |
| `last_check_at`, `last_check_ok` | | Health-Check |

### `routines` und `alerts`

**Alles Wiederkehrende in einer Tabelle.** Zeitgesteuerte Routinen (Morning Check-in, VfB-Ergebnis um 20:00) und bedingungsgesteuerte Anlässe (4 Tage kein Training) sind dieselbe Sache mit unterschiedlichem Auslöser.

**Dynamisch heißt neue Zeilen, nicht neue Struktur.** Deshalb kein JSON-Blob: JARVIS kann beliebig viele Routinen anlegen, und es bleibt trotzdem abfragbar ("was ist heute fällig", "welche Routinen sind aktiv").

**`routines`**

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | TEXT PK | `morning_checkin`, `vfb_result`, `no_training_4days` |
| `name` | TEXT | |
| `trigger_type` | TEXT | `time` / `condition` |
| `trigger` | TEXT | Cron-artig (`weekdays 08:00`) oder Bedingungsausdruck |
| `action` | TEXT | `notify` / `start_conversation` / `job` |
| `agenda` | TEXT | Freitext — was JARVIS tun/fragen soll |
| `tools` | TEXT | JSON-Array erlaubter Werkzeuge |
| `level` | INTEGER | 1 = Ablage, 2 = Push, 3 = gesprochen |
| `cooldown_hours` | INTEGER | verhindert Wiederholung |
| `active` | INTEGER | abschaltbar |
| `last_run_at` | TEXT | |

Zwei Beispiele:

```
morning_checkin | time | weekdays 08:00 | start_conversation | level 2
  agenda: "Frag nach Schlaf, Tagesplan, wichtigstem Ziel heute.
           Zeig offene Punkte und heutige Termine."

vfb_result      | time | daily 20:00    | notify             | level 1
  tools: ["web_search"]
  agenda: "Prüf, ob der VfB heute gespielt hat. Wenn ja: Ergebnis kurz
           melden. Wenn nein: nichts."
```

**Die Agenda ist Text** — also im Gespräch änderbar ("nimm Sport mit rein"), ohne Code.

**Eine Routine muss schweigen dürfen.** Ohne das "wenn nein: nichts" entstehen 300 Meldungen im Jahr, von denen 250 nichts sagen.

**`alerts`** (ausgelöste Instanzen)

| Spalte | Bedeutung |
|---|---|
| `id`, `routine_id` | |
| `subject_ref` | worauf er sich bezieht (z. B. Todo-ID) — Cooldown gilt pro Bezug, nicht pro Routine |
| `text` | formuliert, nur bei `level` ≥ 2 |
| `status` | `open` / `seen` / `dismissed` |
| `triggered_at` | |

**Rückkopplung:** Häuft sich `dismissed` bei einer Routine, wird deren `level` gesenkt oder `cooldown_hours` erhöht. Sonst ist Wegwischen folgenlos und man schaltet irgendwann alles ab.

**Eskalation:** Gewünschtes Level versuchen; ist kein passender Client online, eine Stufe tiefer.

**JARVIS darf Routinen anlegen** — "ab jetzt jeden Morgen ein Check-in" erzeugt einen Vorschlag, Simon bestätigt. Anlegen und Ändern braucht Bestätigung, Ausführen nicht. Die Liste der aktiven Routinen fährt im Prompt mit, sonst entsteht beim nächsten Mal ein zweites Check-in.

### `collections` und `entries`

**Der generische Teil.** Damit ein neues Thema (Gitarrenüben, Bücher, Gewicht) kein Code-Projekt ist.

Trennlinie: **Hat es Logik → feste Tabelle. Sind es nur Daten → Collection.** Jobs haben Statusübergänge, Facts steuern den Prompt, Projects steuern Client und Autonomie — das kann nicht generisch sein. Erfassen, anzeigen, auswerten dagegen ist immer dasselbe.

**`collections`**

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | TEXT PK | `guitar`, `sport`, `weight` |
| `name` | TEXT | Anzeigename |
| `icon` | TEXT | |
| `fields` | TEXT | JSON-Schema (siehe unten) |
| `data_scope` | TEXT | |

```json
{"fields": [
  {"key": "piece",  "type": "text",    "label": "Stück"},
  {"key": "minutes","type": "number",  "label": "Dauer", "target": 30},
  {"key": "bpm",    "type": "number",  "label": "Tempo"},
  {"key": "clean",  "type": "boolean", "label": "Fehlerfrei"}
]}
```

**`target` ist optional und das Zuhause der bisherigen `tracking.goals`.** Ein Zielwert pro Topic (`sport` → `kalorien_ziel: 2800`) ist weder ein Fact noch ein Entry, sondern eine Eigenschaft des Feldes. Die generische Ansicht zeichnet daraus automatisch eine Ziellinie.

Nicht zu verwechseln mit persönlichen Zielen ("will in 2 Jahren ausziehen") — die sind `facts.category = 'goals'`.

**`entries`**

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | INTEGER PK | |
| `collection_id` | TEXT FK | |
| `date` | TEXT | |
| `data` | TEXT | JSON nach Schema |
| `project_id` | INTEGER FK NULL | |
| `created_at` | TEXT | |

**Die UI wird einmal gebaut, nicht pro Thema.** Aus der Felddefinition entstehen Tabelle, Eingabeformular und Diagramm (für alles vom Typ `number` oder `date`) automatisch. Die Seitenleiste zeigt "Sammlungen" mit dem, was existiert.

**JARVIS darf Collections anlegen** — Simon sagt "ich will Gitarrenüben tracken", JARVIS schlägt Felder vor, nach Bestätigung existiert die Ansicht sofort. Danach reicht "heute 25 Minuten Blackbird, 88 BPM".

Zwei Regeln:
- **Collection anlegen oder Felder ändern braucht Bestätigung** (wie ein Wiki-Update). Entries anlegen nicht.
- **Die Collection-Liste fährt im Prompt mit**, wie der Wissens-Index. Sonst legt JARVIS beim nächsten Mal "music_practice" neben "guitar" an.

**Beförderung:** Jedes neue Thema startet als Collection. Braucht es später eigene Logik (wie die Buchhaltung mit SevDesk-Import und Hochrechnung), zieht es in eine eigene Tabelle um — dann weiß man, welche Felder tatsächlich gebraucht werden, statt es vorher zu raten. Was nie über Erfassen und Anzeigen hinauskommt, bleibt für immer Collection und hat null Code gekostet.

**Preis:** Abfragen über JSON (`json_extract`) sind langsamer als echte Spalten — bei einigen tausend Einträgen pro Collection irrelevant. Und generische Ansichten sehen generisch aus: der Gewinn-Trend der Finanzansicht wäre so nicht baubar. Genau dafür gibt es die Beförderung.

### Drei generische Bausteine

Zusammen ersetzen sie Code pro Thema:

| Baustein | Beantwortet |
|---|---|
| **collections** | was erfasst wird |
| **routines** | was wann passiert |
| **documents** | wie etwas gemacht wird |

Alle drei sind Daten, alle drei kann JARVIS selbst anlegen, alle drei sind im Gespräch änderbar.

---

## Änderungen an bestehenden Tabellen

Abgleich mit dem Stand vom 29.07.2026 (`brain.db`, `local_data.db`, `sessions.db`, `knowledge_index.db`).

**Alle bestehenden Tabellen werden dabei auf englische Bezeichner umgestellt:** `projekte` → `projects`, `kontakte` → `contacts`, `rechnungen` → `invoices`, `ausgaben` → `expenses`, `seiten` → `documents`, `logs` → `entries`.

### Bleibt inhaltlich unverändert

| Bestehend | Anmerkung |
|---|---|
| `kontakte` → `contacts` | wird durch die Mail-Zuordnung aufgewertet — `email` ist bereits da |
| `rechnungen` → `invoices`, `ausgaben` → `expenses` | inkl. `locked`-Flag (bisher `gesperrt`) gegen Überschreiben beim CSV-Import — gutes Detail, behalten |
| `knowledge_index` → `documents` | nur `data_scope` und Bezüge ergänzen |
| `knowledge_links` → `document_links` | unverändert übernehmen |

**Korrektur (29.07.):** `knowledge_suggestions` → `document_suggestions` ist **nicht** unverändert übernehmbar, wie hier ursprünglich stand. Das Zielschema ersetzt `topic`/`file`/`heading` durch `document_id` (FK) / `title` / `section`, und `status` wechselt von `pending` auf `open`. Gehört damit zu den Umbauten, gekoppelt an die `documents`-Verschmelzung.

### Ergänzungen

**Fehlende Beziehungen** — der Kern des Abgleichs. Ohne sie sind ganze Fragestellungen nicht beantwortbar:

| Tabelle | Neu | Warum |
|---|---|---|
| `projects` | `contact_id` | Der Kunde war nirgends verknüpft. "Alle Projekte von Kunde X" ist sonst nicht abfragbar. |
| `invoices` | `contact_id` (ersetzt `kunde TEXT`) | Freitext lässt sich nicht joinen. Der Name bleibt als `customer_name` Rückfallwert für Importe ohne Treffer. |
| `expenses` | `project_id` (optional), `contact_id` | Ausgaben sind überwiegend laufend (Lizenzen, Abos), nicht projektbezogen — `project_id` ist die Ausnahme für Fremdleistung oder projektspezifisches Hosting. `contact_id` = Lieferant. |
| `todos` | `project_id` | |
| `contacts` | `company` | "Digital Mindset" ist eine Firma, der Ansprechpartner eine Person. |
| `projects` | `estimated_hours` | `estimated_value` ist Geld. Für den Kreislauf "war aufwendiger als gedacht" braucht es Aufwand. Woher die tatsächlichen Stunden kommen (Clockodo-Import oder Collection), bleibt offen. |

**Weitere Spalten**

| Tabelle | Neu |
|---|---|
| `projects` | `data_scope`, `client_id`, `autonomy`, `path`, `repo` |
| `invoices`, `expenses` | `data_scope` |
| alle Gedächtnis-Tabellen | `data_scope` |

**Ein Projekt hat einen Hauptkontakt** (1:n). Mehrere Ansprechpartner pro Projekt wären n:m über eine Zwischentabelle — bewusst nicht jetzt, erst wenn es real stört.

**Beim CSV-Import schützt `locked` auch die Zuordnung.** Wird `project_id` oder `contact_id` von Hand gesetzt, darf der nächste SevDesk-Import sie nicht überschreiben.

### Umbauten

**`brain.db` → echte Tabellen.** Ein JSON-Blob pro Section lässt sich nicht abfragen: "alle aktiven Ziele" oder "alle Fakten mit `data_scope = 'employer'`" erfordert, das ganze Blob zu laden und im Code zu filtern. Deshalb:

- `memory`, `followups`, `profile` → Tabelle `facts` mit echten Spalten
- `events`, `behavior` → Tabelle `routines`, sofern dort Wiederkehrendes konfiguriert war. Der Blob war vermutlich der Versuch, Erweiterbarkeit über Schemalosigkeit zu erreichen — gebraucht werden aber neue **Zeilen**, keine neue Struktur.
- `config`, `modules` → **bleiben Key-Value.** Für Konfiguration ist das genau richtig; nicht anfassen.

**`logs` → `collections` + `entries`.** `logs` ist der generische Datenteil bereits — `topic` entspricht der Collection, `key`/`value`/`text_value` dem Feldpaar. Was fehlt, ist die **Schema-Definition**: ohne `collections` weiß die UI nicht, welche Felder ein Topic hat, und kann keine Ansicht erzeugen. Bestehende Log-Zeilen lassen sich pro `topic` in Entries umschreiben.

Damit wird auch Sport-Tracking eine Collection, keine feste Tabelle — bis dort echte Trainingslogik entsteht.

**`sessions` → `messages` + `threads` + `daily_summaries`.** Die vorhandenen Felder `clients`, `category` und `tab_id` zeigen bereits in Richtung Threads; sie werden dorthin überführt. Bestehende Transkripte bleiben als Rückblicke durchsuchbar.

**`seiten` und `knowledge/*.md` zusammenlegen.** Zwei Dokumentsysteme nebeneinander — `seiten` hängt hierarchisch an Todos/Projekten/Kontakten, `knowledge/` ist frei und verlinkt. Sie machen dasselbe. Zusammengelegt in `documents`, die Verschachtelung wird zu `document_links`.

**Dabei `parent_typ`/`parent_id` auflösen.** Ein polymorpher Verweis (mal auf `todos`, mal auf `projekte`, mal auf `kontakte`) lässt keinen Fremdschlüssel zu, die Datenbank kann nichts prüfen, und jede Abfrage braucht eine Fallunterscheidung. Stattdessen drei optionale Spalten: `project_id`, `contact_id`, `todo_id`.

**GitHub-Felder aus `todos` herauslösen** (`source`, `external_id`, `repo`, `body`, `labels`) → `issue_cache`. Aktuell liegen Issues als Todos in derselben Tabelle: damit ist Verfallendes mit Bleibendem vermischt. Ein Issue-Cache soll jederzeit wegwerfbar und neu ziehbar sein, ein Todo nicht.

Was bleibt: ein Todo darf auf einen Issue verweisen (`issue_ref`), wenn Simon daraus bewusst eine Aufgabe gemacht hat.

---

## Der API-Schnitt

**Notation:** Die HTTP-Schreibweise unten ist **konzeptionell, nicht wörtlich.** Sie benennt Fähigkeiten und die Grenze zwischen Kern und Client — nicht das Transportprotokoll. JARVIS spricht WebSocket (`protocol.py`), das bleibt so. **Keine zweite HTTP-Schicht daneben bauen.** `POST /api/chat` heißt: eine Nachricht mit diesem Zweck und dieser Nutzlast.

**Grundsatz: ein dicker Endpunkt für Gespräche, dünne Endpunkte für Ansichten.**

Der Client schickt nur Text. Der Kern baut den Prompt, sucht bei Bedarf, ruft das Modell, schreibt in den Strom, gibt die Antwort zurück. **Kein Client baut jemals selbst einen Prompt oder ruft die Anthropic-API auf.** Sonst muss jede Änderung dreimal gemacht werden, und ein Speaker ist kein Projekt von Stunden, sondern von Wochen.

### Gespräch

```
POST /api/chat
  { "text": "...", "client_id": "jarvis-web", "project_id": 42 (optional) }
  → { "reply": "...", "thread_id": 7, "message_id": 913 }
```

`project_id` ist optional: die Web-App kann sie setzen, der Speaker nicht. Fehlt sie, bestimmt der Kern den Thread selbst.

Zusätzlich `POST /api/chat/stream` für tokenweise Ausgabe.

### Ansichten (dünn, CRUD)

```
GET    /api/projects                    ?status= &type= &data_scope=
POST   /api/projects
PATCH  /api/projects/:id
POST   /api/projects/:id/commission     Demo → Kundenprojekt (setzt 4 Felder)

GET    /api/projects/:id/jobs
GET    /api/projects/:id/issues         aus dem Cache, mit fetched_at
GET    /api/projects/:id/messages

GET    /api/facts                       ?category= &status=
PATCH  /api/facts/:id                   Status setzen (Ziel erreicht)

GET    /api/documents                   Index
GET    /api/documents/:id
GET    /api/search?q=                   über documents, daily_summaries, messages

GET    /api/alerts?status=open          "Offene Punkte"-Karte
POST   /api/alerts/:id/dismiss

GET    /api/demos
POST   /api/demos/:id/extend
POST   /api/demos/:id/reset

GET    /api/collections                 Liste inkl. Feldschema
POST   /api/collections                 (Bestätigung nötig)
PATCH  /api/collections/:id             Felder ändern (Bestätigung nötig)
GET    /api/collections/:id/entries     ?from= &to=
POST   /api/collections/:id/entries     keine Bestätigung

GET    /api/routines                    ?active=
POST   /api/routines                    (Bestätigung nötig)
PATCH  /api/routines/:id                Agenda/Level/active ändern
POST   /api/routines/:id/run            manuell auslösen
```

### Jobs

```
POST  /api/projects/:id/plan            Planungslauf über offene Issues
      → Liste von Job-Objekten (Status planned)
POST  /api/jobs/:id/approve             → approved, landet in der Queue
GET   /api/jobs/:id/diff                für die Review-Ansicht
POST  /api/jobs/:id/merge
POST  /api/jobs/:id/revise              { "comment": "..." } → --resume
POST  /api/jobs/:id/discard
```

### Statistiken: der Kern rechnet, der Client zeichnet

**Regel:** Alles mit Fachlogik wird serverseitig berechnet und über einen Endpunkt geliefert. Der Client bekommt fertige Zahlen und stellt sie dar — mehr nicht.

Grund: Liegt die Berechnung in der Vue-Komponente, kann nur die Web-App die Frage beantworten. Der Speaker weiß dann nichts, und JARVIS kann die Zahlen nicht im Gespräch verwenden ("dein Gesamtpotenzial liegt bei 34.176, davon 14.601 tatsächlich").

Ausnahme: bei generischen Collections darf die UI selbst aggregieren — dort sind es nur Summen und Durchschnitte.

**Vorprogrammierte Auswertungen**

```
GET /api/finance/summary              die drei Kacheln
  → { estimated, actual, total_potential }

GET /api/finance/trend?months=24      der Gewinn-Trend
  → { actual: [...],     aus bezahlten Rechnungen
      pipeline: [...],   aus projects.estimated_value + expected_close_date
      forecast: [...],   Hochrechnung
      cumulative: [...],
      monthly_run_rate: 635 }

GET /api/projects/:id/profitability
  → { revenue, estimated_hours, actual_hours,
      effective_hourly_rate, estimate_deviation }

GET /api/contacts/:id/revenue         was ein Kunde insgesamt gebracht hat
GET /api/costs/recurring              laufende Ausgaben, Abos, €/Monat
GET /api/costs/jarvis?months=12       Summe aus jobs.cost_usd
```

**Rentabilität ist eine Stundensatz-Frage, keine Ausgabenrechnung.** Da Ausgaben überwiegend laufend sind, sagt "Einnahmen minus zugeordnete Kosten" nichts aus. Aussagekräftig ist der effektive Stundensatz (10.950 € bei 120 Stunden = 91 €/h) — daran zeigt sich, welche Art von Projekt sich lohnt. Die laufenden Kosten stehen als monatlicher Sockel dagegen (`/api/costs/recurring`), nicht in einzelnen Projekten.

`estimate_deviation` ist die Zahl, die den Kreislauf Demo-Erkenntnis → bessere Schätzung messbar macht.

### Worker-Protokoll

Der Client meldet sich an und holt sich Arbeit — die Verbindung geht immer von innen nach außen.

```
POST /api/clients/register      { "client_id": "mac-private" }
GET  /api/clients/:id/next      → Job aus der Queue oder leer
POST /api/clients/:id/result    { "job_id": .., "status": .., "output": ..,
                                  "cost_usd": .., "session_id": .., "denials": [] }
```

Dauerverbindung (WebSocket) ist die spätere Optimierung; für den Anfang reicht regelmäßiges Nachfragen. Am Modell ändert das nichts.

### Werkzeuge (intern, nicht über HTTP)

Was das Modell aufrufen darf. Global verfügbar, pro Persona nur vorausgewählt:

```
search_knowledge(query)             documents
search_conversations(query)         daily_summaries + messages
create_fact(text, category)
update_fact(id, status)
list_todos / create_todo
read_calendar(from, to)
read_mail(since, unread)
create_project(...)
plan_jobs(project_id)
create_collection(name, fields)     Bestätigung
create_entry(collection_id, data)
create_routine(name, trigger, agenda)   Bestätigung
web_search(query)                   nur wo freigegeben
```

**Reihenfolge im Prompt** (byteweises Caching):

1. Werkzeugbeschreibungen — ändern sich nie
2. Basis-Systemprompt + Persona — selten
3. Facts + Dokument-Index + Collection-Liste + aktive Routines — selten
4. Tagesübersicht, Datum — täglich
5. Nachrichten des Threads — ständig

Kein Zeitstempel weiter vorne als Stufe 4.

---

## Was bewusst nicht im Modell steht

- **Mails** — werden abgefragt, nicht gespeichert. Nur Abgeleitetes wird zum Fact.
- **Sessions** — existieren nicht; ein Thread ist ein Etikett, kein Behälter.
- **Persona-Zuordnung an Werkzeugen** — Werkzeuge sind global, die Vorauswahl liegt in der Persona-Konfiguration, nicht als Berechtigung in der Datenbank.
- **Berechtigungen zwischen Clients** — Erreichbarkeit ersetzt Berechtigungslogik. Ein Client, der offline ist, kann nichts tun.
