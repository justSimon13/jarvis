"""
Backup aller JARVIS-Daten — Schritt 0 aus docs-draft/MIGRATION.md.

Sichert alles unter ~/.jarvis/ was sich nicht wiederbeschaffen lässt, plus die
Zustandsdateien, die außerhalb davon liegen. Läuft im laufenden Betrieb: der
Dienst muss NICHT gestoppt werden.

Warum kein `cp` für die Datenbanken
-----------------------------------
Eine SQLite-Datei, die gerade beschrieben wird, ist als Byte-Kopie nicht
zwingend konsistent — bei aktivem WAL liegt ein Teil des Zustands in
`-wal`/`-shm` daneben. `VACUUM INTO` erzeugt dagegen eine in sich geschlossene,
konsistente Kopie unter derselben Transaktions-Semantik wie ein Lesezugriff.
Das ist der einzige Grund, warum dieses Skript überhaupt existieren muss statt
eines Einzeilers mit `tar`.

Geheimnisse
-----------
`google_credentials.json`, `google_token.json` und `.env` werden standardmäßig
NICHT gesichert. Ein Backup wandert erfahrungsgemäß irgendwann an einen
weniger geschützten Ort als das Original; OAuth-Token und API-Schlüssel sind
zudem neu erzeugbar, die Daten daneben nicht. Mit `--include-secrets` bewusst
zuschaltbar.

Aufruf
------
    python3 scripts/backup.py                     # normaler Lauf
    python3 scripts/backup.py --tag vor-imports   # vor einem Schema-Umbau
    python3 scripts/backup.py --list              # vorhandene Backups zeigen
    python3 scripts/backup.py --verify PFAD       # ein Backup nachträglich prüfen

Vor jedem Datenbank-Umbau mit `--tag` aufrufen. Getaggte Backups werden von der
Aufräumlogik nie gelöscht.
"""
import argparse
import gzip
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

JARVIS_DIR = Path.home() / ".jarvis"
REPO_DIR = Path(__file__).resolve().parent.parent
BACKUP_ROOT = Path.home() / "jarvis-backups"

# Wie viele ungetaggte Läufe aufbewahrt werden. Getaggte sind davon nie betroffen.
KEEP_RUNS = 30

# Datenbanken werden per VACUUM INTO gesichert, nie kopiert. Fehlt eine, ist das
# kein Fehler — nicht jede Installation hat jeden Dienst benutzt (z.B. sleep.db).
DATABASES = [
    "brain.db",            # Brain-Sections
    "sessions.db",         # messages/threads/daily_summaries + alte sessions
    "local_data.db",       # Todos/Projekte/Kontakte/Rechnungen/Ausgaben/Seiten
    "knowledge_index.db",  # Wissens-Index, Link-Graph, knowledge_suggestions
    "tracking.db",         # Ziele und Logs
    "jobs.db",             # Coding-Aufträge
    "notifications.db",    # Notification-Historie
    "sleep.db",            # Sleep-Coach
]

# Zustandsdateien: verlustbehaftet ersetzbar, aber winzig — mitnehmen ist billiger
# als die Diskussion, ob man sie braucht.
STATE_FILES = [
    "history.json",
    "proactive_state.json",
    "idle_status.json",
]

# Verzeichnisse, die vollständig mitgehen. knowledge/ ist der eigentliche Grund
# für dieses Skript — die Markdown-Dateien sind das Herzstück, nicht die DBs.
DIRECTORIES = ["knowledge"]

# Liegt im Repo-Verzeichnis statt in ~/.jarvis (services/alarm.py) — bekannte
# Unsauberkeit, hier bewusst mitgesichert statt stillschweigend zu verlieren.
REPO_STATE_FILES = ["alarm_registry.json"]

SECRET_FILES = ["google_credentials.json", "google_token.json"]
REPO_SECRET_FILES = [".env"]

# Reine Caches — bewusst NICHT gesichert (calendar_cache.json, btc_cache.json).
# Sie werden beim nächsten Abruf neu gefüllt und würden ein Backup nur aufblähen.


def _log(msg: str) -> None:
    print(f"[backup] {msg}", flush=True)


