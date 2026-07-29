# Entwurfsdokumente — noch nicht umgesetzt

**Status: Konzeptphase, kein Bezug zum aktuell laufenden Code.** Diese drei Dateien
beschreiben eine größere Neuausrichtung von JARVIS (Datenmodell, API-Schnitt,
Personas, Gedächtnis-Redesign, Coding-Executor-Modell). Bewusst getrennt von den
aktiven Root-Docs (`CLAUDE.md`, `ROADMAP.md`, `ARCHITECTURE.md`, `PRODUCT.md`,
`CODE_REFERENCE.md`, `TOOLS.md`) abgelegt, damit nichts vermischt wird — die
Root-Docs beschreiben weiterhin den tatsächlichen IST-Stand.

Wird laut Simon noch weiter überarbeitet ("es wird sich wieder viel ändern") —
nicht als abgeschlossen behandeln.

## Dateien

- `JARVIS-Konzept-2026-07-28.md` — Leitbild-Ebene (Fortschreibung von
  `jarvis/konzept_grundsatz.md` in der Wissensdatenbank, dort Stand 2026-07-27)
- `JARVIS-Datenmodell-und-API.md` — Maschinenraum-Ebene: Tabellen, API-Endpunkte,
  Migrationsplan von den bestehenden Tabellen
- `CLAUDE.md` — Entwurf für die Arbeitsanweisung, die gelten SOLL, sobald die
  Migration beginnt. **Liegt bewusst hier und nicht im Repo-Root** — würde dort
  sofort als aktive Instruktion gelten, obwohl der beschriebene Zustand (`data_scope`
  Pflichtfeld, englische Bezeichner, `docs/`-Struktur) noch nirgends existiert.

## Bekannte Inkonsistenz (noch nicht aufgelöst)

Das `CLAUDE.md` referenziert `docs/JARVIS-Konzept.md` (ohne Datum) und
`docs/MIGRATION.md` — Zukunftsreferenzen auf Dateinamen, die es in dieser Form noch
nicht gibt (die tatsächliche Konzept-Datei trägt hier das Datum im Namen,
`MIGRATION.md` existiert noch gar nicht). Vermutlich so gedacht: bei echtem
Migrationsstart wandert der Inhalt nach `docs/` und verliert das Datum im Namen.
Nicht automatisch aufgelöst, sondern hier vermerkt, damit es nicht übersehen wird.
