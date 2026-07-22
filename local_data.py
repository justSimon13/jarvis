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
    # D35/externe Issue-Integration: zusätzliche, nullable Felder für todos —
    # bestehende Zeilen bleiben unberührt (ALTER TABLE ADD COLUMN mit NULL-Default).
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
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now().isoformat()


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
            "SELECT id, name, status, beschreibung, typ, notizen, externe_id FROM projekte ORDER BY id"
        ).fetchall()
    elif isinstance(status_filter, str):
        rows = conn.execute(
            "SELECT id, name, status, beschreibung, typ, notizen, externe_id FROM projekte WHERE status = ? ORDER BY id",
            (status_filter,)
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(status_filter))
        rows = conn.execute(
            f"SELECT id, name, status, beschreibung, typ, notizen, externe_id FROM projekte WHERE status IN ({placeholders}) ORDER BY id",
            list(status_filter)
        ).fetchall()
    conn.close()
    cols = ["id", "name", "status", "beschreibung", "typ", "notizen", "externe_id"]
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


def add_projekt(name: str, status: str | None = None, beschreibung: str | None = None,
                 typ: str | None = None) -> int:
    conn = _get_db()
    now = _now()
    cur = conn.execute(
        "INSERT INTO projekte (name, status, beschreibung, typ, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, status, beschreibung, typ, now, now)
    )
    conn.commit()
    projekt_id = cur.lastrowid
    conn.close()
    return projekt_id


def update_projekt(projekt_id: int, **fields) -> None:
    allowed = {"name", "status", "beschreibung", "typ", "notizen"}
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


# ── Generischer Dispatch für die LLM-Tools (data_query/write/update/delete) ───
# Feldnamen sind die lokalen (name/status/datum/prioritaet/...).

def query(database: str, search: str | None = None, status: str | None = None, limit: int = 10) -> list[dict]:
    if database == "todos":
        cols = ["id", "name", "status", "datum", "prioritaet", "bereich", "aufwand", "notizen",
                "source", "external_id", "repo", "body", "labels"]
        table = "todos"
    elif database == "projekte":
        cols = ["id", "name", "status", "beschreibung", "typ", "notizen"]
        table = "projekte"
    else:
        raise ValueError(f"Unbekannte Datenbank: {database}. Verfügbar: todos, projekte")

    sql = f"SELECT {', '.join(cols)} FROM {table} WHERE 1=1"
    params: list = []
    if search:
        sql += " AND name LIKE ?"
        params.append(f"%{search}%")
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    conn = _get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    results = [dict(zip(cols, r)) for r in rows]

    # Lazy-Load-Hinweis: nur Titel+id der Unterseiten, kein Inhalt — Volltext
    # erst über read_seite() bei Bedarf nachladen (Kosten-Rücksicht).
    for r in results:
        unterseiten = list_seiten(table, r["id"])
        if unterseiten:
            r["unterseiten"] = unterseiten
    return results


def write(database: str, properties: dict) -> int:
    if database == "todos":
        return add_todo(**{k: v for k, v in properties.items()
                            if k in {"name", "status", "datum", "prioritaet", "bereich", "aufwand",
                                     "source", "external_id", "repo", "body", "labels"}})
    if database == "projekte":
        return add_projekt(**{k: v for k, v in properties.items()
                               if k in {"name", "status", "beschreibung", "typ"}})
    raise ValueError(f"Unbekannte Datenbank: {database}. Verfügbar: todos, projekte")


def update(item_id: int, database: str, properties: dict) -> None:
    if database == "todos":
        update_todo(item_id, **properties)
    elif database == "projekte":
        update_projekt(item_id, **properties)
    else:
        raise ValueError(f"Unbekannte Datenbank: {database}. Verfügbar: todos, projekte")


def delete(item_id: int, database: str) -> None:
    if database == "todos":
        delete_todo(item_id)
    elif database == "projekte":
        delete_projekt(item_id)
    else:
        raise ValueError(f"Unbekannte Datenbank: {database}. Verfügbar: todos, projekte")


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
