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
