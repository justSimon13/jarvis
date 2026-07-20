"""
Einmalige Migration: alte Notion-Anbindung (Todos/Projekte/Kontakte) → local_data.py.
Konzepte entfallen (siehe Notion-Ablösungs-Plan).

Muss auf dem HP-Server laufen (braucht dortiges NOTION_API_KEY aus .env und
schreibt nach ~/.jarvis/local_data.db auf dem Server) — nicht auf dem Mac:

    python3 scripts/migrate_stammdaten.py            # normal, re-run-sicher
    python3 scripts/migrate_stammdaten.py --force     # holt Seiteninhalt für ALLE Zeilen neu
                                                       # (z.B. nach Änderungen an der Seiten-
                                                       # Textextraktion, um schon befüllte
                                                       # Zeilen zu korrigieren/vervollständigen)

Zwei Schritte:
  1. migrate_stammdaten()     — Properties (Name/Status/...), bricht pro Tabelle
     ab und warnt falls dort schon Zeilen existieren.
  2. backfill_seiten_inhalte() — voller Seiteninhalt (z.B. Notizen/Unterseiten
     unter einem Projekt), matched über den Titel. Ohne --force: überspringt
     Zeilen die schon einen Inhalt haben.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: F401 — Seiteneffekt: load_dotenv(), damit NOTION_API_KEY in os.environ landet
import local_data

if __name__ == "__main__":
    force = "--force" in sys.argv
    counts = local_data.migrate_stammdaten()
    print(f"[migrate_stammdaten] Properties: {counts}", flush=True)
    notes = local_data.backfill_seiten_inhalte(force=force)
    print(f"[migrate_stammdaten] Seiteninhalt: {notes}", flush=True)
