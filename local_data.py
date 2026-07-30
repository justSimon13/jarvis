from __future__ import annotations
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

_DB_PATH = Path.home() / ".jarvis" / "local_data.db"

_GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "yahoo.de", "gmx.de", "gmx.net", "gmx.at", "web.de",
    "icloud.com", "me.com", "mac.com", "protonmail.com", "proton.me",
}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """ALTER TABLE ... ADD COLUMN, idempotent (SQLite kennt kein IF NOT EXISTS dafür)."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _rename_table_if_exists(conn: sqlite3.Connection, old: str, new: str) -> None:
    old_exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (old,)).fetchone()
    new_exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (new,)).fetchone()
    if old_exists and not new_exists:
        conn.execute(f"ALTER TABLE {old} RENAME TO {new}")


def _rename_column_if_exists(conn: sqlite3.Connection, table: str, old: str, new: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if old in cols and new not in cols:
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")


def _get_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)

    # Migration von einem früheren Zwischenstand (notion_seiten/notion_page_id) —
    # rein umbenannt, keine Daten verändert. Vor den CREATE/ADD-Statements, damit
    # die danach als bereits vorhanden erkannt werden.
    _rename_table_if_exists(conn, "notion_seiten", "seiten")
    for table in ("todos", "projekte", "kontakte", "seiten"):
        _rename_column_if_exists(conn, table, "notion_page_id", "externe_id")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            status     TEXT DEFAULT 'Nicht begonnen',
            datum      TEXT,
            prioritaet TEXT,
            bereich    TEXT,
            aufwand    TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projekte (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            status       TEXT,
            beschreibung TEXT,
            typ          TEXT,
            created_at   TEXT,
            updated_at   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kontakte (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            email      TEXT,
            telefon    TEXT,
            tags       TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    # notizen = Direktinhalt der Wurzel-Seite selbst (ohne Unterseiten-Inhalte —
    # die stehen einzeln in der seiten-Tabelle, damit sie im FE als eigene Seite
    # mit Breadcrumbs navigierbar sind statt in einen Textblock gequetscht).
    # externe_id = id der Quelle (z.B. Notion-Seiten-ID), erlaubt künftigen Re-Sync.
    for table in ("todos", "projekte", "kontakte"):
        _ensure_column(conn, table, "notizen", "TEXT")
        _ensure_column(conn, table, "externe_id", "TEXT")
    # Geschätzter Auftragswert für die Finanzen-Übersicht (TrackingView.vue) —
    # Summe über alle nicht abgeschlossenen Projekte ergibt den "geschätzten
    # Gewinn", als Gegenstück zu den tatsächlichen tracking.py-Gewinn-Logs.
    _ensure_column(conn, "projekte", "geschaetzter_wert", "REAL")
    # Erwartetes Abschlussdatum (YYYY-MM-DD, seit 2026-07-27) — für den Gewinn-
    # Trend-Chart: bekannte Pipeline-Projekte werden im jeweiligen Monat als
    # erwarteter Zufluss eingebucht statt nur pauschal in die Gesamtschätzung.
    _ensure_column(conn, "projekte", "erwartetes_abschlussdatum", "TEXT")
    # Mac-Worker-Coding-Jobs (seit 2026-07-30, Migrationsschritt C aus
    # docs-draft/JARVIS-Konzept-2026-07-28.md vorgezogen): additiv, bestehende
    # Zeilen bleiben unberührt. 'path' muss zeichengenau mit einem Eintrag in
    # jarvis-web's localExec.js::PROJECT_ALLOWLIST übereinstimmen (der Server
    # ist keine vertrauenswürdige Quelle für Pfade). 'base_branch' ist eine
    # pragmatische Ergänzung über die Zielbild-Spalten hinaus (dort nur bei
    # 'jobs' gelistet, nicht bei 'projects') — ohne sie weiß coding_jobs.py
    # nicht, von welchem Branch abgezweigt wird. 'autonomy'/'data_scope'
    # werden vorerst nur gespeichert, nicht ausgewertet (siehe ROADMAP.md).
    for column in ("path", "repo", "base_branch", "client_id", "autonomy", "data_scope"):
        _ensure_column(conn, "projekte", column, "TEXT")
    # Externe Ticket-Quellen (z.B. GitHub Issues, siehe services/tickets.py):
    # zusätzliche, nullable Felder für todos — bestehende Zeilen bleiben
    # unberührt (ALTER TABLE ADD COLUMN mit NULL-Default).
    for column in ("source", "external_id", "repo", "body", "labels"):
        _ensure_column(conn, "todos", column, "TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seiten (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_typ      TEXT,      -- 'projekte'|'todos'|'kontakte', nur wenn eltern_seite_id NULL
            parent_id       INTEGER,   -- FK auf {parent_typ}.id, nur wenn eltern_seite_id NULL
            eltern_seite_id INTEGER,   -- FK auf seiten.id, für tiefer verschachtelte Unterseiten
            externe_id      TEXT,
            titel           TEXT NOT NULL,
            inhalt          TEXT,
            created_at      TEXT,
            updated_at      TEXT
        )
    """)
    # Rechnungen/Ausgaben (seit 2026-07-27) — Import aus SevDesk-CSV-Exports
    # (keine API-Anbindung, würde extra kosten). rechnungsnummer/belegnummer sind
    # SevDesks eigene, stabile IDs — UNIQUE + Upsert-Ziel beim (wiederholten)
    # Import, damit ein erneuter CSV-Export nie Duplikate erzeugt.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rechnungen (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rechnungsnummer TEXT NOT NULL UNIQUE,
            rechnungsdatum  TEXT,
            faellig_am      TEXT,
            bezahlt_am      TEXT,      -- NULL = noch nicht bezahlt
            betreff         TEXT,
            betrag_netto    REAL,
            betrag_brutto   REAL,
            offener_betrag  REAL,
            kunde           TEXT,      -- SevDesks Empfänger-Adresse (Kunde) — bestimmt NICHT
                                        -- zuverlässig das Projekt, ein Kunde kann mehrere
                                        -- Projekte haben (siehe projekt_id)
            projekt_id      INTEGER,   -- FK auf projekte.id, manuell/von JARVIS gesetzt
            notizen         TEXT,
            gesperrt        INTEGER DEFAULT 0,  -- 1 = Import überschreibt diese Zeile nicht (siehe upsert_rechnung)
            created_at      TEXT,
            updated_at      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ausgaben (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            belegnummer     TEXT NOT NULL UNIQUE,
            status          TEXT,      -- z.B. 'bezahlt', 'Entwurf'
            lieferant       TEXT,
            kategorie       TEXT,
            beschreibung    TEXT,
            datum           TEXT,
            faellig_am      TEXT,
            bezahlt_am      TEXT,      -- NULL = noch nicht bezahlt
            offener_betrag  REAL,
            betrag          REAL,
            gesperrt        INTEGER DEFAULT 0,  -- 1 = Import überschreibt diese Zeile nicht (siehe upsert_ausgabe)
            created_at      TEXT,
            updated_at      TEXT
        )
    """)
    # Für Installs, die die beiden Tabellen schon ohne 'gesperrt' angelegt hatten
    # (ALTER TABLE ADD COLUMN, idempotent — siehe _ensure_column).
    _ensure_column(conn, "rechnungen", "gesperrt", "INTEGER DEFAULT 0")
    _ensure_column(conn, "ausgaben", "gesperrt", "INTEGER DEFAULT 0")
    # Einmalige Reparatur (seit 2026-07-27): update_todo() schrieb bisher ein leeres
    # Datumsfeld als '' statt NULL (Frontend füllt <input type="date"> beim Bearbeiten
    # mit '' statt None vor, das ging beim Speichern unverändert durch). list_todos()s
    # Filter 'datum IS NULL OR datum >= cutoff' greift bei '' auf keinen der beiden
    # Zweige — jedes so betroffene Todo verschwand seitdem dauerhaft aus der Liste,
    # obwohl es in der DB weiter existierte. Idempotent (WHERE datum='' matcht nach
    # dem ersten Lauf nichts mehr), _normalize_fields() verhindert das Nachwachsen.
    conn.execute("UPDATE todos SET datum = NULL WHERE datum = ''")
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now().isoformat()


def _normalize_fields(fields: dict) -> dict:
    """Leere Strings -> None. Edit-Formulare im Frontend (z.B. TodoItem.vue) befüllen ein
    <input type="date"> für ein bisher leeres Feld mit '' statt None und schicken das beim
    Speichern unverändert mit — ohne diese Normalisierung landet dann ein echter Leerstring
    in der Spalte statt NULL. Bei 'datum' war das kein kosmetisches Problem: list_todos()s
    Filter 'datum IS NULL OR datum >= cutoff' greift bei '' auf KEINEN der beiden Zweige
    (Leerstring ist weder NULL noch >= irgendeinem echten Datum, da lexikographisch kleiner
    als jeder YYYY-MM-DD-String) — das Todo verschwand dadurch nach jedem Speichern dauerhaft
    aus der Liste, obwohl es in der DB unverändert existierte (wirkte wie Datenverlust)."""
    return {k: (None if v == "" else v) for k, v in fields.items()}


# ── Todos ─────────────────────────────────────────────────────────────────────

def list_todos(tage_zurueck: int = 7, max_results: int = 20) -> list[dict]:
    cutoff = (date.today() - timedelta(days=tage_zurueck)).isoformat()
    conn = _get_db()
    rows = conn.execute(
        """SELECT id, name, status, datum, prioritaet, bereich, aufwand, notizen, externe_id,
                  source, external_id, repo, body, labels FROM todos
           WHERE (status IS NULL OR status NOT IN ('Erledigt', 'Archiviert'))
             AND (datum IS NULL OR datum >= ?)
           ORDER BY (datum IS NULL) ASC, datum ASC
           LIMIT ?""",
        (cutoff, max_results)
    ).fetchall()
    conn.close()
    cols = ["id", "name", "status", "datum", "prioritaet", "bereich", "aufwand", "notizen", "externe_id",
            "source", "external_id", "repo", "body", "labels"]
    return [dict(zip(cols, r)) for r in rows]


def list_tickets(status_filter: str | None = None) -> list[dict]:
    """Todos mit source='github' (services/tickets.py) — dünner Filter auf
    dieselben Spalten wie list_todos(), aber ohne dessen Erledigt/Archiviert-
    Ausschluss und Datums-Cutoff, damit auch geschlossene Tickets sichtbar
    bleiben."""
    conn = _get_db()
    query = """SELECT id, name, status, datum, prioritaet, bereich, aufwand, notizen, externe_id,
                      source, external_id, repo, body, labels FROM todos
               WHERE source = 'github'"""
    params: tuple = ()
    if status_filter:
        query += " AND status = ?"
        params = (status_filter,)
    query += """ ORDER BY CASE prioritaet WHEN 'Hoch' THEN 0 WHEN 'Mittel' THEN 1
                 WHEN 'Niedrig' THEN 2 ELSE 3 END, updated_at DESC"""
    rows = conn.execute(query, params).fetchall()
    conn.close()
    cols = ["id", "name", "status", "datum", "prioritaet", "bereich", "aufwand", "notizen", "externe_id",
            "source", "external_id", "repo", "body", "labels"]
    return [dict(zip(cols, r)) for r in rows]


def add_todo(name: str, status: str | None = None, datum: str | None = None,
             prioritaet: str | None = None, bereich: str | None = None,
             aufwand: str | None = None, source: str | None = None,
             external_id: str | None = None, repo: str | None = None,
             body: str | None = None, labels: str | None = None) -> int:
    conn = _get_db()
    now = _now()
    cur = conn.execute(
        """INSERT INTO todos (name, status, datum, prioritaet, bereich, aufwand, created_at, updated_at,
                               source, external_id, repo, body, labels)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, status or "Nicht begonnen", datum, prioritaet, bereich, aufwand, now, now,
         source, external_id, repo, body, labels)
    )
    conn.commit()
    todo_id = cur.lastrowid
    conn.close()
    return todo_id


