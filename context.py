import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from notion_client import Client as NotionClient
import config
import brain
from services import btc
from services import calendar as calendar_service
import session_memory


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


def _fetch_todos(notion: NotionClient, tage_zurueck: int = 7, max_results: int = 20) -> list[dict]:
    week_ago = (date.today() - timedelta(days=tage_zurueck)).isoformat()
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
            page_size=max_results,
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
            bereich_prop = props.get("Bereich", {}).get("select")
            bereich = bereich_prop.get("name") if bereich_prop else None
            results.append({"name": name, "datum": datum, "status": status, "prioritaet": prioritaet, "bereich": bereich})
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


def _fetch_projekte(notion: NotionClient, status_filter: list | None = None) -> list[dict]:
    if status_filter is None:
        status_filter = ["In Bearbeitung", "Planung", "Angebot"]
    try:
        response = notion.databases.query(
            database_id=config.NOTION_PROJEKTE_DB_ID,
            filter={"or": [{"property": "Status", "select": {"equals": s}} for s in status_filter]},
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
            typ_prop = props.get("Typ", {}).get("select")
            typ = typ_prop.get("name") if typ_prop else None
            results.append({"name": name, "status": status, "beschreibung": desc, "typ": typ})
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
    # Lese aus brain.config.notion (neu) mit Fallback auf context_config (alt)
    brain_config = brain.read("config") or {}
    cc = brain_config.get("notion", {}) if isinstance(brain_config, dict) else {}
    if not cc:
        cc = brain.read("context_config") or {}
    if not isinstance(cc, dict):
        cc = {}
    cc_todos = cc.get("todos", {})
    cc_proj = cc.get("projekte", {})
    cc_konz = cc.get("konzepte", {})

    conn = _get_db()
    notion = NotionClient(auth=config.NOTION_API_KEY)
    if _is_stale(conn, "todos"):
        print("[context] Lade Todos aus Notion...")
        _set_cached(conn, "todos", _fetch_todos(
            notion,
            tage_zurueck=cc_todos.get("tage_zurueck", 7),
            max_results=cc_todos.get("max", 20),
        ))
    if _is_stale(conn, "projekte"):
        print("[context] Lade Projekte aus Notion...")
        _set_cached(conn, "projekte", _fetch_projekte(
            notion,
            status_filter=cc_proj.get("status_filter"),
        ))
    if cc_konz.get("laden", True) and _is_stale(conn, "konzepte"):
        print("[context] Lade Konzepte aus Notion...")
        _set_cached(conn, "konzepte", _fetch_konzepte(notion))
    conn.close()


_WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni",
           "Juli", "August", "September", "Oktober", "November", "Dezember"]


def build_static_prompt(mode: str = "assistent") -> str:
    """Stabiler Teil — für Prompt Caching geeignet. Ändert sich höchstens alle 15 min."""
    # Persönlichkeit: brain.modules wenn vorhanden, sonst config.SYSTEM_PROMPT_BASE
    modules_prompt = brain.build_modules_prompt(mode)
    parts = [modules_prompt if modules_prompt else "Du bist J.A.R.V.I.S., der persönliche KI-Assistent von Simon Fischer. Antworte auf Deutsch."]

    brain_section = brain.build_prompt_section(mode)
    if brain_section:
        parts.append(brain_section)

    session_section = session_memory.load_for_prompt(days=3)
    if session_section:
        parts.append(session_section)

    if not config.NOTION_API_KEY:
        return "\n\n".join(parts)

    cc = brain.read("context_config") or {}
    if not isinstance(cc, dict):
        cc = {}
    cc_proj = cc.get("projekte", {})
    cc_konz = cc.get("konzepte", {})
    proj_extra = cc_proj.get("extra_felder", [])

    conn = _get_db()
    todos = _get_cached(conn, "todos") or []
    projekte = _get_cached(conn, "projekte") or []
    konzepte = _get_cached(conn, "konzepte") or [] if cc_konz.get("laden", True) else []
    conn.close()

    today = date.today()
    week_start = today - timedelta(days=today.weekday())

    if todos:
        overdue, today_todos, future_todos, undated = [], [], [], []
        for t in todos:
            datum = t.get("datum")
            name = t.get("name")
            status = t.get("status") or ""
            if status in ("Erledigt", "Archiviert"):
                continue
            prio = t.get("prioritaet")
            prio_str = f", {prio}" if prio else ""
            if datum:
                try:
                    todo_date = date.fromisoformat(datum)
                    entry = f"- [{datum}] {name} ({status}{prio_str})"
                    if todo_date < today:
                        overdue.append(f"- ÜBERFÄLLIG ({datum}): {name}{prio_str}")
                    elif todo_date == today:
                        today_todos.append(entry)
                    else:
                        future_todos.append(entry)
                except ValueError:
                    undated.append(f"- {name} ({status}{prio_str})")
            else:
                undated.append(f"- {name} ({status})")
        lines = []
        if overdue:
            lines.append("## Überfällige Todos")
            lines.extend(overdue)
        if today_todos:
            lines.append("## Todos heute")
            lines.extend(today_todos)
        if future_todos:
            lines.append("## Geplante Todos (nicht heute)")
            lines.extend(future_todos)
        if undated:
            lines.append("## Todos ohne Datum")
            lines.extend(undated)
        if lines:
            parts.append("\n".join(lines))

    if projekte:
        lines = ["## Aktive Projekte"]
        for p in projekte:
            meta_parts = [p["status"]]
            if "typ" in proj_extra and p.get("typ"):
                meta_parts.append(p["typ"])
            line = f"- {p['name']} ({', '.join(meta_parts)})"
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

    return "\n\n".join(parts)


def build_dynamic_prompt(room: str | None = None) -> str:
    """Volatiler Teil — Uhrzeit, BTC, Kalender, Raum. Nie gecacht."""
    now = datetime.now()
    hour = now.hour
    today = now.date()
    today_str = f"{_WOCHENTAGE[today.weekday()]}, {today.day}. {_MONATE[today.month - 1]} {today.year}"

    if 5 <= hour < 11:
        tageszeit = "Morgen"
    elif 11 <= hour < 14:
        tageszeit = "Mittag"
    elif 14 <= hour < 18:
        tageszeit = "Nachmittag"
    elif 18 <= hour < 22:
        tageszeit = "Abend"
    else:
        tageszeit = "Nacht"

    time_line = f"Aktuelle Zeit: {today_str}, {now.strftime('%H:%M')} ({tageszeit})"
    if room:
        time_line += f"\nAktueller Raum: {room}"
    parts = [time_line]

    cal = calendar_service.format_for_prompt()
    if cal:
        parts.append(cal)

    btc_str = btc.format_for_prompt()
    if btc_str:
        parts.append(btc_str)

    return "\n\n".join(parts)
