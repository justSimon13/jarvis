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

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system="Du analysierst Gespräche zwischen Simon und seinem KI-Assistenten JARVIS. Antworte nur mit validem JSON, kein Markdown.",
            messages=[{
                "role": "user",
                "content": (
                    "Analysiere dieses Gespräch und gib JSON zurück:\n"
                    '{"summary": "2-3 Sätze: Was wurde besprochen, entschieden, getan?", '
                    '"context": ["Max 3 kurze Hinweise zu Simons Stimmung/Zustand/Situation"], '
                    '"follow_ups": ["Max 3 offene Punkte die beim nächsten Gespräch relevant sein könnten"]}\n\n'
                    f"Gespräch:\n{chr(10).join(turns)}"
                ),
            }],
        )
        return json.loads(response.content[0].text.strip())
    except Exception as e:
        print(f"[session] Zusammenfassung fehlgeschlagen: {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def save(history: list[dict]) -> threading.Thread:
    """Session asynchron zusammenfassen und in SQLite speichern."""
    def _do_save():
        summary_data = _summarize(history)
        if not summary_data:
            return
        now = datetime.now()
        with _get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (date, time, summary, context, follow_ups) VALUES (?, ?, ?, ?, ?)",
                (
                    now.date().isoformat(),
                    now.strftime("%H:%M"),
                    summary_data.get("summary", ""),
                    json.dumps(summary_data.get("context", []), ensure_ascii=False),
                    json.dumps(summary_data.get("follow_ups", []), ensure_ascii=False),
                )
            )
        print(f"[session] Gespeichert: {summary_data.get('summary', '')[:60]}...")

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
