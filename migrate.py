"""
Einmalige Datenmigration → SQLite.
Liest von Supabase (wenn konfiguriert) oder lokalen JSON-Dateien,
schreibt nach ~/.jarvis/brain.db und ~/.jarvis/sessions.db.

Ausführen auf dem Mac (mit Supabase-Zugriff), danach brain.db + sessions.db
auf den Server kopieren.

python3 migrate.py
"""
import json
import sqlite3
from pathlib import Path

BRAIN_DB    = Path.home() / ".jarvis" / "brain.db"
SESSIONS_DB = Path.home() / ".jarvis" / "sessions.db"
LOCAL_BRAIN = Path.home() / ".jarvis" / "brain"
SESSIONS_JSONL = LOCAL_BRAIN / "sessions.jsonl"
SECTIONS = ["profile", "settings", "memory", "followups", "context_config"]


# ── Brain ─────────────────────────────────────────────────────────────────────

def migrate_brain():
    print("── Brain ────────────────────────────────")
    BRAIN_DB.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(BRAIN_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brain (
            section TEXT PRIMARY KEY,
            data    TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.commit()

    data = _read_supabase_brain() or _read_local_brain()

    if not data:
        print("  Keine Daten gefunden.")
        conn.close()
        return

    for section, value in data.items():
        conn.execute(
            "INSERT OR REPLACE INTO brain (section, data) VALUES (?, ?)",
            (section, json.dumps(value, ensure_ascii=False))
        )
        keys = len(value) if isinstance(value, dict) else len(value) if isinstance(value, list) else "?"
        print(f"  {section}: {keys} Einträge")

    conn.commit()
    conn.close()
    print(f"  → {BRAIN_DB}")


def _read_supabase_brain() -> dict | None:
    try:
        import config  # holt URL/Key auch aus Hardcode-Defaults
        url = config.SUPABASE_URL
        key = config.SUPABASE_KEY
        if not url or not key:
            return None
        from supabase import create_client
        result = create_client(url, key).table("brain").select("section, data").execute()
        if not result.data:
            return None
        print("  Quelle: Supabase")
        return {row["section"]: row["data"] for row in result.data}
    except Exception as e:
        print(f"  Supabase nicht verfügbar: {e}")
        return None


def _read_local_brain() -> dict | None:
    found = {}
    for section in SECTIONS:
        p = LOCAL_BRAIN / f"{section}.json"
        if p.exists():
            try:
                found[section] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    if found:
        print("  Quelle: Lokale JSON-Dateien")
        return found
    return None


# ── Sessions ──────────────────────────────────────────────────────────────────

def migrate_sessions():
    print("── Sessions ─────────────────────────────")
    SESSIONS_DB.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(SESSIONS_DB)
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

    if not SESSIONS_JSONL.exists():
        print(f"  {SESSIONS_JSONL} nicht gefunden, übersprungen.")
        conn.close()
        return

    migrated = 0
    with open(SESSIONS_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                conn.execute(
                    "INSERT INTO sessions (date, time, summary, context, follow_ups) VALUES (?, ?, ?, ?, ?)",
                    (
                        entry.get("date", ""),
                        entry.get("time", ""),
                        entry.get("summary", ""),
                        json.dumps(entry.get("context", []), ensure_ascii=False),
                        json.dumps(entry.get("follow_ups", []), ensure_ascii=False),
                    )
                )
                migrated += 1
            except Exception as e:
                print(f"  Zeile übersprungen: {e}")

    conn.commit()
    conn.close()
    print(f"  {migrated} Sessions migriert → {SESSIONS_DB}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("J.A.R.V.I.S. Migration → SQLite")
    print("=" * 40)
    migrate_brain()
    print()
    migrate_sessions()
    print()
    print("Fertig!")
    print()
    print("Nächste Schritte:")
    print(f"  scp {BRAIN_DB} server:~/.jarvis/brain.db")
    print(f"  scp {SESSIONS_DB} server:~/.jarvis/sessions.db")
    print()
    print("Danach aus .env entfernen: SUPABASE_URL, SUPABASE_KEY")
