"""
learning.py — Autonomes Lernen aus Gesprächen.

Läuft als Background-Task nach jeder Session (session_memory.save()).
Analysiert Konversation mit Haiku und schreibt Erkenntnisse in:
  - brain.memory (Micro-Facts, Staging)
  - tracking.db (Logs + Ziele — direkt, kein Confirm nötig)
  - knowledge/*.md (via Suggestion — immer mit Nutzer-Bestätigung)

Suggestions werden in SQLite persistiert und beim Dashboard-Connect nachgeliefert.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

import anthropic

import brain
import config
import knowledge
import protocol as P
import tracking

_SUGGESTIONS_DB = Path.home() / ".jarvis" / "knowledge_index.db"
_manager = None


def init(client_manager) -> None:
    global _manager
    _manager = client_manager


# ── SQLite Suggestions ────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    _SUGGESTIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_SUGGESTIONS_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_suggestions (
            id         TEXT PRIMARY KEY,
            topic      TEXT NOT NULL,
            file       TEXT NOT NULL,
            heading    TEXT DEFAULT '',
            content    TEXT NOT NULL,
            preview    TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            status     TEXT DEFAULT 'pending'
        )
    """)
    conn.commit()
    return conn


# ── Öffentliche API ───────────────────────────────────────────────────────────

def process_session(history: list[dict]) -> None:
    """Startet Post-Session-Extraktion als Background-Thread. Non-blocking."""
    if not history:
        return
    threading.Thread(target=_run, args=(list(history),), daemon=True).start()


def deliver_pending(client_id: str) -> None:
    """Liefert ausstehende Suggestions an einen neu verbundenen Dashboard-Client."""
    if not _manager:
        return
    cb = _manager.get_event_callback(client_id)
    if not cb:
        return
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, topic, file, preview FROM knowledge_suggestions WHERE status='pending' ORDER BY created_at"
        ).fetchall()
    for row in rows:
        try:
            cb({"type": P.KNOWLEDGE_SUGGESTION, "id": row[0], "topic": row[1],
                "file": row[2], "preview": row[3]})
        except Exception as e:
            print(f"[learning] Pending-Delivery Fehler: {e}", flush=True)


