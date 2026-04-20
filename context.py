import json
import sqlite3
import time
from datetime import date, timedelta
from notion_client import Client as NotionClient
import config
import brain
import btc
import calendar_service


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.NOTION_CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            fetched_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    return conn


def _is_stale(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute("SELECT fetched_at FROM cache WHERE key = ?", (key,)).fetchone()
    if not row:
        return True
    return (time.time() - row[0]) > config.NOTION_CACHE_TTL


def _get_cached(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT data FROM cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _set_cached(conn: sqlite3.Connection, key: str, data):
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, data, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(data, ensure_ascii=False), int(time.time())),
    )
    conn.commit()


def _fetch_todos(notion: NotionClient) -> list[dict]:
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    try:
        response = notion.databases.query(
            database_id=config.NOTION_TODOS_DB_ID,
            filter={
                "and": [
                    {
                        "or": [
                            {"property": "Datum", "date": {"on_or_after": week_ago}},
                            {"property": "Datum", "date": {"is_empty": True}},
                        ]
                    },
                    {"property": "Status", "status": {"does_not_equal": "Erledigt"}},
                    {"property": "Status", "status": {"does_not_equal": "Archiviert"}},
                ]
            },
            sorts=[{"property": "Datum", "direction": "ascending"}],
        )
        results = []
        for page in response.get("results", []):
            props = page.get("properties", {})
            title_list = props.get("Name", {}).get("title", [])
            name = title_list[0]["plain_text"] if title_list else "(kein Name)"
            datum_prop = props.get("Datum", {}).get("date")
            datum = datum_prop.get("start") if datum_prop else None
            status_prop = props.get("Status", {}).get("status")
            status = status_prop.get("name") if status_prop else None
            prio_prop = props.get("Priorität", {}).get("select")
            prioritaet = prio_prop.get("name") if prio_prop else None
            results.append({"name": name, "datum": datum, "status": status, "prioritaet": prioritaet})
        return results
    except Exception as e:
        print(f"[context] Todos-Fetch fehlgeschlagen: {e}")
        return []


def _fetch_konzepte(notion: NotionClient) -> list[dict]:
    try:
        response = notion.databases.query(
            database_id=config.NOTION_KONZEPTE_DB_ID,
            filter={
                "or": [
                    {"property": "Status", "select": {"equals": "Offen"}},
                    {"property": "Status", "select": {"equals": "In Prüfung"}},
                ]
            },
        )
        results = []
        for page in response.get("results", []):
            props = page.get("properties", {})
            title_list = props.get("Name", {}).get("title", [])
            name = title_list[0]["plain_text"] if title_list else "(kein Name)"
            notiz_list = props.get("Notiz", {}).get("rich_text", [])
            notiz = notiz_list[0]["plain_text"][:80] if notiz_list else None
            results.append({"name": name, "notiz": notiz})
        return results
    except Exception as e:
        print(f"[context] Konzepte-Fetch fehlgeschlagen: {e}")
        return []


def _fetch_projekte(notion: NotionClient) -> list[dict]:
    try:
        response = notion.databases.query(
            database_id=config.NOTION_PROJEKTE_DB_ID,
            filter={
                "or": [
                    {"property": "Status", "select": {"equals": "In Bearbeitung"}},
                    {"property": "Status", "select": {"equals": "Planung"}},
                    {"property": "Status", "select": {"equals": "Angebot"}},
                ]
            },
        )
        results = []
        for page in response.get("results", []):
            props = page.get("properties", {})
            title_list = props.get("Projekt", {}).get("title", [])
            name = title_list[0]["plain_text"] if title_list else "(kein Name)"
            status_prop = props.get("Status", {}).get("select")
            status = status_prop.get("name") if status_prop else None
            desc_list = props.get("Beschreibung", {}).get("rich_text", [])
            desc = desc_list[0]["plain_text"][:100] if desc_list else None
            results.append({"name": name, "status": status, "beschreibung": desc})
        return results
    except Exception as e:
        print(f"[context] Projekte-Fetch fehlgeschlagen: {e}")
        return []


def invalidate(key: str):
    conn = _get_db()
    conn.execute("DELETE FROM cache WHERE key = ?", (key,))
    conn.commit()
    conn.close()


def refresh_if_stale():
    if not config.NOTION_API_KEY:
        return
    conn = _get_db()
    notion = NotionClient(auth=config.NOTION_API_KEY)
    if _is_stale(conn, "todos"):
        print("[context] Lade Todos aus Notion...")
        _set_cached(conn, "todos", _fetch_todos(notion))
    if _is_stale(conn, "projekte"):
        print("[context] Lade Projekte aus Notion...")
        _set_cached(conn, "projekte", _fetch_projekte(notion))
    if _is_stale(conn, "konzepte"):
        print("[context] Lade Konzepte aus Notion...")
        _set_cached(conn, "konzepte", _fetch_konzepte(notion))
    conn.close()


_WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni",
           "Juli", "August", "September", "Oktober", "November", "Dezember"]


def build_system_prompt() -> str:
    today = date.today()
    today_str = f"{_WOCHENTAGE[today.weekday()]}, {today.day}. {_MONATE[today.month - 1]} {today.year}"
    parts = [config.SYSTEM_PROMPT_BASE + f"\n\nHeute ist {today_str}."]

    brain_section = brain.build_prompt_section()
    if brain_section:
        parts.append(brain_section)

    if not config.NOTION_API_KEY:
        return "\n\n".join(parts)

    conn = _get_db()
    todos = _get_cached(conn, "todos") or []
    projekte = _get_cached(conn, "projekte") or []
    konzepte = _get_cached(conn, "konzepte") or []
    conn.close()

    today = date.today()
    week_start = today - timedelta(days=today.weekday())

    if todos:
        lines = ["## Aktuelle Todos"]
        for t in todos:
            datum = t.get("datum")
            name = t.get("name")
            status = t.get("status") or ""
            prio = t.get("prioritaet")
            prio_str = f", {prio}" if prio else ""
            if datum:
                todo_date = date.fromisoformat(datum)
                if todo_date < week_start:
                    lines.append(f"- OFFEN GEBLIEBEN ({datum}): {name}{prio_str}")
                else:
                    lines.append(f"- [{datum}] {name} ({status}{prio_str})")
            else:
                lines.append(f"- {name} ({status})")
        parts.append("\n".join(lines))

    if projekte:
        lines = ["## Aktive Projekte"]
        for p in projekte:
            line = f"- {p['name']} ({p['status']})"
            if p.get("beschreibung"):
                line += f": {p['beschreibung']}"
            lines.append(line)
        parts.append("\n".join(lines))

    if konzepte:
        lines = ["## Offene Konzepte"]
        for k in konzepte:
            line = f"- {k['name']}"
            if k.get("notiz"):
                line += f": {k['notiz']}"
            lines.append(line)
        parts.append("\n".join(lines))

    cal = calendar_service.format_for_prompt()
    if cal:
        parts.append(cal)

    btc_str = btc.format_for_prompt()
    if btc_str:
        parts.append(btc_str)

    return "\n\n".join(parts)
