"""
Einmaliges, manuelles Löschen des Chat-Bestands vor Teil 2 (Threads, siehe
ROADMAP.md) — messages/history_windows/die alte sessions-Tabelle werden ohne
Rücksicht auf den migrierten Bestand geleert, NICHT migriert. Simons Vorgabe:
die relevanten Inhalte alter Gespräche stehen bereits in der Wissensdatenbank,
ein rückwirkendes Thread-Labeling der Altbestände ist nicht sinnvoll möglich.

NICHT aus server.py::main() aufgerufen (anders als die automatischen Teil-1-
Reparaturläufe) — ein unbeabsichtigtes erneutes Ausführen würde hier tatsächlich
Daten vernichten. Schema bleibt erhalten (DELETE, kein DROP TABLE).

Voraussetzung: ein aktuelles Backup von sessions.db. Das macht Simon manuell,
vorher, in eigenem Ermessen — nicht Teil dieses Skripts.

Ausführen auf dem Server:
    python3 scripts/wipe_chat_history.py
"""
import sqlite3
import sys
from pathlib import Path

SESSIONS_DB = Path.home() / ".jarvis" / "sessions.db"

TABLES = ["messages", "history_windows", "sessions"]


def main():
    if not SESSIONS_DB.exists():
        print(f"{SESSIONS_DB} existiert nicht — nichts zu tun.")
        return

    conn = sqlite3.connect(SESSIONS_DB)
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    print(f"Datenbank: {SESSIONS_DB}")
    for table in TABLES:
        if table not in existing:
            print(f"  {table}: Tabelle existiert nicht, wird übersprungen")
            continue
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count} Zeile(n) werden gelöscht")

    print()
    print("Voraussetzung: ein aktuelles Backup dieser Datei existiert bereits.")
    answer = input("Zum Bestätigen 'LÖSCHEN' eingeben: ")
    if answer.strip() != "LÖSCHEN":
        print("Abgebrochen — nichts gelöscht.")
        sys.exit(1)

    for table in TABLES:
        if table in existing:
            conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    print("Fertig — messages/history_windows/sessions sind leer, Schema unverändert.")


if __name__ == "__main__":
    main()
