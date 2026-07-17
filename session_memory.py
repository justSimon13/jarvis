from __future__ import annotations
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
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
            summary    TEXT,
            context    TEXT,
            follow_ups TEXT
        )
    """)
    conn.commit()
    return conn


# ── Zusammenfassung via LLM ───────────────────────────────────────────────────

def _summarize(history: list[dict]) -> dict | None:
    if not history:
        return None

    turns = []
    for msg in history[-20:]:
        role = "Simon" if msg["role"] == "user" else "JARVIS"
        content = msg["content"]
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if content:
            turns.append(f"{role}: {str(content)[:200]}")

    if not turns:
        return None

    prompt_text = (
        "Analysiere dieses Gespräch und gib exakt dieses JSON zurück (kein Markdown, kein Text davor/danach):\n"
        '{"summary": "2-3 Sätze: Was wurde besprochen, entschieden, getan?", '
        '"context": ["Max 3 kurze Hinweise zu Simons Stimmung/Zustand/Situation"], '
        '"follow_ups": ["Max 3 offene Punkte die beim nächsten Gespräch relevant sein könnten"]}\n\n'
        f"Gespräch:\n{chr(10).join(turns)}"
    )

    last_error = None
    for attempt in range(3):
        try:
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                system=(
                    "Du analysierst Gespräche zwischen Simon und seinem KI-Assistenten JARVIS. "
                    "Antworte NUR mit validem JSON. Kein Markdown, kein Text vor oder nach dem JSON."
                ),
                messages=[{"role": "user", "content": prompt_text}],
            )
            raw = response.content[0].text.strip()
            if not raw:
                continue
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            return json.loads(raw)
        except Exception as e:
            last_error = e
            print(f"[session] Versuch {attempt + 1}/3 fehlgeschlagen: {e}", flush=True)

    print(f"[session] Alle Versuche fehlgeschlagen: {last_error}", flush=True)
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def save(history: list[dict]) -> threading.Thread:
    """Session asynchron zusammenfassen, speichern und Lernextraktion starten."""
    def _do_save():
        now = datetime.now()
        summary_data = _summarize(history)

        if summary_data:
            summary_text   = summary_data.get("summary", "")
            context_json   = json.dumps(summary_data.get("context", []), ensure_ascii=False)
            follow_ups_json = json.dumps(summary_data.get("follow_ups", []), ensure_ascii=False)
        else:
            # Fallback: Raw-Transcript der letzten 10 Turns speichern
            raw_turns = []
            for msg in history[-10:]:
                role = "Simon" if msg["role"] == "user" else "JARVIS"
                content = msg["content"]
                if isinstance(content, str):
                    raw_turns.append(f"{role}: {content[:150]}")
            summary_text    = "[Zusammenfassung fehlgeschlagen] " + " | ".join(raw_turns)
            context_json    = "[]"
            follow_ups_json = "[]"
            print("[session] Fallback: Raw-Transcript gespeichert.", flush=True)

        with _get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (date, time, summary, context, follow_ups) VALUES (?, ?, ?, ?, ?)",
                (now.date().isoformat(), now.strftime("%H:%M"), summary_text, context_json, follow_ups_json)
            )
        print(f"[session] Gespeichert: {summary_text[:60]}…", flush=True)

        # Lernextraktion im Hintergrund starten (daemon=True — blockiert kein Shutdown)
        try:
            import learning
            learning.process_session(history)
        except Exception as e:
            print(f"[session] Learning-Start Fehler: {e}", flush=True)

    t = threading.Thread(target=_do_save, daemon=False)
    t.start()
    return t


def load_for_prompt(days: int = 3) -> str:
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()

    try:
        with _get_db() as conn:
            rows = conn.execute(
                """SELECT date, time, summary, context, follow_ups
                   FROM sessions WHERE date >= ?
                   ORDER BY date, time""",
                (cutoff,)
            ).fetchall()
    except Exception:
        return ""

    if not rows:
        return ""

    lines = ["## Letzte Sessions"]
    for row in rows[-5:]:
        date_str, time_str, summary, context_json, follow_ups_json = row
        context_items = json.loads(context_json or "[]")
        follow_ups = json.loads(follow_ups_json or "[]")

        line = f"- {date_str} {time_str}: {summary}"
        if context_items:
            line += f" | Kontext: {', '.join(context_items)}"
        if follow_ups:
            line += f" | Offen: {', '.join(follow_ups)}"
        lines.append(line)

    return "\n".join(lines)


def list_sessions(limit: int = 30) -> list[dict]:
    """Gibt eine Liste vergangener Sessions für die UI zurück."""
    try:
        with _get_db() as conn:
            rows = conn.execute(
                """SELECT id, date, time, summary, follow_ups
                   FROM sessions
                   ORDER BY date DESC, time DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
    except Exception:
        return []

    result = []
    for row in rows:
        sid, date_str, time_str, summary, follow_ups_json = row
        follow_ups = json.loads(follow_ups_json or "[]")
        result.append({
            "id": sid,
            "date": date_str,
            "time": time_str,
            "summary": summary or "",
            "follow_ups": follow_ups,
        })
    return result
