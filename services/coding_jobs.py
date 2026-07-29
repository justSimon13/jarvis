"""Coding-Jobs über den Mac-Worker-Kanal: JARVIS orchestriert (Auftrag rein,
Branch+PR raus), 'claude -p' headless auf dem Mac-Worker ist der Executor
(siehe jarvis-web's src/lib/localExec.js::runClaudeCodeRun). Ein anderer Weg
als services/coding_engine.py (Claude Agent SDK + Git-Worktree, läuft
server-seitig für JARVIS' Eigenentwicklung) — beide bestehen nebeneinander,
dieses Modul fasst coding_engine.py nicht an.

Vollständig asynchron: start_job() legt die Job-Zeile an, schickt die Anfrage
per local_exec.dispatch_nowait() ab (fire-and-forget, KEIN Warten auf eine
Quittierung — das Warten hat real zu falschen "nichts angestoßen"-Meldungen
und dadurch doppelt gestarteten Jobs geführt, obwohl der Job längst lief) und
kehrt sofort zurück. Das Ergebnis kommt Minuten später als eigene
coding_job_result-Nachricht (P.CODING_JOB_RESULT), von server.py an
resolve_job_result() weitergereicht. Fehler bei der Vorbereitung auf dem Mac
(Allowlist, Konto, Git) kommen über denselben Kanal als status='failed'.

Ist kein Worker verbunden (oder läuft bereits ein Job), bleibt der neue Job
auf status='pending' und wird automatisch gestartet, sobald sich ein Worker
anmeldet (on_worker_connected, von server.py bei client_hello mit
'local_exec'-Capability aufgerufen) bzw. der laufende Job fertig ist
(resolve_job_result stößt den nächsten an) — immer nur einer zur Zeit, das
Arbeitsverzeichnis verträgt keine parallelen Läufe.

V1: genau ein hartcodiertes privates Projekt (kein project-Parameter, keine
projects-Tabelle) — _JOB_CWD muss mit PROJECT_ALLOWLIST[0] in jarvis-web's
localExec.js übereinstimmen, beide Seiten geben unabhängig voneinander frei."""
from __future__ import annotations
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from services import local_exec

_DB_PATH = Path.home() / ".jarvis" / "jobs.db"

# V1-Projektkonfiguration — ein einziges privates Projekt. Auswahl mehrerer
# Projekte ist Schritt C ("data_scope und fehlende Beziehungen") aus der
# Konzept-Reihenfolge, nicht dieser Schritt.
_JOB_CWD = "/Users/simon/Documents/Arbeit/Simon Fischer Consulting/Apps/jarvis-testrepo"
_JOB_BASE_BRANCH = "main"

_STALE_AFTER = timedelta(hours=1)

_manager = None
_dispatcher = None


