from __future__ import annotations
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import config

_DB_PATH = Path.home() / ".jarvis" / "sessions.db"


# ── SQLite I/O ────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL,
            time       TEXT NOT NULL,
            title      TEXT,
            transcript TEXT
        )
    """)
    conn.commit()
    # Migrate old schema if needed
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN transcript TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN clients TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN category TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN tab_id TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    return conn


def _first_user_message(history: list[dict]) -> str:
    for msg in history:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            if content:
                text = str(content).strip()
                return text[:60] + ("…" if len(text) > 60 else "")
    return "Gespräch"


# ── Public API ────────────────────────────────────────────────────────────────

def save(history: list[dict], clients: list[str] | None = None, category: str | None = None) -> threading.Thread:
    """Speichert die Session mit vollständigem Transcript. Kein LLM-Call.

    clients: Namen der Clients, die zu dieser Session beigetragen haben —
    nur für Anzeige/Filterung in jarvis-web, keine Auswirkung auf den Inhalt.
    category: "voice" oder "web" — welche History-Kategorie das war.
    """
    def _do_save():
        if not history:
            return
        now = datetime.now()
        title = _first_user_message(history)

        # Nur user/assistant Messages serialisieren
        transcript_msgs = []
        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            if role not in ("user", "assistant"):
                continue
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if content:
                transcript_msgs.append({"role": role, "text": str(content)})

        if not transcript_msgs:
            return

        transcript_json = json.dumps(transcript_msgs, ensure_ascii=False)
        clients_json = json.dumps(sorted(set(clients or [])), ensure_ascii=False)

        with _get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (date, time, title, transcript, clients, category) VALUES (?, ?, ?, ?, ?, ?)",
                (now.date().isoformat(), now.strftime("%H:%M"), title, transcript_json, clients_json, category)
            )
        print(f"[session] Gespeichert: {title} ({len(transcript_msgs)} Nachrichten)", flush=True)

        # Lernextraktion im Hintergrund
        try:
            import learning
            learning.process_session(history)
        except Exception as e:
            print(f"[session] Learning-Start Fehler: {e}", flush=True)

    t = threading.Thread(target=_do_save, daemon=False)
    t.start()
    return t


def upsert(session_id: int | None, history: list[dict], clients: list[str] | None = None,
           category: str | None = None, finalize: bool = False, tab_id: str | None = None) -> int | None:
    """Wie save(), aber schreibt bei jeder Nachricht durch statt erst beim Verbindungsende
    — sonst geht eine Konversation komplett verloren, wenn z.B. ein Browser-Tab einfach
    geschlossen wird, ohne dass ein Neustart oder "+ Neuer Chat" je einen Save auslöst
    (2026-07-20 entdeckt: Tab zu, Inhalt war für immer weg, weder im Verlauf noch sonstwo).

    session_id=None → legt eine neue Zeile an (Titel = erste User-Nachricht), sonst wird
    die bestehende Zeile aktualisiert (Titel bleibt). Gibt die (neue oder bestehende)
    session_id zurück — vom Aufrufer zu merken und beim nächsten Aufruf wieder mitzugeben.

    tab_id wird NUR beim Anlegen der Zeile (session_id=None) gesetzt und danach nie wieder
    verändert — spätere upsert()-Aufrufe für dieselbe Zeile müssen tab_id nicht mitgeben.
    Ermöglicht find_active_session(): einen wiederverbindenden Web-Tab nach einem
    Server-Neustart an seiner letzten Zeile wiederzuerkennen (2026-07-22: 'der Chat
    sollte schon wieder mitgegeben werden' — vorher hatte ein Web-Tab über einen
    Neustart hinweg keine stabile Identität, jede neue Verbindung fing bei null an,
    obwohl der Verlauf die ganze Zeit schon in sessions.db lag).

    finalize=True stößt zusätzlich die Lernextraktion an — NUR bei echtem Abschluss
    (Session-Reset, Session-Wechsel, Shutdown), nicht bei jedem Zwischen-Save, sonst
    würde jede einzelne Nachricht einen zusätzlichen LLM-Call kosten."""
    if not history:
        return session_id

    now = datetime.now()
    transcript_msgs = []
    for msg in history:
        role = msg.get("role")
        content = msg.get("content", "")
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if content:
            transcript_msgs.append({"role": role, "text": str(content)})

    if not transcript_msgs:
        return session_id

    transcript_json = json.dumps(transcript_msgs, ensure_ascii=False)
    clients_json = json.dumps(sorted(set(clients or [])), ensure_ascii=False)

    with _get_db() as conn:
        if session_id is None:
            title = _first_user_message(history)
            cur = conn.execute(
                "INSERT INTO sessions (date, time, title, transcript, clients, category, tab_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now.date().isoformat(), now.strftime("%H:%M"), title, transcript_json, clients_json, category, tab_id)
            )
            session_id = cur.lastrowid
        else:
            conn.execute(
                "UPDATE sessions SET time = ?, transcript = ?, clients = ? WHERE id = ?",
                (now.strftime("%H:%M"), transcript_json, clients_json, session_id)
            )

    if finalize:
        print(f"[session] Abgeschlossen (id={session_id}): {len(transcript_msgs)} Nachrichten", flush=True)
        try:
            import learning
            learning.process_session(history)
        except Exception as e:
            print(f"[session] Learning-Start Fehler: {e}", flush=True)

    return session_id


def find_active_session(tab_id: str) -> dict | None:
    """Für die Wiederherstellung eines Web-Tabs nach einem Server-Neustart: findet
    die zuletzt aktualisierte Session-Zeile für genau diesen tab_id. Gibt {"id",
    "transcript"} zurück (transcript = Liste von {"role", "text"}, wie von upsert()
    gespeichert) oder None wenn keine Zeile existiert."""
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT id, transcript FROM sessions WHERE tab_id = ? ORDER BY id DESC LIMIT 1",
                (tab_id,),
            ).fetchone()
        if row and row[1]:
            return {"id": row[0], "transcript": json.loads(row[1])}
    except Exception as e:
        print(f"[session] find_active_session Fehler: {e}", flush=True)
    return None


def list_sessions(limit: int = 30) -> list[dict]:
    """Gibt eine Liste vergangener Sessions für die UI zurück."""
    try:
        with _get_db() as conn:
            rows = conn.execute(
                """SELECT id, date, time, title, clients, category
                   FROM sessions
                   ORDER BY date DESC, time DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
    except Exception:
        return []

    result = []
    for row in rows:
        try:
            clients = json.loads(row[4]) if row[4] else []
        except Exception:
            clients = []
        result.append({
            "id": row[0], "date": row[1], "time": row[2], "title": row[3] or "Gespräch",
            "clients": clients, "category": row[5],
        })
    return result


def get_transcript(session_id: int) -> list[dict]:
    """Gibt den vollständigen Transcript einer Session zurück."""
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT transcript FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as e:
        print(f"[session] get_transcript Fehler: {e}", flush=True)
    return []


def delete(session_id: int) -> bool:
    """Löscht eine Session aus der Datenbank."""
    try:
        with _get_db() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        print(f"[session] Session {session_id} gelöscht.", flush=True)
        return True
    except Exception as e:
        print(f"[session] delete Fehler: {e}", flush=True)
        return False


def load_for_prompt(days: int = 3) -> str:
    """Legacy — nicht mehr in context.py verwendet. Bleibt für Kompatibilität."""
    return ""
