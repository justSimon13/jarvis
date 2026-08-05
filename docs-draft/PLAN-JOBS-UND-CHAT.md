# Plan: Coding-Jobs als Gespräch

Grundlage: `FAZIT-JOBS-UND-CHAT-2026-08-04.md`. Dort stehen die Befunde aus zwei
echten Verläufen. Dieses Dokument beschreibt das Zielbild und ordnet die Befunde
darin ein — die erste Fassung war nach Einzelbefunden sortiert und dadurch am
eigentlichen Ziel vorbei.

---

## Das Zielbild in einem Satz

**Ein Job ist ein Gespräch über eine Aufgabe, geführt von JARVIS mit Claude
Code, mit beliebig vielen Läufen — nicht ein Abschuss mit genau zwei Stationen.**

Der klare Zuschnitt bleibt: ein Job ist eine Aufgabe (ein Ticket, ein Branch,
ein Ergebnis). Das ist die Stärke des heutigen Modells und wird nicht angetastet.
Was sich ändert, ist alles darunter.

## Die drei Ebenen

Weil „das Modell" mehrdeutig ist, hier die Begriffe, die im Rest des Dokuments
gelten:

| Ebene | Was | Wo |
|---|---|---|
| **JARVIS** | der Chat, formuliert Aufträge, entscheidet oder fragt Simon | HP-Server |
| **Worker** | nimmt Aufträge an, startet Claude Code, macht Git | Tauri-App auf dem Mac |
| **Claude Code** | `claude -p`, liest und schreibt Code | vom Worker gestartet |

## Die eine technische Grenze

`claude -p` ist **nicht interaktiv**. Der Prozess läuft bis zum Ende und
beendet sich. Er kann nicht mittendrin fragen und auf Antwort warten.

Was geht: ein Lauf **endet** mit einer Frage, JARVIS beantwortet sie, indem er
den nächsten Lauf mit `--resume` in derselben Sitzung startet.

**Der Dialog findet auf Ebene der Läufe statt, nicht der Nachrichten.** Das ist
die Form, in die sich alles Weitere fügen muss — und für den gewünschten Ablauf
reicht sie vollständig aus.

Konsequenz für den Prompt: heute sagt er sinngemäß „frag nicht, niemand hört
zu". Künftig muss er sagen: **„Brauchst du eine Entscheidung, beende den Lauf und
stelle die Frage."** Aus einem Abbruch wird damit ein regulärer Zug im Gespräch.

---

## Der Ablauf am Beispiel

Ticket #1488, „Fahrzeug-Art-Filter für Ansprechpartner ergänzen".

**1 — Simon:** „Nimm dir Issue 1488 im Webshop-Frontend vor."

**2 — JARVIS** holt das Ticket, liest es, formuliert einen Auftrag daraus und
legt den Job an. Er legt dabei gleich fest, was der Worker sonst raten müsste:
Branch, Ziel, **und die Commit-Nachricht** (`feat: mfs-1488 fahrzeug-art-filter
für ansprechpartner ergänzen`).

→ Job #52 angelegt, Lauf 1 startet (`kind = plan`).

**3 — Claude Code** sieht sich den Code an und endet mit einem Plan. Der Worker
gibt ihn samt `session_id` an JARVIS zurück.

**4 — JARVIS** zeigt den Plan im Chat und ordnet ein: was daran unklar ist, was
er selbst entscheiden würde.

**5 — Simon** antwortet frei. Nicht nur ja oder nein: *„Ja, aber lass die
Migration weg, die kommt separat."*

**6 — JARVIS** startet Lauf 2 (`kind = execute`) mit `--resume` und dieser
Antwort. Claude Code arbeitet weiter in derselben Sitzung, kennt also den
gesamten Verlauf.

**7 — Claude Code** endet mit einer Rückfrage: *„Der Filter existiert in zwei
Varianten, welche ist gemeint?"*

Heute wäre das ein Abbruch. Künftig ist es ein Zug: der Lauf endet sauber, die
Frage steht im Ergebnis, der Job wartet.

