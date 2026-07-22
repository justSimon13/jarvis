# Git-Konventionen für JARVIS' Coding-Engine

Gilt für jeden Commit, den `services/coding_engine.py` selbst erstellt (`delegate_coding_task`,
`commit_and_push`) — egal ob im `j.a.r.v.i.s.`-Server-Repo selbst oder in einem Projekt unter
`config.PROJECTS_ROOT`. Nicht gedacht für Commits, die Simon oder Claude Code von Hand machen.

**Projekt-spezifische Version:** Legt ein Projekt eine eigene `GIT_CONVENTIONS.md` in seinem
Wurzelverzeichnis an, gilt DIESE statt der hier beschriebenen Default-Version für Coding-Tasks
in genau diesem Projekt (Details → `coding_engine._load_git_conventions()`).

---

## Commit-Message-Format

```
<typ>: <kurze Zusammenfassung, Deutsch, unter 72 Zeichen, Imperativ>

<Body: was geändert wurde und WARUM — nicht nur eine Wiederholung der
Subject-Zeile. 2-5 Sätze reichen meistens. Deutsch oder Englisch, gemischt
ist ok (wie im restlichen Repo).>
```

**Typen** (angelehnt an Conventional Commits, aber nicht strikt durchgesetzt):

| Typ | Wann |
|---|---|
| `feat` | Neue Funktionalität |
| `fix` | Bugfix |
| `docs` | Nur Dokumentation (README, ROADMAP, Kommentare) |
| `refactor` | Umbau ohne Verhaltensänderung |
| `chore` | Aufräumen, Abhängigkeiten, Konfiguration |
| `test` | Nur Tests |
| `style` | Formatierung, keine Logikänderung |

Kein Typ passt sauber? Dann `feat` oder `fix` als Default, nicht erzwingen.

**Nicht ins Commit:**
- Keine Emojis, außer explizit von Simon verlangt
- Keine Chat-Referenzen ("wie besprochen", "gemäß Simons Anfrage") — der Commit muss für sich
  stehen, auch ohne den Chat-Verlauf zu kennen
- Kein Boilerplate-Footer — die Autor-Identität (`JARVIS Coding-Engine <jarvis@localhost>`) steht
  bereits im Commit-Autor-Feld, muss nicht nochmal im Body wiederholt werden

## Branch-Naming

`jarvis/auto-<unix-timestamp>` — automatisch von `_run_task()` vergeben, nicht Teil dessen was
der Agent selbst beeinflusst.

## Wie das technisch funktioniert

Der Agent committet nicht selbst (`_build_system_prompt()` weist das explizit an) — die
aufrufende Umgebung (`_finalize_commit()`) macht das nach Abschluss des Tasks. Damit der
Commit trotzdem eine echte, zum tatsächlichen Diff passende Nachricht bekommt (nicht nur die
ursprüngliche Aufgabenbeschreibung gekürzt auf 72 Zeichen), bittet der System-Prompt den Agent,
seine Abschlussantwort mit einem klar abgegrenzten Block zu beenden:

```
---COMMIT---
<typ>: <Zusammenfassung>

<Body>
---END---
```

`_finalize_commit()` parst diesen Block heraus und nutzt ihn direkt als Commit-Message. Fehlt
der Block (Agent hat sich nicht daran gehalten, oder Task brach vorzeitig ab), fällt der Code
auf eine simple Heuristik zurück (Typ-Präfix per Keyword-Suche in der ursprünglichen Aufgabe,
Body = die Aufgabenbeschreibung selbst) — nie ein kompletter Fehlschlag, nur weniger genau.
