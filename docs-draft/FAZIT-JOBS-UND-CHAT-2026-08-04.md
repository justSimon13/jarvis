# Fazit: Coding-Jobs und Chat-System

**Stand 04.08.2026.** Grundlage sind zwei echte Verläufe, nicht Vermutungen:
Thread 35 („Webshop Frontend v1", 78 Nachrichten, Jobs #39–#41) und Thread 40
(„mfs-docs", 34 Nachrichten, Job #44). Nachrichtennummern in Klammern beziehen
sich auf diese Threads.

Dieses Dokument hält fest, **was beobachtet wurde und was daraus folgt** — es ist
kein Umbauplan. Der kommt danach, auf dieser Grundlage.

---

## Die zentrale Erkenntnis

**`careful` funktioniert bei Neuarbeit und versagt bei Anschlussarbeit.**

Das erklärt beide Threads mit einer einzigen Ursache.

Thread 40 sollte ein neues Dokument schreiben. Der Planungslauf braucht dafür
keinen Git-Zustand — es gibt keinen. Der Job lief sauber durch.

Thread 35 sollte auf einem bestehenden Branch aufsetzen. Der Planungslauf hat
nur `Read/Grep/Glob`, kein Bash. Er kann also weder `git log` noch `git status`
noch `git diff` ausführen und muss raten, was auf dem Zielbranch bereits
passiert ist. Job #40 schreibt das wörtlich in seinen eigenen Plan (589).

Daraus folgten unmittelbar: ein Folgejob, der den Stand nicht kannte, und eine
Kaskade aus drei Jobs für eine einzige Commit-Zeile, die am Ende immer noch
kaputt war.

---

## Das eigentliche Strukturproblem

**Der Job ist das einzige Werkzeug, das den Mac erreicht.**

Alles, was auf dem Arbeitsrechner passieren soll — ein `git log` lesen, einen
Branch-Zustand prüfen, eine Commit-Nachricht korrigieren — muss als Job laufen.
`run_command` läuft auf dem HP-Server, nicht auf dem Worker (591/593).

Ein Job ist aber von Bauart schwergewichtig: eigener Branch, Planungslauf,
Freigabe, Budget, Benachrichtigung. Und er ist **schreibfähig**. Wer über einen
Job etwas nur *ansehen* will, übergibt trotzdem Schreibrechte und verlässt sich
darauf, dass die Anweisung eingehalten wird. Simons Beobachtung dazu: an einer
Stelle sollte nur gelesen und geprüft werden, tatsächlich wurde an der Seite
gearbeitet — nicht gewollt.

**`runs` löst das nicht.** Ein Lauf als eigenes Objekt am Job hilft bei
Kostenzuordnung und Fortsetzung innerhalb einer Aufgabe. Es hilft nicht bei
„ich will nur nachsehen".

Was fehlt, ist **ein leichteres Instrument**: ein rein lesender Befehl auf dem
Worker, ohne Branch, ohne Plan, ohne Freigabe, ohne nennenswerte Kosten. Der
würde zwei Probleme gleichzeitig erledigen — die Kaskade oben wäre nie
entstanden, und der Planungslauf könnte den Zielbranch endlich sehen.

---

## Risiko liegt an der Operation, nicht am Projekt

`autonomy=careful` ist heute eine Projekt-Eigenschaft. Damit bekommt jede
Kleinigkeit dieselbe Zeremonie wie eine Feature-Änderung: ein Amend auf einem
nicht gepushten Branch durchläuft denselben zweistufigen Freigabeprozess wie ein
Eingriff in die Produktivseite.

Das ist der Grund, warum sich die Bedienung zäh anfühlt, obwohl jeder einzelne
Schritt für sich verteidigbar ist.

---

## Konkrete Fehler, beide Threads

| Befund | Belegstelle | Status |
|---|---|---|
| `data_write` auf `projekte` verschluckt Felder beim Anlegen (`path`, `repo`, `base_branch`, `client_id`); dieselben Felder per `data_update` funktionieren | 552 **und** 833 — in beiden Threads | **offen** |
| `start_coding_job` meldet „Projekt nicht gefunden", obwohl es existiert und nur `path` fehlt | 549, 830 | **offen** |
| Planungslauf ohne Bash, blind für den Zielbranch | 589, 595 | **offen** |
| Kein Weg, eine Commit-Nachricht zu korrigieren (Amend blockiert) | 590 | **offen** |
| `run_command` läuft auf dem Server statt auf dem Worker, kein Shell-Zugang zum Mac | 591/592 | **offen** |
| Kaputte Commit-Nachricht: JSON-Fragment aus einem Tool-Log statt Text | 590 | **offen** |
| Job als `failed` geführt, obwohl fertig committet (Turn-Limit) | 587 | behoben (`incomplete`) |
| `check_coding_job_status` gibt 177.823 Zeichen Roh-Mitschnitt zurück | 586 | behoben |

**Der teuerste Einzelfall:** Job #39 kostete $5,31 und galt als gescheitert,
obwohl der Code fertig war. Die zwei Folgejobs für die Commit-Nachricht
erzeugten am Ende keine Änderung.

**Das teuerste Muster:** Fehlermeldungen sagen nicht, was fehlt. „No such file
or directory" statt „läuft auf dem Server, nicht auf dem Worker". „Projekt nicht
gefunden" statt „`path` fehlt". In beiden Fällen schloss JARVIS daraufhin
plausibel, aber falsch weiter und verbrannte Züge. Das ist billig zu beheben und
hat eine große Wirkung.

---

## Chat-System

**Der Thread hat als Themen-Etikett nicht getragen.** Thread 35 heißt „Webshop
Frontend v1", hat Projektbezug und enthält drei unzusammenhängende Themen:
Coding-Jobs (533–596), ein persönliches Gespräch über Anstellung und
Freiberuflichkeit (598), und ab 768 Trackboxx-Debugging für einen anderen
Kunden. Das Etikett bleibt, das Thema wandert. Die bewusst zurückgestellte
Drift-Erkennung ist damit kein theoretischer Punkt mehr.

**Zweimal „Hallo?" hintereinander** (782/784). Die Antwort auf die vorherige
Nachricht ging in derselben Sekunde raus, in der die erste Rückfrage kam — sie
wurde also nicht gesehen. Die Nachrichten tragen wechselnde Client-Kennungen,
was auf mehrere gleichzeitig offene Verbindungen hindeutet. Passt zum bekannten
Verhalten, dass zwei Fenster im selben Thread die Antworten des jeweils anderen
erst nach dem Neuladen sehen. **Vor einem Umbau nachstellen**, nicht auf Verdacht
beheben.

**Elf Threads mit null Nachrichten** — angelegt durch das Find-or-Reuse aus dem
Thread-Umbau. Sie stehen in der Seitenleiste und tragen nichts.

**Kein Fenster-Limit-Hinweis:** das Prompt-Fenster ist bei 150 Nachrichten hart
gekappt, ohne Verdichtung und ohne Meldung. Lange Programmier-Threads sind genau
der Fall, in dem das zuerst weh tut. `daily_summaries` existiert als Tabelle und
wird nirgends befüllt.

---

## Was daraus für die Reihenfolge folgt

Nicht als Auftrag, sondern als Ableitung aus dem Befund:

1. **Lesender Worker-Befehl.** Löst die Kaskade, den blinden Planungslauf und den
   fehlenden Mac-Shell-Zugang auf einmal. Größter Hebel.
2. **Fehlermeldungen, die den Grund nennen.** Sehr billig, sofort spürbar.
3. **`data_write`-Bug.** Klar umrissen, tritt zuverlässig auf.
4. **Autonomie an der Operation statt am Projekt.** Nimmt der Bedienung die Zähigkeit.
5. **`runs` am Job.** Sinnvoll, aber nachrangig — es löst keines der Probleme
   oben, sondern macht mehrstufige Arbeit an *einer* Aufgabe sauber abrechenbar.
