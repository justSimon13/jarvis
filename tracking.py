"""
tracking.py — Strukturierte Ziele und Logs für JARVIS.

Verwaltet ~/.jarvis/tracking.db mit generischen Tabellen.
Neue Topics erfordern keinen Schema-Change — topic ist ein Datenwert.

Schreib-Regeln (verbindlich):
  - Zielwerte die grafisch dargestellt werden → set_goal() hier
  - Zeitreihen-Logs (Training, Gewicht, Kalorien) → add_log() hier
  - Prose/Kontext → knowledge.py
  - JARVIS-Regeln/Config → brain.db
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path

_TRACKING_DB = Path.home() / ".jarvis" / "tracking.db"


def _get_db() -> sqlite3.Connection:
    _TRACKING_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_TRACKING_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            topic      TEXT NOT NULL,
            key        TEXT NOT NULL,
            value      REAL,
            unit       TEXT DEFAULT '',
            label      TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (topic, key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id         TEXT PRIMARY KEY,
            topic      TEXT NOT NULL,
            date       TEXT NOT NULL,
            key        TEXT NOT NULL,
            value      REAL,
            text_value TEXT,
            unit       TEXT DEFAULT '',
            notes      TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_topic_key ON logs (topic, key, date)")

    # Einmalige Bereinigung (2026-07-27): 3 pauschale Gewinneinträge (Datum =
    # Anlage-Tag, nicht echtes Zahlungsdatum) wurden durch feiner aufgeteilte,
    # korrekt datierte Einträge ersetzt (knowHere: 6800€ -> 4800+800+1200€ an den
    # echten Rechnungsdaten; Ticketsystem: 10900€ -> 10000+900€; Michelle: blieb
    # bei 0€). Die alten Pauschal-Einträge blieben liegen und hätten den
    # tatsächlichen Gesamtgewinn doppelt gezählt. Exakter id-Match statt Datums-/
    # Wert-Kriterium, damit garantiert nur genau diese drei bereits identifizierten
    # Zeilen betroffen sind, nie ein künftiger, unabhängiger Eintrag mit ähnlichem
    # Datum/Wert. Idempotent (SELECT vor DELETE, Log nur beim ersten Treffer).
    _STALE_GEWINN_LOG_IDS = (
        "921f7cd5-22d0-48f8-b131-436c5a090bdf",  # knowHere Theme, 2026-07-26, 6800€
        "1f1a5d25-ae40-469a-838e-117f52c4887f",  # Ticketsystem, 2026-07-26, 10900€
        "e65b28a5-bd28-477a-877d-6e892fe5e1ce",  # Michelle Webseite, 2026-07-26, 0€
    )
    for stale_id in _STALE_GEWINN_LOG_IDS:
        row = conn.execute("SELECT topic, key, date, value, notes FROM logs WHERE id = ?", (stale_id,)).fetchone()
        if row:
            print(f"[tracking] Bereinigung: lösche veralteten Pauschal-Gewinneintrag {stale_id} "
                  f"(topic={row[0]!r} key={row[1]!r} date={row[2]!r} value={row[3]!r} notes={row[4]!r})", flush=True)
            conn.execute("DELETE FROM logs WHERE id = ?", (stale_id,))

    conn.commit()
    return conn


# ── Ziele ─────────────────────────────────────────────────────────────────────

def set_goal(topic: str, key: str, value: float | None = None,
             unit: str = "", label: str = "") -> str:
    """Setzt oder aktualisiert ein Ziel. topic+key ist der Primärschlüssel."""
    with _get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO goals (topic, key, value, unit, label, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (topic, key, value, unit, label, datetime.now().isoformat()),
        )
    label_str = f" ({label})" if label else ""
    return f"Ziel gesetzt: {topic}.{key}{label_str} = {value} {unit}".strip()


def get_goal(topic: str, key: str) -> dict | None:
    with _get_db() as conn:
        row = conn.execute(
            "SELECT topic, key, value, unit, label, updated_at FROM goals WHERE topic=? AND key=?",
            (topic, key),
        ).fetchone()
    if not row:
        return None
    return {"topic": row[0], "key": row[1], "value": row[2], "unit": row[3],
            "label": row[4], "updated_at": row[5]}


def get_goals(topic: str) -> list[dict]:
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT key, value, unit, label, updated_at FROM goals WHERE topic=? ORDER BY key",
            (topic,),
        ).fetchall()
    return [{"key": r[0], "value": r[1], "unit": r[2], "label": r[3], "updated_at": r[4]}
            for r in rows]


# ── Logs ──────────────────────────────────────────────────────────────────────

def add_log(topic: str, key: str, value: float | None = None,
            text_value: str | None = None, unit: str = "",
            notes: str = "", log_date: str | None = None) -> str:
    """Fügt einen Log-Eintrag hinzu. Gibt die Entry-ID zurück."""
    entry_id = str(uuid.uuid4())
    log_date = log_date or date.today().isoformat()
    with _get_db() as conn:
        conn.execute(
            """INSERT INTO logs (id, topic, date, key, value, text_value, unit, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry_id, topic, log_date, key, value, text_value, unit, notes),
        )
    return entry_id


