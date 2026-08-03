"""
Ablage hochgeladener Quelldateien für Wissens-Importe.

Getrennt von import_store.py (Datenbank) und knowledge_import.py (Destillation),
weil hier eine eigene Verantwortung liegt: Dateien aus einer Client-Nachricht
sicher auf die Platte schreiben. Die Dateinamen kommen vom Browser und sind
damit nicht vertrauenswürdig — die Prüfung darauf ist der eigentliche Grund für
dieses Modul.

Ablageort ist ~/.jarvis/imports/<id>/, NICHT knowledge/. Rohmaterial gehört nicht
in die Wissensdatenbank (siehe docs-draft/JARVIS-Datenmodell-und-API.md,
Abschnitt `imports`) — dort landet nur das Destillat.
"""
import base64
import re
import shutil
from pathlib import Path

IMPORTS_DIR = Path.home() / ".jarvis" / "imports"

# Serverseitige Obergrenzen. Der Client bremst zwar schon, aber eine Grenze, die
# nur im Browser existiert, ist keine Grenze.
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 15 * 1024 * 1024
MAX_FILES = 500

# Was eingelesen werden kann. Alles andere wäre entweder nutzlos (Bilder) oder
# ein unnötiges Risiko (ausführbare Dateien).
ALLOWED_SUFFIXES = {".txt", ".md", ".vtt", ".srt"}

_SAFE_NAME_RE = re.compile(r"^[\w\säöüÄÖÜß.,()\[\]&+#'-]+$", re.UNICODE)


class UploadError(Exception):
    pass


def _safe_filename(name: str) -> str:
    """Lässt nur einen einfachen Dateinamen durch — kein Pfad, keine Tricks.

    Geprüft wird ausdrücklich mehr als nur "..": ein Name wie "a/../../b" oder
    ein absoluter Pfad käme sonst durch. Deshalb: Verzeichnisanteile werden
    verworfen (der Browser liefert bei Ordner-Auswahl relative Pfade), und der
    verbleibende Rest muss einem engen Muster entsprechen.
    """
    if not name or not isinstance(name, str):
        raise UploadError("Dateiname fehlt")
    # Nur den letzten Bestandteil nehmen, egal ob / oder \ als Trenner kam.
    base = name.replace("\\", "/").split("/")[-1].strip()
    if not base or base in (".", ".."):
        raise UploadError(f"Unzulässiger Dateiname: {name!r}")
    if base.startswith("."):
        raise UploadError(f"Versteckte Dateien werden nicht übernommen: {base!r}")
    if not _SAFE_NAME_RE.match(base):
        raise UploadError(f"Dateiname enthält unzulässige Zeichen: {base!r}")
    if Path(base).suffix.lower() not in ALLOWED_SUFFIXES:
        raise UploadError(
            f"Dateityp nicht unterstützt: {base!r} "
            f"(erlaubt: {', '.join(sorted(ALLOWED_SUFFIXES))})"
        )
    return base


def target_dir(import_id: int) -> Path:
    return IMPORTS_DIR / str(int(import_id))


def store_files(import_id: int, files: list[dict]) -> dict:
    """Schreibt die Dateien einer Upload-Nachricht in das Verzeichnis des Imports.

    files: [{"filename": str, "data_base64": str}, ...]

    Erst vollständig prüfen, dann schreiben — ein Upload mit einer unzulässigen
    Datei an Position 80 soll nicht 79 Dateien halb abgelegt hinterlassen.
    Gibt {"count": int, "bytes": int, "path": str} zurück.
    """
    if not files:
        raise UploadError("Keine Dateien übermittelt")
    if len(files) > MAX_FILES:
        raise UploadError(f"Zu viele Dateien ({len(files)}, erlaubt {MAX_FILES})")

    prepared: list[tuple[str, bytes]] = []
    total = 0
    seen: set[str] = set()
    for entry in files:
        name = _safe_filename(entry.get("filename", ""))
        if name in seen:
            raise UploadError(f"Dateiname doppelt: {name!r}")
        seen.add(name)
        try:
            payload = base64.b64decode(entry.get("data_base64") or "", validate=True)
        except Exception:
            raise UploadError(f"Datei nicht lesbar (kein gültiges Base64): {name!r}")
        if len(payload) > MAX_FILE_BYTES:
            raise UploadError(f"{name!r} ist zu groß ({len(payload)} B)")
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            raise UploadError(f"Upload insgesamt zu groß (> {MAX_TOTAL_BYTES} B)")
        prepared.append((name, payload))

    directory = target_dir(import_id)
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in prepared:
        (directory / name).write_bytes(payload)

    print(f"[import_upload] {len(prepared)} Dateien ({total} B) → {directory}", flush=True)
    return {"count": len(prepared), "bytes": total, "path": str(directory)}


def discard(import_id: int) -> None:
    """Entfernt das Verzeichnis eines Imports. Wird beim Fehlschlag direkt nach
    dem Anlegen aufgerufen, damit keine verwaisten Dateien zurückbleiben."""
    directory = target_dir(import_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