def update_todo(todo_id: int, **fields) -> None:
    fields = _normalize_fields(fields)
    allowed = {"name", "status", "datum", "prioritaet", "bereich", "aufwand", "notizen",
               "source", "external_id", "repo", "body", "labels"}
    sets = [f"{k} = ?" for k in fields if k in allowed]
    values = [v for k, v in fields.items() if k in allowed]
    if not sets:
        return
    sets.append("updated_at = ?")
    values.append(_now())
    values.append(todo_id)
    conn = _get_db()
    conn.execute(f"UPDATE todos SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    conn.close()


def complete_todo(todo_id: int) -> None:
    update_todo(todo_id, status="Erledigt")


def delete_todo(todo_id: int) -> None:
    conn = _get_db()
    conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    conn.commit()
    conn.close()


# ── Projekte ──────────────────────────────────────────────────────────────────

def list_projekte(status_filter: str | list[str] | None = None) -> list[dict]:
    """status_filter: None = alle, str = exakter Status, list = einer von mehreren
    (z.B. Kontext-Prompt-Aufbau: aktive Projekte über mehrere Status hinweg)."""
    conn = _get_db()
    if status_filter is None:
        rows = conn.execute(
            "SELECT id, name, status, beschreibung, typ, notizen, externe_id, geschaetzter_wert, erwartetes_abschlussdatum FROM projekte ORDER BY id"
        ).fetchall()
    elif isinstance(status_filter, str):
        rows = conn.execute(
            "SELECT id, name, status, beschreibung, typ, notizen, externe_id, geschaetzter_wert, erwartetes_abschlussdatum FROM projekte WHERE status = ? ORDER BY id",
            (status_filter,)
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(status_filter))
        rows = conn.execute(
            f"SELECT id, name, status, beschreibung, typ, notizen, externe_id, geschaetzter_wert, erwartetes_abschlussdatum FROM projekte WHERE status IN ({placeholders}) ORDER BY id",
            list(status_filter)
        ).fetchall()
    conn.close()
    cols = ["id", "name", "status", "beschreibung", "typ", "notizen", "externe_id", "geschaetzter_wert", "erwartetes_abschlussdatum"]
    results = [dict(zip(cols, r)) for r in rows]
    # Gleicher Hinweis wie in query() (LLM-Pfad) — sonst hat das Frontend keine
    # Möglichkeit zu wissen ob es Unterseiten gibt, ohne pro Projekt einen
    # extra Request zu machen. Vorher stützte sich ProjektItem.vue fälschlich
    # auf externe_id (nur bei Notion-migrierten Zeilen gesetzt) um zu
    # entscheiden ob der Name klickbar ist — seit create_seite() können auch
    # NEUE, nie-migrierte Projekte Unterseiten bekommen (2026-07-23: 'Warum
    # kann ich mich nicht [...] rein klicken?').
    for r in results:
        unterseiten = list_seiten("projekte", r["id"])
        if unterseiten:
            r["unterseiten"] = unterseiten
    return results


def list_coding_projects() -> list[dict]:
    """Für services/coding_jobs.py::_resolve_project() — alle Projekte, die für
    Mac-Worker-Coding-Jobs freigegeben sind (path gesetzt), über ALLE Worker-
    Rollen hinweg (client_id wird mit zurückgegeben, nicht mehr gefiltert —
    seit 2026-07-31 bestimmt coding_jobs.py anhand von client_id, welcher
    Worker den Job bekommt, dafür muss die Rolle hier sichtbar sein). Eigene,
    gezielte Abfrage statt der generischen query(): die hat kein WHERE für
    "hat einen Pfad". Absichtlich NICHT Teil von list_projekte()
    (Kontext-Prompt-Aufbau) — diese Felder fahren nicht bei jedem Gespräch
    mit, nur wenn tatsächlich ein Coding-Job startet."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, name, path, repo, base_branch, client_id FROM projekte WHERE path IS NOT NULL ORDER BY id",
    ).fetchall()
    conn.close()
    cols = ["id", "name", "path", "repo", "base_branch", "client_id"]
    return [dict(zip(cols, r)) for r in rows]


def add_projekt(name: str, status: str | None = None, beschreibung: str | None = None,
                 typ: str | None = None, geschaetzter_wert: float | None = None,
                 erwartetes_abschlussdatum: str | None = None,
                 path: str | None = None, repo: str | None = None, base_branch: str | None = None,
                 client_id: str | None = None, autonomy: str | None = None,
                 data_scope: str | None = None) -> int:
    conn = _get_db()
    now = _now()
    cur = conn.execute(
        """INSERT INTO projekte (name, status, beschreibung, typ, geschaetzter_wert,
                                  erwartetes_abschlussdatum, path, repo, base_branch, client_id,
                                  autonomy, data_scope, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, status, beschreibung, typ, geschaetzter_wert, erwartetes_abschlussdatum,
         path, repo, base_branch, client_id, autonomy, data_scope, now, now)
    )
    conn.commit()
    projekt_id = cur.lastrowid
    conn.close()
    return projekt_id