def _get_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT,
            instruction   TEXT,
            cwd           TEXT,
            base_branch   TEXT,
            branch        TEXT,
            status        TEXT,
            session_id    TEXT,
            cost_usd      REAL,
            result        TEXT,
            changed_files TEXT,
            denials       TEXT,
            pr_url        TEXT,
            created_at    TEXT,
            updated_at    TEXT
        )
    """)
    return conn


def _now() -> str:
    return datetime.now().isoformat()


def init(client_manager, dispatcher) -> None:
    global _manager, _dispatcher
    _manager = client_manager
    _dispatcher = dispatcher
    _fail_stale_jobs()


def _fail_stale_jobs() -> None:
    """Bricht der Worker ab, ohne coding_job_result zu senden (Absturz,
    Verbindungsabbruch, Mac schläft ein, Server-Neustart mitten im Lauf),
    bliebe eine Zeile für immer auf 'running' stehen. Läuft einmalig beim
    Serverstart — 1h Schwelle, damit ein Neustart während eines echten, noch
    laufenden Jobs ihn nicht fälschlich als gescheitert markiert.

    Bewusst über updated_at statt created_at: ein Job kann inzwischen längere
    Zeit auf 'pending' gewartet haben, bevor er startete (updated_at wird beim
    tatsächlichen Losschicken gesetzt) — created_at würde so einen Job direkt
    nach dem Start fälschlich als hängend werten. 'pending'-Jobs selbst sind
    hier ausgenommen: die warten absichtlich, ohne Frist."""
    cutoff = (datetime.now() - _STALE_AFTER).isoformat()
    conn = _get_db()
    stale_ids = [
        row[0] for row in
        conn.execute("SELECT id FROM jobs WHERE status = 'running' AND updated_at < ?", (cutoff,)).fetchall()
    ]
    if stale_ids:
        conn.execute(
            "UPDATE jobs SET status = 'failed', result = ?, updated_at = ? WHERE status = 'running' AND updated_at < ?",
            ("Kein Ergebnis empfangen (Server-Neustart, Job vermutlich hängen geblieben).", _now(), cutoff),
        )
        conn.commit()
        print(f"[coding_jobs] {len(stale_ids)} hängende(n) Job(s) als 'failed' markiert: {stale_ids}", flush=True)
    conn.close()


def _build_prompt(instruction: str) -> str:
    """Kein Client baut Prompts (siehe docs-draft/JARVIS-Datenmodell-und-API.md)
    — der volle -p-Text entsteht hier, der Worker ruft claude -p mit diesem
    String unverändert auf."""
    return (
        f"{instruction}\n\n"
        "Beende deine Antwort mit einer kurzen Zusammenfassung: was geändert wurde, "
        "was bewusst nicht, und wo du vom naheliegenden Vorgehen abgewichen bist "
        "(falls zutreffend). Committe/pushe/erstelle keinen PR selbst — das übernimmt "
        "die aufrufende Umgebung."
    )


def start_job(instruction: str, title: str | None = None) -> str:
    """Von tools.execute() aufgerufen ('start_coding_job'). Vollständig
    asynchron: legt die Zeile an, schickt den Auftrag fire-and-forget ab
    (KEIN Warten auf irgendeine Antwort — das Warten hat real zu falschen
    "nichts angestoßen"-Timeout-Meldungen und dadurch doppelt gestarteten Jobs
    geführt) und kehrt sofort zurück. Alle Fehler nach dem Absenden (Allowlist,
    Konto, Git) kommen als coding_job_result mit status='failed' per
    Notification. Ist kein Worker verbunden oder läuft bereits ein Job, bleibt
    der neue auf 'pending' und startet automatisch später."""
    if not instruction or not instruction.strip():
        return "Keine Aufgabenbeschreibung angegeben."

    conn = _get_db()
    now = _now()
    cur = conn.execute(
        """INSERT INTO jobs (title, instruction, cwd, base_branch, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
        (title or instruction[:80], instruction, _JOB_CWD, _JOB_BASE_BRANCH, now, now),
    )
    conn.commit()
    job_id = cur.lastrowid
    branch = f"jarvis/job-{job_id}"
    conn.execute("UPDATE jobs SET branch = ? WHERE id = ?", (branch, job_id))
    conn.commit()
    running = conn.execute(
        "SELECT id FROM jobs WHERE status = 'running' AND id != ? LIMIT 1", (job_id,)
    ).fetchone()
    conn.close()

    # Immer nur ein Lauf zur Zeit — das eine Arbeitsverzeichnis verträgt keine
    # parallelen git checkout/branch/commit (client-seitig zusätzlich per Lock
    # abgesichert, aber dort würde der zweite Job scheitern statt zu warten).
    if running:
        return (
            f"Job #{job_id} vorgemerkt — Job #{running[0]} läuft noch, der neue startet "
            "automatisch danach. Ergebnis kommt per Benachrichtigung."
        )

    if _try_dispatch(job_id, instruction, branch):
        return f"Job #{job_id} angelegt, läuft — Ergebnis kommt per Benachrichtigung."

    return (
        f"Job #{job_id} vorgemerkt — gerade kein Mac-Worker verbunden. Er startet "
        "automatisch, sobald sich einer anmeldet. Ergebnis kommt per Benachrichtigung."
    )


def _try_dispatch(job_id: int, instruction: str, branch: str) -> bool:
    """Schickt einen Job fire-and-forget an den Worker. True = rausgeschickt
    (Zeile auf 'running'), False = kein Worker erreichbar (Zeile bleibt/wird
    'pending', kein Fehler — Wiederholung über on_worker_connected bzw.
    resolve_job_result)."""
    result = local_exec.dispatch_nowait(
        "claude_code_run", cwd=_JOB_CWD, base_branch=_JOB_BASE_BRANCH, branch=branch,
        instruction=_build_prompt(instruction), job_id=job_id,
    )
    if not result.get("ok"):
        print(f"[coding_jobs] Job #{job_id} bleibt vorgemerkt: {result.get('error')}", flush=True)
        return False

    conn = _get_db()
    conn.execute("UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?", (_now(), job_id))
    conn.commit()
    conn.close()
    print(f"[coding_jobs] Job #{job_id} an den Worker geschickt (Branch {branch}).", flush=True)
    return True