**8 — JARVIS** kann das aus dem Ticket beantworten und tut es selbst — Lauf 3
mit der Antwort. Könnte er es nicht, fragt er Simon. **Was er selbst entscheiden
darf, steht in der Autonomie-Einstellung.**

**9 — Claude Code** meldet fertig. Optional folgt Lauf 4 (`kind = review`): ein
eigener Lauf, der ausschließlich den Diff prüft und Auffälligkeiten meldet.

**10 — JARVIS** zeigt das Ergebnis: geänderte Dateien mit Zeilenzahl, Abgleich
gegen den freigegebenen Plan, Testnachweis, Review-Anmerkungen.

**11 — Simon** gibt frei. Erst **jetzt** committet der Worker — mit der
Nachricht aus Schritt 2 — und pusht oder erstellt einen PR, je nach `delivery`.

**Der Job hat vier Läufe, eine Sitzung, einen Branch, einen Commit, eine
Kostensumme.** Heute wären das drei Jobs mit drei Branches gewesen.

---

## Was sich dafür ändern muss

### A. `runs` — die tragende Struktur

Ohne sie bleibt jedes Hin und Her ein neuer Job mit neuem Branch. Genau das ist
in Thread 35 passiert: drei Jobs für eine Aufgabe.

Neue Tabelle: `job_id`, `kind` (`plan` / `execute` / `answer` / `review`),
`session_id`, `cost_usd`, `status`, `started_at`, `ended_at`, `result`,
`question` (gesetzt, wenn der Lauf mit einer Rückfrage endete).

Der Job aggregiert die Kosten. Der Status des Jobs ergibt sich aus dem letzten
Lauf: `awaiting_answer` ist ein eigener Zustand neben `awaiting_review`.

**In der ersten Fassung dieses Plans stand `runs` an letzter Stelle** mit der
Begründung, es löse keinen Befund. Das war falsch — es ist die Voraussetzung für
alles andere hier.

### A2. Zwei Arten von Pause — nicht vermischen

Der Planungslauf mit Freigabe **bleibt unverändert**. Er ist nicht bloß eine
Zeremonie, sondern der Lauf, in dem der Plan entsteht — den will Simon sehen,
bevor Code angefasst wird.

Neu ist nur, dass es daneben eine zweite Art Pause gibt:

| Pause | Wann | Was Simon tut | Job-Status |
|---|---|---|---|
| **Plan-Freigabe** | nach Lauf 1, wenn `careful` | genehmigen, ändern, verwerfen | `awaiting_review` |
| **Rückfrage** | wenn ein Lauf mit einer Frage endet | antworten | `awaiting_answer` |

**Eine Rückfrage braucht keine erneute Plan-Freigabe.** „Welche der beiden
Filtervarianten?" ist keine Planänderung, sondern eine Wissenslücke — sie zu
beantworten heißt weitermachen, nicht neu genehmigen.

Genau daran scheiterte Thread 35: es gab nur eine Sorte Pause. Also wurde aus
jeder Rückfrage entweder ein Abbruch oder ein neuer Job mit neuem Plan und neuer
Freigabe.

Die Antwort auf den Plan ist übrigens schon heute Freitext (`revise`). Die
Plan-Freigabe ist damit selbst der erste Gesprächszug und fügt sich ein, statt
ein Fremdkörper zu sein.

### B. Rückfragen als regulärer Ausgang

Prompt umstellen (siehe oben), `question` aus dem Ergebnis erkennen, Job auf
`awaiting_answer`, Anzeige im Chat mit Antwortfeld. Antwort startet den nächsten
Lauf mit `--resume`.

**Sicherung gegen endloses Hin und Her:** das Budget gilt pro Job über alle Läufe
(existiert bereits als `max_budget_usd`), zusätzlich eine Obergrenze für die
Anzahl der Läufe. Ein Gespräch, das nicht konvergiert, muss auffallen.

### C. JARVIS liefert die Commit-Nachricht

