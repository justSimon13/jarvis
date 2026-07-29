"""Coding-Jobs über den Mac-Worker-Kanal: JARVIS orchestriert (Auftrag rein,
Branch+PR raus), 'claude -p' headless auf dem Mac-Worker ist der Executor
(siehe jarvis-web's src/lib/localExec.js::runClaudeCodeRun). Ein anderer Weg
als services/coding_engine.py (Claude Agent SDK + Git-Worktree, läuft
server-seitig für JARVIS' Eigenentwicklung) — beide bestehen nebeneinander,
dieses Modul fasst coding_engine.py nicht an.

Zweiphasig, weil ein Lauf Minuten dauert (local_exec.py's Default-Timeout ist
60s): start_job() schickt die Anfrage über den bestehenden, unveränderten
local_exec.dispatch() — die Antwort kommt schnell zurück ("gestartet", Phase
1). Das eigentliche Ergebnis kommt Minuten später als eigene
coding_job_result-Nachricht (P.CODING_JOB_RESULT), von server.py an
resolve_job_result() weitergereicht (Phase 2).

V1: genau ein hartcodiertes privates Projekt (kein project-Parameter, keine
projects-Tabelle) — _JOB_CWD muss mit PROJECT_ALLOWLIST[0] in jarvis-web's
localExec.js übereinstimmen, beide Seiten geben unabhängig voneinander frei."""
from __future__ import annotations
import json
import sqlite3
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
    laufenden Jobs ihn nicht fälschlich als gescheitert markiert."""
    cutoff = (datetime.now() - _STALE_AFTER).isoformat()
    conn = _get_db()
    stale_ids = [
        row[0] for row in
        conn.execute("SELECT id FROM jobs WHERE status = 'running' AND created_at < ?", (cutoff,)).fetchall()
    ]
    if stale_ids:
        conn.execute(
            "UPDATE jobs SET status = 'failed', result = ?, updated_at = ? WHERE status = 'running' AND created_at < ?",
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
    """Von tools.execute() aufgerufen ('start_coding_job'). Kehrt zurück sobald
    der Worker den Lauf gestartet hat (lokal_exec.dispatch() blockiert nur für
    Phase 1, Sekunden) — das eigentliche Ergebnis kommt per Notification, wenn
    resolve_job_result() aufgerufen wird. Gleiches Blockier-Muster wie
    services/tickets.py::sync_tickets() — kein Deadlock, weil die Antwort über
    eine andere WebSocket-Verbindung (den Tauri-Client) hereinkommt, während
    der Event-Loop weiterläuft."""
    if not instruction or not instruction.strip():
        return "Keine Aufgabenbeschreibung angegeben."

    conn = _get_db()
    now = _now()
    cur = conn.execute(
        """INSERT INTO jobs (title, instruction, cwd, base_branch, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'running', ?, ?)""",
        (title or instruction[:80], instruction, _JOB_CWD, _JOB_BASE_BRANCH, now, now),
    )
    conn.commit()
    job_id = cur.lastrowid
    branch = f"jarvis/job-{job_id}"
    conn.execute("UPDATE jobs SET branch = ? WHERE id = ?", (branch, job_id))
    conn.commit()
    conn.close()

    result = local_exec.dispatch(
        "claude_code_run", cwd=_JOB_CWD, base_branch=_JOB_BASE_BRANCH, branch=branch,
        instruction=_build_prompt(instruction), job_id=job_id,
    )
    if not result.get("ok"):
        _mark_failed(job_id, result.get("error") or "Unbekannter Fehler beim Start.")
        return f"Konnte den Coding-Job nicht starten: {result.get('error')}"

    # Kein Branch-Name hier — der Client quittiert jetzt sofort nach Empfang,
    # bevor Allowlist-Prüfung/Konto-Check/Git-Vorbereitung überhaupt gelaufen
    # sind (die dauerten real über 60s und liefen sonst in local_exec.py's
    # Dispatch-Timeout, obwohl der Job tatsächlich durchlief). Der Branch steht
    # zu diesem Zeitpunkt noch nicht wirklich, erst nach der Allowlist-Prüfung
    # auf dem Worker wird er angelegt — Fehler dabei kommen als eigenes
    # coding_job_result mit status='failed', nicht mehr als Dispatch-Fehler hier.
    return f"Job #{job_id} angenommen. Ich melde mich per Notification, wenn er fertig ist."


def _mark_failed(job_id: int, reason: str) -> None:
    conn = _get_db()
    conn.execute(
        "UPDATE jobs SET status = 'failed', result = ?, updated_at = ? WHERE id = ?",
        (reason, _now(), job_id),
    )
    conn.commit()
    conn.close()


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
    if data.get("status") == "running" and data.get("created_at"):
        try:
            started = datetime.fromisoformat(data["created_at"])
            data["running_since_minutes"] = round((datetime.now() - started).total_seconds() / 60, 1)
        except ValueError:
            pass
    return data


def _notify(text: str, priority: str = "normal", expires_in_min: int = 60) -> None:
    if _dispatcher:
        _dispatcher.notify(text, channels=["dashboard"], priority=priority, expires_in_min=expires_in_min)
    else:
        print(f"[coding_jobs] (kein Dispatcher) {text}", flush=True)