def _start_next_pending() -> None:
    """Startet den ältesten 'pending'-Job, sofern gerade keiner läuft. Wird
    aufgerufen wenn ein Worker sich anmeldet (on_worker_connected) und wenn ein
    Lauf fertig wird (resolve_job_result) — dadurch arbeiten mehrere vorgemerkte
    Jobs nacheinander ab, nie parallel."""
    conn = _get_db()
    running = conn.execute("SELECT id FROM jobs WHERE status = 'running' LIMIT 1").fetchone()
    row = None
    if not running:
        row = conn.execute(
            "SELECT id, instruction, branch FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
    conn.close()
    if row:
        _try_dispatch(row[0], row[1], row[2])


def on_worker_connected() -> None:
    """Von server.py bei jedem client_hello mit 'local_exec'-Capability
    aufgerufen. Läuft in einem eigenen Thread, damit der aufrufende
    WebSocket-Handler (async) nicht auf SQLite/Senden wartet."""
    threading.Thread(target=_start_next_pending, daemon=True).start()


def resolve_job_result(payload: dict) -> None:
    """Von server.py bei CODING_JOB_RESULT aufgerufen — Phase 2, unabhängig von
    jeder offenen Anfrage (kein pending_events-Mechanismus nötig, im Gegensatz
    zu local_exec.py's Phase 1: hier sucht die Zeile einfach über job_id).
    Schreibt das Ergebnis, stößt eine Notification an."""
    job_id = payload.get("job_id")
    if job_id is None:
        print(f"[coding_jobs] coding_job_result ohne job_id: {payload}", flush=True)
        return

    status = payload.get("status") or ("done" if payload.get("ok") else "failed")
    conn = _get_db()
    conn.execute(
        """UPDATE jobs SET status = ?, session_id = ?, cost_usd = ?, result = ?,
                            changed_files = ?, denials = ?, pr_url = ?, updated_at = ?
           WHERE id = ?""",
        (
            status, payload.get("session_id"), payload.get("cost_usd"), payload.get("result"),
            payload.get("changed_files"), json.dumps(payload.get("denials") or []),
            payload.get("pr_url"), _now(), job_id,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT title, branch FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()

    title = row[0] if row else f"Job #{job_id}"
    branch = row[1] if row else "?"
    pr_note = f" — PR: {payload['pr_url']}" if payload.get("pr_url") else ""
    summary = (payload.get("result") or "")[:300]
    error_note = "" if status == "done" else " ⚠️ mit Fehler/Abbruch beendet"
    cost = payload.get("cost_usd") or 0.0

    _notify(
        f"[JARVIS Code] Job #{job_id} ({title}) auf {branch} fertig{error_note}{pr_note} "
        f"— ${cost:.2f}: {summary}",
        # priority="high": bleibt stehen bis manuell weggeklickt, gleiches
        # Muster wie coding_engine._notify — eine verpasste Fertig-Meldung ist
        # genauso schlimm wie keine.
        priority="high", expires_in_min=1440,
    )

    # Der Worker ist jetzt frei — falls Jobs auf 'pending' warten, den ältesten
    # direkt anstoßen (sequenzielle Abarbeitung, siehe _start_next_pending).
    _start_next_pending()


def get_job_status(job_id: int | None = None) -> dict:
    """Für check_coding_job_status — ohne job_id der zuletzt gestartete Job.
    Bei status='running' zusätzlich die bisherige Laufzeit in Minuten, damit
    Simon "läuft das noch normal?" beantwortet bekommt, ohne auf den
    1-Stunden-Stale-Cleanup warten zu müssen."""
    conn = _get_db()
    if job_id is not None:
        cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    else:
        cur = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        conn.close()
        return {"active": False}
    cols = [d[0] for d in cur.description]
    conn.close()

    data = dict(zip(cols, row))
    # updated_at statt created_at: ein Job kann vor dem Start auf 'pending'
    # gewartet haben — die Laufzeit zählt ab dem tatsächlichen Losschicken.
    if data.get("status") == "running" and data.get("updated_at"):
        try:
            started = datetime.fromisoformat(data["updated_at"])
            data["running_since_minutes"] = round((datetime.now() - started).total_seconds() / 60, 1)
        except ValueError:
            pass
    return data


def _notify(text: str, priority: str = "normal", expires_in_min: int = 60) -> None:
    if _dispatcher:
        _dispatcher.notify(text, channels=["dashboard"], priority=priority, expires_in_min=expires_in_min)
    else:
        print(f"[coding_jobs] (kein Dispatcher) {text}", flush=True)
