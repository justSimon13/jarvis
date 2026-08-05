"""
Zustand des HP-Servers als Abfrage — Ersatz für die lesende Hälfte von
`run_command` (services/coding_engine.py, entfernt 2026-08-04).

Unterschied zum alten Weg: der Aufrufer sagt WAS er wissen will, nicht WELCHER
Befehl läuft. `run_command` nahm einen freien Shell-Befehl entgegen und prüfte
ihn gegen eine Whitelist — das ist eine Shell mit Türsteher und damit derselbe
Ansatz, der für die Clients bewusst verworfen wurde. Hier stehen die Befehle
fest im Code.

Die SCHREIBENDE Hälfte von run_command (sudo, Pakete installieren, Dienste
neu starten — jeweils mit Freigabe im Dashboard und Passwort-Popup) ist
ersatzlos entfallen: Simon hat SSH und benutzt es täglich; ein Befehl, der erst
eine Freigabe braucht, dann asynchron läuft und dessen Ergebnis per
Benachrichtigung kommt, gewinnt gegen ein Terminal nicht.

Alles hier läuft ohne Shell (subprocess mit Argumentliste), ohne sudo, und
verändert nichts.
"""
import shutil
import subprocess

_TIMEOUT = 10

# Dienste, nach denen gefragt werden darf. Bewusst eine feste Liste statt eines
# freien Namens — sonst wäre "systemctl status <beliebig>" wieder ein Stück
# Shell durch die Hintertür.
_SERVICES = ("jarvis", "jarvis-web", "jarvis-dashboard", "jarvis-backup.timer")


def _run(args: list[str]) -> str:
    """Führt einen festen Befehl aus. Keine Shell, kein Nutzer-Eingabewert in
    args — die Aufrufer unten setzen ausschließlich Konstanten ein."""
    if not shutil.which(args[0]):
        return f"({args[0]} nicht verfügbar)"
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "(Zeitüberschreitung)"
    except Exception as e:  # noqa: BLE001 — Diagnose darf nie den Aufrufer stören
        return f"(Fehler: {e})"
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return out or "(keine Ausgabe)"


def get_status(service: str | None = None, log_lines: int = 0) -> dict:
    """Zustand des Servers: Laufzeit, Speicherplatz, Arbeitsspeicher, Dienste.

    service/log_lines optional — mit beidem zusätzlich die letzten Journal-Zeilen
    genau eines bekannten Dienstes.
    """
    daten = {
        "uptime": _run(["uptime"]),
        "disk": _run(["df", "-h", "/"]),
        "memory": _run(["free", "-h"]),
        "services": {
            name: _run(["systemctl", "is-active", name]) for name in _SERVICES
        },
    }

    if service:
        if service not in _SERVICES:
            daten["log_error"] = (
                f"Unbekannter Dienst: {service}. Bekannt: {', '.join(_SERVICES)}"
            )
        elif log_lines:
            zeilen = max(1, min(int(log_lines), 200))
            daten["log"] = {
                "service": service,
                "lines": zeilen,
                "text": _run(["journalctl", "-u", service, "-n", str(zeilen), "--no-pager"]),
            }
    return daten