Der Worker soll nichts erraten. Heute nimmt er die erste Zeile des
Abschlussberichts und kürzt sie auf 72 Zeichen — bei Job #39 war das ein
JSON-Fetzen aus einem Tool-Log, weil das Turn-Limit einen ordentlichen Bericht
verhindert hatte.

JARVIS hat das Ticket gelesen und formuliert den Auftrag; er liefert die
Nachricht gleich mit. Format nach der `GIT_CONVENTIONS.md` des jeweiligen
Projekts, sofern vorhanden.

Damit entfällt der Grund, aus dem in Thread 35 zwei zusätzliche Jobs entstanden.
Ein Amend-Werkzeug im Worker bleibt sinnvoll — aber als Korrektur für
Ausnahmefälle, nicht als Reparatur eines Regelfehlers.

### D. Autonomie beantwortet zwei Fragen, nicht eine

Im Beispiel entscheidet JARVIS in Schritt 8 selbst und fragt in Schritt 11.
Diese Grenze muss einstellbar sein — und weil es jetzt zwei Arten von Pause gibt
(siehe A2), muss `autonomy` beide abdecken:

| Stufe | Plan-Freigabe | Rückfragen beantwortet |
|---|---|---|
| `careful` | ja, durch Simon | Simon |
| `guided` | ja, durch Simon | JARVIS, sichtbar im Verlauf |
| `auto` | nein | JARVIS |

`careful` bleibt damit exakt das heutige Verhalten. `guided` ist neu und
vermutlich der Alltagsmodus: Simon entscheidet über den Plan, aber die Rückfrage
nach einem Dateinamen beantwortet JARVIS, ohne zu unterbrechen.

`autonomy` bleibt Projekt-Vorgabe, wird aber pro Job überschreibbar. Die
tatsächlich verwendete Stufe wird in die Job-Zeile geschrieben und angezeigt —
sichtbar, nicht stillschweigend.

**Bewusst keine automatische Risikoeinstufung durch Claude Code.** Eine falsch
eingeschätzte Operation, die deshalb ohne Freigabe läuft, kostet mehr als jede
gesparte Rückfrage.

### E. Der Planungslauf muss den Branch sehen

Lauf 1 hat heute nur `Read/Grep/Glob` — kein Bash, also kein `git log`, kein
`git status`. Bei Neuarbeit egal, bei Anschlussarbeit fatal: der Plan rät.

**Nicht über zusätzliche Rechte lösen.** Das ist in `localExec.js` begründet und
empirisch geprüft: eine schmale Bash-Allowlist kann Schreibzugriff über
Umleitung nicht ausschließen, und eine Ausnahme aus einer breiten Sperre greift
nicht — die Sperre gewinnt.

Stattdessen: **der Worker führt die Git-Abfragen selbst aus und hängt das
Ergebnis an den Auftrag.** Dasselbe Muster wie beim Issue-Inhalt (`_ghIssueView`).

### F. Repo-Zustand als Abfrage

Damit JARVIS ohne Job nachsehen kann. **Kein freier Befehlsparameter** — das wäre
der verworfene Ansatz mit einer Allowlist davor. Stattdessen feste Bedeutung:
`get_repo_state(projekt)` liefert immer aktuellen Branch, Sauberkeit des
Arbeitsbaums, letzte Commits, vorhandene Job-Branches.

Der Worker entscheidet, welche Befehle dafür laufen. Gleiche Bauform wie
`_ghIssueView`: eine Abfrage, keine Shell.

### F2. Den Worker aus der Desktop-App herauslösen

**Voraussetzung dafür, dass `run_command` ersatzlos verschwinden kann** — und
unabhängig davon überfällig.

`localExec.js` ist heute Teil der Tauri-App: Allowlist, Pfadprüfung,
Claude-Code-Start, Git-Abschluss. Das ist ein Ausführungsagent, kein
Oberflächenteil. Er wohnt dort, weil Tauri der schnellste Weg zu Shell-Zugriff
war — eine Zwischenlösung aus der Not, keine Entwurfsentscheidung.

