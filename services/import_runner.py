"""
Hintergrundlauf für Wissens-Importe.

Ein Import sind je nach Kurs 50–150 Modellaufrufe über mehrere Minuten. Im
Event-Loop des Servers ausgeführt würde das den Chat für alle Clients anhalten,
also läuft er in einem eigenen Thread — gleiches Muster wie
learning.process_session() und thread_naming.run_startup_sweep().

Trennung zu knowledge_import.py: dort steht, WAS destilliert wird, hier, WIE ein
Lauf verwaltet wird (nur einer gleichzeitig, anhalten, Fortschritt melden). Das
Kommandozeilenwerkzeug kommt ohne dieses Modul aus.
"""
import threading
from pathlib import Path

from services import import_store
from services import knowledge_import

# Nur ein Import gleichzeitig. Zwei parallele Läufe über dieselbe Quelle würden
# einander die Abschnitte wegschreiben, zwei über verschiedene Quellen das
# Budget unbemerkt verdoppeln. Die Beschränkung ist absichtlich global, nicht
# pro Import.
_lock = threading.Lock()
_current: dict | None = None
_stop_flag = threading.Event()


def status() -> dict:
    """Was läuft gerade? Für die Ansicht beim Verbindungsaufbau — ohne das
    wüsste ein frisch geöffnetes Fenster nichts von einem laufenden Import."""
    with _lock:
        if not _current:
            return {"running": False}
        return {"running": True, **_current}


def request_stop(import_id: int | None = None) -> bool:
    """Bittet den laufenden Import anzuhalten. Kooperativ: der Lauf prüft die
    Marke vor der nächsten Einheit, bricht also nie mitten in einem bezahlten
    Aufruf ab. Rückgabe: ob überhaupt etwas zum Anhalten da war."""
    with _lock:
        if not _current:
            return False
        if import_id is not None and _current.get("import_id") != import_id:
            return False
    _stop_flag.set()
    return True


def start(import_id: int, on_progress=None, on_finished=None) -> dict:
    """Startet einen Import im Hintergrund.

    Gibt sofort zurück — {"ok": True} heißt "angenommen", nicht "fertig".
    """
    record = import_store.get(import_id)
    if not record:
        return {"ok": False, "error": f"Import #{import_id} nicht gefunden"}
    source = Path(record["source_path"] or "")
    if not record["source_path"] or not source.exists():
        return {"ok": False, "error": "Quelle nicht vorhanden — Upload unvollständig?"}

    with _lock:
        global _current
        if _current:
            return {"ok": False,
                    "error": f"Import #{_current['import_id']} läuft bereits. "
                             f"Es läuft immer nur einer."}
        _current = {"import_id": import_id, "title": record["title"],
                    "topic": record["topic"], "done": 0,
                    "total": record["lecture_count"], "cost_usd": record["cost_usd"] or 0.0}
        _stop_flag.clear()

    def _progress(update: dict):
        with _lock:
            if _current is not None:
                _current.update({k: update[k] for k in ("done", "total", "cost_usd")
                                 if k in update})
        if on_progress:
            on_progress(update)

    def _work():
        global _current
        try:
            result = knowledge_import.run(
                source=source,
                topic=record["topic"],
                course_title=record["title"],
                model=record["model"] or "claude-sonnet-5",
                budget_usd=record["max_budget_usd"] if record["max_budget_usd"] is not None else 5.0,
                on_progress=_progress,
                should_stop=_stop_flag.is_set,
            )
        except Exception as e:
            print(f"[import_runner] Import #{import_id} abgebrochen: {e}", flush=True)
            # Status selbst setzen: knowledge_import.run() kam nicht bis zu
            # seinem eigenen Abschluss, die Zeile stünde sonst dauerhaft auf
            # 'running' und wäre nicht mehr startbar.
            try:
                import_store.update(import_id, status="failed")
            except Exception:
                pass
            result = {"status": "failed", "error": str(e)}
        finally:
            with _lock:
                _current = None
            _stop_flag.clear()
        if on_finished:
            try:
                on_finished({"import_id": import_id, **result})
            except Exception as e:
                print(f"[import_runner] Abschlussmeldung fehlgeschlagen: {e}", flush=True)

    threading.Thread(target=_work, name=f"import-{import_id}", daemon=True).start()
    print(f"[import_runner] Import #{import_id} gestartet", flush=True)
    return {"ok": True, "import_id": import_id}