def get_logs(topic: str, key: str | None = None,
             since_date: str | None = None, limit: int = 30) -> list[dict]:
    query = "SELECT id, date, key, value, text_value, unit, notes FROM logs WHERE topic=?"
    params: list = [topic]
    if key:
        query += " AND key=?"
        params.append(key)
    if since_date:
        query += " AND date>=?"
        params.append(since_date)
    query += " ORDER BY date DESC LIMIT ?"
    params.append(limit)
    with _get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [{"id": r[0], "date": r[1], "key": r[2], "value": r[3],
             "text_value": r[4], "unit": r[5], "notes": r[6]}
            for r in rows]


def get_last_log(topic: str, key: str) -> dict | None:
    logs = get_logs(topic, key=key, limit=1)
    return logs[0] if logs else None


def delete_log(entry_id: str) -> bool:
    """Löscht einen einzelnen Log-Eintrag per id. Generisch für jedes Topic —
    z.B. um einen doppelten/fehlerhaften Eintrag zu korrigieren. Gibt True zurück,
    wenn eine Zeile betroffen war."""
    with _get_db() as conn:
        cur = conn.execute("DELETE FROM logs WHERE id = ?", (entry_id,))
    return cur.rowcount > 0


def get_monthly_series(topic: str, key: str, months: list[str]) -> dict[str, float]:
    """Summiert 'value' pro Kalendermonat (YYYY-MM) für eine explizite Liste von
    Monaten. Generisch (kein Finanzen-Bezug) — reine Zeitreihen-Aggregation über
    logs, wiederverwendbar für jedes Topic/Key (Gewinn, aber genauso z.B. ein
    Sport- oder Ausgaben-Trend). Monate ohne Einträge fehlen im Ergebnis (nicht 0)
    — der Aufrufer entscheidet, wie er fehlende Monate behandelt."""
    if not months:
        return {}
    with _get_db() as conn:
        placeholders = ",".join("?" * len(months))
        rows = conn.execute(
            f"""SELECT substr(date, 1, 7) AS month, SUM(value) FROM logs
                WHERE topic = ? AND key = ? AND substr(date, 1, 7) IN ({placeholders})
                GROUP BY month""",
            (topic, key, *months),
        ).fetchall()
    return {r[0]: r[1] or 0.0 for r in rows}


# ── Fortschritt ───────────────────────────────────────────────────────────────

def get_progress(topic: str) -> dict:
    """Gibt Ziele + letzten Log-Wert + Trend für ein komplettes Topic zurück."""
    goals = get_goals(topic)
    enriched = []
    for goal in goals:
        recent = get_logs(topic, key=goal["key"], limit=5)
        goal["last_log"] = recent[0] if recent else None
        if len(recent) >= 2 and recent[0]["value"] is not None and recent[1]["value"] is not None:
            goal["trend"] = recent[0]["value"] - recent[1]["value"]
        else:
            goal["trend"] = None
        enriched.append(goal)
    return {"topic": topic, "goals": enriched}


def list_topics() -> list[str]:
    """Alle Topics die mindestens ein Ziel oder einen Log-Eintrag haben."""
    with _get_db() as conn:
        goal_topics = {r[0] for r in conn.execute("SELECT DISTINCT topic FROM goals").fetchall()}
        log_topics  = {r[0] for r in conn.execute("SELECT DISTINCT topic FROM logs").fetchall()}
    return sorted(goal_topics | log_topics)