Folgen, solange es so bleibt:

- Jeder Coding-Job hängt daran, dass ein Fenster offen ist.
- Auf dem HP läuft es gar nicht (headless, kein Desktop).
- Die Oberfläche trägt Verantwortung, die nichts mit Anzeigen zu tun hat.

**Ziel:** ein eigenständiger Worker-Prozess, der sich am Server anmeldet wie
heute die Tauri-App — auf dem Mac, auf dem Arbeits-Mac, **und auf dem HP**. Die
Tauri-App wird wieder reine Oberfläche.

Damit erfüllt sich auch der `jarvis-server`-Client aus dem Konzept („immer
online, für spontane Demo-Projekte, wenn Mac und Windows-PC aus sind"), und die
Demo-Automatisierung auf dem HP wird möglich, ohne dafür einen zweiten
Ausführungsweg zu bauen.

Technisch nicht trivial: `localExec.js` nutzt Tauri-Plugin-APIs für Prozesse und
Dateien, die durch Node-Bordmittel ersetzt werden müssen. Eigener Block, nicht
nebenbei.

**Angrenzende offene Frage:** `ARCHITECTURE.md` schließt Docker aus, das Konzept
setzt für Demos Container-Isolation voraus. Der Ausschluss war wegen Audio auf
den Kern gemünzt, nicht auf Demo-Container — das gehört festgehalten, bevor
jemand darüber stolpert. `MIGRATION.md` führt es bereits als offenen Widerspruch.

### G. Den alten Weg entfernen

`services/coding_engine.py`, 1.286 Zeilen — der ursprüngliche Ansatz mit
direktem Zugriff. Noch verdrahtet an `run_command`, `create_project`,
`sync_project`, `commit_and_push`.

**Vorher entwirren:** `server.py` nutzt dasselbe Modul für die Kostenanzeige
(`get_usage_summary`) und `refresh_idle_status`. Das gehört nicht zum alten
Ausführungsweg und darf nicht mit abgerissen werden.

**Offene Entscheidung:** `run_command` läuft auf dem HP-Server, nicht auf einem
Client — Logs, `systemctl status`, Speicherplatz. Das ist Server-Administration
und nicht der verworfene Client-Zugriff. Herauslösen und behalten, oder
streichen? Entscheidet Simon.

---

## Reihenfolge

| # | Schritt | Warum hier |
|---|---|---|
| 1 | `runs` + Rückfragen als Ausgang (A, B) | Trägt alles andere; ohne das bleibt jedes Hin und Her ein neuer Job |
| 2 | JARVIS liefert die Commit-Nachricht (C) | Klein, behebt die Ursache der Kaskade aus Thread 35 |
| 3 | Repo-Zustand als Abfrage (F) | Ersatz für das, wofür `run_command` zweckentfremdet würde |
| 4 | Planungslauf sieht den Branch (E) | Baut auf 3 auf, behebt den Unterschied zwischen Thread 35 und 40 |
| 5 | Autonomie pro Job (D) | Unabhängig, im Alltag sofort spürbar |
| 6 | Alten Weg entfernen (G) | Erst wenn 3 den Ersatz liefert |

Schritte 1 bis 4 fassen beide Repos an und lassen sich hier nicht testen — die
Ausführung liegt im Tauri-Worker. Dafür einen zusammenhängenden Block einplanen.

---

## Was aus dem Fazit hier nicht auftaucht

**Bereits behoben (04.08.):** `data_write` verwarf Projekt-Felder,
`start_coding_job` meldete unvollständige Projekte als „nicht gefunden", und die
Dashboard-Eingabe landete im zuletzt aktiven Thread statt in einem neuen.

**Chat-System, eigener Strang:** doppeltes „Hallo?" (erst nachstellen, dann
entscheiden), elf leere Threads aus dem Find-or-Reuse, und die stille Kappung des
Prompt-Fensters bei 150 Nachrichten ohne Verdichtung — bei langen
Programmier-Gesprächen der Fall, in dem das zuerst weh tut.