def _backup_database(src: Path, dest: Path) -> dict:
    """Konsistente Kopie einer SQLite-Datei per VACUUM INTO.

    Fällt auf die Backup-API der sqlite3-Bibliothek zurück, falls VACUUM INTO
    nicht verfügbar ist (SQLite < 3.27). Beide Wege sind gegenüber laufenden
    Schreibzugriffen sicher, VACUUM INTO erzeugt zusätzlich eine kompaktierte
    Datei — bei einer über Monate gewachsenen DB spürbar kleiner.
    """
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        try:
            conn.execute("VACUUM INTO ?", (str(dest),))
            method = "vacuum"
        except sqlite3.OperationalError:
            dest_conn = sqlite3.connect(dest)
            try:
                conn.backup(dest_conn)
            finally:
                dest_conn.close()
            method = "backup-api"
    finally:
        conn.close()
    return {"method": method, "bytes": dest.stat().st_size}


def _integrity_check(path: Path) -> str:
    """Prüft die erzeugte Kopie, nicht das Original. Ein Backup, das niemand je
    aufgemacht hat, ist eine Vermutung — deshalb läuft der Check bei jedem Lauf
    und nicht nur auf Zuruf."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def _copy_tree(src: Path, dest: Path) -> int:
    shutil.copytree(src, dest)
    return sum(1 for p in dest.rglob("*") if p.is_file())


def run_backup(tag: str | None = None, include_secrets: bool = False,
               compress: bool = True) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    name = f"{stamp}_{tag}" if tag else stamp
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    # Erst in ein temporäres Verzeichnis, dann umbenennen: ein abgebrochener Lauf
    # hinterlässt damit nie ein halbes Backup, das später wie ein vollständiges
    # aussieht.
    tmp_dir = Path(tempfile.mkdtemp(prefix="jarvis-backup-", dir=BACKUP_ROOT))
    manifest: dict = {
        "created_at": datetime.now().isoformat(),
        "tag": tag,
        "source": str(JARVIS_DIR),
        "includes_secrets": include_secrets,
        "databases": {},
        "files": {},
        "directories": {},
        "skipped": [],
        "errors": [],
    }

    _log(f"Ziel: {BACKUP_ROOT / name}")

    for db_name in DATABASES:
        src = JARVIS_DIR / db_name
        if not src.exists():
            manifest["skipped"].append(db_name)
            continue
        dest = tmp_dir / db_name
        try:
            info = _backup_database(src, dest)
            info["integrity"] = _integrity_check(dest)
            manifest["databases"][db_name] = info
            status = "ok" if info["integrity"] == "ok" else f"PRÜFUNG: {info['integrity']}"
            _log(f"  {db_name}: {info['bytes']} B ({info['method']}) — {status}")
            if info["integrity"] != "ok":
                manifest["errors"].append(f"{db_name}: integrity_check = {info['integrity']}")
        except Exception as e:
            manifest["errors"].append(f"{db_name}: {e}")
            _log(f"  {db_name}: FEHLER — {e}")

    for file_name in STATE_FILES:
        src = JARVIS_DIR / file_name
        if not src.exists():
            manifest["skipped"].append(file_name)
            continue
        shutil.copy2(src, tmp_dir / file_name)
        manifest["files"][file_name] = src.stat().st_size

    for file_name in REPO_STATE_FILES:
        src = REPO_DIR / file_name
        if not src.exists():
            manifest["skipped"].append(file_name)
            continue
        shutil.copy2(src, tmp_dir / file_name)
        manifest["files"][file_name] = src.stat().st_size

    if include_secrets:
        secrets_dir = tmp_dir / "secrets"
        secrets_dir.mkdir()
        for file_name in SECRET_FILES:
            src = JARVIS_DIR / file_name
            if src.exists():
                shutil.copy2(src, secrets_dir / file_name)
                manifest["files"][f"secrets/{file_name}"] = src.stat().st_size
        for file_name in REPO_SECRET_FILES:
            src = REPO_DIR / file_name
            if src.exists():
                shutil.copy2(src, secrets_dir / file_name)
                manifest["files"][f"secrets/{file_name}"] = src.stat().st_size
        _log("  Geheimnisse eingeschlossen (--include-secrets)")

    for dir_name in DIRECTORIES:
        src = JARVIS_DIR / dir_name
        if not src.exists():
            manifest["skipped"].append(dir_name + "/")
            continue
        count = _copy_tree(src, tmp_dir / dir_name)
        manifest["directories"][dir_name] = count
        _log(f"  {dir_name}/: {count} Dateien")

    (tmp_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    final_dir = BACKUP_ROOT / name
    tmp_dir.rename(final_dir)

    if compress:
        archive = BACKUP_ROOT / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(final_dir, arcname=name)
        shutil.rmtree(final_dir)
        result = archive
    else:
        result = final_dir

    size = result.stat().st_size if result.is_file() else sum(
        p.stat().st_size for p in result.rglob("*") if p.is_file()
    )
    _log(f"Fertig: {result.name} ({size / 1024:.1f} KB)")

    if manifest["errors"]:
        _log(f"MIT FEHLERN: {len(manifest['errors'])} — siehe manifest.json")
    return result


def cleanup(keep: int = KEEP_RUNS) -> None:
    """Löscht alte, UNGETAGGTE Läufe. Getaggte Backups (--tag) markieren einen
    Zustand vor einem Umbau und sind genau dann wertvoll, wenn der Umbau lange
    her ist — sie fallen deshalb nie unter die Aufbewahrungsgrenze."""
    if not BACKUP_ROOT.exists():
        return
    entries = []
    for p in BACKUP_ROOT.iterdir():
        base = p.name[:-7] if p.name.endswith(".tar.gz") else p.name
        # Ungetaggt = exakt "YYYY-MM-DD_HHMMSS", ein Tag hängt mit "_" dahinter.
        parts = base.split("_")
        if len(parts) == 2 and len(parts[0]) == 10 and len(parts[1]) == 6:
            entries.append(p)
    entries.sort(key=lambda p: p.name, reverse=True)
    for old in entries[keep:]:
        if old.is_dir():
            shutil.rmtree(old)
        else:
            old.unlink()
        _log(f"Alt entfernt: {old.name}")


def list_backups() -> None:
    if not BACKUP_ROOT.exists():
        print("Noch keine Backups vorhanden.")
        return
    entries = sorted(BACKUP_ROOT.iterdir(), key=lambda p: p.name, reverse=True)
    if not entries:
        print("Noch keine Backups vorhanden.")
        return
    for p in entries:
        size = p.stat().st_size if p.is_file() else sum(
            f.stat().st_size for f in p.rglob("*") if f.is_file()
        )
        print(f"  {p.name:<44} {size / 1024:>9.1f} KB")


def verify(path: Path) -> int:
    """Prüft ein bestehendes Backup nachträglich: Manifest lesbar, jede darin
    genannte Datenbank vorhanden und integer."""
    if path.is_file() and path.name.endswith(".tar.gz"):
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-verify-"))
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(tmp)
        candidates = [p for p in tmp.iterdir() if p.is_dir()]
        if not candidates:
            print("Archiv enthält kein Backup-Verzeichnis.")
            return 1
        target = candidates[0]
    else:
        target = path
        tmp = None

    manifest_path = target / "manifest.json"
    if not manifest_path.exists():
        print(f"Kein manifest.json in {target}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    failures = 0
    for db_name in manifest.get("databases", {}):
        db_path = target / db_name
        if not db_path.exists():
            print(f"  {db_name}: FEHLT")
            failures += 1
            continue
        result = _integrity_check(db_path)
        print(f"  {db_name}: {result}")
        if result != "ok":
            failures += 1

    for dir_name, expected in manifest.get("directories", {}).items():
        actual = sum(1 for p in (target / dir_name).rglob("*") if p.is_file())
        mark = "ok" if actual == expected else f"ABWEICHUNG (erwartet {expected})"
        print(f"  {dir_name}/: {actual} Dateien — {mark}")
        if actual != expected:
            failures += 1

    if tmp:
        shutil.rmtree(tmp)
    print("Ergebnis:", "in Ordnung" if failures == 0 else f"{failures} Beanstandungen")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup aller JARVIS-Daten")
    parser.add_argument("--tag", help="Kennzeichnung, z.B. 'vor-imports' — wird nie automatisch gelöscht")
    parser.add_argument("--include-secrets", action="store_true",
                        help="OAuth-Token und .env mitsichern (standardmäßig aus)")
    parser.add_argument("--no-compress", action="store_true", help="Verzeichnis statt tar.gz")
    parser.add_argument("--keep", type=int, default=KEEP_RUNS, help=f"Ungetaggte Läufe behalten (Standard {KEEP_RUNS})")
    parser.add_argument("--list", action="store_true", help="Vorhandene Backups anzeigen")
    parser.add_argument("--verify", metavar="PFAD", help="Ein bestehendes Backup prüfen")
    args = parser.parse_args()

    if args.list:
        list_backups()
        return 0
    if args.verify:
        return verify(Path(args.verify))

    if not JARVIS_DIR.exists():
        _log(f"{JARVIS_DIR} existiert nicht — nichts zu sichern.")
        return 1

    run_backup(tag=args.tag, include_secrets=args.include_secrets,
               compress=not args.no_compress)
    cleanup(keep=args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