def apply_suggestion(suggestion_id: str) -> bool:
    """Wendet eine vom Nutzer bestätigte Suggestion auf die Knowledge-Datei an."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT topic, file, heading, content FROM knowledge_suggestions WHERE id=? AND status='pending'",
            (suggestion_id,),
        ).fetchone()
    if not row:
        return False
    topic, file, heading, content = row
    if heading:
        knowledge.append_section(topic, file, heading, content)
    else:
        existing = knowledge.read(topic, file)
        if existing:
            knowledge.append_section(topic, file, "Aktualisierung", content)
        else:
            knowledge.write(topic, file, content)
    with _get_db() as conn:
        conn.execute("UPDATE knowledge_suggestions SET status='applied' WHERE id=?", (suggestion_id,))
    print(f"[learning] Suggestion angewandt: {topic}/{file}", flush=True)
    return True


def reject_suggestion(suggestion_id: str) -> bool:
    """Markiert eine Suggestion als abgelehnt."""
    with _get_db() as conn:
        rows_affected = conn.execute(
            "UPDATE knowledge_suggestions SET status='rejected' WHERE id=? AND status='pending'",
            (suggestion_id,),
        ).rowcount
    return rows_affected > 0


# ── Interner Ablauf ───────────────────────────────────────────────────────────

def _run(history: list[dict]) -> None:
    data = _extract(history)
    if not data:
        return

    # Micro-Facts → brain.memory (direkt, kein Confirm)
    facts = [f for f in data.get("micro_facts", []) if f and isinstance(f, str)]
    for fact in facts:
        brain.remember(fact, category="kontext", source="gespräch")
    if facts:
        print(f"[learning] {len(facts)} Micro-Fact(s) gespeichert.", flush=True)

    # Tracking-Einträge → tracking.db (direkt, Simon hat es berichtet)
    for entry in data.get("tracking_entries", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("topic") and entry.get("key"):
            tracking.add_log(
                topic=entry["topic"],
                key=entry["key"],
                value=entry.get("value"),
                text_value=entry.get("text_value"),
                unit=entry.get("unit", ""),
                notes=entry.get("notes", ""),
            )
    entries = data.get("tracking_entries", [])
    if entries:
        print(f"[learning] {len(entries)} Tracking-Eintrag/Einträge gespeichert.", flush=True)

    # Ziele → tracking.db (direkt, Simon hat es explizit genannt)
    for goal in data.get("goals", []):
        if not isinstance(goal, dict):
            continue
        if goal.get("topic") and goal.get("key") and goal.get("value") is not None:
            tracking.set_goal(
                topic=goal["topic"],
                key=goal["key"],
                value=float(goal["value"]),
                unit=goal.get("unit", ""),
                label=goal.get("label", ""),
            )
    goals = data.get("goals", [])
    if goals:
        print(f"[learning] {len(goals)} Ziel(e) gesetzt.", flush=True)

    # Knowledge-Updates → Suggestion (immer mit Nutzer-Bestätigung)
    for update in data.get("knowledge_updates", []):
        if not isinstance(update, dict):
            continue
        if update.get("topic") and update.get("file") and update.get("content"):
            _store_and_deliver(
                topic=update["topic"],
                file=update["file"],
                heading=update.get("heading", ""),
                content=update["content"],
                preview=update.get("preview", update["content"][:100]),
            )


def _extract(history: list[dict]) -> dict | None:
    """Ruft Haiku auf um Lernpunkte aus der Konversation zu extrahieren."""
    turns = []
    for msg in history[-30:]:
        role = "Simon" if msg["role"] == "user" else "JARVIS"
        content = msg["content"]
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if isinstance(content, str) and content.strip():
            turns.append(f"{role}: {content.strip()[:300]}")

    if not turns:
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    prompt = (
        f"Analysiere dieses Gespräch und extrahiere Lernpunkte für JARVIS.\n\n"
        f"Gespräch:\n{chr(10).join(turns)}\n\n"
        f"Gib EXAKT dieses JSON zurück (kein Markdown, kein Text davor/danach):\n"
        '{"micro_facts": [], "knowledge_updates": [], "tracking_entries": [], "goals": []}\n\n'
        "Regeln:\n"
        "- micro_facts: Kurze, universelle Fakten über Simon (max 3). Nur NEUE Info. Beispiel: 'Simon trinkt keinen Kaffee'. Leer wenn nichts Neues.\n"
        f"- knowledge_updates: Nur wenn ein Plan/Konzept/Erkenntnis konkret besprochen wurde. Format: {{\"topic\": \"sport\", \"file\": \"fitnessplan\", \"heading\": \"Änderung {today}\", \"content\": \"Markdown-Text\", \"preview\": \"max 80 Zeichen\"}}\n"
        "- tracking_entries: Nur wenn Simon explizit berichtet (Training, Gewicht, etc.). Format: {\"topic\": \"sport\", \"key\": \"training\", \"value\": null, \"text_value\": \"Pull-Day\", \"notes\": \"\"}\n"
        "- goals: Nur wenn Simon explizit ein Zielwert nennt. Format: {\"topic\": \"sport\", \"key\": \"kalorien_ziel\", \"value\": 2800, \"unit\": \"kcal\", \"label\": \"Tägliches Kalorienziel\"}\n"
        "- Leere Arrays wenn nichts relevant ist. Lieber zu wenig als zu viel."
    )

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system="Du extrahierst Lernpunkte aus Gesprächen. Antworte NUR mit validem JSON. Kein Markdown.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        result = json.loads(raw)
        # Sanity check: alle erwarteten Keys vorhanden
        for key in ("micro_facts", "knowledge_updates", "tracking_entries", "goals"):
            result.setdefault(key, [])
        return result
    except Exception as e:
        print(f"[learning] Extraktion fehlgeschlagen: {e}", flush=True)
        return None


def _store_and_deliver(topic: str, file: str, heading: str, content: str, preview: str):
    """Speichert Suggestion in SQLite und liefert sie an verbundene Dashboards."""
    suggestion_id = str(uuid.uuid4())
    with _get_db() as conn:
        conn.execute(
            """INSERT INTO knowledge_suggestions (id, topic, file, heading, content, preview, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (suggestion_id, topic, file, heading, content, preview[:120], datetime.now().isoformat()),
        )
    print(f"[learning] Suggestion erstellt: {topic}/{file} — '{preview[:50]}'", flush=True)

    if not _manager:
        return
    msg = {"type": P.KNOWLEDGE_SUGGESTION, "id": suggestion_id,
           "topic": topic, "file": file, "preview": preview[:120]}
    for cb, _ in _manager.get_dashboard_event_callbacks():
        try:
            cb(msg)
        except Exception as e:
            print(f"[learning] Suggestion-Push Fehler: {e}", flush=True)