def update_projekt(projekt_id: int, **fields) -> None:
    fields = _normalize_fields(fields)
    allowed = {"name", "status", "beschreibung", "typ", "notizen", "geschaetzter_wert", "erwartetes_abschlussdatum",
               "path", "repo", "base_branch", "client_id", "autonomy", "data_scope"}
    sets = [f"{k} = ?" for k in fields if k in allowed]
    values = [v for k, v in fields.items() if k in allowed]
    if not sets:
        return
    sets.append("updated_at = ?")
    values.append(_now())
    values.append(projekt_id)
    conn = _get_db()
    conn.execute(f"UPDATE projekte SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_projekt(projekt_id: int) -> None:
    conn = _get_db()
    conn.execute("DELETE FROM projekte WHERE id = ?", (projekt_id,))
    conn.commit()
    conn.close()


# ── Kontakte ──────────────────────────────────────────────────────────────────

def list_kontakte(tag_filter: str | None = None) -> list[dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, name, email, telefon, tags, notizen, externe_id FROM kontakte ORDER BY id"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        tags = json.loads(r[4]) if r[4] else []
        if tag_filter and tag_filter not in tags:
            continue
        entry = {
            "id": r[0], "name": r[1], "email": r[2], "telefon": r[3],
            "tags": tags, "notizen": r[5], "externe_id": r[6],
        }
        unterseiten = list_seiten("kontakte", r[0])
        if unterseiten:
            entry["unterseiten"] = unterseiten
        result.append(entry)
    return result


def add_kontakt(name: str, email: str | None = None, telefon: str | None = None,
                 tags: list[str] | None = None) -> int:
    conn = _get_db()
    now = _now()
    cur = conn.execute(
        "INSERT INTO kontakte (name, email, telefon, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, email, telefon, json.dumps(tags or [], ensure_ascii=False), now, now)
    )
    conn.commit()
    kontakt_id = cur.lastrowid
    conn.close()
    return kontakt_id


def update_kontakt(kontakt_id: int, **fields) -> None:
    fields = _normalize_fields(fields)
    allowed = {"name", "email", "telefon", "tags", "notizen"}
    sets, values = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "tags":
            v = json.dumps(v or [], ensure_ascii=False)
        sets.append(f"{k} = ?")
        values.append(v)
    if not sets:
        return
    sets.append("updated_at = ?")
    values.append(_now())
    values.append(kontakt_id)
    conn = _get_db()
    conn.execute(f"UPDATE kontakte SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_kontakt(kontakt_id: int) -> None:
    conn = _get_db()
    conn.execute("DELETE FROM kontakte WHERE id = ?", (kontakt_id,))
    conn.commit()
    conn.close()


# ── Rechnungen ────────────────────────────────────────────────────────────────

_RECHNUNGEN_COLS = ["id", "rechnungsnummer", "rechnungsdatum", "faellig_am", "bezahlt_am", "betreff",
                    "betrag_netto", "betrag_brutto", "offener_betrag", "kunde", "projekt_id", "notizen",
                    "gesperrt"]


def list_rechnungen(projekt_id: int | None = None) -> list[dict]:
    conn = _get_db()
    if projekt_id is None:
        rows = conn.execute(f"SELECT {', '.join(_RECHNUNGEN_COLS)} FROM rechnungen ORDER BY rechnungsdatum DESC").fetchall()
    else:
        rows = conn.execute(
            f"SELECT {', '.join(_RECHNUNGEN_COLS)} FROM rechnungen WHERE projekt_id = ? ORDER BY rechnungsdatum DESC",
            (projekt_id,)
        ).fetchall()
    conn.close()
    return [dict(zip(_RECHNUNGEN_COLS, r)) for r in rows]


def add_rechnung(rechnungsnummer: str, rechnungsdatum: str | None = None, faellig_am: str | None = None,
                  bezahlt_am: str | None = None, betreff: str | None = None, betrag_netto: float | None = None,
                  betrag_brutto: float | None = None, offener_betrag: float | None = None,
                  kunde: str | None = None, projekt_id: int | None = None, notizen: str | None = None,
                  gesperrt: bool | int | None = None, conn: sqlite3.Connection | None = None) -> int:
    own_conn = conn is None
    if own_conn:
        conn = _get_db()
    now = _now()
    cur = conn.execute(
        """INSERT INTO rechnungen (rechnungsnummer, rechnungsdatum, faellig_am, bezahlt_am, betreff,
                                    betrag_netto, betrag_brutto, offener_betrag, kunde, projekt_id, notizen,
                                    gesperrt, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rechnungsnummer, rechnungsdatum, faellig_am, bezahlt_am, betreff, betrag_netto, betrag_brutto,
         offener_betrag, kunde, projekt_id, notizen, int(bool(gesperrt)), now, now)
    )
    rechnung_id = cur.lastrowid
    if own_conn:
        conn.commit()
        conn.close()
    return rechnung_id


def update_rechnung(rechnung_id: int, conn: sqlite3.Connection | None = None, **fields) -> None:
    fields = _normalize_fields(fields)
    allowed = {"rechnungsnummer", "rechnungsdatum", "faellig_am", "bezahlt_am", "betreff", "betrag_netto",
               "betrag_brutto", "offener_betrag", "kunde", "projekt_id", "notizen", "gesperrt"}
    if "gesperrt" in fields:
        fields["gesperrt"] = int(bool(fields["gesperrt"]))
    sets = [f"{k} = ?" for k in fields if k in allowed]
    values = [v for k, v in fields.items() if k in allowed]
    if not sets:
        return
    sets.append("updated_at = ?")
    values.append(_now())
    values.append(rechnung_id)
    own_conn = conn is None
    if own_conn:
        conn = _get_db()
    conn.execute(f"UPDATE rechnungen SET {', '.join(sets)} WHERE id = ?", values)
    if own_conn:
        conn.commit()
        conn.close()


def delete_rechnung(rechnung_id: int) -> None:
    conn = _get_db()
    conn.execute("DELETE FROM rechnungen WHERE id = ?", (rechnung_id,))
    conn.commit()
    conn.close()


def upsert_rechnung(rechnungsnummer: str, conn: sqlite3.Connection | None = None, **fields) -> int | None:
    """Für den CSV-Import: legt neu an oder aktualisiert per rechnungsnummer (SevDesks
    stabile ID) — ein wiederholter Export/Import derselben Rechnung erzeugt nie ein
    Duplikat. projekt_id wird bei einem Update NIE überschrieben, wenn bereits gesetzt
    (manuelle/JARVIS-Zuordnung soll ein erneuter Import nicht wieder wegwischen).
    Ist die bestehende Zeile 'gesperrt' (manuell markiert, z.B. weil Simon sie von Hand
    korrigiert hat oder sie unabhängig von SevDesk gepflegt wird), wird sie vom Import
    komplett übersprungen — gibt dann None zurück statt der id, damit der Aufrufer das
    von einem echten Update/Neuanlage unterscheiden kann.
    conn: optionale bestehende Connection (siehe upsert_rechnungen_bulk) — ohne diese
    öffnet/committed/schließt die Funktion wie gehabt selbst eine eigene."""
    own_conn = conn is None
    if own_conn:
        conn = _get_db()
    row = conn.execute(
        "SELECT id, projekt_id, gesperrt FROM rechnungen WHERE rechnungsnummer = ?", (rechnungsnummer,)
    ).fetchone()
    if row is None:
        result = add_rechnung(rechnungsnummer, conn=conn, **fields)
    else:
        existing_id, existing_projekt_id, gesperrt = row
        if gesperrt:
            result = None
        else:
            fields.pop("projekt_id", None) if existing_projekt_id is not None else None
            update_rechnung(existing_id, conn=conn, **fields)
            result = existing_id
    if own_conn:
        conn.commit()
        conn.close()
    return result


def upsert_rechnungen_bulk(entries: list[dict]) -> dict:
    """Für den CSV-Import (finanzen_import.py): EINE Connection + EIN Commit für den
    ganzen Batch statt einer Connection pro Zeile. upsert_rechnung() pro Zeile aufrufen
    hätte bei >100 Zeilen zu spürbarer Latenz geführt (jede Zeile öffnet+migriert+
    committed eine eigene SQLite-Connection inkl. fsync) — live beobachtet: ein
    174-Zeilen-Ausgaben-Import überschritt dadurch das 10s-Frontend-Timeout und schlug
    scheinbar grundlos fehl. Gibt {created, updated, skipped_locked} zurück."""
    conn = _get_db()
    existing = {r[0] for r in conn.execute("SELECT rechnungsnummer FROM rechnungen").fetchall()}
    created = updated = skipped_locked = 0
    for entry in entries:
        nummer = entry["rechnungsnummer"]
        is_new = nummer not in existing
        fields = {k: v for k, v in entry.items() if k != "rechnungsnummer"}
        result = upsert_rechnung(nummer, conn=conn, **fields)
        if result is None:
            skipped_locked += 1
        elif is_new:
            created += 1
        else:
            updated += 1
    conn.commit()
    conn.close()
    return {"created": created, "updated": updated, "skipped_locked": skipped_locked}


# ── Ausgaben ──────────────────────────────────────────────────────────────────

_AUSGABEN_COLS = ["id", "belegnummer", "status", "lieferant", "kategorie", "beschreibung",
                  "datum", "faellig_am", "bezahlt_am", "offener_betrag", "betrag", "gesperrt"]


def list_ausgaben() -> list[dict]:
    conn = _get_db()
    rows = conn.execute(f"SELECT {', '.join(_AUSGABEN_COLS)} FROM ausgaben ORDER BY datum DESC").fetchall()
    conn.close()
    return [dict(zip(_AUSGABEN_COLS, r)) for r in rows]


def add_ausgabe(belegnummer: str, status: str | None = None, lieferant: str | None = None,
                kategorie: str | None = None, beschreibung: str | None = None, datum: str | None = None,
                faellig_am: str | None = None, bezahlt_am: str | None = None,
                offener_betrag: float | None = None, betrag: float | None = None,
                gesperrt: bool | int | None = None, conn: sqlite3.Connection | None = None) -> int:
    own_conn = conn is None
    if own_conn:
        conn = _get_db()
    now = _now()
    cur = conn.execute(
        """INSERT INTO ausgaben (belegnummer, status, lieferant, kategorie, beschreibung, datum,
                                  faellig_am, bezahlt_am, offener_betrag, betrag, gesperrt, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (belegnummer, status, lieferant, kategorie, beschreibung, datum, faellig_am, bezahlt_am,
         offener_betrag, betrag, int(bool(gesperrt)), now, now)
    )
    ausgabe_id = cur.lastrowid
    if own_conn:
        conn.commit()
        conn.close()
    return ausgabe_id


def update_ausgabe(ausgabe_id: int, conn: sqlite3.Connection | None = None, **fields) -> None:
    fields = _normalize_fields(fields)
    allowed = {"belegnummer", "status", "lieferant", "kategorie", "beschreibung", "datum",
               "faellig_am", "bezahlt_am", "offener_betrag", "betrag", "gesperrt"}
    if "gesperrt" in fields:
        fields["gesperrt"] = int(bool(fields["gesperrt"]))
    sets = [f"{k} = ?" for k in fields if k in allowed]
    values = [v for k, v in fields.items() if k in allowed]
    if not sets:
        return
    sets.append("updated_at = ?")
    values.append(_now())
    values.append(ausgabe_id)
    own_conn = conn is None
    if own_conn:
        conn = _get_db()
    conn.execute(f"UPDATE ausgaben SET {', '.join(sets)} WHERE id = ?", values)
    if own_conn:
        conn.commit()
        conn.close()


def delete_ausgabe(ausgabe_id: int) -> None:
    conn = _get_db()
    conn.execute("DELETE FROM ausgaben WHERE id = ?", (ausgabe_id,))
    conn.commit()
    conn.close()


def upsert_ausgabe(belegnummer: str, conn: sqlite3.Connection | None = None, **fields) -> int | None:
    """Für den CSV-Import: legt neu an oder aktualisiert per belegnummer (SevDesks
    stabile ID) — ein wiederholter Export/Import derselben Ausgabe erzeugt nie ein
    Duplikat. Ist die bestehende Zeile 'gesperrt', wird sie übersprungen — gibt dann
    None zurück statt der id (siehe upsert_rechnung, gleiches Prinzip).
    conn: optionale bestehende Connection (siehe upsert_ausgaben_bulk)."""
    own_conn = conn is None
    if own_conn:
        conn = _get_db()
    row = conn.execute("SELECT id, gesperrt FROM ausgaben WHERE belegnummer = ?", (belegnummer,)).fetchone()
    if row is None:
        result = add_ausgabe(belegnummer, conn=conn, **fields)
    else:
        existing_id, gesperrt = row
        if gesperrt:
            result = None
        else:
            update_ausgabe(existing_id, conn=conn, **fields)
            result = existing_id
    if own_conn:
        conn.commit()
        conn.close()
    return result


def upsert_ausgaben_bulk(entries: list[dict]) -> dict:
    """Wie upsert_rechnungen_bulk() — EINE Connection/EIN Commit für den ganzen
    CSV-Import-Batch statt einer pro Zeile. Gibt {created, updated, skipped_locked}
    zurück."""
    conn = _get_db()
    existing = {r[0] for r in conn.execute("SELECT belegnummer FROM ausgaben").fetchall()}
    created = updated = skipped_locked = 0
    for entry in entries:
        belegnummer = entry["belegnummer"]
        is_new = belegnummer not in existing
        fields = {k: v for k, v in entry.items() if k != "belegnummer"}
        result = upsert_ausgabe(belegnummer, conn=conn, **fields)
        if result is None:
            skipped_locked += 1
        elif is_new:
            created += 1
        else:
            updated += 1
    conn.commit()
    conn.close()
    return {"created": created, "updated": updated, "skipped_locked": skipped_locked}


# ── Generischer Dispatch für die LLM-Tools (data_query/write/update/delete) ───
# Feldnamen sind die lokalen (name/status/datum/prioritaet/...).

_QUERY_META = {
    "todos": {
        "cols": ["id", "name", "status", "datum", "prioritaet", "bereich", "aufwand", "notizen",
                 "source", "external_id", "repo", "body", "labels"],
        "default_limit": 10, "search_col": "name", "status_col": "status", "unterseiten": True,
    },
    "projekte": {
        "cols": ["id", "name", "status", "beschreibung", "typ", "notizen", "geschaetzter_wert", "erwartetes_abschlussdatum",
                 "path", "repo", "base_branch", "client_id", "autonomy", "data_scope"],
        "default_limit": 200, "search_col": "name", "status_col": "status", "unterseiten": True,
    },
    "rechnungen": {
        "cols": _RECHNUNGEN_COLS, "default_limit": 200, "search_col": "betreff", "status_col": None, "unterseiten": False,
    },
    "ausgaben": {
        "cols": _AUSGABEN_COLS, "default_limit": 200, "search_col": "beschreibung", "status_col": "status", "unterseiten": False,
    },
}


def query(database: str, search: str | None = None, status: str | None = None, limit: int | None = None) -> list[dict]:
    """limit=None nutzt einen pro Datenbank sinnvollen Default — Todos können über Jahre auf
    hunderte anwachsen (10 ist da ein bewusster Kosten-Filter), Projekte/Rechnungen/Ausgaben sind
    dagegen kleine, begrenzte Listen, ein Default von 10 hätte dort früher stillschweigend
    ältere/weitere Einträge verschluckt, ohne dass das LLM das je bemerkt hätte
    (Simon: 'Ich glaube Jarvis hat keine Möglichkeit alle Projekte zu ziehen')."""
    meta = _QUERY_META.get(database)
    if meta is None:
        raise ValueError(f"Unbekannte Datenbank: {database}. Verfügbar: {', '.join(_QUERY_META)}")
    cols, table = meta["cols"], database
    if limit is None:
        limit = meta["default_limit"]

    sql = f"SELECT {', '.join(cols)} FROM {table} WHERE 1=1"
    params: list = []
    if search:
        sql += f" AND {meta['search_col']} LIKE ?"
        params.append(f"%{search}%")
    if status and meta["status_col"]:
        sql += f" AND {meta['status_col']} = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    conn = _get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    results = [dict(zip(cols, r)) for r in rows]

    # Lazy-Load-Hinweis: nur Titel+id der Unterseiten, kein Inhalt — Volltext
    # erst über read_seite() bei Bedarf nachladen (Kosten-Rücksicht). Rechnungen/
    # Ausgaben unterstützen keine Unterseiten, kein Sinn in der Extra-Abfrage.
    if meta["unterseiten"]:
        for r in results:
            unterseiten = list_seiten(table, r["id"])
            if unterseiten:
                r["unterseiten"] = unterseiten
    return results


def write(database: str, properties: dict) -> int:
    properties = _normalize_fields(properties)
    if database == "todos":
        return add_todo(**{k: v for k, v in properties.items()
                            if k in {"name", "status", "datum", "prioritaet", "bereich", "aufwand",
                                     "source", "external_id", "repo", "body", "labels"}})
    if database == "projekte":
        return add_projekt(**{k: v for k, v in properties.items()
                               if k in {"name", "status", "beschreibung", "typ", "geschaetzter_wert", "erwartetes_abschlussdatum"}})
    if database == "rechnungen":
        return add_rechnung(**{k: v for k, v in properties.items() if k in set(_RECHNUNGEN_COLS) - {"id"}})
    if database == "ausgaben":
        return add_ausgabe(**{k: v for k, v in properties.items() if k in set(_AUSGABEN_COLS) - {"id"}})
    raise ValueError(f"Unbekannte Datenbank: {database}. Verfügbar: {', '.join(_QUERY_META)}")


def update(item_id: int, database: str, properties: dict) -> None:
    if database == "todos":
        update_todo(item_id, **properties)
    elif database == "projekte":
        update_projekt(item_id, **properties)
    elif database == "rechnungen":
        update_rechnung(item_id, **properties)
    elif database == "ausgaben":
        update_ausgabe(item_id, **properties)
    else:
        raise ValueError(f"Unbekannte Datenbank: {database}. Verfügbar: {', '.join(_QUERY_META)}")


def delete(item_id: int, database: str) -> None:
    if database == "todos":
        delete_todo(item_id)
    elif database == "projekte":
        delete_projekt(item_id)
    elif database == "rechnungen":
        delete_rechnung(item_id)
    elif database == "ausgaben":
        delete_ausgabe(item_id)
    else:
        raise ValueError(f"Unbekannte Datenbank: {database}. Verfügbar: {', '.join(_QUERY_META)}")


def sync_vip_emails() -> list[str]:
    """Domains/Adressen von Kontakten mit Tag 'Kunde' — für VIP-Email-Erkennung."""
    vip_patterns = []
    for k in list_kontakte(tag_filter="Kunde"):
        addr = (k.get("email") or "").strip().lower()
        if addr:
            domain = addr.split("@")[-1] if "@" in addr else addr
            vip_patterns.append(addr if domain in _GENERIC_EMAIL_DOMAINS else domain)
    return vip_patterns


# ── Einmal-Migration von Notion (Altsystem, abgelöst) ───────────────────────────
# DB-IDs waren zuvor als Fallback-Defaults in config.py hardcodiert (nie in .env),
# hier 1:1 übernommen. Rein lesender Export — die Notion-DBs selbst bleiben
# unverändert. Konzepte werden bewusst NICHT migriert (siehe Plan).

_QUELLE_TODOS_DB_ID = "10ab63fa-fc26-80f5-9865-cf57555d8002"
_QUELLE_PROJEKTE_DB_ID = "194b63fa-fc26-80d1-9832-dceb4301afd3"
_QUELLE_KONTAKTE_DB_ID = "1a4b63fa-fc26-808c-ad83-e4973e38f570"


def _quelle_paginate(client, db_id: str):
    cursor = None
    while True:
        kwargs = {"database_id": db_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.databases.query(**kwargs)
        yield from response.get("results", [])
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")


def _quelle_prop(props: dict, key: str, prop_type: str):
    raw = props.get(key, {})
    try:
        if prop_type == "title":
            items = raw.get("title", [])
            return items[0]["plain_text"] if items else None
        if prop_type == "status":
            s = raw.get("status")
            return s["name"] if s else None
        if prop_type == "select":
            s = raw.get("select")
            return s["name"] if s else None
        if prop_type == "date":
            d = raw.get("date")
            return d["start"] if d else None
        if prop_type in ("rich_text", "text"):
            items = raw.get("rich_text", [])
            return items[0]["plain_text"] if items else None
        if prop_type == "multi_select":
            return [o["name"] for o in raw.get("multi_select", [])]
    except (KeyError, IndexError, TypeError):
        return None
    return None


def migrate_stammdaten() -> dict:
    """Einmalige Migration: kopiert Todos/Projekte/Kontakte aus der alten Notion-
    Anbindung in die lokalen SQLite-Tabellen. Rein additiv/lesend gegenüber der
    Quelle — nichts wird dort verändert oder gelöscht. Bricht pro Tabelle ab und
    warnt, falls dort schon Zeilen existieren (verhindert Duplikate bei
    versehentlichem zweiten Lauf). Braucht NOTION_API_KEY in der Umgebung (z.B.
    via config.py's load_dotenv()) und das Paket 'notion-client'. Gibt
    {"todos": n, "projekte": n, "kontakte": n} zurück (n=-1 bedeutet: übersprungen,
    Tabelle war nicht leer)."""
    import os
    from notion_client import Client as NotionClient

    api_key = os.getenv("NOTION_API_KEY")
    if not api_key:
        raise RuntimeError("NOTION_API_KEY nicht gesetzt — Migration abgebrochen.")
    client = NotionClient(auth=api_key)

    counts = {}

    conn = _get_db()
    existing_todos = conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0]
    conn.close()
    if existing_todos:
        print(f"[migrate] todos: {existing_todos} Zeilen bereits vorhanden — übersprungen.", flush=True)
        counts["todos"] = -1
    else:
        n = 0
        for page in _quelle_paginate(client, _QUELLE_TODOS_DB_ID):
            props = page.get("properties", {})
            name = _quelle_prop(props, "Name", "title")
            if not name:
                continue
            add_todo(
                name=name,
                status=_quelle_prop(props, "Status", "status"),
                datum=_quelle_prop(props, "Datum", "date"),
                prioritaet=_quelle_prop(props, "Priorität", "select"),
                bereich=_quelle_prop(props, "Bereich", "select"),
                aufwand=_quelle_prop(props, "Aufwand", "select"),
            )
            n += 1
        print(f"[migrate] todos: {n} Zeilen migriert.", flush=True)
        counts["todos"] = n

    conn = _get_db()
    existing_projekte = conn.execute("SELECT COUNT(*) FROM projekte").fetchone()[0]
    conn.close()
    if existing_projekte:
        print(f"[migrate] projekte: {existing_projekte} Zeilen bereits vorhanden — übersprungen.", flush=True)
        counts["projekte"] = -1
    else:
        n = 0
        for page in _quelle_paginate(client, _QUELLE_PROJEKTE_DB_ID):
            props = page.get("properties", {})
            name = _quelle_prop(props, "Projekt", "title")
            if not name:
                continue
            add_projekt(
                name=name,
                status=_quelle_prop(props, "Status", "select"),
                beschreibung=_quelle_prop(props, "Beschreibung", "rich_text"),
                typ=_quelle_prop(props, "Typ", "select"),
            )
            n += 1
        print(f"[migrate] projekte: {n} Zeilen migriert.", flush=True)
        counts["projekte"] = n

    conn = _get_db()
    existing_kontakte = conn.execute("SELECT COUNT(*) FROM kontakte").fetchone()[0]
    conn.close()
    if existing_kontakte:
        print(f"[migrate] kontakte: {existing_kontakte} Zeilen bereits vorhanden — übersprungen.", flush=True)
        counts["kontakte"] = -1
    else:
        n = 0
        for page in _quelle_paginate(client, _QUELLE_KONTAKTE_DB_ID):
            props = page.get("properties", {})
            name = _quelle_prop(props, "Name", "title")
            if not name:
                continue
            add_kontakt(
                name=name,
                email=_quelle_prop(props, "Email", "text"),
                telefon=_quelle_prop(props, "Tel. Nummer", "text"),
                tags=_quelle_prop(props, "Mehrfachauswahl", "multi_select") or [],
            )
            n += 1
        print(f"[migrate] kontakte: {n} Zeilen migriert.", flush=True)
        counts["kontakte"] = n

    return counts


def _block_to_text(block: dict) -> str:
    t = block.get("type", "")
    data = block.get(t, {})
    rt = data.get("rich_text", [])
    text = "".join(x.get("plain_text", "") for x in rt)
    if not text:
        return ""
    if t == "heading_1":
        return f"# {text}"
    if t == "heading_2":
        return f"## {text}"
    if t == "heading_3":
        return f"### {text}"
    if t == "bulleted_list_item":
        return f"- {text}"
    if t == "numbered_list_item":
        return f"• {text}"
    if t == "to_do":
        return f"{'✓' if data.get('checked') else '○'} {text}"
    if t == "quote":
        return f"> {text}"
    return text


def _fetch_direct_blocks(client, page_id: str, max_blocks: int = 200) -> tuple[str, list[dict]]:
    """Liest NUR den direkten Textinhalt einer Quell-Seite (keine Unterseiten-
    Inhalte). child_page-Blöcke werden separat gesammelt und zurückgegeben statt
    reingerendert — die werden als eigene seiten-Zeile abgelegt, damit sie im FE
    als eigene Seite mit Breadcrumbs navigierbar sind."""
    lines = []
    children = []
    cursor = None
    fetched = 0
    while fetched < max_blocks:
        kwargs = {"block_id": page_id, "page_size": min(100, max_blocks - fetched)}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        results = response.get("results", [])
        for block in results:
            if block.get("type") == "child_page":
                children.append({
                    "id": block["id"],
                    "title": block.get("child_page", {}).get("title") or "Unterseite",
                })
                continue
            line = _block_to_text(block)
            if line:
                lines.append(line)
        fetched += len(results)
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return "\n".join(lines), children


def _delete_seiten_tree(root_table: str, root_id: int) -> None:
    """Löscht alle seiten unter einem Projekt/Todo/Kontakt (rekursiv, auch tiefer
    verschachtelte). Vor jedem (Re-)Sync aufgerufen, damit --force nicht
    Duplikate anhäuft."""
    conn = _get_db()
    conn.execute("""
        WITH RECURSIVE tree(id) AS (
            SELECT id FROM seiten WHERE parent_typ = ? AND parent_id = ?
            UNION ALL
            SELECT s.id FROM seiten s JOIN tree t ON s.eltern_seite_id = t.id
        )
        DELETE FROM seiten WHERE id IN (SELECT id FROM tree)
    """, (root_table, root_id))
    conn.commit()
    conn.close()


def _sync_quellseite(client, quell_seiten_id: str, *, root_table: str | None = None,
                      root_id: int | None = None, seite_id: int | None = None,
                      _depth: int = 0, _max_depth: int = 6) -> None:
    """Rekursiv: holt Direktinhalt einer Quell-Seite und legt für jede gefundene
    Unterseite (child_page-Block) eine eigene seiten-Zeile an, die dann ihrerseits
    synchronisiert wird. Genau einer von (root_table+root_id) oder seite_id ist
    gesetzt — root für die Wurzel-Seite eines Projekts/Todos/Kontakts, seite_id
    für eine bereits angelegte seiten-Zeile."""
    text, children = _fetch_direct_blocks(client, quell_seiten_id)

    conn = _get_db()
    if root_table:
        conn.execute(
            f"UPDATE {root_table} SET notizen = ?, externe_id = ? WHERE id = ?",
            (text, quell_seiten_id, root_id),
        )
    else:
        conn.execute(
            "UPDATE seiten SET inhalt = ?, externe_id = ? WHERE id = ?",
            (text, quell_seiten_id, seite_id),
        )
    conn.commit()
    conn.close()

    if _depth >= _max_depth:
        return

    for child in children:
        conn = _get_db()
        now = _now()
        cur = conn.execute(
            "INSERT INTO seiten (parent_typ, parent_id, eltern_seite_id, externe_id, "
            "titel, inhalt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                root_table if root_table else None,
                root_id if root_table else None,
                None if root_table else seite_id,
                child["id"], child["title"], "", now, now,
            ),
        )
        new_seite_id = cur.lastrowid
        conn.commit()
        conn.close()
        _sync_quellseite(client, child["id"], seite_id=new_seite_id, _depth=_depth + 1, _max_depth=_max_depth)


def backfill_seiten_inhalte(force: bool = False) -> dict:
    """Holt nachträglich den vollen Seiteninhalt (nicht nur die Properties) für
    bereits migrierte Todos/Projekte/Kontakte nach — inkl. rekursiv verschachtelter
    Unterseiten (als eigene seiten-Zeilen, nicht in einen Textblock gequetscht).
    Matched Quell-Seiten zu lokalen Zeilen über den Titel (name) — nötig weil die
    ursprüngliche Properties-Migration keine Seiten-ID gespeichert hat.
    force=False (default): überspringt Zeilen die schon eine notizen befüllt haben
    (Re-Run-sicher). force=True: baut den Seitenbaum für ALLE Zeilen neu auf (löscht
    vorher alte seiten dieser Zeile, keine Duplikate).
    Gibt {"todos": {"updated": n, "unmatched": n}, ...} zurück."""
    import os
    from notion_client import Client as NotionClient

    api_key = os.getenv("NOTION_API_KEY")
    if not api_key:
        raise RuntimeError("NOTION_API_KEY nicht gesetzt — Backfill abgebrochen.")
    client = NotionClient(auth=api_key)

    targets = [
        ("todos", _QUELLE_TODOS_DB_ID, "Name"),
        ("projekte", _QUELLE_PROJEKTE_DB_ID, "Projekt"),
        ("kontakte", _QUELLE_KONTAKTE_DB_ID, "Name"),
    ]

    result = {}
    for table, db_id, title_field in targets:
        conn = _get_db()
        if force:
            rows = conn.execute(f"SELECT id, name FROM {table}").fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, name FROM {table} WHERE notizen IS NULL OR notizen = ''"
            ).fetchall()
        conn.close()
        by_name: dict[str, list[int]] = {}
        for row_id, name in rows:
            by_name.setdefault((name or "").strip(), []).append(row_id)

        updated = 0
        for page in _quelle_paginate(client, db_id):
            props = page.get("properties", {})
            title = (_quelle_prop(props, title_field, "title") or "").strip()
            matches = by_name.get(title)
            if not matches:
                continue
            for row_id in matches:
                _delete_seiten_tree(table, row_id)
                _sync_quellseite(client, page["id"], root_table=table, root_id=row_id)
                updated += 1

        conn = _get_db()
        remaining = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE notizen IS NULL OR notizen = ''"
        ).fetchone()[0]
        conn.close()
        print(f"[backfill] {table}: {updated} Zeilen synchronisiert, {remaining} ohne Treffer/Inhalt.", flush=True)
        result[table] = {"updated": updated, "unmatched": remaining}

    return result


# ── Seiten fürs Frontend + LLM-Tool (Detail-Ansicht mit Breadcrumbs, Lazy-Load) ─

_ROOT_TABLES = {"projekte", "todos", "kontakte"}


def create_seite(titel: str, inhalt: str = "", *, parent_typ: str | None = None,
                  parent_id: int | None = None, eltern_seite_id: int | None = None) -> int:
    """Legt eine neue Seite an — entweder direkt an einem Todo/Projekt/Kontakt
    (parent_typ+parent_id) oder als Unterseite einer bestehenden Seite
    (eltern_seite_id). Genau eine der beiden Varianten angeben, nie beide/keine
    (2026-07-23: bisher gab es nur update_seite() für BESTEHENDE Zeilen — kein
    Weg, überhaupt eine neue Seite anzulegen, weder fürs Frontend noch fürs LLM.
    JARVIS hatte deshalb bei 'dokumentier das als Unterseite' keine echte
    Möglichkeit dazu und hat stattdessen fälschlich behauptet, in "Notion" zu
    schreiben — Notion existiert in diesem System seit 2026-07-19 nicht mehr)."""
    has_root = bool(parent_typ) and parent_id is not None
    has_eltern = eltern_seite_id is not None
    if has_root == has_eltern:
        raise ValueError("Entweder parent_typ+parent_id ODER eltern_seite_id angeben, nicht beides/keins.")
    if has_root and parent_typ not in _ROOT_TABLES:
        raise ValueError(f"parent_typ muss eines von {_ROOT_TABLES} sein, nicht {parent_typ!r}.")
    now = _now()
    conn = _get_db()
    cur = conn.execute(
        "INSERT INTO seiten (parent_typ, parent_id, eltern_seite_id, titel, inhalt, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (parent_typ, parent_id, eltern_seite_id, titel, inhalt, now, now),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_seite(seite_id: int, **fields) -> None:
    allowed = {"titel", "inhalt"}
    sets = [f"{k} = ?" for k in fields if k in allowed]
    values = [v for k, v in fields.items() if k in allowed]
    if not sets:
        return
    sets.append("updated_at = ?")
    values.append(_now())
    values.append(seite_id)
    conn = _get_db()
    conn.execute(f"UPDATE seiten SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    conn.close()


def get_seite(seite_id: int) -> dict | None:
    conn = _get_db()
    row = conn.execute(
        "SELECT id, parent_typ, parent_id, eltern_seite_id, titel, inhalt FROM seiten WHERE id = ?",
        (seite_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "parent_typ": row[1], "parent_id": row[2],
        "eltern_seite_id": row[3], "titel": row[4], "inhalt": row[5],
    }


def list_seiten(parent_typ: str, parent_id: int) -> list[dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, titel FROM seiten WHERE parent_typ = ? AND parent_id = ? ORDER BY id",
        (parent_typ, parent_id)
    ).fetchall()
    conn.close()
    return [{"id": r[0], "titel": r[1]} for r in rows]


def list_unterseiten(seite_id: int) -> list[dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, titel FROM seiten WHERE eltern_seite_id = ? ORDER BY id",
        (seite_id,)
    ).fetchall()
    conn.close()
    return [{"id": r[0], "titel": r[1]} for r in rows]


def get_seite_breadcrumbs(seite_id: int) -> list[dict]:
    """Kette von Wurzel (Projekt/Todo/Kontakt) bis zur Seite selbst."""
    chain = []
    conn = _get_db()
    current_id = seite_id
    while current_id is not None:
        row = conn.execute(
            "SELECT id, parent_typ, parent_id, eltern_seite_id, titel FROM seiten WHERE id = ?",
            (current_id,)
        ).fetchone()
        if not row:
            break
        chain.append({"typ": "seite", "id": row[0], "titel": row[4]})
        if row[3] is not None:
            current_id = row[3]
        else:
            parent_typ, parent_id = row[1], row[2]
            if parent_typ in _ROOT_TABLES:
                root_row = conn.execute(f"SELECT name FROM {parent_typ} WHERE id = ?", (parent_id,)).fetchone()
                if root_row:
                    chain.append({"typ": parent_typ, "id": parent_id, "titel": root_row[0]})
            current_id = None
    conn.close()
    chain.reverse()
    return chain


def get_seite_view(typ: str, item_id: int) -> dict | None:
    """Einheitliche Sicht fürs Frontend — funktioniert sowohl für die Wurzel-Seite
    eines Projekts/Todos/Kontakts als auch für eine verschachtelte Unterseite.
    Gibt {typ, id, titel, inhalt, meta, unterseiten, breadcrumbs} zurück."""
    if typ == "seite":
        seite = get_seite(item_id)
        if not seite:
            return None
        return {
            "typ": "seite", "id": seite["id"], "titel": seite["titel"],
            "inhalt": seite["inhalt"] or "", "meta": {},
            "unterseiten": list_unterseiten(item_id),
            "breadcrumbs": get_seite_breadcrumbs(item_id),
        }

    if typ not in _ROOT_TABLES:
        return None
    conn = _get_db()
    row = conn.execute(f"SELECT * FROM {typ} WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        return None
    cols = [d[0] for d in conn.execute(f"SELECT * FROM {typ} LIMIT 0").description]
    conn.close()
    data = dict(zip(cols, row))
    meta = {k: v for k, v in data.items()
            if k not in ("id", "name", "notizen", "externe_id", "created_at", "updated_at")}
    return {
        "typ": typ, "id": item_id, "titel": data.get("name"),
        "inhalt": data.get("notizen") or "", "meta": meta,
        "unterseiten": list_seiten(typ, item_id),
        "breadcrumbs": [{"typ": typ, "id": item_id, "titel": data.get("name")}],
    }


def read_seite(seite_id: int) -> dict | None:
    """Lazy-Load für JARVIS's read_seite-Tool: voller Inhalt EINER Seite plus
    Titel+id ihrer eigenen Unterseiten (kein Inhalt davon — für die braucht's
    einen weiteren Aufruf)."""
    seite = get_seite(seite_id)
    if not seite:
        return None
    return {
        "id": seite["id"], "titel": seite["titel"], "inhalt": seite["inhalt"] or "",
        "unterseiten": list_unterseiten(seite_id),
    }
