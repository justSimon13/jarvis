"""
Entfernt Threads ohne Nachrichten.

Anlass: aus dem Thread-Umbau (Teil 2) stammen Threads, die beim Anlegen eines
Projekts automatisch mitentstanden und nie benutzt wurden — sie stehen in der
Seitenleiste und tragen nichts bei. Das Find-or-Reuse, das sie erzeugte, gibt
es nicht mehr; neue kommen also nicht dazu.

Bewusst manuell, NICHT beim Serverstart:
- Ein gerade per "+ Neuer Chat" geöffneter Thread ist ebenfalls leer. Ein
  automatischer Lauf würde ihn wegräumen, während Simon hineinschreibt.
- Deshalb zusätzlich eine Altersgrenze: nur Threads, die lange genug
  unangetastet sind.

Löscht ausschließlich die threads-Zeile. Nachrichten sind per Definition keine
vorhanden, es kann also nichts verloren gehen.

    python3 scripts/cleanup_empty_threads.py            # zeigt nur an
    python3 scripts/cleanup_empty_threads.py --apply    # löscht wirklich
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path.home() / ".jarvis" / "sessions.db"
MIN_ALTER_STUNDEN = 24


def _leere_threads(conn: sqlite3.Connection, min_alter_h: int) -> list[tuple]:
    grenze = (datetime.now() - timedelta(hours=min_alter_h)).isoformat()
    return conn.execute(
        """
        SELECT t.id, t.title, t.project_id, COALESCE(t.created_at, t.last_activity_at)
        FROM threads t
        WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.thread_id = t.id)
          AND COALESCE(t.created_at, t.last_activity_at, '') < ?
        ORDER BY t.id
        """,
        (grenze,),
    ).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description="Leere Threads entfernen")
    parser.add_argument("--apply", action="store_true", help="Wirklich löschen (sonst nur anzeigen)")
    parser.add_argument("--min-age-hours", type=int, default=MIN_ALTER_STUNDEN,
                        help=f"Mindestalter in Stunden (Standard {MIN_ALTER_STUNDEN})")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[cleanup] Nicht gefunden: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        kandidaten = _leere_threads(conn, args.min_age_hours)
        gesamt = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]

        if not kandidaten:
            print(f"[cleanup] Keine leeren Threads älter als {args.min_age_hours}h "
                  f"({gesamt} Threads insgesamt).")
            return 0

        print(f"[cleanup] {len(kandidaten)} leere Thread(s) von {gesamt}, "
              f"älter als {args.min_age_hours}h:")
        for tid, titel, projekt, angelegt in kandidaten:
            label = titel or "(unbenannt)"
            if projekt:
                label += f"  [Projekt {projekt}]"
            print(f"   #{tid:<5} {label:<52} {str(angelegt or '')[:19].replace('T', ' ')}")

        if not args.apply:
            print("\n[cleanup] Nur angezeigt. Mit --apply tatsächlich löschen.")
            return 0

        conn.executemany("DELETE FROM threads WHERE id = ?", [(t[0],) for t in kandidaten])
        conn.commit()
        print(f"\n[cleanup] {len(kandidaten)} Thread(s) entfernt.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
