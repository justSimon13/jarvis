# JARVIS — Arbeitsanweisung

## Was dieses Repo ist

JARVIS ist Simons persönlicher Assistent: ein kopfloser Kern mit mehreren Clients (Web-App/Tauri, später Speaker und Sensoren).

## Verbindliche Dokumente

Vor Struktur- oder Datenbankänderungen **immer zuerst lesen**:

- `docs/JARVIS-Datenmodell-und-API.md` — **verbindlich.** Tabellen, Spalten, Enum-Werte, Endpunkte. Nicht davon abweichen, ohne es vorher anzusprechen.
- `docs/JARVIS-Konzept.md` — das Warum. Bei Zweifelsfällen die Begründung dort nachlesen, statt zu raten.
- `docs/MIGRATION.md` — der Weg vom IST zum Ziel, inkl. Reihenfolge.

Weicht der bestehende Code vom Datenmodell ab, gilt das Datenmodell — aber der Umbau passiert nur in dem Schritt, der gerade beauftragt ist, nicht nebenbei.

## Harte Regeln

**Sprache:** Bezeichner (Tabellen, Spalten, Enums, Endpunkte, Funktionen, Variablen) auf Englisch, snake_case. Kommentare und Commit-Messages auf Deutsch.

**Namen, die nie vermischt werden:**
- `client` = Gerät (Mac, Server, Web-App)
- `customer` = Kunde

**`data_scope`** (`own` / `customer` / `employer`) ist Pflicht bei allem, was dauerhaft gespeichert wird. Neue Tabelle ohne diese Spalte = Fehler.

**Kein Client baut Prompts oder ruft die Anthropic-API auf.** Das passiert ausschließlich im Kern. Clients schicken Text und stellen dar.

**Rechenlogik gehört in den Kern**, nicht in Vue-Komponenten. Statistiken kommen fertig über einen Endpunkt.

**Prompt-Reihenfolge nicht ändern** (Caching hängt daran): Werkzeuge → Systemprompt/Persona → Facts/Indizes → Tagesübersicht → Nachrichten. Nie einen Zeitstempel weiter vorne einbauen.

## Vorgehen bei Umbauten

**Schrittweise, nicht auf einmal.** Neue Struktur neben die alte legen, migrieren, alte entfernen. Nach jedem Schritt muss das System lauffähig sein.

Ein Umbau ist erst fertig, wenn die alte Struktur weg ist — aber das ist ein eigener Schritt, kein Teil des ersten.

**Vor jedem Datenbank-Umbau:** prüfen, ob ein aktuelles Backup existiert. Wenn nicht, erst darauf hinweisen.

## Nicht ohne Rückfrage

- Migrationen, die Daten löschen oder überschreiben
- Änderungen am Datenmodell, die vom Dokument abweichen
- Neue Abhängigkeiten
- Alles, was nach außen wirkt (Mails, Deployments, Pushes auf main)

## Was hier nicht passiert

- Keine Worktrees. Direkt im Arbeitsverzeichnis, Branch pro Aufgabe.
- Kein `--dangerously-skip-permissions`.
- Keine `ANTHROPIC_API_KEY` in Umgebungsdateien — die Abrechnung läuft über das Abo.
